"""Sort comments into categories so creators never have to read the cruel ones.

Two classifiers live here:
  * ``classify_with_claude`` - understands sarcasm, context and coded insults.
    Used when ANTHROPIC_API_KEY (or another Anthropic credential) is set.
  * ``classify_offline``     - a simple word-list fallback, so the app still
    works with no API key at all (good for trying it out).

``classify`` picks the best one available.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path

# The five buckets every comment lands in.
SUPPORTIVE = "supportive"   # kind words - shown first in "Highlights"
NEUTRAL = "neutral"         # questions, chit-chat
CRITICISM = "criticism"     # honest feedback about the content (kept visible)
HARASSMENT = "harassment"   # insults, bullying, hate - hidden by default
THREAT = "threat"           # threats, doxxing, self-harm encouragement - urgent

CATEGORIES = [SUPPORTIVE, NEUTRAL, CRITICISM, HARASSMENT, THREAT]
HIDDEN_BY_DEFAULT = {HARASSMENT, THREAT}

MODEL = os.environ.get("GUARDIAN_MODEL", "claude-opus-5-5")
BATCH_SIZE = 40  # comments sent to Claude per request


@dataclass
class Comment:
    id: str
    text: str
    author: str = "anonymous"
    platform: str = "pasted"
    created_at: str = ""
    category: str = NEUTRAL
    reason: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["hidden"] = self.category in HIDDEN_BY_DEFAULT
        return d


# --------------------------------------------------------------------------
# Offline fallback: word lists + "leetspeak" normalisation (k!ll -> kill)
# --------------------------------------------------------------------------

_LEET = str.maketrans({"0": "o", "1": "i", "!": "i", "3": "e", "4": "a",
                       "@": "a", "$": "s", "5": "s", "7": "t", "*": ""})

# Word lists are shared with the browser extension (extension/wordlists.json
# is a copy - a test checks the two stay identical).
_WORDLISTS = json.loads(
    (Path(__file__).with_name("wordlists.json")).read_text(encoding="utf-8"))
_THREAT_PATTERNS = _WORDLISTS["threat_patterns"]
_HARASS_WORDS = _WORDLISTS["harassment"]
_CRITIC_WORDS = _WORDLISTS["criticism"]
_SUPPORT_WORDS = _WORDLISTS["supportive"]


def _normalise(text: str) -> str:
    t = text.lower().translate(_LEET)
    return re.sub(r"(.)\1{2,}", r"\1\1", t)  # "sooooo" -> "soo"


def classify_offline(comments: list[Comment]) -> list[Comment]:
    for c in comments:
        t = _normalise(c.text)
        if any(re.search(p, t) for p in _THREAT_PATTERNS):
            c.category, c.reason = THREAT, "Matches a threat / self-harm pattern"
        elif any(w in t for w in _HARASS_WORDS):
            c.category, c.reason = HARASSMENT, "Contains insulting language"
        elif any(w in t for w in _CRITIC_WORDS):
            c.category, c.reason = CRITICISM, "Looks like feedback about the content"
        elif any(w in t for w in _SUPPORT_WORDS):
            c.category, c.reason = SUPPORTIVE, "Positive words"
        else:
            c.category, c.reason = NEUTRAL, ""
    return comments


# --------------------------------------------------------------------------
# Claude classifier
# --------------------------------------------------------------------------

SYSTEM_PROMPT = """You protect content creators' mental health by sorting the \
comments left on their social media posts. For each comment pick ONE category:

- supportive: kind, encouraging, appreciative.
- neutral: questions, jokes between fans, off-topic chat, anything harmless.
- criticism: honest, even blunt, feedback about the CONTENT (audio, ideas, \
facts, editing). Disagreement is not bullying - creators want to see this.
- harassment: attacks the PERSON - insults about looks, body, identity, \
intelligence; mockery, slurs, hate, "nobody likes you", telling them to quit. \
Includes sarcasm or coded/misspelled insults that are clearly meant to hurt.
- threat: threats of violence, doxxing or "I know where you live", or \
encouraging self-harm or suicide (including "kys", "unalive yourself").

When unsure between criticism and harassment, ask: is it about the work, or \
about the person? Give a short reason (max 12 words) written for the creator, \
calm and non-graphic. Never repeat slurs in the reason."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "reason": {"type": "string"},
                },
                "required": ["id", "category", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def _claude_batch(client, batch: list[Comment]) -> None:
    payload = [{"id": c.id, "text": c.text} for c in batch]
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=8000,
        # If a safety classifier declines, the API retries on a fallback model.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={
            "effort": "low",  # simple sorting task - keeps it fast and cheap
            "format": {"type": "json_schema", "schema": _SCHEMA},
        },
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": "Classify these comments:\n" + json.dumps(payload, ensure_ascii=False),
        }],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined this batch")
    text = next(b.text for b in response.content if b.type == "text")
    by_id = {r["id"]: r for r in json.loads(text)["results"]}
    for c in batch:
        r = by_id.get(c.id)
        if r:
            c.category, c.reason = r["category"], r["reason"]
        else:  # model skipped one - fall back for that comment only
            classify_offline([c])


def classify_with_claude(comments: list[Comment]) -> list[Comment]:
    import anthropic  # imported lazily so offline mode needs no SDK setup

    client = anthropic.Anthropic()
    for i in range(0, len(comments), BATCH_SIZE):
        batch = comments[i:i + BATCH_SIZE]
        try:
            _claude_batch(client, batch)
        except (anthropic.APIError, RuntimeError, ValueError, StopIteration) as e:
            print(f"[guardian] Claude unavailable for a batch ({e}); using offline filter")
            classify_offline(batch)
    return comments


def ai_enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def classify(comments: list[Comment]) -> tuple[list[Comment], str]:
    """Returns (comments, engine_name)."""
    if ai_enabled():
        return classify_with_claude(comments), "Claude AI"
    return classify_offline(comments), "offline word filter"
