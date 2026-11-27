# Contributing to Guardian

Thanks for helping creators read the love and skip the hate. 💚 All kinds of help are
welcome: code, better word lists, translations, docs, and reports of comments Guardian
got wrong.

## Get set up (2 minutes)

```bash
git clone https://github.com/swetha-8219/Guardian
cd Guardian
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest            # all tests should pass
guardian          # opens the app at http://localhost:5000
```

No API keys are needed to develop: everything runs with the offline word filter, and the
tests mock every platform.

## Good first issues

These are self-contained and a great way to start. Look for the
[`good first issue`](../../issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) label.

- **Word lists in more languages.** `guardian/wordlists.json` is English-only. Spanish,
  Hindi, Portuguese and others would help a lot of creators.
- **Twitch and Bluesky support.** Add a fetcher in `guardian/platforms.py` and a watcher in
  `guardian/watcher.py`.
- **Firefox version of the extension.** `extension/` is Manifest V3 for Chrome-based browsers.
- **Dark-mode screenshots** for the README and playground.
- **More sample comments** in the playground (`docs/index.html`) that show tricky cases.

## Ground rules

- **Never show creators the hate by default.** New features should blur, summarise or count
  hurtful comments, not display them.
- **Never delete comments.** Guardian hides, so every action can be undone.
- **No secrets in code or issues.** Keys live in `.env`, which is git-ignored.
- **Tests for new behaviour.** Mock network calls the way `tests/` already does.
- If you change `guardian/wordlists.json`, sync the copies (a test checks):
  ```bash
  python -c "import json;d=json.load(open('guardian/wordlists.json'));open('extension/wordlists.js','w').write('// Generated copy of guardian/wordlists.json - keep in sync (a test checks).\nglobalThis.GUARDIAN_WORDLISTS = '+json.dumps(d,ensure_ascii=False,indent=2)+';\n')"
  cp extension/wordlists.js extension/filter.js docs/
  ```

## Reporting wrongly hidden or missed comments

Use the **"Wrongly hidden or missed comment"** issue template. These reports are the
fastest way to make Guardian more accurate.
