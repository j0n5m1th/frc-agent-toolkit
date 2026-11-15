"""Decode WPILib `struct:<Name>` values using the server's own schemas.

Each struct type is published as a retained `/.schema/struct:<Name>` topic (type `structschema`) holding a text schema, e.g.
``Pose2d`` = ``Translation2d translation;Rotation2d rotation``. We cache those and decode raw bytes with them, so any struct
works (no hard-coded table).

Format (WPILib struct spec): fields separated by ``;``, each ``[enum{a=0,b=1}] type name[N] [: bits]``. Little-endian, no
padding. Bit-fields pack LSB-first into storage words of their declared type. Nested structs are flattened to dotted keys
(``translation.x``). Packing rule inferred from real data: a `bool:1` bit-field joins whatever integer storage word is open
(ControlWord is 8 bytes: uint64 bit-fields, then bool bit-fields).
"""

import logging
import re
import struct
import threading
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from ..errors import WPILibMCPError

_log = logging.getLogger(__name__)
_logged: set[str] = set()  # decode failures already reported, so a stream of bad samples logs once per type
_MAX_LOGGED = 100
MAX_FIELD_NAMES = 10_000  # most dotted names `field_names` lists for one struct (a hostile `Inner a[100000000]` would need gigabytes)
MAX_DECODED = 30_000  # decoded values kept per schema revision; a full cache starts over
MAX_DECODED_BYTES = 64 * 1024 * 1024  # and at most this much memory, estimated (see `_decode_uncached`)
MAX_DECODE_UNITS = 300_000  # most names a single decode call may produce (a struct's names times its array length); more fails fast
_UNIT_CAP = MAX_DECODE_UNITS + 1  # counts of names and cells are clipped here
_MAX_LEVELS = 9  # struct inside struct inside ..., counting the outer one
# What a decoded value costs, measured with tracemalloc on CPython 3.13 and rounded up: a dotted key is a new string (~90 bytes
# with its dict slot), a number or an element of a primitive array ~40, and every value starts at ~256.
_COST_PER_NAME, _COST_PER_CELL, _COST_PER_ENTRY, _COST_PER_BYTE = 160, 48, 256, 2


def debug_once(type_str: str, e: Exception) -> None:
    if type_str not in _logged and len(_logged) < _MAX_LOGGED:
        _logged.add(type_str)
        _log.debug("cannot decode %s: %r", type_str, e)

_PRIMS = {
    "bool": ("?", 1), "char": ("c", 1),
    "int8": ("b", 1), "uint8": ("B", 1), "int16": ("h", 2), "uint16": ("H", 2),
    "int32": ("i", 4), "uint32": ("I", 4), "int64": ("q", 8), "uint64": ("Q", 8),
    "float": ("f", 4), "float32": ("f", 4), "double": ("d", 8), "float64": ("d", 8),
}
_FIELD = re.compile(
    r"^(?:enum\s*\{(?P<enum>[^}]*)\}\s*)?(?P<type>\w+)\s+(?P<name>\w+)"
    r"(?:\[(?P<count>\d+)\])?(?:\s*:\s*(?P<bits>\d+))?$"
)


class SchemaError(WPILibMCPError):
    """A struct or protobuf schema we cannot parse or resolve."""


class _Shape(NamedTuple):
    """How much one decoded struct holds, counted without decoding: dict keys, primitive-array cells and nesting levels."""

    names: int  # keys of the flat dict; a struct with no fields counts as one, so empty structs cannot be nested for free
    cells: int  # values inside primitive arrays (`double a[100]` is one name and 100 cells)
    levels: int


class _OpenWord(NamedTuple):
    """The bit-field storage word currently being filled (`raw` is only meaningful when decoding)."""

    type: str
    bits_used: int
    width: int  # bytes
    raw: int = 0


@dataclass
class Field:
    name: str
    type: str  # primitive or struct name
    count: int | None = None
    bits: int | None = None
    enum: dict[int, str] = field(default_factory=dict)


def _parse_enum(body: str) -> dict[int, str]:
    enum: dict[int, str] = {}
    for item in body.split(","):
        k, separator, v = item.partition("=")
        if not separator or not k.strip():
            raise SchemaError(f"unparseable enum entry {item!r}")
        try:
            enum[int(v)] = k.strip()
        except ValueError as e:
            raise SchemaError(f"unparseable enum entry {item!r}") from e
    return enum


