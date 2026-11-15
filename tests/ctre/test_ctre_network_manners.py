"""How the package treats CTRE's server: paced, conditional, never retried, never leaving the allowed host; and what it leaves on disk."""

import io
import json
import os
import time
import urllib.error

import pytest
from ctre_helpers import INDEX, PAYLOAD

from frc_mcp_ctre import config, fetch
from frc_mcp_ctre.errors import CtreError
from frc_mcp_ctre.fetch import (
    http_get as real_http_get,  # imported at collection, before the autouse fixture replaces it
)
from frc_mcp_ctre.fetch import load_index

URL = "https://redist.ctr-electronics.com/index.json"


class FakeResponse(io.BytesIO):
    def __init__(self, body=b"", headers=None):
        super().__init__(body)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_answering(monkeypatch, answer, seen=None):
    """Replace the network layer under the real http_get: `answer(request)` returns a FakeResponse or raises."""
    def open_(self, request, *args, **kwargs):
        if seen is not None:
            seen.append(request)
        return answer(request)

    monkeypatch.setattr("urllib.request.OpenerDirector.open", open_)


def test_requests_to_ctre_are_spaced_by_the_configured_delay(monkeypatch):
    clock = {"now": 100.0, "slept": []}
    monkeypatch.setattr(fetch.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(fetch.time, "sleep", lambda s: (clock["slept"].append(round(s, 3)), clock.__setitem__("now", clock["now"] + s)))
    monkeypatch.setenv("CTRE_REQUEST_DELAY_S", "2")
    monkeypatch.setattr(fetch, "_last_request", 0.0)
    fetch._pace()  # the first request of a process goes straight out
    clock["now"] += 0.5
    fetch._pace()
    fetch._pace()
    assert clock["slept"] == [1.5, 2.0]


def test_the_delay_can_be_turned_off_and_a_bad_value_is_refused(monkeypatch):
    monkeypatch.setenv("CTRE_REQUEST_DELAY_S", "0")
    assert config.request_delay_s() == 0
    monkeypatch.setenv("CTRE_REQUEST_DELAY_S", "-1")
    with pytest.raises(CtreError, match="CTRE_REQUEST_DELAY_S"):
        config.request_delay_s()


def test_every_real_request_is_paced_and_identifies_itself(monkeypatch):
    paced, seen = [], []
    monkeypatch.setattr(fetch, "_pace", lambda: paced.append(1))
    opener_answering(monkeypatch, lambda r: FakeResponse(b"{}", {"ETag": '"abc"'}), seen)
    sink = io.BytesIO()
    assert real_http_get(URL, sink, 1000, 5) == '"abc"' and sink.getvalue() == b"{}"
    assert paced == [1] and seen[0].get_header("User-agent") == "mcp-ctre-tools" and not seen[0].has_header("If-none-match")


def test_a_304_answer_means_the_saved_copy_is_current(monkeypatch):
    seen = []
    def answer(request):
        raise urllib.error.HTTPError(URL, 304, "Not Modified", {}, None)
    opener_answering(monkeypatch, answer, seen)
    monkeypatch.setattr(fetch, "_pace", lambda: None)
    with pytest.raises(fetch.NotModified):
        real_http_get(URL, io.BytesIO(), 1000, 5, etag='"abc"')
    assert seen[0].get_header("If-none-match") == '"abc"'


@pytest.mark.parametrize(("code", "slow"), [(429, True), (503, True), (404, False)])
def test_an_error_answer_is_reported_once_never_retried_and_slowdown_answers_say_to_wait(monkeypatch, code, slow):
    seen = []
    def answer(request):
        raise urllib.error.HTTPError(URL, code, "no", {}, None)
    opener_answering(monkeypatch, answer, seen)
    monkeypatch.setattr(fetch, "_pace", lambda: None)
    with pytest.raises(CtreError) as caught:
        real_http_get(URL, io.BytesIO(), 1000, 5)
    assert len(seen) == 1 and (f"answered {code}" in str(caught.value)) and (("wait a few minutes" in str(caught.value)) is slow)


def test_a_body_over_the_limit_is_refused_by_its_declared_size_and_by_its_actual_size(monkeypatch):
    monkeypatch.setattr(fetch, "_pace", lambda: None)
    opener_answering(monkeypatch, lambda r: FakeResponse(b"x", {"Content-Length": "999999999"}))
    with pytest.raises(CtreError, match="over the CTRE_MAX_DOWNLOAD_MB limit"):
        real_http_get(URL, io.BytesIO(), 1000, 5)
    opener_answering(monkeypatch, lambda r: FakeResponse(b"x" * 5000, {}))
    with pytest.raises(CtreError, match="bigger than the allowed"):
        real_http_get(URL, io.BytesIO(), 1000, 5)


def test_a_redirect_to_another_host_is_refused():
    handler = fetch._CheckedRedirects()
    with pytest.raises(CtreError, match="only https links"):
        handler.redirect_request(None, None, 302, "Found", {}, "https://elsewhere.example/index.json")


def serve(monkeypatch, etag='"v1"', not_modified=False, calls=None):
    def fake_get(url, sink, limit, timeout, **kw):
        if calls is not None:
            calls.append(kw)
        if kw.get("etag") and not_modified:
            raise fetch.NotModified
        sink.write(json.dumps(INDEX).encode())
        return etag

    monkeypatch.setattr(fetch, "http_get", fake_get)


def test_a_refresh_asks_whether_the_index_changed_and_an_unchanged_one_is_not_downloaded_again(monkeypatch):
    calls = []
    serve(monkeypatch, calls=calls)
    assert load_index().source == "network"
    saved = config.tools_dir() / ".mcp-ctre"
    assert (saved / "index.etag").read_text(encoding="utf-8") == '"v1"'
    old = time.time() - 3 * 86400
    os.utime(saved / "index.json", (old, old))
    serve(monkeypatch, not_modified=True, calls=calls)
    result = load_index()  # stale: asks with the saved ETag, gets 304
    assert calls[1] == {"etag": '"v1"'} and result.source == "cache" and result.age_s == 0.0
    assert time.time() - (saved / "index.json").stat().st_mtime < 60  # counted as fresh again: no request for another day
    assert load_index().source == "cache" and len(calls) == 2


def test_the_first_fetch_sends_no_etag_and_a_changed_index_replaces_the_saved_one(monkeypatch):
    calls = []
    serve(monkeypatch, etag='"v1"', calls=calls)
    load_index()
    serve(monkeypatch, etag='"v2"', calls=calls)  # changed: the fake ignores the ETag and sends the full body
    load_index(refresh=True)
    assert calls == [{}, {"etag": '"v1"'}] and (config.tools_dir() / ".mcp-ctre" / "index.etag").read_text(encoding="utf-8") == '"v2"'


def test_a_failed_download_leaves_no_temporary_file_behind(tools_dir, monkeypatch):
    from ctre_helpers import fake_install  # noqa: F401  (documents that installs live in the same folder)

    from frc_mcp_ctre.index import parse_index

    item = parse_index(json.dumps(INDEX)).find("owlet", "26.3.0")

    def breaks_midway(url, sink, limit, timeout):
        sink.write(PAYLOAD[:5])
        raise CtreError("connection dropped")

    monkeypatch.setattr(fetch, "http_get", breaks_midway)
    with pytest.raises(CtreError, match="connection dropped"):
        fetch.download_tool(item, "windowsx86-64")
    assert list(tools_dir.rglob("*.part")) == [] and not (tools_dir / "owlet" / "26.3.0" / "owlet-26.3.0-windowsx86-64.exe").exists()
