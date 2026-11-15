"""Write a read-only NT4 evaluation for YOUR robot project, with answers worked out straight from the robot.

Nothing here is tied to one team's code. Start your own robot simulation (for example `./gradlew simulateJava` in your project),
then this script connects with ntcore directly (not through the MCP server), reads the topics, and writes questions whose
answers it computed itself:

    uv run --project plugins/frc python evaluations/make_sim_eval.py [--host 127.0.0.1] [--port 5810] [--out evaluations/last_sim_eval.xml]
    uv run --project plugins/frc python evaluations/run_with_claude_code.py --eval-file evaluations/last_sim_eval.xml

The output holds your robot's topic names, so `evaluations/last_*` is ignored by git: do not commit it. Answers only hold for an idle
simulation; regenerate after you change the robot code. The script only reads: it publishes nothing and changes nothing.
"""

import argparse
import json
import math
import random
import sys
import time
import xml.etree.ElementTree as ET  # nosec B405 - builds the file, parses nothing
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import ntcore
from eval_common import ROOT, atomic_write_text

DEFAULT_OUT = ROOT / "evaluations" / "last_sim_eval.xml"
MIN_QUESTIONS = 3
SETTLE_S = 1.5  # two value snapshots this far apart: a value that moved is not a fair question
GROUP_SIZE = (3, 60)  # a prefix with this many topics makes a fair counting question


@dataclass(frozen=True)
class Fact:
    """What the robot said about one topic."""

    name: str
    type_string: str
    retained: bool
    value: object  # a plain Python value, or None when the topic has none yet
    stable: bool  # the value was the same in both snapshots


def is_internal(name: str) -> bool:
    """Meta topics and schema topics, which the questions leave out."""
    return name.startswith("$") or name.startswith("/.") or name.startswith("/$") or "/.schema" in name


def groups(facts: list[Fact], prefix: str = "/") -> Counter:
    """Topics per immediate sub-group under `prefix` (all depths below that sub-group count)."""
    found: Counter = Counter()
    for fact in facts:
        if fact.name.startswith(prefix) and "/" in fact.name[len(prefix):]:
            found[fact.name[len(prefix):].split("/", 1)[0]] += 1
    return found


def _round(value: float) -> str:
    return f"{value:.2f}"


def question_type(facts: list[Fact], rng: random.Random) -> tuple[str, str] | None:
    odd = [f for f in facts if f.type_string.startswith("struct:") or f.type_string.endswith("[]")] or facts
    if not odd:
        return None
    fact = rng.choice(odd)
    return (f"What is the NetworkTables type string of the topic `{fact.name}`? Answer with the exact type string.", fact.type_string)


def question_number(facts: list[Fact], rng: random.Random) -> tuple[str, str] | None:
    steady = [f for f in facts if f.type_string == "double" and f.stable and isinstance(f.value, float)
              and math.isfinite(f.value) and abs(f.value) < 1e6]
    if not steady:
        return None
    fact = rng.choice(steady)
    return (f"What is the current value of the topic `{fact.name}`, in the simulation sitting idle? "
            "Round to 2 decimal places and answer as a decimal number.", _round(float(fact.value)))  # type: ignore[arg-type]


def question_text_or_flag(facts: list[Fact], rng: random.Random) -> tuple[str, str] | None:
    steady = [f for f in facts if f.stable and ((f.type_string == "string" and isinstance(f.value, str) and f.value.strip()
                                                  and len(f.value) <= 24 and f.value.replace(" ", "").isalnum())
                                                 or (f.type_string == "boolean" and isinstance(f.value, bool)))]
    if not steady:
        return None
    fact = rng.choice(steady)
    if fact.type_string == "boolean":
        return (f"Is the value of the boolean topic `{fact.name}` true or false right now? Answer exactly TRUE or FALSE.",
                "TRUE" if fact.value else "FALSE")
    return (f"What is the current value of the string topic `{fact.name}`? Answer with the text exactly as it is.", str(fact.value))


def question_group_count(facts: list[Fact], rng: random.Random) -> tuple[str, str] | None:
    low, high = GROUP_SIZE
    fair = sorted(name for name, count in groups(facts).items() if low <= count <= high)
    if not fair:
        return None
    name = rng.choice(fair)
    return (f"How many topics are published under `/{name}/`, at any depth? Internal topics that start with `$` or `/.` are not "
            "counted. Answer with a single integer.", str(groups(facts)[name]))


def question_biggest_subgroup(facts: list[Fact], rng: random.Random) -> tuple[str, str] | None:
    candidates = []
    for top in sorted(groups(facts)):
        sub = groups(facts, f"/{top}/")
        ranked = sub.most_common()
        if len(ranked) >= 3 and ranked[0][1] > ranked[1][1] and sum(sub.values()) <= 400:
            candidates.append((top, ranked[0][0]))
    if not candidates:
        return None
    top, winner = rng.choice(candidates)
    return (f"Under `/{top}/`, which immediate sub-group publishes the largest number of topics (all depths below it count)? "
            "Answer with the sub-group name only, exactly as it appears.", winner)


