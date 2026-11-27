"""Hide (and un-hide) bullying comments on the platform itself, so fans never see them.

| Platform  | How hiding works                                   | How to undo                         |
|-----------|----------------------------------------------------|-------------------------------------|
| YouTube   | Comment set to "Held for review" (Google login     | "Unhide" in Guardian, or YouTube    |
|           | once, via "Connect YouTube" in the app)            | Studio > Comments > Held for review |
| Instagram | Comment hidden (hide=true) with your access token  | "Unhide" in Guardian, or the        |
|           |                                                    | Instagram app                       |
| X         | Reply hidden (X login once, via "Connect X")       | "Unhide" in Guardian, or the        |
|           |                                                    | "hidden replies" view on X          |
| TikTok    | Not possible: TikTok has no comments API           | -                                   |

Nothing is ever deleted.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

from .platforms import TIMEOUT, PlatformError

TOKEN_FILE = Path(os.environ.get("GUARDIAN_TOKEN_FILE", ".tokens.json"))
_token_lock = threading.Lock()

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"

X_AUTH_URL = "https://x.com/i/oauth2/authorize"
X_TOKEN_URL = "https://api.x.com/2/oauth2/token"
X_SCOPES = "tweet.read users.read tweet.moderate.write offline.access"

GRAPH = "https://graph.facebook.com/v21.0"


# ------------------------------------------------------------ token storage

def _load_tokens() -> dict:
    try:
        return json.loads(TOKEN_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save_tokens(tokens: dict) -> None:
    TOKEN_FILE.write_text(json.dumps(tokens, indent=2))
    try:
        TOKEN_FILE.chmod(0o600)  # only you can read it
    except OSError:
        pass


def _update_tokens(platform: str, values: dict | None) -> None:
    with _token_lock:
        tokens = _load_tokens()
        if values is None:
            tokens.pop(platform, None)
        else:
            tokens[platform] = {**tokens.get(platform, {}), **values}
        _save_tokens(tokens)


def _post(url: str, what: str, **kw) -> requests.Response:
    try:
        return requests.post(url, timeout=TIMEOUT, **kw)
    except requests.RequestException as e:
        raise PlatformError(f"Couldn't reach {what}: {e}") from e


# ------------------------------------------------------------ YouTube login (OAuth)

def youtube_oauth_configured() -> bool:
    return bool(os.environ.get("YOUTUBE_CLIENT_ID") and os.environ.get("YOUTUBE_CLIENT_SECRET"))


def youtube_logged_in() -> bool:
    return "refresh_token" in _load_tokens().get("youtube", {})


def youtube_login_url(redirect_uri: str, state: str) -> str:
    if not youtube_oauth_configured():
        raise PlatformError("Add YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET to .env first (see README).")
    return GOOGLE_AUTH_URL + "?" + urlencode({
        "client_id": os.environ["YOUTUBE_CLIENT_ID"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": YOUTUBE_SCOPE,
        "access_type": "offline",   # gives a refresh token so you only log in once
        "prompt": "consent",
        "state": state,
    })


def _google_token_request(data: dict) -> dict:
    data = {**data, "client_id": os.environ["YOUTUBE_CLIENT_ID"],
            "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"]}
    r = _post(GOOGLE_TOKEN_URL, "Google", data=data)
    if r.status_code != 200:
        raise PlatformError(f"Google login failed: {r.text[:200]}")
    return r.json()


def youtube_finish_login(code: str, redirect_uri: str) -> None:
    tok = _google_token_request({"code": code, "grant_type": "authorization_code",
                                 "redirect_uri": redirect_uri})
    old = _load_tokens().get("youtube", {})
    _update_tokens("youtube", {
        "access_token": tok["access_token"],
        "refresh_token": tok.get("refresh_token") or old.get("refresh_token"),
        "expires_at": time.time() + tok.get("expires_in", 3600) - 60,
    })


def youtube_logout() -> None:
    _update_tokens("youtube", None)


def youtube_access_token() -> str:
    yt = _load_tokens().get("youtube", {})
    if not yt.get("refresh_token"):
        raise PlatformError("Connect your YouTube account first (button at the top of the app).")
    if time.time() < yt.get("expires_at", 0):
        return yt["access_token"]
    tok = _google_token_request({"refresh_token": yt["refresh_token"], "grant_type": "refresh_token"})
    _update_tokens("youtube", {"access_token": tok["access_token"],
                               "expires_at": time.time() + tok.get("expires_in", 3600) - 60})
    return tok["access_token"]


# ------------------------------------------------------------ X login (OAuth 2.0 with PKCE)

def x_oauth_configured() -> bool:
    return bool(os.environ.get("X_CLIENT_ID"))


def x_logged_in() -> bool:
    return "refresh_token" in _load_tokens().get("x", {})


def x_login_url(redirect_uri: str, state: str) -> tuple[str, str]:
    """Returns (url, code_verifier). Keep the verifier for x_finish_login."""
    if not x_oauth_configured():
        raise PlatformError("Add X_CLIENT_ID (and X_CLIENT_SECRET if your app has one) to .env first (see README).")
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    url = X_AUTH_URL + "?" + urlencode({
        "response_type": "code",
        "client_id": os.environ["X_CLIENT_ID"],
        "redirect_uri": redirect_uri,
        "scope": X_SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return url, verifier


def _x_token_request(data: dict) -> dict:
    data = {**data, "client_id": os.environ["X_CLIENT_ID"]}
    secret = os.environ.get("X_CLIENT_SECRET")
    auth = (os.environ["X_CLIENT_ID"], secret) if secret else None  # confidential vs public app
    r = _post(X_TOKEN_URL, "X", data=data, auth=auth)
    if r.status_code != 200:
        raise PlatformError(f"X login failed: {r.text[:200]}")
    return r.json()


def x_finish_login(code: str, redirect_uri: str, verifier: str) -> None:
    tok = _x_token_request({"code": code, "grant_type": "authorization_code",
                            "redirect_uri": redirect_uri, "code_verifier": verifier})
    _update_tokens("x", {
        "access_token": tok["access_token"],
        "refresh_token": tok.get("refresh_token"),
        "expires_at": time.time() + tok.get("expires_in", 7200) - 60,
    })


def x_logout() -> None:
    _update_tokens("x", None)


def x_access_token() -> str:
    xt = _load_tokens().get("x", {})
    if not xt.get("refresh_token"):
        raise PlatformError("Connect your X account first (button at the top of the app).")
    if time.time() < xt.get("expires_at", 0):
        return xt["access_token"]
    tok = _x_token_request({"refresh_token": xt["refresh_token"], "grant_type": "refresh_token"})
    # X rotates refresh tokens: always store the new one.
    _update_tokens("x", {"access_token": tok["access_token"],
                         "refresh_token": tok.get("refresh_token", xt["refresh_token"]),
                         "expires_at": time.time() + tok.get("expires_in", 7200) - 60})
    return tok["access_token"]


# ------------------------------------------------------------ hide / unhide

def _youtube_set_status(comment_ids: list[str], status: str) -> int:
    token = youtube_access_token()
    done = 0
    for i in range(0, len(comment_ids), 50):  # API accepts up to 50 IDs per call
        chunk = comment_ids[i:i + 50]
        r = _post("https://www.googleapis.com/youtube/v3/comments/setModerationStatus", "YouTube",
                  params={"id": ",".join(chunk), "moderationStatus": status},
                  headers={"Authorization": f"Bearer {token}"})
        if r.status_code not in (200, 204):
            raise PlatformError(
                f"YouTube refused ({r.status_code}). You can only moderate comments on "
                f"your own channel's videos. Details: {r.text[:200]}")
        done += len(chunk)
    return done


def _instagram_set_hidden(comment_ids: list[str], hidden: bool) -> int:
    token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip()
    if not token:
        raise PlatformError("Add INSTAGRAM_ACCESS_TOKEN to .env first.")
    done = 0
    for cid in comment_ids:
        r = _post(f"{GRAPH}/{cid}", "Instagram",
                  params={"hide": "true" if hidden else "false", "access_token": token})
        if r.status_code != 200:
            raise PlatformError(
                f"Instagram refused after {done} ({r.status_code}). Your token needs "
                f"the instagram_manage_comments permission. Details: {r.text[:200]}")
        done += 1
    return done


def _x_set_hidden(reply_ids: list[str], hidden: bool) -> int:
    token = x_access_token()
    done = 0
    for rid in reply_ids:
        try:
            r = requests.put(f"https://api.x.com/2/tweets/{rid}/hidden", json={"hidden": hidden},
                             headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
        except requests.RequestException as e:
            raise PlatformError(f"Couldn't reach X: {e}") from e
        if r.status_code != 200:
            raise PlatformError(
                f"X refused after {done} ({r.status_code}). You can only hide replies to "
                f"your own posts. Details: {r.text[:200]}")
        done += 1
    return done


def hide_youtube(ids: list[str]) -> int:
    return _youtube_set_status(ids, "heldForReview")


def hide_instagram(ids: list[str]) -> int:
    return _instagram_set_hidden(ids, True)


def hide_x(ids: list[str]) -> int:
    return _x_set_hidden(ids, True)


_PREFIX = {"youtube": "yt-", "instagram": "ig-", "x": "x-"}
_HIDERS = {"youtube": hide_youtube, "instagram": hide_instagram, "x": hide_x}
_UNHIDERS = {
    "youtube": lambda ids: _youtube_set_status(ids, "published"),
    "instagram": lambda ids: _instagram_set_hidden(ids, False),
    "x": lambda ids: _x_set_hidden(ids, False),
}


def can_hide() -> dict[str, bool]:
    return {"youtube": youtube_logged_in(),
            "instagram": bool(os.environ.get("INSTAGRAM_ACCESS_TOKEN")),
            "x": x_logged_in(),
            "tiktok": False}


def _raw_ids(platform: str, app_ids: list[str]) -> list[str]:
    if platform not in _HIDERS:
        raise PlatformError("TikTok doesn't let apps hide comments - use the browser extension "
                            "and TikTok's own comment filters instead.")
    prefix = _PREFIX[platform]
    raw = [i[len(prefix):] for i in app_ids if i.startswith(prefix)]
    if not raw:
        raise PlatformError("Only comments fetched from a post link can be hidden on the platform.")
    return raw


def hide_on_platform(platform: str, app_ids: list[str]) -> int:
    """app_ids are Guardian comment IDs like 'yt-Ugx...', 'ig-1789...' or 'x-1234...'."""
    raw = _raw_ids(platform, app_ids)
    return _HIDERS[platform](raw)


def unhide_on_platform(platform: str, app_ids: list[str]) -> int:
    raw = _raw_ids(platform, app_ids)
    return _UNHIDERS[platform](raw)
