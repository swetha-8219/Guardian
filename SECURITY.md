# Security

Guardian handles access to creators' social media accounts, so security reports matter.

**Please don't open a public issue for security problems.** Instead, use GitHub's
[private vulnerability reporting](../../security/advisories/new) for this repository.

How Guardian keeps your accounts safe:
- Logins (`.tokens.json`) and keys (`.env`) stay on your own computer or server and are
  git-ignored. Guardian has no server of its own.
- Guardian only asks platforms for the permissions it needs to read and hide comments.
- Comments are only sent to Anthropic's API if you add an Anthropic key.
- Nothing is ever deleted: every hide can be undone.
