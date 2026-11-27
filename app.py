"""Start Guardian from a cloned repo: python app.py  (same as the `guardian` command)."""
from guardian.app import app, main  # noqa: F401  (app is imported by tests)

if __name__ == "__main__":
    main()
