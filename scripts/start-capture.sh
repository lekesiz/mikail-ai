#!/bin/zsh
# Mikail-AI — capture layer launcher (Phase 1)
# Starts at login via launchd, stops at logout/shutdown.
# Captures screen + OCR + keyboard + clipboard, fully local.
#
# Privacy: relies on FileVault (full-disk encryption) + --encrypt-secrets
# (secrets in OS keychain) + --pause-on-drm-content + localhost-only binding.

export PATH="$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

# Location-independent: logs live next to this repo
SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
LOG_DIR="$PROJECT_DIR/logs"
# 700: logs may contain fragments of highly sensitive capture
mkdir -p -m 700 "$LOG_DIR"
[ -d "$HOME/.screenpipe" ] && chmod 700 "$HOME/.screenpipe" 2>/dev/null

# Resolve the REAL Mach-O binary (not the node shim). macOS TCC permissions
# (Screen Recording / Accessibility / Input Monitoring) attach to this binary —
# launching the node wrapper would silently break capture.
NPM_ROOT="$(npm root -g 2>/dev/null)"
ARCH="$(uname -m)"; [ "$ARCH" = "x86_64" ] && ARCH="x64"
SP_BIN="$NPM_ROOT/screenpipe/node_modules/@screenpipe/cli-darwin-$ARCH/bin/screenpipe"
[ -x "$SP_BIN" ] || SP_BIN="$(ls "$NPM_ROOT"/screenpipe/node_modules/@screenpipe/cli-darwin-*/bin/screenpipe 2>/dev/null | head -1)"

if [ -z "$SP_BIN" ] || [ ! -x "$SP_BIN" ]; then
  echo "ERROR: screenpipe binary not found. Run: npm install -g screenpipe" >&2
  exit 1
fi
if ! file "$SP_BIN" 2>/dev/null | grep -q "Mach-O"; then
  echo "ERROR: resolved screenpipe is not the native Mach-O binary ($SP_BIN); TCC would break." >&2
  exit 1
fi

# OCR languages. Override by putting space/newline-separated names in
# config/languages.conf (e.g. "turkish french english"). Defaults to english.
LANG_CONF="$PROJECT_DIR/config/languages.conf"
if [ -f "$LANG_CONF" ]; then
  LANG_NAMES=( $(grep -vE '^[[:space:]]*#' "$LANG_CONF" 2>/dev/null) )
else
  LANG_NAMES=(english)
fi
[ ${#LANG_NAMES[@]} -eq 0 ] && LANG_NAMES=(english)
LANGS=()
for l in "${LANG_NAMES[@]}"; do LANGS+=(-l "$l"); done

# NOTE: audio is disabled by default. macOS does not reliably grant the
# Microphone permission to a background launchd agent (the Microphone pane
# has no "+" button). To enable meeting transcription, use the screenpipe
# desktop app to grant mic access, then remove --disable-audio and add e.g.
#   --audio-transcription-engine whisper-large-v3-turbo
exec "$SP_BIN" record \
  --use-all-monitors \
  --disable-audio \
  --disable-telemetry \
  "${LANGS[@]}" \
  --pause-on-drm-content \
  --encrypt-secrets \
  --retention-days 30 \
  --retention-mode media \
  --port 3030
