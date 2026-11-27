"""Fetch comments from YouTube, Instagram, X and TikTok.

Every platform guards comments differently:

| Platform  | How we get comments                  | What you need                         |
|-----------|--------------------------------------|---------------------------------------|
| YouTube   | YouTube Data API v3                  | Free API key (YOUTUBE_API_KEY)        |
| Instagram | Instagram Graph API                  | Business/Creator account access token |
| X         | X API v2 recent search               | Bearer token (paid API tier)          |
| TikTok    | Paste or upload exported comments    | Nothing - TikTok has no public        |
|           |                                      | comments API for creators             |

Any platform also works with "paste comments" mode, no keys needed.
"""

from __future__ import annotations

import csv
import io
import os
import re

import requests

from .classifier import Comment

TIMEOUT = 15


class PlatformError(Exception):
    """A friendly message to show the creator."""


def _need(env: str, platform: str) -> str:
    value = os.environ.get(env, "").strip()
    if not value:
        raise PlatformError(
            f"{platform} isn't connected yet. Add {env} to your .env file "
            f"(see README), or use 'Paste comments' instead."
        )
    return value


def _get(url: str, **kw) -> dict:
    try:
        r = requests.get(url, timeout=TIMEOUT, **kw)
    except requests.RequestException as e:
        raise PlatformError(f"Couldn't reach the platform: {e}") from e
    if r.status_code != 200:
        try:
            detail = r.json()
        except ValueError:
            detail = r.text[:200]
        raise PlatformError(f"Platform returned an error ({r.status_code}): {detail}")
    return r.json()


# ---------------------------------------------------------------- YouTube

def youtube_video_id(url_or_id: str) -> str:
    s = url_or_id.strip()
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([A-Za-z0-9_-]{11})", s)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    raise PlatformError("That doesn't look like a YouTube video link.")


def fetch_youtube(url_or_id: str, limit: int = 300) -> list[Comment]:
    key = _need("YOUTUBE_API_KEY", "YouTube")
    video_id = youtube_video_id(url_or_id)
    comments, page = [], None
    while len(comments) < limit:
        params = {"part": "snippet", "videoId": video_id, "maxResults": 100,
                  "textFormat": "plainText", "key": key}
        if page:
            params["pageToken"] = page
        data = _get("https://www.googleapis.com/youtube/v3/commentThreads", params=params)
        for item in data.get("items", []):
            s = item["snippet"]["topLevelComment"]["snippet"]
            comments.append(Comment(
                id=f"yt-{item['id']}", text=s.get("textDisplay", ""),
                author=s.get("authorDisplayName", "anonymous"),
                platform="youtube", created_at=s.get("publishedAt", "")))
        page = data.get("nextPageToken")
        if not page:
            break
    return comments[:limit]


# ---------------------------------------------------------------- Instagram

def fetch_instagram(media_id: str, limit: int = 300) -> list[Comment]:
    token = _need("INSTAGRAM_ACCESS_TOKEN", "Instagram")
    media_id = media_id.strip()
    if not media_id.isdigit():
        raise PlatformError(
            "Instagram needs the numeric media ID of the post (not the link). "
            "Find it with GET /me/media in the Graph API Explorer - see README.")
    url = f"https://graph.facebook.com/v21.0/{media_id}/comments"
    params = {"fields": "id,text,username,timestamp", "limit": 100, "access_token": token}
    comments = []
    while url and len(comments) < limit:
        data = _get(url, params=params)
        for c in data.get("data", []):
            comments.append(Comment(
                id=f"ig-{c['id']}", text=c.get("text", ""),
                author=c.get("username", "anonymous"),
                platform="instagram", created_at=c.get("timestamp", "")))
        url, params = data.get("paging", {}).get("next"), None  # "next" has params baked in
    return comments[:limit]


# ---------------------------------------------------------------- X (Twitter)

def x_post_id(url_or_id: str) -> str:
    s = url_or_id.strip()
    m = re.search(r"/status(?:es)?/(\d+)", s)
    if m:
        return m.group(1)
    if s.isdigit():
        return s
    raise PlatformError("That doesn't look like an X post link.")


def fetch_x(url_or_id: str, limit: int = 300) -> list[Comment]:
    token = _need("X_BEARER_TOKEN", "X")
    post_id = x_post_id(url_or_id)
    params = {"query": f"conversation_id:{post_id}", "max_results": 100,
              "tweet.fields": "author_id,created_at", "expansions": "author_id",
              "user.fields": "username"}
    headers = {"Authorization": f"Bearer {token}"}
    comments = []
    while len(comments) < limit:
        data = _get("https://api.x.com/2/tweets/search/recent", params=params, headers=headers)
        users = {u["id"]: u["username"] for u in data.get("includes", {}).get("users", [])}
        for t in data.get("data", []):
            comments.append(Comment(
                id=f"x-{t['id']}", text=t.get("text", ""),
                author="@" + users.get(t.get("author_id"), "anonymous"),
                platform="x", created_at=t.get("created_at", "")))
        nxt = data.get("meta", {}).get("next_token")
        if not nxt:
            break
        params["next_token"] = nxt
    return comments[:limit]


# ---------------------------------------------------------------- Paste / CSV (TikTok & everyone)

def parse_pasted(text: str, platform: str = "tiktok") -> list[Comment]:
    """Accepts either a CSV with a 'text'/'comment' column, or one comment per line.

    Lines like ``username: comment`` keep the username.
    """
    text = text.strip()
    if not text:
        return []
    first = text.splitlines()[0].lower()
    if "," in first and any(h in first for h in ("text", "comment")):
        reader = csv.DictReader(io.StringIO(text))
        out = []
        for i, row in enumerate(reader):
            row = {k.strip().lower(): (v or "") for k, v in row.items() if k}
            body = row.get("text") or row.get("comment") or row.get("comment text") or ""
            if body.strip():
                out.append(Comment(
                    id=f"{platform}-{i}", text=body.strip(),
                    author=row.get("username") or row.get("user") or row.get("author") or "anonymous",
                    platform=platform, created_at=row.get("date") or row.get("time") or ""))
        return out

    out = []
    for i, line in enumerate(l.strip() for l in text.splitlines()):
        if not line:
            continue
        author, body = "anonymous", line
        m = re.match(r"^@?([\w.]{2,30}):\s+(.+)$", line)
        if m:
            author, body = m.group(1), m.group(2)
        out.append(Comment(id=f"{platform}-{i}", text=body, author=author, platform=platform))
    return out


FETCHERS = {"youtube": fetch_youtube, "instagram": fetch_instagram, "x": fetch_x}


def connected_platforms() -> dict[str, bool]:
    return {
        "youtube": bool(os.environ.get("YOUTUBE_API_KEY")),
        "instagram": bool(os.environ.get("INSTAGRAM_ACCESS_TOKEN")),
        "x": bool(os.environ.get("X_BEARER_TOKEN")),
        "tiktok": False,  # paste/upload only
    }
