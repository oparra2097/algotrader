#!/usr/bin/env bash
# Set up a macOS launchd agent that ticks all bots on a schedule.
#
# Default cadence: every 4 hours, runs at load.
# Override with: TICK_INTERVAL_SECONDS=3600 ./scripts/setup_launchd.sh
#
# What this does:
#   - Writes ~/Library/LaunchAgents/com.parramacro.algotrader.plist
#   - (Re)loads it via launchctl
#   - Logs go to results/launchd.log and results/launchd.err
#
# To stop:    launchctl unload ~/Library/LaunchAgents/com.parramacro.algotrader.plist
# To remove:  launchctl unload ... && rm ~/Library/LaunchAgents/com.parramacro.algotrader.plist
# To check:   launchctl list | grep parramacro

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST_NAME="com.parramacro.algotrader"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"
INTERVAL="${TICK_INTERVAL_SECONDS:-14400}"   # default 4h

PYTHON_BIN="${REPO_DIR}/.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "ERROR: $PYTHON_BIN not found." >&2
    echo "Activate your venv and ensure it's at .venv/ inside the repo." >&2
    exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "${REPO_DIR}/results"

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_BIN}</string>
        <string>${REPO_DIR}/scripts/tick_all_bots.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${REPO_DIR}</string>
    <key>StartInterval</key>
    <integer>${INTERVAL}</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${REPO_DIR}/results/launchd.log</string>
    <key>StandardErrorPath</key>
    <string>${REPO_DIR}/results/launchd.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
EOF

# Reload (unload first to pick up changes).
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

echo "loaded:        $PLIST_PATH"
echo "interval:      ${INTERVAL}s ($((INTERVAL/3600))h)"
echo "stdout log:    ${REPO_DIR}/results/launchd.log"
echo "stderr log:    ${REPO_DIR}/results/launchd.err"
echo
echo "First run is firing now (RunAtLoad=true)."
echo
echo "Status:        launchctl list | grep parramacro"
echo "Disable:       launchctl unload $PLIST_PATH"
echo "Re-enable:     launchctl load $PLIST_PATH"
echo "Remove:        launchctl unload $PLIST_PATH && rm $PLIST_PATH"