def _check_bit_field(typ: str, bits: int, decl: str) -> None:
    if typ not in _PRIMS or typ in ("char", "float", "float32", "double", "float64") \
            or not 1 <= bits <= _PRIMS[typ][1] * 8 or (typ == "bool" and bits != 1):
        raise SchemaError(f"invalid bit field {decl!r}")


def parse_schema(text: str) -> list[Field]:
    fields = []
    for decl in filter(None, (d.strip() for d in text.split(";"))):
        m = _FIELD.match(decl)
        if not m:
            raise SchemaError(f"unparseable field {decl!r}")
        enum = _parse_enum(m["enum"]) if m["enum"] else {}
        if m["bits"]:
            _check_bit_field(m["type"], int(m["bits"]), decl)
        fields.append(Field(m["name"], m["type"], int(m["count"]) if m["count"] else None,
                            int(m["bits"]) if m["bits"] else None, enum))
    return fields


class Registry:
    """Schema text by struct name, filled from `/.schema/` topics; parsed lazily.
    Thread safe by copy-on-write: `_text` is never changed in place and `_parsed` only gains entries; a writer builds a new dict
    and assigns it (atomic), so a reader never sees a half-cleared one. A lock only keeps two writers from losing each other's
    schema. `_sizes` and `_decoded` are caches for the current text only: `add` and `clear` swap in fresh ones, and a lost entry
    only costs a recompute.
    """

    def __init__(self) -> None:
        self._text: dict[str, str] = {}
        self._parsed: dict[str, list[Field]] = {}
        self._sizes: dict[str, int] = {}
        self._shapes: dict[str, _Shape] = {}
        self._decoded: dict[tuple[str, bytes], tuple[int, Any]] = {}  # (type, bytes) -> (estimated cost, value)
        self._decoded_cost = 0  # estimated bytes held by `_decoded` (changed only under `_lock`, with the swap of `_decoded`)
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._text = {}
            self._parsed = {}
            self._sizes = {}
            self._shapes = {}
            self._decoded = {}
            self._decoded_cost = 0
        _logged.clear()  # with the schemas gone, the once-per-type decode reasons start over on the next connection

    def entries(self) -> list[tuple[str, bytes]]:
        """Every loaded schema as the (`/.schema/struct:<Name>`, UTF-8 text) pair `add` was given."""
        return [(f"/.schema/struct:{name}", text.encode("utf-8")) for name, text in self._text.items()]

    def add(self, topic_name: str, text: str) -> None:
        # '/.schema/struct:Pose2d' -> 'Pose2d'
        with self._lock:
            self._text = {**self._text, topic_name.rsplit("struct:", 1)[-1]: text}
            self._parsed = {}  # a fresh dict, not `.clear()`: a reader holding the old one is not disturbed
            self._sizes = {}
            self._shapes = {}
            self._decoded = {}
            self._decoded_cost = 0

    def fields(self, name: str) -> list[Field]:
        parsed = self._parsed  # one read each: the dicts may be swapped under us, but never changed
        hit = parsed.get(name)
        if hit is not None:
            return hit
        text = self._text.get(name)
        if text is None:
            raise SchemaError(f"no schema for struct {name!r}")
        fields = parse_schema(text)
        parsed[name] = fields  # at worst into a dict that was just replaced: only a lost cache entry
        return fields

    def size(self, name: str) -> int:
        """Encoded size in bytes of one struct (cached until a schema is added)."""
        sizes = self._sizes  # one read: the dict may be swapped under us, never changed except by adding entries
        hit = sizes.get(name)
        if hit is not None:
            return hit
        total = 0
        open_word: _OpenWord | None = None
        for f in self.fields(name):
            width = _PRIMS[f.type][1] if f.type in _PRIMS else self.size(f.type)
            if f.bits is not None:
                if open_word is not None and _fits_open_word(open_word, f):
                    open_word = open_word._replace(bits_used=open_word.bits_used + f.bits)
                else:
                    total += width
                    open_word = _OpenWord(f.type, f.bits, width)
                continue
            open_word = None
            total += width * (f.count or 1)
        sizes[name] = total
        return total

    def decode(self, type_str: str, data: bytes) -> Any:
        """`struct:Name` or `struct:Name[]` -> flat dict (or list of dicts); None if undecodable."""
        try:
            return self.decode_strict(type_str, data)
        except SchemaError as e:
            debug_once(type_str, e)
            return None

    def decode_strict(self, type_str: str, data: bytes) -> Any:
        """Like `decode`, but raises SchemaError saying why the schema or the bytes cannot be decoded."""
        decoded = self._decoded  # one read: a schema added meanwhile swaps in a new dict, this one is then only dropped
        key = (type_str, data)
        hit = decoded.get(key)
        if hit is not None:
            return _fresh(hit[1])
        value, cost = self._decode_uncached(type_str, data)
        if cost > MAX_DECODED_BYTES:
            return value  # one value alone is too big to keep
        with self._lock:  # the dict, its cost and the swap in `add`/`clear` change together
            if decoded is not self._decoded:
                return value  # the schemas changed while this decoded: the entry would belong to the old ones
            if len(decoded) >= MAX_DECODED or self._decoded_cost + cost > MAX_DECODED_BYTES:
                decoded.clear()  # full: start over (an LRU would thrash on a scan of a longer topic)
                self._decoded_cost = 0
            decoded[key] = (cost, _fresh(value))
            self._decoded_cost += cost
        return value

    def _decode_uncached(self, type_str: str, data: bytes) -> tuple[Any, int]:
        """The decoded value and its estimated size in memory."""
        is_array = type_str.endswith("[]")
        name = type_str.removeprefix("struct:").removesuffix("[]")
        if name not in self._text:
            raise SchemaError(f"no schema for {type_str!r}: the server has not announced it (yet)")
        try:
            size = self.size(name)
            if size == 0:
                raise SchemaError(f"{type_str!r} has zero size, so no bytes can decode to it")
            if not data:
                raise SchemaError(f"no bytes to decode as {type_str!r}")
            if len(data) % size or (not is_array and len(data) != size):
                raise SchemaError(f"{len(data)} bytes is the wrong length for {type_str!r} "
                                  f"(each one is {size} bytes)")
            shape = self._shape(name)
            count = len(data) // size
            if shape.names * count > MAX_DECODE_UNITS:
                raise SchemaError(f"{type_str!r} would decode to {shape.names * count:,} values (the limit is {MAX_DECODE_UNITS:,})")
            items = [self._decode_one(name, data, o) for o in range(0, len(data), size)]
            cost = _COST_PER_ENTRY + len(data) * _COST_PER_BYTE + count * (shape.names * _COST_PER_NAME + shape.cells * _COST_PER_CELL)
        except struct.error as e:
            raise SchemaError(f"malformed bytes for {type_str!r}: {e}") from e
        except KeyError as e:
            raise SchemaError(f"malformed bytes for {type_str!r}: unresolved {e}") from e
        except RecursionError as e:
            raise SchemaError(f"{type_str!r} nests too deeply") from e
        return (items if is_array else items[0]), cost

    def field_names(self, name: str) -> list[str]:
        """Dotted names a decoded value of this struct will have (nested structs flattened, as in `decode`)."""
        n = self._shape(name).names
        if n > MAX_FIELD_NAMES:
            raise SchemaError(f"struct {name!r} has {_over(n)} field names once its arrays are expanded (the limit is {MAX_FIELD_NAMES:,})")
        return self._names_of(name)

    def _names_of(self, name: str) -> list[str]:
        out: list[str] = []
        for f in self.fields(name):
            if f.type in _PRIMS:
                out.append(f.name)
                continue
            for i in range(f.count or 1):
                prefix = f.name if f.count is None else f"{f.name}[{i}]"
                out.extend(f"{prefix}.{k}" for k in self._names_of(f.type))
        return out

    def _shape(self, name: str, trail: tuple[str, ...] = ()) -> _Shape:
        """What one decoded `name` holds, counted without decoding and remembered per struct until a schema is added, so a
        schema that nests the same struct many times is walked once per struct, not once per use. Counts stop growing at a
        limit (`_UNIT_CAP`), so a hostile `Inner a[100000000]` costs nothing. Raises SchemaError for a cycle or too deep."""
        shapes = self._shapes  # one read, as in `fields`
        hit = shapes.get(name)
        if hit is None:
            if name in trail or len(trail) >= _MAX_LEVELS:
                raise SchemaError(f"struct {name!r} nests too deeply")
            names = cells = 0
            levels = 1
            for f in self.fields(name):
                if f.type in _PRIMS:
                    names += 1
                    cells += f.count or 0
                else:
                    inner = self._shape(f.type, (*trail, name))
                    names += (f.count or 1) * inner.names
                    cells += (f.count or 1) * inner.cells
                    levels = max(levels, 1 + inner.levels)
                if names > _UNIT_CAP or cells > _UNIT_CAP:
                    names, cells = min(names, _UNIT_CAP), min(cells, _UNIT_CAP)
                    break  # over the limit already: the rest does not change the answer
            hit = _Shape(max(1, names), cells, levels)
            shapes[name] = hit
        if len(trail) + hit.levels > _MAX_LEVELS:
            raise SchemaError(f"struct {name!r} nests too deeply")
        return hit

    def _decode_one(self, name: str, data: bytes, base: int) -> dict[str, Any]:
        out: dict[str, Any] = {}
        off = base
        open_word: _OpenWord | None = None
        for f in self.fields(name):
            if f.bits is not None:
                out[f.name], off, open_word = _read_bit_field(f, f.bits, data, off, open_word)
                continue
            open_word = None
            if f.type in _PRIMS:
                out[f.name], off = _read_primitive(f, data, off)
            else:
                size = self.size(f.type)
                for i in range(f.count or 1):
                    sub = self._decode_one(f.type, data, off)
                    prefix = f.name if f.count is None else f"{f.name}[{i}]"
                    out.update({f"{prefix}.{k}": v for k, v in sub.items()})
                    off += size
        return out


