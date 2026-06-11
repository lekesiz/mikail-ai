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
mkdir -p "$LOG_DIR"

# Resolve the REAL Mach-O binary (not the node shim).
# macOS TCC permissions (Screen Recording / Accessibility / Input Monitoring)
# attach to this binary — launching the node wrapper would break them.
NPM_ROOT="$(npm root -g 2>/dev/null)"
SP_BIN="$(ls "$NPM_ROOT"/screenpipe/node_modules/@screenpipe/cli-darwin-*/bin/screenpipe 2>/dev/null | head -1)"
[ -z "$SP_BIN" ] && SP_BIN="$(command -v screenpipe)"

if [ -z "$SP_BIN" ] || [ ! -x "$SP_BIN" ]; then
  echo "ERROR: screenpipe binary not found. Run: npm install -g screenpipe" >&2
  exit 1
fi

# OCR/transcription languages. Add more with extra -l flags, e.g. -l turkish -l french
LANGS=(-l english)

# NOTE: audio is disabled by default. macOS does not reliably grant the
# Microphone permission to a background launchd agent (the Microphone pane
# has no "+" button). To enable meeting transcription, use the screenpipe
# desktop app to grant mic access, then remove --disable-audio and add e.g.
#   --audio-transcription-engine whisper-large-v3-turbo
exec "$SP_BIN" record \
  --use-all-monitors \
  --disable-audio \
  $LANGS \
  --pause-on-drm-content \
  --encrypt-secrets \
  --retention-days 30 \
  --retention-mode media \
  --port 3030
