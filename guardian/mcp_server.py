"""Guardian as an MCP server: let Claude (or any MCP client) check your comments for you.

Ask things like "How are people reacting to my latest YouTube video?" and Claude
uses Guardian to fetch and sort the comments, then tells you the good news and the
useful feedback, without reading you the hate.

Run:  guardian-mcp     (stdio transport; see README for Claude Desktop / Claude Code setup)
"""

from __future__ import annotations

from collections import Counter

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

from guardian import classifier, watcher  # noqa: E402
from guardian.classifier import Comment  # noqa: E402
from guardian.platforms import FETCHERS, PlatformError, parse_pasted  # noqa: E402

INSTRUCTIONS = """Guardian protects content creators from online bullying.
Use it to sort comments into supportive, neutral, criticism (feedback about the work),
harassment and threat. Lead with the supportive comments and the useful feedback.
Do not repeat the text of harassment or threats unless the user explicitly asks to see
it; give counts and calm, non-graphic descriptions instead. If there are threats or
self-harm encouragement, suggest reporting them to the platform."""

server = MCPServer(name="guardian", title="Guardian", instructions=INSTRUCTIONS,
                   website_url="https://github.com/swetha-8219/Guardian")
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


def _summary(comments: list[Comment], include_hurtful_text: bool) -> dict:
    counts = Counter(c.category for c in comments)
    hurtful = [c for c in comments if c.category in classifier.HIDDEN_BY_DEFAULT]
    offenders = Counter(c.author for c in hurtful if c.author != "anonymous")
    out = {
        "total": len(comments),
        "counts": {k: counts[k] for k in classifier.CATEGORIES},
        "percent_ok": round(100 * (len(comments) - len(hurtful)) / len(comments)) if comments else 100,
        "supportive": [{"author": c.author, "text": c.text} for c in comments
                       if c.category == classifier.SUPPORTIVE][:15],
        "feedback": [{"author": c.author, "text": c.text, "why": c.reason} for c in comments
                     if c.category == classifier.CRITICISM][:10],
        "repeat_offenders": [a for a, n in offenders.most_common(5) if n > 1],
        "hurtful": [{"author": c.author, "category": c.category, "why": c.reason,
                     **({"text": c.text} if include_hurtful_text else {})} for c in hurtful],
    }
    return out


@server.tool(annotations=READ_ONLY)
def sort_comments(comments: list[str], include_hurtful_text: bool = False) -> dict:
    """Sort a list of comments (plain text, optionally 'username: comment') into
    supportive, neutral, criticism, harassment and threat. Returns counts, the supportive
    comments, the useful feedback, and a calm summary of anything hurtful. Set
    include_hurtful_text only if the user explicitly asks to read the hurtful comments."""
    parsed = parse_pasted("\n".join(c.replace("\n", " ") for c in comments), "pasted")
    parsed, engine = classifier.classify(parsed)
    return {"sorted_by": engine, **_summary(parsed, include_hurtful_text)}


@server.tool(annotations=READ_ONLY)
def check_post(platform: str, link: str, include_hurtful_text: bool = False) -> dict:
    """Fetch and sort the comments on one post. platform is 'youtube' (video link),
    'instagram' (numeric media ID) or 'x' (post link). TikTok has no comments API, so use
    sort_comments with pasted comments instead. Needs the matching key in Guardian's .env."""
    platform = platform.lower().strip()
    if platform not in FETCHERS:
        return {"error": "Supported platforms are youtube, instagram and x. TikTok has no "
                         "comments API: paste the comments into sort_comments instead."}
    try:
        comments = FETCHERS[platform](link)
    except PlatformError as e:
        return {"error": str(e)}
    comments, engine = classifier.classify(comments)
    return {"platform": platform, "sorted_by": engine, **_summary(comments, include_hurtful_text)}


@server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def always_on_status() -> dict:
    """Show what Guardian's always-on protection is doing: which platforms it watches,
    when it last checked, and how many bullying comments it has hidden automatically."""
    state = watcher.public_state()
    return {
        "paused": state["paused"],
        "last_run": state.get("last_run"),
        "platforms": {p: {k: v for k, v in info.items() if k != "last_attempt"}
                      for p, info in state["platforms"].items()},
        "recently_hidden": [{"platform": e["platform"], "author": e["author"],
                             "category": e["category"], "why": e.get("reason", ""),
                             "at": e["at"], "still_hidden": e.get("hidden", False)}
                            for e in state["log"][:20]],
    }


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