def _over(n: int) -> str:
    return f"over {_UNIT_CAP - 1:,}" if n >= _UNIT_CAP else f"{n:,}"


def _fresh(value: Any) -> Any:
    """A copy of a decoded value (one dict, or a list of them) so a caller can never change what the cache holds."""
    if isinstance(value, list):
        return [_fresh(v) for v in value]
    return {k: list(x) if isinstance(x, list) else x for k, x in value.items()}


def _read_bit_field(f: Field, bits: int, data: bytes, off: int,
                    word: _OpenWord | None) -> tuple[Any, int, _OpenWord]:
    """Read one `bits`-wide field; returns (value, new offset, updated open storage word)."""
    fmt, width = _PRIMS[f.type]
    if word is None or not _fits_open_word(word, f):
        raw = int.from_bytes(data[off:off + width], "little")
        off += width
        word = _OpenWord(f.type, 0, width, raw)
    shift = word.bits_used
    val = (word.raw >> shift) & ((1 << bits) - 1)
    if fmt.islower() and val >> (bits - 1):  # signed types: sign-extend
        val -= 1 << bits
    word = word._replace(bits_used=shift + bits)
    return (bool(val) if f.type == "bool" else _label(f, val)), off, word


def _read_primitive(f: Field, data: bytes, off: int) -> tuple[Any, int]:
    """Read a primitive field (or array of them); returns (value, new offset)."""
    fmt, width = _PRIMS[f.type]
    n = f.count or 1
    if fmt == "c":  # char[N] is a string
        value: Any = data[off:off + n].split(b"\0", 1)[0].decode("utf-8", "replace")
    else:
        vals = list(struct.unpack_from(f"<{n}{fmt}", data, off))
        value = _label(f, vals[0]) if f.count is None else vals
    return value, off + width * n


def _fits_open_word(word: _OpenWord | None, f: Field) -> bool:
    """Does bit-field `f` fit in the open storage word? (bool bit-fields share any integer word.)"""
    return (word is not None and (word.type == f.type or f.type == "bool")
            and f.bits is not None and word.bits_used + f.bits <= word.width * 8)


def _label(f: Field, val: Any) -> Any:
    return f.enum.get(val, val) if f.enum else val


REGISTRY = Registry()  # process-wide (one connection per server process); reached only via schema/state.py, cleared by state.forget
