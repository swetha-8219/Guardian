import json
from urllib.parse import parse_qs, urlparse

import pytest

from guardian import moderation, watcher
from guardian.classifier import Comment
from guardian.platforms import PlatformError


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(moderation, "TOKEN_FILE", tmp_path / "tokens.json")
    monkeypatch.setattr(watcher, "STATE_FILE", tmp_path / "watcher.json")
    watcher._cache.clear()


@pytest.fixture
def fake_platforms(monkeypatch):
    """YouTube 'connected' with two new comments; hiding is recorded, not sent."""
    feed = {"youtube": [
        Comment(id="yt-1", text="love this video!", author="amy", platform="youtube"),
        Comment(id="yt-2", text="ur so ugly lol", author="troll", platform="youtube"),
    ]}
    hidden, unhidden = [], []
    monkeypatch.setattr(watcher, "connected", lambda: {"youtube": True, "instagram": False, "x": False})
    monkeypatch.setitem(watcher.WATCHERS, "youtube", lambda state: [Comment(**vars(c)) for c in feed["youtube"]])
    monkeypatch.setattr(moderation, "hide_on_platform", lambda p, ids: hidden.extend(ids) or len(ids))
    monkeypatch.setattr(moderation, "unhide_on_platform", lambda p, ids: unhidden.extend(ids) or len(ids))
    return feed, hidden, unhidden


def test_hides_bullying_automatically(fake_platforms):
    feed, hidden, _ = fake_platforms
    state = watcher.run_once(force=True)
    assert hidden == ["yt-2"]
    info = state["platforms"]["youtube"]
    assert info["status"] == "watching" and info["hidden_total"] == 1 and info["checked_total"] == 2
    assert state["platforms"]["instagram"]["status"] == "not_connected"
    assert state["log"][0]["id"] == "yt-2" and state["log"][0]["hidden"] is True


def test_each_comment_checked_once(fake_platforms):
    feed, hidden, _ = fake_platforms
    watcher.run_once(force=True)
    feed["youtube"].append(Comment(id="yt-3", text="loser", author="troll", platform="youtube"))
    state = watcher.run_once(force=True)
    assert hidden == ["yt-2", "yt-3"]
    assert state["platforms"]["youtube"]["checked_total"] == 3


def test_respects_interval(fake_platforms):
    feed, hidden, _ = fake_platforms
    watcher.run_once(force=True)
    feed["youtube"].append(Comment(id="yt-3", text="loser", author="troll", platform="youtube"))
    watcher.run_once()  # not due yet (2 minute interval)
    assert hidden == ["yt-2"]


def test_pause_and_resume(fake_platforms):
    _, hidden, _ = fake_platforms
    watcher.set_paused(True)
    watcher.run_once(force=True)
    assert hidden == []
    watcher.set_paused(False)
    watcher.run_once(force=True)
    assert hidden == ["yt-2"]


def test_unhide_wrong_call(fake_platforms):
    _, _, unhidden = fake_platforms
    watcher.run_once(force=True)
    watcher.unhide("yt-2")
    assert unhidden == ["yt-2"]
    entry = watcher.load_state()["log"][0]
    assert entry["hidden"] is False and "undone_at" in entry
    with pytest.raises(PlatformError):
        watcher.unhide("yt-2")  # already unhidden


def test_hide_failure_is_logged_not_fatal(fake_platforms, monkeypatch):
    def refuse(p, ids):
        raise PlatformError("not your conversation")
    monkeypatch.setattr(moderation, "hide_on_platform", refuse)
    state = watcher.run_once(force=True)
    assert state["platforms"]["youtube"]["status"] == "watching"
    assert state["log"][0]["hidden"] is False and "not your conversation" in state["log"][0]["error"]


def test_platform_error_shows_in_status(fake_platforms, monkeypatch):
    def broken(state):
        raise PlatformError("quota exceeded")
    monkeypatch.setitem(watcher.WATCHERS, "youtube", broken)
    state = watcher.run_once(force=True)
    assert state["platforms"]["youtube"] == {**state["platforms"]["youtube"], "status": "error",
                                             "message": "quota exceeded"}


def test_public_state_explains_tiktok(fake_platforms):
    watcher.run_once(force=True)
    pub = watcher.public_state()
    assert pub["platforms"]["tiktok"]["status"] == "unsupported"
    assert "seen" not in pub and pub["intervals"]["youtube"] == 120


def test_interval_override(monkeypatch):
    monkeypatch.setenv("GUARDIAN_INSTAGRAM_INTERVAL", "600")
    assert watcher.interval("instagram") == 600
    monkeypatch.setenv("GUARDIAN_INSTAGRAM_INTERVAL", "5")  # too fast: ignored
    assert watcher.interval("instagram") == 300


# ------------------------------------------------------------ watchers parse real API shapes

def test_watch_youtube(monkeypatch):
    monkeypatch.setattr(moderation, "youtube_access_token", lambda: "tok")
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if url.endswith("/channels"):
            return {"items": [{"id": "UC123"}]}
        assert kw["params"]["allThreadsRelatedToChannelId"] == "UC123"
        return {"items": [{"snippet": {"topLevelComment": {"id": "c1", "snippet": {
            "textDisplay": "hi", "authorDisplayName": "Amy", "publishedAt": "2026-11-27T00:00:00Z"}}}}]}
    monkeypatch.setattr(watcher, "_get", fake_get)
    out = watcher.watch_youtube(watcher.load_state())
    assert [(c.id, c.author) for c in out] == [("yt-c1", "Amy")]
    watcher.watch_youtube(watcher.load_state())
    assert sum(u.endswith("/channels") for u in calls) == 1  # channel looked up once


