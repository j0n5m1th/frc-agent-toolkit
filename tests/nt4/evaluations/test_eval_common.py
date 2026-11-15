"""Tests for the helpers shared by the evaluation scripts."""

import json
import math
import os
import subprocess
import sys

import eval_common
import pytest


@pytest.mark.parametrize("value, expected", [
    (0, 0), (1.5, 1.5), (-1, None), (True, None), ("1", None), (None, None),
    (math.inf, None), (math.nan, None), (10 ** 400, None),
])
def test_number_accepts_only_nonnegative_finite_numbers(value, expected):
    assert eval_common.number(value) == expected


@pytest.mark.parametrize("calls, valid", [
    (None, True), ({}, True), ({"a": 1}, True), ({"a": -1}, False), ({"": 1}, False),
    ({"a": True}, False), ({"a": 1.0}, False), ([], False), ({1: 1}, False),
])
def test_valid_tool_call_counts(calls, valid):
    assert eval_common.valid_tool_call_counts(calls) is valid


def test_reject_constant_blocks_non_finite_json():
    with pytest.raises(ValueError, match="NaN"):
        json.loads("[NaN]", parse_constant=eval_common.reject_constant)


def test_atomic_write_text_replaces_content_and_leaves_no_temp_files(tmp_path):
    target = tmp_path / "out.txt"
    target.write_text("old", encoding="utf-8")
    eval_common.atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"
    assert os.listdir(tmp_path) == ["out.txt"]


def test_atomic_write_text_failure_keeps_original(tmp_path, monkeypatch):
    target = tmp_path / "out.txt"
    target.write_text("old", encoding="utf-8")

    def boom(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(eval_common.os, "replace", boom)
    with pytest.raises(OSError):
        eval_common.atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "old"
    assert os.listdir(tmp_path) == ["out.txt"]


def test_stop_process_terminates_a_running_child():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])  # nosec B603
    eval_common.stop_process(proc, timeout=10)
    assert proc.poll() is not None
