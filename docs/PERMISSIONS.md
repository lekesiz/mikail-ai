# macOS Permissions — Step by Step

macOS protects screen, keyboard and microphone access with TCC. These permissions
**cannot be granted by a script** — Apple requires you to toggle them manually, once.
They attach to the **screenpipe binary**, not to your terminal.

Find the binary path (also printed by `install.sh`, and copied to your clipboard):

```bash
ls "$(npm root -g)"/screenpipe/node_modules/@screenpipe/cli-darwin-*/bin/screenpipe
```

## Required (capture works with these)

Open **System Settings → Privacy & Security**, then for each list below, turn the
**screenpipe** entry ON. If it isn't listed, click `+`, press `Cmd+Shift+G`, paste the
binary path, select it, and enable.

1. **Screen Recording** — screen frames + OCR
   `x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture`
2. **Accessibility** — UI events (required even for clipboard-only mode)
   `x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility`
3. **Input Monitoring** — keyboard capture
   `x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent`

After enabling, restart the agent:

```bash
UID_NUM=$(id -u)
PLIST="$HOME/Library/LaunchAgents/com.mikail.ai.capture.plist"
launchctl bootout   gui/$UID_NUM "$PLIST"
launchctl bootstrap gui/$UID_NUM "$PLIST"
```

Verify with `scripts/status.sh` — you want `port 3030: listening`, monitors detected,
and `ui_recorder mode=full`.

## Optional: Audio / meeting transcription

The **Microphone** pane has no `+` button — macOS only lists apps that *requested* mic
access, and a background launchd agent can't reliably trigger that prompt. So audio is
**off by default** (`--disable-audio` in `scripts/start-capture.sh`).

To enable it, install the **screenpipe desktop app** (it triggers the mic prompt
properly), grant Microphone, then edit `scripts/start-capture.sh`: remove
`--disable-audio` and add `--audio-transcription-engine whisper-large-v3-turbo`.
