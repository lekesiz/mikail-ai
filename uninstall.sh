#!/bin/zsh
# Mikail-AI uninstaller — stops and removes the capture agent.
# Does NOT delete captured data (~/.screenpipe) or screenpipe itself.
set -u
LABEL="com.mikail.ai.capture"
UID_NUM=$(id -u)
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"

echo "==> Stopping agent"
launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null && echo "  ✓ agent stopped" || echo "  ! agent not running"

echo "==> Removing LaunchAgent plist"
rm -f "$PLIST_DST" && echo "  ✓ removed $PLIST_DST"

echo ""
echo "Captured data is kept at ~/.screenpipe (delete manually if desired)."
echo "Remove the capture engine with: npm uninstall -g screenpipe"
