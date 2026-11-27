"""Command-line tools that come with Guardian.

    guardian-always-on           # hide bullying around the clock (servers)
    guardian-always-on --once    # check once and show each platform's status
    guardian-weekly-report       # email the weekly kindness report
    guardian-weekly-report --preview
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import find_dotenv, load_dotenv


def _env() -> None:
    load_dotenv(find_dotenv(usecwd=True))


def always_on_main() -> int:
    """Run Guardian's always-on protection by itself (for example on a server that's always on).

    You don't need this if you keep the `guardian` app running: it starts always-on
    protection automatically. Run only one of the two at a time.
    """
    _env()
    from guardian import watcher

    ap = argparse.ArgumentParser(prog="guardian-always-on", description=always_on_main.__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="check every connected platform once, then exit")
    args = ap.parse_args()

    if not any(watcher.connected().values()):
        print("No platforms are connected for hiding yet. Open the app (guardian) and "
              "connect YouTube or X, or set INSTAGRAM_ACCESS_TOKEN in .env.", file=sys.stderr)
        return 1
    if args.once:
        state = watcher.run_once(force=True)
        for name, info in state["platforms"].items():
            print(f"{name}: {info.get('status')} {info.get('message', '')}".rstrip())
        return 0
    try:
        watcher.run_forever()
    except KeyboardInterrupt:
        print("\n[always-on] stopped.")
    return 0


def weekly_report_main() -> int:
    """Send the weekly kindness report (schedule it to run every week, see README)."""
    _env()
    from guardian.platforms import PlatformError
    from guardian.report import build_report, parse_watchlist, render_email, send_email

    ap = argparse.ArgumentParser(prog="guardian-weekly-report", description=weekly_report_main.__doc__)
    ap.add_argument("--preview", action="store_true", help="write kindness-report.html, don't email")
    ap.add_argument("--days", type=int, default=7, help="how many days back to look (default 7)")
    args = ap.parse_args()

    try:
        posts = parse_watchlist(os.environ.get("WATCH_POSTS", ""))
        if not posts:
            raise PlatformError("Add the posts to watch to WATCH_POSTS in .env (see README).")
        auto_hide = os.environ.get("AUTO_HIDE", "").lower() in ("1", "true", "yes")
        report = build_report(posts, days=args.days, auto_hide=auto_hide)
        subject, body = render_email(report)
        if args.preview:
            with open("kindness-report.html", "w", encoding="utf-8") as f:
                f.write(body)
            print("Saved kindness-report.html - open it in your browser.")
        else:
            send_email(subject, body)
            print(f"Sent: {subject}")
    except PlatformError as e:
        print(f"Couldn't make the report: {e}", file=sys.stderr)
        return 1
    return 0
