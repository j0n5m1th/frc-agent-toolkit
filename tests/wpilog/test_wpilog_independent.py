"""The log server's answers are checked against a second reader written from the spec and against Python's `statistics`, which share no code with it."""

import json
import statistics
import struct

import pytest
from independent_log import parse
from log_builder import BASE, POSE_SCHEMAS, S, pose_bytes

from frc_mcp_wpilib.wpilog_writer import LogWriter
from frc_mcp_wpilog.logfile import load
from frc_mcp_wpilog.tools import data_tools, file_tools


def build(path):
    """Scalars, text, a struct and an array; an uneven sample rate, a 6-byte timestamp and a topic that is declared late."""
    w = LogWriter("extra header text")
    for name, text in POSE_SCHEMAS.items():
        w.schema(name, text, t_us=BASE)
    volts = w.start("/Robot/Volts", "double", '{"unit":"V"}', BASE)
    count = w.start("/Robot/Count", "int64", "", BASE)
    flag = w.start("/Robot/Flag", "boolean", "", BASE)
    small = w.start("/Robot/Small", "float", "", BASE)
    pose = w.start("/Robot/Pose", "struct:Pose2d", "", BASE)
    array = w.start("/Robot/Array", "double[]", "", BASE)
    for n in range(60):
        t = BASE + n * 20_000 + (n * n % 7) * 100  # uneven by up to 0.6 ms
        w.append(volts, 12.0 - (n % 9) * 0.37 + n * 0.001, t)
        w.append(count, n * n - 500, t)
        w.append(flag, n % 4 == 0, t)
        w.append(small, n / 7, t)
        w.append(pose, pose_bytes(n * 0.5, -n * 0.25, n * 0.01), t)
        w.append(array, [n, n / 3, -n], t)
    late = w.start("/Robot/Text", "string", "", BASE + 2 * S)
    w.append(late, "héllo wörld", BASE + 2 * S)
    w.append(late, "second", 2**40 + BASE)  # a timestamp that needs 6 bytes
    return w.save(path)


@pytest.fixture
def written(log_dir):
    path = build(log_dir / "check.wpilog")
    return parse(path.read_bytes()), path


async def query(name, **options):
    await file_tools.wpilog_open("check.wpilog")
    return json.loads((await data_tools.wpilog_query(topics=[name], **options)).content[0].text)["data"][name]


def test_the_server_sees_the_same_topics_counts_and_values_as_the_spec_reader(written):
    expected, path = written
    log = load(path)
    visible = {name for name in log.topics if not name.startswith("/.schema/")}
    assert visible == {name for name in expected if not name.startswith("/.schema/")}
    rows = log.rows(sorted(visible))
    for name in visible:
        assert log.topics[name].count == len(expected[name]), name
        if not name.endswith(("Pose", "Array")):  # a struct and an array are decoded below
            assert list(rows[name]) == expected[name], name


def test_a_struct_and_an_array_decode_to_what_the_spec_readers_bytes_say(written):
    expected, path = written
    log = load(path)
    rows = log.rows(["/Robot/Array", "/Robot/Pose"])
    for (t_us, got), (spec_t, raw) in zip(rows["/Robot/Array"], expected["/Robot/Array"], strict=True):
        assert t_us == spec_t and list(got) == list(struct.unpack("<3d", raw))
    assert [t for t, _ in rows["/Robot/Pose"]] == [t for t, _ in expected["/Robot/Pose"]]


@pytest.mark.parametrize("name", ["/Robot/Volts", "/Robot/Small"])
async def test_a_summary_matches_statistics_over_the_spec_readers_values(written, name):
    values = [v for _, v in written[0][name]]
    out = await query(name)
    assert out["n"] == len(values) and out["min"] == pytest.approx(min(values), abs=1e-3) and out["max"] == pytest.approx(max(values), abs=1e-3)
    assert out["mean"] == pytest.approx(statistics.fmean(values), abs=1e-3)
    assert out["first"] == pytest.approx(values[0], abs=1e-3) and out["last"] == pytest.approx(values[-1], abs=1e-3)


async def test_a_time_weighted_mean_matches_the_spec_readers_hold_arithmetic(written):
    samples = written[0]["/Robot/Volts"]
    held = [(b[0] - a[0], a[1]) for a, b in zip(samples, samples[1:], strict=False)]  # each value holds until the next update
    expected = sum(d * v for d, v in held) / sum(d for d, _ in held)
    out = await query("/Robot/Volts", reduce="time_weighted")
    assert out["mean"] == pytest.approx(expected, abs=1e-3) and out["covered_ms"] == pytest.approx(sum(d for d, _ in held) / 1000, abs=0.01)


async def test_a_struct_value_has_the_fields_the_spec_readers_bytes_hold(written):
    expected, path = written
    t_us, raw = expected["/Robot/Pose"][10]
    x, y, theta = struct.unpack("<3d", raw)
    at_ms = (t_us - load(path).start_us) / 1000
    await file_tools.wpilog_open("check.wpilog")
    out = json.loads((await data_tools.wpilog_get_values(topics=["/Robot/Pose"], at_ms=at_ms)).content[0].text)["/Robot/Pose"]["v"]
    assert out["translation.x"] == pytest.approx(x, abs=1e-3) and out["translation.y"] == pytest.approx(y, abs=1e-3)
    assert out["rotation.value"] == pytest.approx(theta, abs=1e-3)
