#!/usr/bin/env python3
"""Patch georisk routes.py: case-insensitive commodity lookup.

The /api/v1/commodities/forecasts route was lowercasing the input
before looking it up against the mixed-case TICKERS registry, so
?commodity=Gold (the canonical form per the platform docs) failed
with "Unknown commodity: gold".

This patcher replaces the offending three lines with a case-
insensitive resolver that preserves the canonical mixed-case key
and returns a helpful 404 listing known names on a miss.

Idempotent (marker comment); writes a .bak copy of the original.

Usage:
    python scripts/patch_georisk_commodities_case.py
    python scripts/patch_georisk_commodities_case.py /path/to/routes.py

Default target: ~/Desktop/georisk/backend/api_v1/routes.py
"""
from __future__ import annotations

import sys
from pathlib import Path

PATCH_MARKER = "# case-insensitive match -> canonical mixed-case TICKERS key"

OLD = """    name = (request.args.get('commodity') or '').strip().lower()
    if not name:
        return jsonify({'error': 'missing_commodity'}), 400"""

NEW = """    raw = (request.args.get('commodity') or '').strip()
    if not raw:
        return jsonify({'error': 'missing_commodity'}), 400
    # case-insensitive match -> canonical mixed-case TICKERS key
    from backend.data_sources.commodity_models import TICKERS
    name = next((k for k in TICKERS if k.lower() == raw.lower()), None)
    if name is None:
        return jsonify({
            'error': 'unknown_commodity',
            'detail': f'Unknown commodity: {raw!r}',
            'known': sorted(TICKERS.keys()),
        }), 404"""


def main(argv: list[str]) -> int:
    default = Path.home() / "Desktop" / "georisk" / "backend" / "api_v1" / "routes.py"
    target = Path(argv[1]) if len(argv) > 1 else default

    if not target.exists():
        print(f"ERROR: {target} not found", file=sys.stderr)
        return 2

    src = target.read_text()

    if PATCH_MARKER in src:
        print(f"already patched ({target.name}); no changes")
        return 0

    if OLD not in src:
        print(f"ERROR: original code block not found verbatim in {target}.",
              file=sys.stderr)
        print("Expected:", file=sys.stderr)
        print(OLD, file=sys.stderr)
        print("\nThe file may have been refactored. Apply manually.",
              file=sys.stderr)
        return 3

    new_src = src.replace(OLD, NEW)

    backup = Path(str(target) + ".bak")
    backup.write_text(src)
    target.write_text(new_src)

    print(f"PATCHED:  {target}")
    print(f"BACKUP:   {backup}")
    print()
    print("Diff:")
    for line in OLD.splitlines():
        print(f"- {line}")
    for line in NEW.splitlines():
        print(f"+ {line}")
    print()

    georisk_root = target.parent.parent.parent
    print("Next:")
    print(f"  cd {georisk_root}")
    print(f"  git diff backend/api_v1/routes.py")
    print("  # apply any TICKERS import noted above")
    print("  git add backend/api_v1/routes.py")
    print("  git commit -m 'Fix commodity case-sensitivity in /forecasts route'")
    print("  git push   # then redeploy parramacro on Render")
    print()
    print("Verify after deploy:")
    print("  source .env")
    print("  curl -sH \"Authorization: Bearer $PARRAMACRO_API_KEY\" \\")
    print("    \"https://www.parramacro.com/api/v1/commodities/forecasts?commodity=Gold\" | "
          "python -m json.tool | head -20")
    print()
    print("  # Should return real fan data instead of 'Unknown commodity'.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
