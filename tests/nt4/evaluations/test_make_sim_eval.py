"""The evaluation generator: questions for any robot, with answers worked out from the topics themselves."""

import random
import socket
import xml.etree.ElementTree as ET

import make_sim_eval as gen
import pytest
import run_with_claude_code as runner
import runner_support as support


def fact(name, type_string="double", value=None, retained=False, stable=True):
    return gen.Fact(name, type_string, retained, value, stable)


def robot_facts():
    facts = [fact(f"/Arm/Motor/{i}", value=float(i)) for i in range(5)]
    facts += [fact(f"/Arm/Sensor/{i}") for i in range(2)] + [fact("/Arm/Pose", "struct:Pose2d")]
    facts += [fact(f"/Drive/Module{i}/Speed", value=1.0 + i) for i in range(4)] + [fact("/Drive/Module0/Current", value=2.5)]
    facts += [fact("/Drive/Mode", "string", "teleop", retained=True), fact("/Drive/Ready", "boolean", True)]
    facts += [fact("/.schema/struct:Pose2d", "structschema"), fact("$clients", "raw")]
    return facts


def test_each_question_kind_has_the_answer_a_hand_count_gives():
    facts = [f for f in robot_facts() if not gen.is_internal(f.name)]
    rng = random.Random(0)
    assert gen.question_type(facts, rng)[1] in {f.type_string for f in facts}
    count = gen.question_group_count(facts, rng)
    assert count is not None and count[1] in {"8", "7"}  # /Arm/ holds 8 topics, /Drive/ 7
    biggest = gen.question_biggest_subgroup(facts, rng)
    assert biggest is not None and "/Drive/" in biggest[0] and biggest[1] == "Module0"
    number = gen.question_number(facts, rng)
    assert number is not None and number[1] == f"{float(number[1]):.2f}"
    assert gen.question_retained(facts, rng)[1] in {"YES", "NO"}


def test_the_same_facts_and_seed_give_the_same_questions_and_a_new_seed_can_change_them():
    first = gen.build_questions(robot_facts(), seed=3)
    assert gen.build_questions(robot_facts(), seed=3) == first
    assert any(gen.build_questions(robot_facts(), seed=s) != first for s in range(4, 12))


def test_internal_topics_never_appear_in_questions():
    text = " ".join(q for q, _ in gen.build_questions(robot_facts(), seed=0, limit=20))
    assert "$clients" not in text and ".schema" not in text


def test_the_group_count_matches_a_hand_count():
    facts = [f for f in robot_facts() if not gen.is_internal(f.name)]
    assert gen.groups(facts) == {"Arm": 8, "Drive": 7}
    assert gen.groups(facts, "/Arm/") == {"Motor": 5, "Sensor": 2}
    assert gen.groups(facts, "/Drive/") == {"Module0": 2, "Module1": 1, "Module2": 1, "Module3": 1}
    pair = gen.question_group_count(facts, random.Random(0))
    assert pair is not None and pair[1] in {"8", "7"}


def test_an_unsteady_value_is_never_asked_about():
    facts = [fact(f"/A/B/{i}", value=1.0, stable=False) for i in range(5)] + [fact("/A/C/x", "string", "hi", stable=False)]
    assert gen.question_number(facts, random.Random(0)) is None
    assert gen.question_text_or_flag(facts, random.Random(0)) is None


def test_a_robot_with_too_little_to_ask_about_is_refused_with_a_hint():
    with pytest.raises(ValueError, match="run your simulation"):
        gen.build_questions([fact("/Only/One")], seed=0)


def test_written_xml_is_what_the_runner_reads_and_has_no_robot_names_in_the_repo_files():
    pairs = gen.build_questions(robot_facts(), seed=0)
    xml = gen.to_xml(pairs, "note")
    assert runner.parse_eval_pairs(xml.encode()) == [(q, a) for q, a in pairs]
    assert ET.fromstring(xml.split("-->", 1)[1]).tag == "evaluation"


def test_the_runner_names_the_generator_when_the_default_file_is_missing(monkeypatch, tmp_path, capsys):
    ap = runner.build_parser()
    with pytest.raises(SystemExit):
        runner.load_evaluation(ap, tmp_path / "last_sim_eval.xml")
    assert "make_sim_eval.py" in capsys.readouterr().err


def test_main_refuses_bad_options(capsys):
    with pytest.raises(SystemExit):
        gen.main(["--questions", "1"])
    with pytest.raises(SystemExit):
        gen.main(["--port", "0"])


def test_no_server_gives_a_clear_error_and_no_file(tmp_path, capsys):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    out = tmp_path / "eval.xml"
    assert gen.main(["--port", str(port), "--wait", "1", "--out", str(out)]) == 1
    assert "no NetworkTables server" in capsys.readouterr().err and not out.exists()


@pytest.mark.slow
def test_a_real_run_against_the_practice_robot_writes_questions_the_runner_accepts(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    out = tmp_path / "eval.xml"
    with support.fake_robot(port, 0):
        assert gen.main(["--port", str(port), "--out", str(out), "--wait", "20"]) == 0
    pairs = runner.parse_eval_pairs(out.read_bytes())
    assert len(pairs) >= gen.MIN_QUESTIONS and all(q and a for q, a in pairs)