def test_watch_instagram_skips_already_hidden(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "tok")

    def fake_get(url, **kw):
        if url.endswith("/me/accounts"):
            return {"data": [{"id": "page"}, {"instagram_business_account": {"id": "IG1"}}]}
        if url.endswith("/IG1/media"):
            return {"data": [{"id": "M1"}]}
        return {"data": [{"id": "1", "text": "nice", "username": "a"},
                         {"id": "2", "text": "ugly", "username": "b", "hidden": True}]}
    monkeypatch.setattr(watcher, "_get", fake_get)
    out = watcher.watch_instagram(watcher.load_state())
    assert [c.id for c in out] == ["ig-1"]


def test_watch_x_uses_since_id(monkeypatch):
    monkeypatch.setattr(moderation, "x_access_token", lambda: "tok")
    seen_params = []

    def fake_get(url, **kw):
        if url.endswith("/users/me"):
            return {"data": {"id": "9", "username": "creator"}}
        seen_params.append(dict(kw["params"]))
        return {"data": [{"id": "55", "text": "loser", "author_id": "7"}],
                "includes": {"users": [{"id": "7", "username": "troll"}]},
                "meta": {"newest_id": "55"}}
    monkeypatch.setattr(watcher, "_get", fake_get)
    state = watcher.load_state()
    out = watcher.watch_x(state)
    assert out[0].id == "x-55" and out[0].author == "@troll"
    assert seen_params[0]["query"] == "to:creator is:reply -from:creator"
    watcher.watch_x(state)
    assert seen_params[1]["since_id"] == "55"


# ------------------------------------------------------------ X login + hiding

class FakeResp:
    def __init__(self, status=200, data=None):
        self.status_code, self._data, self.text = status, data or {}, json.dumps(data or {})

    def json(self):
        return self._data


def test_x_login_pkce_and_hide(monkeypatch):
    monkeypatch.setenv("X_CLIENT_ID", "cid")
    monkeypatch.delenv("X_CLIENT_SECRET", raising=False)
    url, verifier = moderation.x_login_url("http://localhost:5000/auth/x/callback", "st")
    q = parse_qs(urlparse(url).query)
    assert q["code_challenge_method"] == ["S256"] and "tweet.moderate.write" in q["scope"][0]

    monkeypatch.setattr(moderation.requests, "post",
                        lambda url, **kw: FakeResp(data={"access_token": "A", "refresh_token": "R",
                                                         "expires_in": 7200}))
    moderation.x_finish_login("code", "http://localhost:5000/auth/x/callback", verifier)
    assert moderation.x_logged_in() and moderation.can_hide()["x"]

    puts = []
    monkeypatch.setattr(moderation.requests, "put",
                        lambda url, **kw: puts.append((url, kw["json"])) or FakeResp())
    assert moderation.hide_on_platform("x", ["x-111"]) == 1
    assert moderation.unhide_on_platform("x", ["x-111"]) == 1
    assert puts == [("https://api.x.com/2/tweets/111/hidden", {"hidden": True}),
                    ("https://api.x.com/2/tweets/111/hidden", {"hidden": False})]


def test_x_refresh_rotates_token(monkeypatch):
    monkeypatch.setenv("X_CLIENT_ID", "cid")
    moderation._update_tokens("x", {"access_token": "old", "refresh_token": "R1", "expires_at": 0})
    monkeypatch.setattr(moderation.requests, "post",
                        lambda url, **kw: FakeResp(data={"access_token": "new", "refresh_token": "R2"}))
    assert moderation.x_access_token() == "new"
    assert moderation._load_tokens()["x"]["refresh_token"] == "R2"


def test_instagram_and_youtube_unhide(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "tok")
    posts = []
    monkeypatch.setattr(moderation.requests, "post",
                        lambda url, **kw: posts.append(kw["params"]) or FakeResp())
    moderation.unhide_on_platform("instagram", ["ig-5"])
    assert posts[-1] == {"hide": "false", "access_token": "tok"}
    monkeypatch.setattr(moderation, "youtube_access_token", lambda: "t")
    moderation.unhide_on_platform("youtube", ["yt-5"])
    assert posts[-1]["moderationStatus"] == "published"


def test_tiktok_cannot_be_hidden():
    with pytest.raises(PlatformError, match="TikTok"):
        moderation.hide_on_platform("tiktok", ["tiktok-1"])


# ------------------------------------------------------------ web endpoints

def test_always_on_endpoints(fake_platforms):
    from guardian.app import app
    c = app.test_client()
    assert c.get("/api/always-on").get_json()["paused"] is False
    out = c.post("/api/always-on/check").get_json()
    assert out["platforms"]["youtube"]["hidden_total"] == 1
    assert c.post("/api/always-on/pause", json={"paused": True}).get_json()["paused"] is True
    r = c.post("/api/always-on/unhide", json={"id": "yt-2"})
    assert r.status_code == 200 and r.get_json()["log"][0]["hidden"] is False
    assert c.post("/api/always-on/unhide", json={"id": "nope"}).status_code == 400


def test_x_login_route(monkeypatch):
    monkeypatch.setenv("X_CLIENT_ID", "cid")
    from guardian.app import app
    c = app.test_client()
    r = c.get("/auth/x/login")
    assert r.status_code == 302 and r.location.startswith(moderation.X_AUTH_URL)
    assert c.get("/auth/x/callback?state=bad&code=x").status_code == 400
