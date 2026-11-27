"""Always-on protection: watches your accounts around the clock and hides bullying automatically.

Every minute it checks the platforms that are due, sorts any new comments, and
hides harassment and threats on the platform itself, so you never have to click
anything. Everything it hides is listed in the app, where one click un-hides a
comment if it got one wrong.

How "real-time" it is depends on each platform's API rate limits:

| Platform  | Checks every | What it watches                                  |
|-----------|--------------|--------------------------------------------------|
| YouTube   | 2 minutes    | Newest 100 comments across your whole channel    |
| Instagram | 5 minutes    | Comments on your 10 most recent posts            |
| X         | 3 minutes    | New replies to you (needs X API Basic or higher) |
| TikTok    | -            | Not possible: TikTok has no comments API         |

Intervals can be changed with GUARDIAN_YOUTUBE_INTERVAL etc. (seconds).
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import classifier, moderation
from .classifier import Comment
from .platforms import PlatformError, _get

STATE_FILE = Path(os.environ.get("GUARDIAN_STATE_FILE", ".guardian.json"))
TICK_SECONDS = 60
DEFAULT_INTERVALS = {"youtube": 120, "instagram": 300, "x": 180}
MAX_SEEN = 5000   # remembered comment IDs per platform
MAX_LOG = 300     # hidden comments listed in the app
INSTAGRAM_POSTS = 10

PLATFORMS = ["youtube", "instagram", "x", "tiktok"]
_lock = threading.RLock()
_cache: dict = {}  # account IDs looked up once per run


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def interval(platform: str) -> int:
    env = os.environ.get(f"GUARDIAN_{platform.upper()}_INTERVAL", "")
    return int(env) if env.isdigit() and int(env) >= 30 else DEFAULT_INTERVALS[platform]


# ------------------------------------------------------------ state file

def load_state() -> dict:
    with _lock:
        try:
            state = json.loads(STATE_FILE.read_text())
        except (OSError, ValueError):
            state = {}
    state.setdefault("paused", False)
    state.setdefault("platforms", {})
    state.setdefault("seen", {})
    state.setdefault("log", [])
    state.setdefault("cursors", {})
    return state


def save_state(state: dict) -> None:
    with _lock:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False))
        os.replace(tmp, STATE_FILE)  # atomic: the app never reads a half-written file


def public_state() -> dict:
    """What the web app shows (without the long list of seen IDs)."""
    state = load_state()
    for p in PLATFORMS:
        state["platforms"].setdefault(p, {"status": "not_connected"})
    state["platforms"]["tiktok"] = {
        "status": "unsupported",
        "message": "TikTok has no comments API, so no app can hide TikTok comments for you.",
    }
    state.pop("seen", None)
    state.pop("cursors", None)
    state["intervals"] = {p: interval(p) for p in DEFAULT_INTERVALS}
    # TikTok can't be automated, but its own keyword filter runs 24/7: offer our words for it.
    state["tiktok_filter_words"] = classifier._HARASS_WORDS
    return state


def set_paused(paused: bool) -> None:
    with _lock:
        state = load_state()
        state["paused"] = paused
        save_state(state)


def unhide(log_id: str) -> None:
    """Undo one automatic hide (for when it got one wrong)."""
    with _lock:
        state = load_state()
        entry = next((e for e in state["log"] if e["id"] == log_id), None)
        if not entry:
            raise PlatformError("That comment isn't in the always-on list any more.")
        if not entry.get("hidden"):
            raise PlatformError("That comment isn't hidden.")
        moderation.unhide_on_platform(entry["platform"], [entry["id"]])
        entry["hidden"] = False
        entry["undone_at"] = _now()
        save_state(state)


# ------------------------------------------------------------ watchers: newest comments per platform

def watch_youtube(state: dict) -> list[Comment]:
    token = moderation.youtube_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    if "yt_channel" not in _cache:
        data = _get("https://www.googleapis.com/youtube/v3/channels",
                    params={"part": "id", "mine": "true"}, headers=headers)
        if not data.get("items"):
            raise PlatformError("This Google account has no YouTube channel.")
        _cache["yt_channel"] = data["items"][0]["id"]
    data = _get("https://www.googleapis.com/youtube/v3/commentThreads", headers=headers, params={
        "part": "snippet", "allThreadsRelatedToChannelId": _cache["yt_channel"],
        "order": "time", "maxResults": 100, "textFormat": "plainText"})
    out = []
    for item in data.get("items", []):
        top = item["snippet"]["topLevelComment"]
        s = top["snippet"]
        out.append(Comment(id=f"yt-{top['id']}", text=s.get("textDisplay", ""),
                           author=s.get("authorDisplayName", "anonymous"),
                           platform="youtube", created_at=s.get("publishedAt", "")))
    return out


def watch_instagram(state: dict) -> list[Comment]:
    token = os.environ["INSTAGRAM_ACCESS_TOKEN"].strip()
    if "ig_user" not in _cache:
        ig = os.environ.get("INSTAGRAM_USER_ID", "").strip()
        if not ig:  # find the Instagram account linked to your Facebook Page
            pages = _get(f"{moderation.GRAPH}/me/accounts",
                         params={"fields": "instagram_business_account", "access_token": token})
            ig = next((p["instagram_business_account"]["id"] for p in pages.get("data", [])
                       if p.get("instagram_business_account")), "")
            if not ig:
                raise PlatformError("No Instagram Business/Creator account found. Set INSTAGRAM_USER_ID in .env.")
        _cache["ig_user"] = ig
    media = _get(f"{moderation.GRAPH}/{_cache['ig_user']}/media",
                 params={"fields": "id", "limit": INSTAGRAM_POSTS, "access_token": token})
    out = []
    for m in media.get("data", []):
        comments = _get(f"{moderation.GRAPH}/{m['id']}/comments", params={
            "fields": "id,text,username,timestamp,hidden", "limit": 50, "access_token": token})
        for c in comments.get("data", []):
            if not c.get("hidden"):
                out.append(Comment(id=f"ig-{c['id']}", text=c.get("text", ""),
                                   author=c.get("username", "anonymous"),
                                   platform="instagram", created_at=c.get("timestamp", "")))
    return out


def watch_x(state: dict) -> list[Comment]:
    token = moderation.x_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    if "x_user" not in _cache:
        me = _get("https://api.x.com/2/users/me", headers=headers)["data"]
        _cache["x_user"] = me["username"]
    me = _cache["x_user"]
    params = {"query": f"to:{me} is:reply -from:{me}", "max_results": 100,
              "tweet.fields": "author_id,created_at", "expansions": "author_id",
              "user.fields": "username"}
    since = state["cursors"].get("x_since_id")
    if since:
        params["since_id"] = since
    data = _get("https://api.x.com/2/tweets/search/recent", params=params, headers=headers)
    if data.get("meta", {}).get("newest_id"):
        state["cursors"]["x_since_id"] = data["meta"]["newest_id"]
    users = {u["id"]: u["username"] for u in data.get("includes", {}).get("users", [])}
    return [Comment(id=f"x-{t['id']}", text=t.get("text", ""),
                    author="@" + users.get(t.get("author_id"), "anonymous"),
                    platform="x", created_at=t.get("created_at", ""))
            for t in data.get("data", [])]


WATCHERS: dict[str, Callable[[dict], list[Comment]]] = {
    "youtube": watch_youtube, "instagram": watch_instagram, "x": watch_x}


def connected() -> dict[str, bool]:
    return moderation.can_hide()


# ------------------------------------------------------------ one pass

def check_platform(platform: str, state: dict) -> None:
    info = state["platforms"].setdefault(platform, {})
    seen = set(state["seen"].get(platform, []))
    comments = WATCHERS[platform](state)
    new = [c for c in comments if c.id not in seen]
    if new:
        new, engine = classifier.classify(new)
        info["engine"] = engine
    bad = [c for c in new if c.category in classifier.HIDDEN_BY_DEFAULT]

    for c in bad:
        entry = {"id": c.id, "platform": platform, "author": c.author, "text": c.text,
                 "category": c.category, "reason": c.reason, "at": _now(), "hidden": False}
        try:
            moderation.hide_on_platform(platform, [c.id])
            entry["hidden"] = True
            info["hidden_total"] = info.get("hidden_total", 0) + 1
        except PlatformError as e:  # e.g. an X reply in someone else's conversation
            entry["error"] = str(e)[:200]
        state["log"].insert(0, entry)

    # Remember what we've checked so each comment is sorted only once.
    order = state["seen"].get(platform, []) + [c.id for c in new]
    state["seen"][platform] = order[-MAX_SEEN:]
    state["log"] = state["log"][:MAX_LOG]
    info.update(status="watching", message="", last_check=_now(),
                checked_total=info.get("checked_total", 0) + len(new))


def run_once(force: bool = False) -> dict:
    """Check every connected platform that is due. Returns the new state."""
    with _lock:
        state = load_state()
        if state["paused"]:
            return state
        now = time.time()
        conn = connected()
        for platform in WATCHERS:
            info = state["platforms"].setdefault(platform, {})
            if not conn.get(platform):
                info.update(status="not_connected", message="")
                continue
            if not force and now - info.get("last_attempt", 0) < interval(platform):
                continue
            info["last_attempt"] = now
            try:
                check_platform(platform, state)
            except (PlatformError, KeyError, ValueError) as e:
                info.update(status="error", message=str(e)[:300], last_check=_now())
        state["last_run"] = _now()
        save_state(state)
        return state


def run_forever(stop: threading.Event | None = None, log: Callable[[str], None] = print) -> None:
    stop = stop or threading.Event()
    log("[always-on] watching your comments. Press Ctrl+C to stop.")
    def hidden_total(state: dict) -> int:
        return sum(i.get("hidden_total", 0) for i in state["platforms"].values())

    while not stop.is_set():
        before = hidden_total(load_state())
        try:
            state = run_once()
            added = hidden_total(state) - before
            if added > 0:
                log(f"[always-on] hid {added} bullying comment{'s' if added != 1 else ''}")
        except Exception as e:  # never let one bad cycle stop always-on protection
            log(f"[always-on] check failed, will retry: {e}")
        stop.wait(TICK_SECONDS)


def start_background() -> threading.Event:
    """Run always-on protection in a background thread (used by app.py). Returns a stop event."""
    stop = threading.Event()
    threading.Thread(target=run_forever, args=(stop,), daemon=True, name="always-on").start()
    return stop
