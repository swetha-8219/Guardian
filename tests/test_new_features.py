import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from guardian import moderation, report
from guardian.classifier import Comment
from guardian.platforms import PlatformError

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(moderation, "TOKEN_FILE", tmp_path / "tokens.json")


class FakeResp:
    def __init__(self, status=200, data=None):
        self.status_code, self._data, self.text = status, data or {}, json.dumps(data or {})

    def json(self):
        return self._data


# ------------------------------------------------------------ shared word lists

def test_extension_wordlists_match_python():
    js = (ROOT / "extension/wordlists.js").read_text(encoding="utf-8")
    js_data = json.loads(js[js.index("{"):js.rindex("}") + 1])
    py_data = json.loads((ROOT / "guardian/wordlists.json").read_text(encoding="utf-8"))
    assert js_data == py_data, "Run the sync snippet in README to update extension/wordlists.js"


# ------------------------------------------------------------ hiding on platforms

def test_instagram_hide(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "tok")
    calls = []
    monkeypatch.setattr(moderation.requests, "post",
                        lambda url, **kw: calls.append((url, kw["params"])) or FakeResp())
    assert moderation.hide_on_platform("instagram", ["ig-111", "ig-222", "yt-xxx"]) == 2
    assert calls[0] == ("https://graph.facebook.com/v21.0/111", {"hide": "true", "access_token": "tok"})


def test_youtube_hide_needs_login():
    with pytest.raises(PlatformError, match="Connect your YouTube"):
        moderation.hide_on_platform("youtube", ["yt-abc"])


def test_youtube_login_refresh_and_hide(monkeypatch):
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "cid")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "secret")
    posts = []

    def fake_post(url, **kw):
        posts.append((url, kw))
        if url == moderation.GOOGLE_TOKEN_URL:
            if kw["data"]["grant_type"] == "authorization_code":
                return FakeResp(data={"access_token": "a1", "refresh_token": "r1", "expires_in": 0})
            return FakeResp(data={"access_token": "a2", "expires_in": 3600})
        return FakeResp(204)

    monkeypatch.setattr(moderation.requests, "post", fake_post)
    assert "access_type=offline" in moderation.youtube_login_url("http://localhost/cb", "st")
    moderation.youtube_finish_login("code", "http://localhost/cb")
    assert moderation.youtube_logged_in()

    ids = [f"yt-c{i}" for i in range(60)]
    assert moderation.hide_on_platform("youtube", ids) == 60
    hide_calls = [kw for url, kw in posts if "setModerationStatus" in url]
    assert len(hide_calls) == 2  # 50 + 10
    assert hide_calls[0]["params"]["moderationStatus"] == "heldForReview"
    assert hide_calls[0]["headers"]["Authorization"] == "Bearer a2"  # refreshed token used

    moderation.youtube_logout()
    assert not moderation.youtube_logged_in()


def test_x_and_tiktok_hide_unsupported():
    for p in ("x", "tiktok"):
        with pytest.raises(PlatformError):
            moderation.hide_on_platform(p, ["x-1"])


# ------------------------------------------------------------ weekly report

def test_parse_watchlist():
    assert report.parse_watchlist(" youtube:https://youtu.be/abc, instagram:123 ,") == [
        ("youtube", "https://youtu.be/abc"), ("instagram", "123")]
    with pytest.raises(PlatformError):
        report.parse_watchlist("tiktok:https://tiktok.com/x")


def test_build_and_render_report(monkeypatch):
    now = datetime(2026, 11, 27, tzinfo=timezone.utc)
    fake = [
        Comment(id="yt-1", text="I love this, thank you!", author="amy", platform="youtube",
                created_at="2026-11-25T10:00:00Z"),
        Comment(id="yt-2", text="ur so ugly", author="troll", platform="youtube",
                created_at="2026-11-26T10:00:00Z"),
        Comment(id="yt-3", text="loser lol", author="troll", platform="youtube",
                created_at="2026-11-26T11:00:00Z"),
        Comment(id="yt-4", text="The audio was quiet", author="sam", platform="youtube",
                created_at="2026-11-24T10:00:00Z"),
        Comment(id="yt-5", text="old love", author="old", platform="youtube",
                created_at="2026-10-01T10:00:00Z"),  # older than a week - skipped
    ]
    monkeypatch.setitem(report.FETCHERS, "youtube", lambda link: [Comment(**vars(c)) for c in fake])
    hidden = []
    monkeypatch.setattr(report, "can_hide", lambda: {"youtube": True})
    monkeypatch.setattr(report, "hide_on_platform", lambda p, ids: hidden.extend(ids) or len(ids))

    r = report.build_report([("youtube", "abc")], auto_hide=True, now=now)
    assert r["total"] == 4
    assert r["counts"]["harassment"] == 2
    assert r["hidden_on_platform"] == 2 and hidden == ["yt-2", "yt-3"]
    assert r["repeat_offenders"] == ["troll"]

    subject, body = report.render_email(r)
    assert "1 supportive" in subject
    assert "I love this" in body and "audio was quiet" in body
    assert "ugly" not in body and "loser" not in body  # never show the bullying
    assert "2 hurtful comments were filtered" in body


def test_report_survives_platform_errors(monkeypatch):
    def broken(link):
        raise PlatformError("bad key")
    monkeypatch.setitem(report.FETCHERS, "x", broken)
    r = report.build_report([("x", "123")])
    assert r["total"] == 0 and "bad key" in r["problems"][0]
    assert "couldn't be checked" in report.render_email(r)[1]


def test_send_email_requires_settings(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    with pytest.raises(PlatformError):
        report.send_email("s", "b")


def test_send_email(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent["host"] = (host, port)
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def starttls(self): sent["tls"] = True
        def login(self, u, p): sent["login"] = u
        def send_message(self, m): sent["to"] = m["To"]

    monkeypatch.setattr(report.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "me@example.com")
    monkeypatch.setenv("REPORT_TO", "me@example.com")
    report.send_email("Hello", "<p>hi</p>")
    assert sent == {"host": ("smtp.example.com", 587), "tls": True,
                    "login": "me@example.com", "to": "me@example.com"}


# ------------------------------------------------------------ web app endpoints

def test_api_classify_for_extension():
    from guardian.app import app
    r = app.test_client().post("/api/classify", json={"texts": ["love it!", "kys"]})
    assert r.get_json()["categories"] == ["supportive", "threat"]


def test_api_hide_errors_are_friendly():
    from guardian.app import app
    r = app.test_client().post("/api/hide", json={"platform": "youtube", "ids": ["yt-1"]})
    assert r.status_code == 400 and "Connect your YouTube" in r.get_json()["error"]


def test_youtube_login_redirect_and_state_check(monkeypatch):
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "cid")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "secret")
    from guardian.app import app
    c = app.test_client()
    r = c.get("/auth/youtube/login")
    assert r.status_code == 302 and r.location.startswith(moderation.GOOGLE_AUTH_URL)
    bad = c.get("/auth/youtube/callback?state=wrong&code=x")
    assert bad.status_code == 400


def test_playground_filter_matches_extension():
    for name in ("filter.js", "wordlists.js"):
        assert (ROOT / "docs" / name).read_text() == (ROOT / "extension" / name).read_text(), \
            f"docs/{name} is out of date: cp extension/{name} docs/"
