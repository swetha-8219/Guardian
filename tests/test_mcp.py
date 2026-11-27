import asyncio
import json

import pytest

pytest.importorskip("mcp")
from guardian import mcp_server  # noqa: E402


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(mcp_server.watcher, "STATE_FILE", tmp_path / "state.json")


def call(name, args):
    res = asyncio.run(mcp_server.server.call_tool(name, args))
    return json.loads(res.content[0].text)


def test_tools_listed():
    tools = asyncio.run(mcp_server.server.list_tools())
    assert {t.name for t in tools} == {"sort_comments", "check_post", "always_on_status"}


def test_sort_comments_hides_hurtful_text_by_default():
    out = call("sort_comments", {"comments": ["amy: love this!", "troll: ur so ugly", "troll: loser"]})
    assert out["counts"]["harassment"] == 2 and out["supportive"][0]["text"] == "love this!"
    assert out["repeat_offenders"] == ["troll"]
    assert all("text" not in h for h in out["hurtful"])
    shown = call("sort_comments", {"comments": ["ur so ugly"], "include_hurtful_text": True})
    assert shown["hurtful"][0]["text"] == "ur so ugly"


def test_check_post_explains_tiktok_and_errors(monkeypatch):
    assert "TikTok" in call("check_post", {"platform": "tiktok", "link": "x"})["error"]
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    assert "YouTube" in call("check_post", {"platform": "youtube", "link": "dQw4w9WgXcQ"})["error"]


def test_always_on_status():
    out = call("always_on_status", {})
    assert out["paused"] is False and out["platforms"]["tiktok"]["status"] == "unsupported"
