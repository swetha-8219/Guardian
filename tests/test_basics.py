import pytest

from guardian import classifier
from guardian.classifier import Comment, classify_offline
from guardian.platforms import PlatformError, parse_pasted, x_post_id, youtube_video_id


def cat(text):
    return classify_offline([Comment(id="1", text=text)])[0].category


@pytest.mark.parametrize("text,expected", [
    ("I love this, thank you!", "supportive"),
    ("The audio was too quiet", "criticism"),
    ("ur so ugly lol", "harassment"),
    ("k!ll yourself", "threat"),
    ("kys", "threat"),
    ("What camera do you use?", "neutral"),
])
def test_offline_classifier(text, expected):
    assert cat(text) == expected


def test_hidden_flag():
    c = Comment(id="1", text="x", category="harassment")
    assert c.to_dict()["hidden"] is True
    c.category = "criticism"
    assert c.to_dict()["hidden"] is False


def test_parse_lines_with_usernames():
    out = parse_pasted("amy: nice video\n\njust a comment", "tiktok")
    assert [(c.author, c.text) for c in out] == [("amy", "nice video"), ("anonymous", "just a comment")]


def test_parse_csv():
    out = parse_pasted("Username,Comment,Date\nbob,great stuff,2026-01-01\n", "tiktok")
    assert out[0].author == "bob" and out[0].text == "great stuff"


@pytest.mark.parametrize("link", [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10",
    "https://youtu.be/dQw4w9WgXcQ",
    "https://www.youtube.com/shorts/dQw4w9WgXcQ",
    "dQw4w9WgXcQ",
])
def test_youtube_ids(link):
    assert youtube_video_id(link) == "dQw4w9WgXcQ"


def test_x_ids():
    assert x_post_id("https://x.com/someone/status/1234567890?s=20") == "1234567890"
    with pytest.raises(PlatformError):
        x_post_id("not a link")


def test_api_paste_flow(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    from guardian.app import app
    client = app.test_client()
    r = client.post("/api/analyze", json={"platform": "tiktok", "mode": "paste",
                                          "text": "troll: you're ugly\ntroll: loser\namy: love it"})
    data = r.get_json()
    assert r.status_code == 200
    assert data["summary"]["hidden"] == 2
    assert data["summary"]["repeat_offenders"] == [{"author": "troll", "count": 2}]


def test_tiktok_fetch_explains(monkeypatch):
    from guardian.app import app
    r = app.test_client().post("/api/analyze", json={"platform": "tiktok", "mode": "fetch"})
    assert r.status_code == 400 and "TikTok" in r.get_json()["error"]


def test_claude_path_uses_results(monkeypatch):
    """Claude responses are mapped back onto comments (SDK call mocked)."""
    class Block:
        type = "text"
        text = '{"results":[{"id":"1","category":"harassment","reason":"Mocks appearance"}]}'

    class Resp:
        stop_reason = "end_turn"
        content = [Block()]

    class FakeClient:
        class beta:
            class messages:
                @staticmethod
                def create(**kw):
                    assert kw["output_config"]["format"]["type"] == "json_schema"
                    return Resp()

    comments = [Comment(id="1", text="nice glasses, grandma"), Comment(id="2", text="love it")]
    classifier._claude_batch(FakeClient, comments)
    assert comments[0].category == "harassment"
    assert comments[1].category == "supportive"  # missing from response -> offline fallback
