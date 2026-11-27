"""Guardian - a comment shield for content creators.

Run:  guardian        (or: python app.py)   then open http://localhost:5000
"""

from __future__ import annotations

import argparse
import os
import secrets
import threading
import webbrowser
from collections import Counter

from dotenv import find_dotenv, load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

load_dotenv(find_dotenv(usecwd=True))  # the .env in the folder you run Guardian from

from guardian import classifier, moderation, watcher  # noqa: E402  (needs .env loaded first)
from guardian.platforms import (  # noqa: E402
    FETCHERS, PlatformError, connected_platforms, parse_pasted)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
PLATFORMS = ["youtube", "instagram", "tiktok", "x"]


def summarise(comments: list[classifier.Comment]) -> dict:
    counts = Counter(c.category for c in comments)
    total = len(comments)
    kind = counts[classifier.SUPPORTIVE] + counts[classifier.NEUTRAL] + counts[classifier.CRITICISM]
    offenders = Counter(c.author for c in comments
                        if c.category in classifier.HIDDEN_BY_DEFAULT and c.author != "anonymous")
    return {
        "total": total,
        "counts": {cat: counts[cat] for cat in classifier.CATEGORIES},
        "hidden": total - kind,
        "percent_ok": round(100 * kind / total) if total else 100,
        "repeat_offenders": [{"author": a, "count": n} for a, n in offenders.most_common() if n > 1],
    }


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/status")
def status():
    return jsonify({
        "ai": classifier.ai_enabled(),
        "connected": connected_platforms(),
        "can_hide": moderation.can_hide(),
        "youtube_login_available": moderation.youtube_oauth_configured(),
        "x_login_available": moderation.x_oauth_configured(),
    })


@app.post("/api/analyze")
def analyze():
    body = request.get_json(force=True, silent=True) or {}
    platform = body.get("platform", "youtube")
    mode = body.get("mode", "paste")  # "paste" or "fetch"
    if platform not in PLATFORMS:
        return jsonify({"error": "Unknown platform"}), 400
    try:
        if mode == "fetch":
            if platform not in FETCHERS:
                raise PlatformError("TikTok doesn't offer a comments API - paste or upload them instead.")
            comments = FETCHERS[platform](body.get("link", ""))
        else:
            comments = parse_pasted(body.get("text", ""), platform)
    except PlatformError as e:
        return jsonify({"error": str(e)}), 400

    if not comments:
        return jsonify({"error": "No comments found."}), 400
    comments = comments[:1000]

    comments, engine = classifier.classify(comments)
    return jsonify({
        "engine": engine,
        "platform": platform,
        "mode": mode,
        "summary": summarise(comments),
        "comments": [c.to_dict() for c in comments],
    })


@app.post("/api/hide")
def hide():
    """Hide comments on the platform itself (YouTube: held for review, Instagram: hidden)."""
    body = request.get_json(force=True, silent=True) or {}
    ids = [str(i) for i in body.get("ids", [])][:500]
    try:
        n = moderation.hide_on_platform(body.get("platform", ""), ids)
    except PlatformError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"hidden": n})


@app.post("/api/classify")
def classify_texts():
    """Used by the browser extension: {"texts": [...]} -> {"categories": [...]}."""
    body = request.get_json(force=True, silent=True) or {}
    texts = [str(t)[:2000] for t in body.get("texts", [])][:200]
    comments = [classifier.Comment(id=str(i), text=t) for i, t in enumerate(texts)]
    comments, engine = classifier.classify(comments)
    return jsonify({"engine": engine, "categories": [c.category for c in comments]})


# ------------------------------------------------------------ Always-on protection (auto-hide)

@app.get("/api/always-on")
def always_on_status():
    return jsonify(watcher.public_state())


@app.post("/api/always-on/pause")
def always_on_pause():
    body = request.get_json(force=True, silent=True) or {}
    watcher.set_paused(bool(body.get("paused")))
    return jsonify(watcher.public_state())


@app.post("/api/always-on/check")
def always_on_check_now():
    watcher.run_once(force=True)
    return jsonify(watcher.public_state())


@app.post("/api/always-on/unhide")
def always_on_unhide():
    body = request.get_json(force=True, silent=True) or {}
    try:
        watcher.unhide(str(body.get("id", "")))
    except PlatformError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(watcher.public_state())


# ------------------------------------------------------------ YouTube login

def _redirect_uri() -> str:
    return url_for("youtube_callback", _external=True)


@app.get("/auth/youtube/login")
def youtube_login():
    state = secrets.token_urlsafe(16)
    session["oauth_state"] = state
    try:
        return redirect(moderation.youtube_login_url(_redirect_uri(), state))
    except PlatformError as e:
        return render_template("message.html", title="YouTube login isn't set up", message=str(e)), 400


@app.get("/auth/youtube/callback")
def youtube_callback():
    if request.args.get("state") != session.pop("oauth_state", None):
        return render_template("message.html", title="Login expired",
                               message="Please try connecting YouTube again."), 400
    if "error" in request.args or "code" not in request.args:
        return render_template("message.html", title="YouTube not connected",
                               message="Login was cancelled. Nothing was changed."), 400
    try:
        moderation.youtube_finish_login(request.args["code"], _redirect_uri())
    except PlatformError as e:
        return render_template("message.html", title="YouTube login failed", message=str(e)), 400
    return redirect(url_for("index"))


@app.post("/auth/youtube/logout")
def youtube_logout():
    moderation.youtube_logout()
    return jsonify({"ok": True})


# ------------------------------------------------------------ X login

@app.get("/auth/x/login")
def x_login():
    state = secrets.token_urlsafe(16)
    try:
        url, verifier = moderation.x_login_url(url_for("x_callback", _external=True), state)
    except PlatformError as e:
        return render_template("message.html", title="X login isn't set up", message=str(e)), 400
    session["x_state"], session["x_verifier"] = state, verifier
    return redirect(url)


@app.get("/auth/x/callback")
def x_callback():
    if request.args.get("state") != session.pop("x_state", None):
        return render_template("message.html", title="Login expired",
                               message="Please try connecting X again."), 400
    if "error" in request.args or "code" not in request.args:
        return render_template("message.html", title="X not connected",
                               message="Login was cancelled. Nothing was changed."), 400
    try:
        moderation.x_finish_login(request.args["code"], url_for("x_callback", _external=True),
                                  session.pop("x_verifier", ""))
    except PlatformError as e:
        return render_template("message.html", title="X login failed", message=str(e)), 400
    return redirect(url_for("index"))


@app.post("/auth/x/logout")
def x_logout():
    moderation.x_logout()
    return jsonify({"ok": True})


def main() -> None:
    ap = argparse.ArgumentParser(prog="guardian", description="Guardian: read the love, skip the hate.")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5000)),
                    help="port for the web app (default 5000)")
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")
    args = ap.parse_args()

    if os.environ.get("ALWAYS_ON", "on").lower() not in ("off", "false", "0", "no"):
        watcher.start_background()  # hides bullying around the clock while the app runs
    url = f"http://localhost:{args.port}"
    print(f"\n  🛡️  Guardian is running at {url}\n")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    # use_reloader=False so always-on protection doesn't start twice
    app.run(debug=False, use_reloader=False, port=args.port)


if __name__ == "__main__":
    main()
