"""The weekly kindness report: a summary email that never contains the bullying itself."""

from __future__ import annotations

import html
import os
import smtplib
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from . import classifier
from .classifier import Comment
from .moderation import can_hide, hide_on_platform
from .platforms import FETCHERS, PlatformError

PLATFORM_NAMES = {"youtube": "YouTube", "instagram": "Instagram", "x": "X", "tiktok": "TikTok"}


def parse_watchlist(value: str) -> list[tuple[str, str]]:
    """'youtube:https://youtu.be/abc, instagram:1789' -> [('youtube', 'https://...'), ...]"""
    posts = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        platform, _, link = item.partition(":")
        platform = platform.strip().lower()
        if platform not in FETCHERS or not link.strip():
            raise PlatformError(
                f"Can't read '{item}' in WATCH_POSTS. Use platform:link with platform one of "
                f"{', '.join(FETCHERS)} (TikTok has no comments API).")
        posts.append((platform, link.strip()))
    return posts


def _recent(c: Comment, since: datetime) -> bool:
    if not c.created_at:
        return True
    try:
        return datetime.fromisoformat(c.created_at.replace("Z", "+00:00")) >= since
    except ValueError:
        return True


def build_report(posts: list[tuple[str, str]], days: int = 7, auto_hide: bool = False,
                 now: datetime | None = None) -> dict:
    """Fetch, classify and (optionally) hide. Returns everything the email needs."""
    since = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    comments, problems, hidden_on_platform = [], [], 0
    hideable = can_hide()

    for platform, link in posts:
        try:
            fetched = [c for c in FETCHERS[platform](link) if _recent(c, since)]
        except PlatformError as e:
            problems.append(f"{PLATFORM_NAMES[platform]} ({link}): {e}")
            continue
        fetched, _ = classifier.classify(fetched)
        comments += fetched
        bad = [c.id for c in fetched if c.category in classifier.HIDDEN_BY_DEFAULT]
        if auto_hide and bad and hideable.get(platform):
            try:
                hidden_on_platform += hide_on_platform(platform, bad)
            except PlatformError as e:
                problems.append(f"Couldn't hide on {PLATFORM_NAMES[platform]}: {e}")

    counts = Counter(c.category for c in comments)
    offenders = Counter(c.author for c in comments
                        if c.category in classifier.HIDDEN_BY_DEFAULT and c.author != "anonymous")
    total = len(comments)
    bad_total = sum(counts[k] for k in classifier.HIDDEN_BY_DEFAULT)
    return {
        "days": days,
        "total": total,
        "counts": {k: counts[k] for k in classifier.CATEGORIES},
        "percent_ok": round(100 * (total - bad_total) / total) if total else 100,
        "highlights": [c for c in comments if c.category == classifier.SUPPORTIVE][:10],
        "feedback": [c for c in comments if c.category == classifier.CRITICISM][:5],
        "hidden_on_platform": hidden_on_platform,
        "repeat_offenders": [a for a, n in offenders.most_common(5) if n > 1],
        "problems": problems,
    }


def render_email(r: dict) -> tuple[str, str]:
    """Returns (subject, html_body). Never includes the text of harmful comments."""
    e = html.escape
    bad = r["counts"]["harassment"] + r["counts"]["threat"]
    subject = f"💚 Your weekly kindness report: {r['counts']['supportive']} supportive comments"

    def items(cs):
        return "".join(
            f'<li style="margin:0 0 10px"><b>{e(c.author)}</b> '
            f'<span style="color:#888">on {PLATFORM_NAMES.get(c.platform, c.platform)}</span><br>'
            f'{e(c.text)}</li>' for c in cs)

    parts = [
        '<div style="font-family:system-ui,sans-serif;max-width:560px;margin:auto;color:#1e1b2e">',
        '<h2 style="margin-bottom:4px">🛡️ Your week on social media</h2>',
        f'<p style="color:#6b6880;margin-top:0">The last {r["days"]} days, without the noise.</p>',
        '<table style="width:100%;border-collapse:collapse;margin:16px 0"><tr>',
    ]
    for n, label in [(r["total"], "comments"), (f'{r["percent_ok"]}%', "were OK"),
                     (r["counts"]["supportive"], "supportive"), (bad, "bullying filtered")]:
        parts.append(f'<td style="background:#f6f5fb;padding:12px;border:4px solid #fff;'
                     f'border-radius:10px"><b style="font-size:22px">{n}</b><br>'
                     f'<span style="color:#6b6880;font-size:13px">{label}</span></td>')
    parts.append("</tr></table>")

    if r["highlights"]:
        parts += ["<h3>💚 People who love your work</h3><ul style='padding-left:18px'>",
                  items(r["highlights"]), "</ul>"]
    else:
        parts.append("<p>No new supportive comments this week. Quiet weeks happen to everyone.</p>")
    if r["feedback"]:
        parts += ["<h3>💡 Feedback worth a look</h3><ul style='padding-left:18px'>",
                  items(r["feedback"]), "</ul>"]
    if bad:
        hidden = (f" {r['hidden_on_platform']} of them were hidden on the platform automatically."
                  if r["hidden_on_platform"] else "")
        parts.append(f'<p style="background:#fde7eb;color:#b4233a;padding:12px;border-radius:10px">'
                     f'🛡️ {bad} hurtful comment{"s were" if bad != 1 else " was"} filtered out, so '
                     f"you didn't have to read {'them' if bad != 1 else 'it'}.{hidden}"
                     + (f" {r['counts']['threat']} may need reporting: open Guardian to review "
                        f"when you feel ready." if r["counts"]["threat"] else "")
                     + "</p>")
    if r["repeat_offenders"]:
        parts.append(f"<p>Accounts that keep coming back (consider blocking): "
                     f"{', '.join(e(a) for a in r['repeat_offenders'])}</p>")
    if r["problems"]:
        parts.append('<p style="color:#a15c00;font-size:13px">Some posts couldn\'t be checked:<br>'
                     + "<br>".join(e(p) for p in r["problems"]) + "</p>")
    parts.append('<p style="color:#6b6880;font-size:13px">What strangers type says more about '
                 'them than about you. Take care of yourself. 💚<br>— Guardian</p></div>')
    return subject, "".join(parts)


def send_email(subject: str, html_body: str) -> None:
    host = os.environ.get("SMTP_HOST")
    to = os.environ.get("REPORT_TO")
    if not host or not to:
        raise PlatformError("Set SMTP_HOST and REPORT_TO in .env to send email (see README).")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("REPORT_FROM") or os.environ.get("SMTP_USER") or to
    msg["To"] = to
    msg.set_content("Your weekly kindness report is best viewed in an email app that shows HTML.")
    msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=30) as s:
        s.starttls()
        if os.environ.get("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        s.send_message(msg)
