"""The WPILOG writer produces files the official WPILib reader reads back exactly."""

from wpiutil.log import DataLogReader

from frc_mcp_wpilib.wpilog_writer import LogWriter, encode

S = 1_000_000  # one second in microseconds


def read_back(path):
    reader = DataLogReader(str(path))
    assert reader.isValid()
    types, out = {}, []
    for rec in reader:
        if rec.isStart():
            data = rec.getStartData()
            types[data.entry] = (data.name, data.type, data.metadata)
        elif not rec.isControl():
            name, type_str, _ = types[rec.getEntry()]
            getter = {"double": rec.getDouble, "boolean": rec.getBoolean, "int64": rec.getInteger, "float": rec.getFloat,
                      "string": rec.getString, "double[]": rec.getDoubleArray, "int64[]": rec.getIntegerArray,
                      "boolean[]": rec.getBooleanArray, "string[]": rec.getStringArray}.get(type_str)
            out.append((name, rec.getTimestamp(), getter() if getter else bytes(rec.getRaw())))
    return types, out


def test_every_scalar_and_array_type_round_trips(tmp_path):
    w = LogWriter()
    values = {"double": 1.5, "boolean": True, "int64": -7, "float": 1.25, "string": "héllo",
              "double[]": [1.0, 2.0], "int64[]": [3, 4], "boolean[]": [True, False], "string[]": ["a", "bc"]}
    for type_str, value in values.items():
        w.append(w.start(f"NT:/{type_str}", type_str, "", S), value, 2 * S)
    _, out = read_back(w.save(tmp_path / "t.wpilog"))
    assert {name.removeprefix("NT:/"): value for name, _, value in out} == values


def test_raw_values_metadata_and_big_timestamps_survive(tmp_path):
    w = LogWriter(extra_header="demo")
    entry = w.start("NT:/p", "struct:Pose2d", '{"unit":"m"}', 0)
    w.append(entry, bytes(range(24)), 300 * S * 1000)  # needs a 5-byte timestamp
    types, out = read_back(w.save(tmp_path / "t.wpilog"))
    assert types[entry] == ("NT:/p", "struct:Pose2d", '{"unit":"m"}')
    assert out == [("NT:/p", 300 * S * 1000, bytes(range(24)))]


def test_schemas_are_registered_once_as_schema_entries(tmp_path):
    w = LogWriter()
    w.schema("struct:Pose2d", "Translation2d translation;Rotation2d rotation")
    w.schema("struct:Pose2d", "ignored second registration")
    types, out = read_back(w.save(tmp_path / "t.wpilog"))
    assert list(types.values()) == [("/.schema/struct:Pose2d", "structschema", "")]
    assert out == [("/.schema/struct:Pose2d", 0, b"Translation2d translation;Rotation2d rotation")]


def test_drain_hands_over_only_what_is_new_and_the_pieces_add_up_to_the_whole(tmp_path):
    streamed, whole = LogWriter(), LogWriter()
    pieces = []
    for w in (streamed, whole):
        entry = w.start("NT:/a", "double", "", S)
        w.append(entry, 1.5, 2 * S)
        if w is streamed:
            pieces.append(w.drain())  # the header and the first two records
        w.append(entry, 2.5, 3 * S)
    pieces.append(streamed.drain())
    assert streamed.drain() == b""  # nothing new since
    assert b"".join(pieces) == whole.to_bytes()
    path = tmp_path / "streamed.wpilog"
    path.write_bytes(b"".join(pieces))
    assert [value for _, _, value in read_back(path)[1]] == [1.5, 2.5]


def test_encode_passes_raw_bytes_through():
    assert encode("raw", b"\x01\x02") == b"\x01\x02"
    assert encode("string[]", ["x"]) == (1).to_bytes(4, "little") + (1).to_bytes(4, "little") + b"x"
