#!/usr/bin/env python3
"""Rotate a single env var in .env without echoing the value anywhere.

Usage:
    python scripts/rotate_key.py KEY_NAME
    python scripts/rotate_key.py KEY_NAME --file path/to/.env

Prompts twice via getpass (hidden input). Replaces just the matching
line in the env file; preserves order, comments, and other lines.
Confirms by character count only - the value is never printed.

Examples:
    python scripts/rotate_key.py ALPACA_API_KEY
    python scripts/rotate_key.py ALPACA_API_SECRET
    python scripts/rotate_key.py PARRAMACRO_API_KEY
"""
from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("key",
                        help="env var name, e.g. PARRAMACRO_API_KEY")
    parser.add_argument("--file", default=".env",
                        help="path to env file (default: .env)")
    args = parser.parse_args()

    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", args.key):
        print(f"ERROR: {args.key!r} is not a valid env var name", file=sys.stderr)
        return 2

    env_path = Path(args.file)
    if env_path.exists():
        try:
            os.chmod(env_path, 0o600)
        except Exception:
            pass
        lines = env_path.read_text().splitlines()
    else:
        env_path.touch(mode=0o600)
        lines = []

    pat = re.compile(rf"^\s*{re.escape(args.key)}\s*=")
    matches = [i for i, ln in enumerate(lines) if pat.match(ln)]
    if len(matches) > 1:
        print(f"ERROR: {args.key} appears on {len(matches)} lines in "
              f"{env_path}. Clean up duplicates manually first.",
              file=sys.stderr)
        return 3

    print(f"Rotating {args.key} in {env_path}")
    if matches:
        # show the old value's length without revealing it
        existing = lines[matches[0]].split("=", 1)[1]
        print(f"  current: {len(existing)} chars (will be replaced)")
    else:
        print("  current: not present (will be appended)")

    new_value = getpass.getpass(f"  enter new value (hidden): ")
    if not new_value:
        print("ERROR: empty value, aborting", file=sys.stderr)
        return 4
    if "\n" in new_value or "\r" in new_value:
        print("ERROR: value contains a newline. Paste only the key, "
              "no surrounding text.", file=sys.stderr)
        return 5
    if new_value.startswith(("'", '"')) or new_value.endswith(("'", '"')):
        print("ERROR: don't include surrounding quotes; .env values are raw",
              file=sys.stderr)
        return 6

    confirm = getpass.getpass(f"  confirm (hidden): ")
    if confirm != new_value:
        print("ERROR: confirmation did not match, aborting", file=sys.stderr)
        return 7

    new_line = f"{args.key}={new_value}"
    if matches:
        lines[matches[0]] = new_line
    else:
        lines.append(new_line)

    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    env_path.write_text(text)
    os.chmod(env_path, 0o600)

    print(f"OK: {args.key} updated ({len(new_value)} chars). "
          f"File mode set to 0600.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
