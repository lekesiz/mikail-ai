#!/bin/zsh
# Mikail-AI installer — sets up the local capture layer (Phase 1).
# Idempotent: safe to re-run. Works wherever you cloned this repo.
set -u

PROJECT_DIR="${0:A:h}"
LABEL="com.mikail.ai.capture"
UID_NUM=$(id -u)
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"
START_SCRIPT="$PROJECT_DIR/scripts/start-capture.sh"
LOG_OUT="$PROJECT_DIR/logs/capture.out.log"
LOG_ERR="$PROJECT_DIR/logs/capture.err.log"
SP_BIN_PATH=""

say()  { print -P "%F{cyan}==>%f $*"; }
ok()   { print -P "  %F{green}✓%f $*"; }
warn() { print -P "  %F{yellow}!%f $*"; }
die()  { print -P "%F{red}ERROR:%f $*"; exit 1; }

# --- 0. Platform check ---
say "Checking platform"
[ "$(uname)" = "Darwin" ] || die "This installer is macOS-only."
ok "macOS $(sw_vers -productVersion) ($(uname -m))"

# --- 1. Dependencies ---
say "Checking dependencies"
command -v node >/dev/null 2>&1 || die "Node.js not found. Install from https://nodejs.org (or: brew install node)"
ok "node $(node --version)"
command -v npm >/dev/null 2>&1 || die "npm not found."
ok "npm $(npm --version)"

if ! command -v ffmpeg >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    say "Installing ffmpeg via Homebrew"; brew install ffmpeg || die "ffmpeg install failed"
  else
    die "ffmpeg not found and Homebrew unavailable. Install ffmpeg, then re-run."
  fi
fi
ok "ffmpeg $(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}')"

# --- 2. Install screenpipe (capture engine) ---
if ! npm ls -g screenpipe >/dev/null 2>&1; then
  say "Installing screenpipe (npm global)"; npm install -g screenpipe || die "screenpipe install failed"
fi
NPM_ROOT="$(npm root -g 2>/dev/null)"
# Architecture-explicit: arm64 vs x64 — avoids picking the wrong arch if both dirs exist
ARCH="$(uname -m)"; [ "$ARCH" = "x86_64" ] && ARCH="x64"
SP_BIN_PATH="$NPM_ROOT/screenpipe/node_modules/@screenpipe/cli-darwin-$ARCH/bin/screenpipe"
[ -x "$SP_BIN_PATH" ] || SP_BIN_PATH="$(ls "$NPM_ROOT"/screenpipe/node_modules/@screenpipe/cli-darwin-*/bin/screenpipe 2>/dev/null | head -1)"
[ -n "$SP_BIN_PATH" ] || die "screenpipe binary not resolved after install."
# Must be the native Mach-O binary, NOT the node shim — macOS TCC permissions attach to the
# Mach-O binary; granting them to the shim (node) would silently break screen/keyboard capture.
file "$SP_BIN_PATH" 2>/dev/null | grep -q "Mach-O" \
  || die "Resolved screenpipe is not the native Mach-O binary ($SP_BIN_PATH). TCC permissions would break."
ok "screenpipe: $("$SP_BIN_PATH" --version 2>/dev/null | head -1)"

# --- 3. Security advisory ---
say "Checking disk encryption"
if fdesetup status 2>/dev/null | grep -q "FileVault is On"; then
  ok "FileVault is ON (captured data encrypted at rest)"
else
  warn "FileVault is OFF. Captured screen/keyboard data would be unencrypted on disk."
  warn "Strongly recommended: System Settings > Privacy & Security > FileVault > Turn On."
fi

# --- 4. Directories + perms ---
say "Preparing directories"
# 700: these hold logs/secrets derived from highly sensitive capture — keep out of group/other read
mkdir -p -m 700 "$PROJECT_DIR/logs" "$PROJECT_DIR/config"
chmod 700 "$PROJECT_DIR/logs" "$PROJECT_DIR/config" 2>/dev/null
[ -d "$HOME/.screenpipe" ] && chmod 700 "$HOME/.screenpipe" 2>/dev/null
chmod +x "$PROJECT_DIR/scripts/"*.sh
ok "logs/ and config/ ready (700)"

# --- 5. Generate + load LaunchAgent ---
say "Installing LaunchAgent (auto start at login / stop at logout)"
mkdir -p "$HOME/Library/LaunchAgents"
# XML-escape paths before injecting into the plist (a path with & < > would break XML).
# Use zsh literal substitution (not sed) so '&' in replacements isn't reinterpreted.
xmlesc() { print -r -- "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }
tpl="$(<"$PROJECT_DIR/launchd/$LABEL.plist.template")"
tpl="${tpl//__START_SCRIPT__/$(xmlesc "$START_SCRIPT")}"
tpl="${tpl//__LOG_OUT__/$(xmlesc "$LOG_OUT")}"
tpl="${tpl//__LOG_ERR__/$(xmlesc "$LOG_ERR")}"
print -r -- "$tpl" > "$PLIST_DST"
plutil -lint "$PLIST_DST" >/dev/null || die "Generated plist is invalid."
launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null
launchctl bootstrap "gui/$UID_NUM" "$PLIST_DST" 2>/dev/null && ok "agent loaded" || warn "agent load reported an issue (often fine; check status.sh)"

# --- 6. Permissions guidance ---
say "macOS permissions (one-time, manual — required by Apple's security model)"
print -P "  The %Bscreenpipe%b binary needs these toggles ON in System Settings > Privacy & Security:"
print -P "    1. %BScreen Recording%b   2. %BAccessibility%b   3. %BInput Monitoring%b"
print -P "  Binary path (copied to clipboard if pbcopy available):"
print -P "    %F{magenta}$SP_BIN_PATH%f"
command -v pbcopy >/dev/null 2>&1 && print -n "$SP_BIN_PATH" | pbcopy
print ""
print -P "  Opening the Screen Recording pane now..."
open "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture" 2>/dev/null
print -P "  See %Bdocs/PERMISSIONS.md%b for the full step-by-step."

# --- 7. Done ---
say "Done. After granting permissions, restart the agent and verify:"
print "    launchctl bootout   gui/$UID_NUM \"$PLIST_DST\""
print "    launchctl bootstrap gui/$UID_NUM \"$PLIST_DST\""
print "    $PROJECT_DIR/scripts/status.sh"
