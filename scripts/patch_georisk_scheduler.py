#!/usr/bin/env python3
"""Patch a georisk scheduler.py to add a commodities startup warmup.

Idempotent: detects an already-applied patch via a marker comment.
Surgical: inserts directly after the existing _warm_hpi thread.start().
Safe: writes a .bak copy of the original alongside the file.

Usage:
    python scripts/patch_georisk_scheduler.py
    python scripts/patch_georisk_scheduler.py /path/to/scheduler.py

Default target: ~/Desktop/georisk/backend/scheduler.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PATCH_MARKER = "Pre-warm commodities"

PATCH = '''
    # Pre-warm commodities: the monthly cron is too slow for fresh deploys.
    # Daemon thread so boot isn't blocked; first-time fits can take 5-15 min.
    def _warm_commodities():
        try:
            from backend.data_sources import commodity_models, commodities_forecast
            summaries = commodity_models.refit_all()
            fits = sum(1 for s in summaries.values() if not s.get('fit_error'))
            logger.info(f"commodities warmup: {fits}/{len(summaries)} succeeded")
            commodities_forecast._cache.clear()
        except Exception as e:
            logger.error(f"commodities warmup failed: {e}")
    threading.Thread(target=_warm_commodities, daemon=True).start()
'''

# Anchor: line that starts the HPI warmup daemon thread.
ANCHOR_RE = re.compile(
    r"(threading\.Thread\(target=_warm_hpi,\s*daemon=True\)\.start\(\)\s*\n)"
)


def main(argv: list[str]) -> int:
    default_path = Path.home() / "Desktop" / "georisk" / "backend" / "scheduler.py"
    target = Path(argv[1]) if len(argv) > 1 else default_path

    if not target.exists():
        print(f"ERROR: {target} not found", file=sys.stderr)
        print(f"Pass an explicit path: python {argv[0]} /path/to/scheduler.py",
              file=sys.stderr)
        return 2

    src = target.read_text()

    if PATCH_MARKER in src:
        print(f"already patched (found marker '{PATCH_MARKER}' in {target.name}); "
              f"no changes made")
        return 0

    m = ANCHOR_RE.search(src)
    if not m:
        print("ERROR: could not find the HPI warmup anchor in this file.",
              file=sys.stderr)
        print("Expected: threading.Thread(target=_warm_hpi, daemon=True).start()",
              file=sys.stderr)
        print("Has the file been refactored? Apply the patch manually.",
              file=sys.stderr)
        return 3

    new_src = src[:m.end()] + PATCH + src[m.end():]

    backup = target.with_suffix(target.suffix + ".bak")
    backup.write_text(src)
    target.write_text(new_src)

    added = len([line for line in PATCH.splitlines() if line])
    print(f"PATCHED:  {target}")
    print(f"BACKUP:   {backup}")
    print(f"\n{added} lines added. Diff preview:\n")
    for line in PATCH.splitlines():
        print(f"+ {line}")

    print("\nNext steps:")
    georisk_root = target.parent.parent
    print(f"  cd {georisk_root}")
    print(f"  git diff backend/scheduler.py")
    print(f"  git add backend/scheduler.py")
    print(f"  git commit -m 'Add commodities startup warmup'")
    print(f"  git push    # then redeploy parramacro on Render")
    print(f"\nAlso confirm FRED_API_KEY is set in Render's environment "
          f"(needed for the macro_model warmup).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
