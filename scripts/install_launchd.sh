#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LAUNCH_ROOT="$HOME/daily-x-signal"
LABEL="com.wangyaya.daily-x-signal"
AGENT_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
ln -sfn "$ROOT_DIR" "$LAUNCH_ROOT"
LOG_DIR="$LAUNCH_ROOT/state/logs"
mkdir -p "$LOG_DIR"

cat >"$AGENT_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${LAUNCH_ROOT}/scripts/scheduler_tick.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${LAUNCH_ROOT}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>900</integer>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/launchd.stdout.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/launchd.stderr.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>${HOME}/.npm-global/bin:${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>LANG</key>
    <string>en_US.UTF-8</string>
  </dict>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)" "$AGENT_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$AGENT_PATH"
launchctl enable "gui/$(id -u)/${LABEL}"

echo "Installed launchd agent: ${LABEL}"
echo "Plist: ${AGENT_PATH}"
echo "Launch root alias: ${LAUNCH_ROOT}"
