"""A schema arriving while other threads decode must never make an already-known struct fail to decode."""

import struct
import sys
import threading

from frc_mcp_wpilib.schema.structs import Registry, SchemaError


def test_adding_schemas_while_other_threads_decode_never_loses_a_known_struct():
    reg = Registry()
    reg.add("/.schema/struct:Vec", "double x;double y")
    reg.add("/.schema/struct:Pose", "Vec p;Vec q;double theta")
    data = struct.pack("<5d", 1, 2, 3, 4, 5)
    stop = threading.Event()
    errors: list[str] = []

    def reader() -> None:
        while not stop.is_set():
            try:
                got = reg.decode_strict("struct:Pose", data)
                names = reg.field_names("Pose")
                if got["theta"] != 5 or len(names) != 5:
                    errors.append(f"wrong value {got}")
            except (SchemaError, KeyError) as e:  # "unresolved" shows up as SchemaError
                errors.append(str(e))

    def writer() -> None:
        for i in range(3000):
            reg.add(f"/.schema/struct:Extra{i}", "double a")  # every add used to empty the parse cache under the readers
        stop.set()

    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # switch threads as often as possible so a race shows up
    try:
        threads = [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        writer()
        for t in threads:
            t.join(30)
    finally:
        sys.setswitchinterval(old)
    assert not any(t.is_alive() for t in threads)
    assert not errors, errors[:3]


def test_a_reader_holding_the_old_cache_is_not_disturbed_by_add_and_clear():
    reg = Registry()
    reg.add("/.schema/struct:Vec", "double x;double y")
    before = reg.fields("Vec")
    reg.add("/.schema/struct:Other", "double a")
    assert reg.fields("Vec") == before  # re-parsed after the cache was replaced, same content
    reg.clear()
    try:
        reg.fields("Vec")
    except SchemaError as e:
        assert "no schema" in str(e)
    else:
        raise AssertionError("expected SchemaError")


def test_an_add_between_a_cache_check_and_the_cache_read_does_not_break_the_reader():
    """The race made deterministic: another schema arrives right after the reader found its struct in the cache."""
    reg = Registry()
    reg.add("/.schema/struct:Vec", "double x;double y")
    fired: list[int] = []

    class Racy(dict[str, list]):  # type: ignore[type-arg]
        def _arrive(self) -> None:
            if not fired:
                fired.append(1)
                reg.add("/.schema/struct:Late", "double a")

        def __contains__(self, key: object) -> bool:
            found = super().__contains__(key)
            if found:
                self._arrive()
            return found

        def get(self, key, default=None):  # type: ignore[no-untyped-def,override]
            found = super().get(key, default)
            if found is not default:
                self._arrive()
            return found

    reg.fields("Vec")  # fills the cache
    racy = Racy(reg._parsed)
    reg._parsed = racy  # type: ignore[assignment]
    assert [f.name for f in reg.fields("Vec")] == ["x", "y"]
    assert fired == [1]
    assert reg.decode_strict("struct:Vec", struct.pack("<2d", 1, 2)) == {"x": 1.0, "y": 2.0}