def question_retained(facts: list[Fact], rng: random.Random) -> tuple[str, str] | None:
    flagged = [f for f in facts if f.retained]
    plain = [f for f in facts if not f.retained]
    if not flagged and not plain:
        return None
    fact = rng.choice(flagged or plain)
    return (f"Does the topic `{fact.name}` have the NT4 `retained` property set? Answer exactly YES or NO.", "YES" if fact.retained else "NO")


BUILDERS = (question_type, question_number, question_group_count, question_biggest_subgroup, question_text_or_flag, question_retained)


def build_questions(facts: list[Fact], seed: int = 0, limit: int = 8) -> list[tuple[str, str]]:
    """Question and answer pairs from `facts`, the same for the same facts and seed. Raises ValueError when the robot has too little."""
    fair = sorted((f for f in facts if not is_internal(f.name)), key=lambda f: f.name)
    rng = random.Random(seed)
    pairs: list[tuple[str, str]] = []
    for build in BUILDERS:
        pair = build(fair, rng)
        if pair and pair not in pairs:
            pairs.append(pair)
    if len(pairs) < MIN_QUESTIONS:
        raise ValueError(f"only {len(pairs)} fair questions could be made from {len(fair)} topics; run your simulation first, or "
                         "check that the robot code publishes more to NetworkTables")
    return pairs[:limit]


def to_xml(pairs: list[tuple[str, str]], note: str) -> str:
    root = ET.Element("evaluation")
    for question, answer in pairs:
        pair = ET.SubElement(root, "qa_pair")
        ET.SubElement(pair, "question").text = question
        ET.SubElement(pair, "answer").text = answer
    ET.indent(root)
    return f"<!-- {note} -->\n" + ET.tostring(root, encoding="unicode") + "\n"


def _python_value(value: ntcore.Value) -> object:
    return value.value() if value.isValid() else None


def _is_retained(topic: ntcore.Topic) -> bool:
    props = topic.getProperties()
    if isinstance(props, str):
        try:
            props = json.loads(props)
        except ValueError:
            return False
    return bool(isinstance(props, dict) and props.get("retained") is True)


def collect(host: str, port: int, wait_s: float) -> list[Fact]:
    """Connect read-only with ntcore and describe every topic. Raises RuntimeError when nothing answers."""
    inst = ntcore.NetworkTableInstance.create()
    try:
        inst.startClient4("frc-mcp-eval-generator")
        inst.setServer(host, port)
        subscriber = ntcore.MultiSubscriber(inst, [""])  # keeps values flowing for every topic
        deadline = time.monotonic() + wait_s
        while not inst.isConnected():
            if time.monotonic() > deadline:
                raise RuntimeError(f"no NetworkTables server at {host}:{port} after {wait_s:.0f} s; start your simulation first")
            time.sleep(0.1)
        time.sleep(SETTLE_S)
        first = {t.getName(): _python_value(inst.getEntry(t.getName()).getValue()) for t in inst.getTopics()}
        time.sleep(SETTLE_S)
        facts = []
        for topic in inst.getTopics():
            name = topic.getName()
            value = _python_value(inst.getEntry(name).getValue())
            facts.append(Fact(name, topic.getTypeString(), _is_retained(topic), value, first.get(name) == value))
        del subscriber
        return facts
    finally:
        ntcore.NetworkTableInstance.destroy(inst)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Write a read-only NT4 evaluation from your own simulated robot.")
    ap.add_argument("--host", default="127.0.0.1", help="where your simulation runs (default: this computer)")
    ap.add_argument("--port", type=int, default=5810)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"default: {DEFAULT_OUT.relative_to(ROOT).as_posix()} (git ignores it)")
    ap.add_argument("--seed", type=int, default=0, help="a different number picks different topics")
    ap.add_argument("--questions", type=int, default=8, help="at most this many (3 or more)")
    ap.add_argument("--wait", type=float, default=15.0, help="seconds to wait for the connection")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if not 1 <= args.port <= 65535:
        ap.error("--port must be between 1 and 65535")
    if args.questions < MIN_QUESTIONS:
        ap.error(f"--questions must be at least {MIN_QUESTIONS}")
    try:
        pairs = build_questions(collect(args.host, args.port, args.wait), args.seed, args.questions)
    except (RuntimeError, ValueError) as e:
        print(f"make_sim_eval: {e}", file=sys.stderr)
        return 1
    note = (f"Generated by evaluations/make_sim_eval.py from the robot at {args.host}:{args.port} (seed {args.seed}). "
            "Answers hold for an idle simulation of that robot code. Do not commit.")
    atomic_write_text(args.out, to_xml(pairs, note))
    print(f"wrote {len(pairs)} questions to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
