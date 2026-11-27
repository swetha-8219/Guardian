"""Run always-on protection from a cloned repo (same as `guardian-always-on`)."""
import sys

from guardian.cli import always_on_main

if __name__ == "__main__":
    sys.exit(always_on_main())
