"""Send the weekly kindness report from a cloned repo (same as `guardian-weekly-report`)."""
import sys

from guardian.cli import weekly_report_main

if __name__ == "__main__":
    sys.exit(weekly_report_main())
