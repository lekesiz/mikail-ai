# Mikail-AI — Local Personal AI Clone

> Bilgisayarındaki tüm aktiviteyi **yerel** olarak izleyen, üzerinden öğrenen ve zamanla
> senin tarzında/bilginle cevap veren kişisel bir AI. İnternete sıfır veri gider.
>
> A personal AI that watches your computer activity **locally**, learns from it, and over
> time answers in your style with your knowledge. Zero data leaves your machine.

Built on [screenpipe](https://github.com/screenpipe/screenpipe) (capture) +
[MLX](https://github.com/ml-explore/mlx) LoRA (fine-tuning). Designed for Apple Silicon.

---

## ⚠️ Privacy / Gizlilik

This records **everything on screen, plus keyboard and clipboard** — including passwords,
API keys and personal data. It is meant to run **only on your own machine**.

- All data stays local in `~/.screenpipe/` (SQLite). Nothing is uploaded.
- The API binds to `127.0.0.1` only.
- **Enable FileVault** (full-disk encryption) before using — the installer checks this.
- Secrets are stored in the OS keychain (`--encrypt-secrets`). Note: captured OCR/key/clipboard
  text itself is stored in plain text in the local DB — full-disk encryption is the protection.
- Telemetry is **off** (`--disable-telemetry`) — no error reports leave the machine.
- Capture auto-pauses on DRM apps / banking streams (`--pause-on-drm-content`).
- macOS re-asks for Screen Recording approval roughly every 90 days — if the prompt appears,
  click **"Continue to Allow"**, otherwise capture silently stops.
- Recommended: `chmod 700 ~/.screenpipe` and `tmutil addexclusion ~/.screenpipe`
  (keep keystroke data out of group access and Time Machine backups).
- This repo contains **no captured data and no tokens** — those are git-ignored.

---

## Architecture / Mimari

| Layer | What | Status |
|------|------|--------|
| 1. Capture | screenpipe: screen + OCR + keyboard + clipboard → local SQLite | ✅ Phase 1 |
| 2. RAG / Memory | embed activity into a local vector DB for instant recall | ✅ Phase 2 |
| 3. Fine-tuning | MLX LoRA on your own data to mimic your style | 🔜 Phase 3 |
| 4. Interface | a local assistant that talks like you | 🔜 Phase 4 |

The capture engine exposes a REST API at `http://localhost:3030` and a SQLite DB at
`~/.screenpipe/db.sqlite` — both consumed by the upper layers.

### Phase 2 — RAG / Memory (local)

Incremental ETL embeds captured activity into a **separate** local vector DB
(`rag/index.db`, sqlite-vec + FTS5) — the capture DB is opened **read-only** and never written.

- **Two record kinds**: `screen` (frames → accessibility_text/OCR, "what I saw") and
  `typed` (ui_events keyboard/clipboard text, "what I wrote"). Typed events get app/window
  context from their `frame_id` join, with a nearest-timestamp frame fallback.
- **Embeddings**: `bge-m3` via Ollama (1024-dim, multilingual TR/FR/EN). Fully local.
- **Dedup**: exact `content_hash` + consecutive identical text are skipped; consecutive
  same app+window frames grouped into ~512–700-token session blocks.
- **Incremental**: `rag/state.json` holds two watermarks (`last_frame_id`, `last_ui_event_id`).
- **Search**: hybrid FTS5-BM25 + vector, merged with RRF.

```bash
.venv/bin/python rag/embed_daily.py            # incremental embed (both kinds)
.venv/bin/python rag/search.py "sorgu" -k 8    # hybrid search
.venv/bin/python rag/search.py "sorgu" --kind typed   # only "what I wrote"
```

A nightly LaunchAgent (`com.mikail.ai.embed`, 03:30) runs the incremental embed from the
watermarks. Index files (`rag/index.db*`, `rag/state.json`) are git-ignored (personal data);
only the generic ETL/search code is shareable.

---

## Quick start / Hızlı kurulum

Requirements: macOS (Apple Silicon), Node.js, Homebrew. FileVault recommended.

```bash
git clone https://github.com/lekesiz/mikail-ai.git
cd mikail-ai
./install.sh
```

The installer will: install dependencies (ffmpeg, screenpipe), set up the LaunchAgent
(auto start at login / stop at logout), and open the first macOS permission pane.

Then grant 3 permissions to the **screenpipe** binary — see
[`docs/PERMISSIONS.md`](docs/PERMISSIONS.md) — and verify:

```bash
./scripts/status.sh
```

You want: `port 3030: listening`, monitors detected, `ui_recorder mode=full`.

---

## Lifecycle / Yaşam döngüsü

A user LaunchAgent (`com.mikail.ai.capture`) runs in your GUI session:

- **Starts** automatically when you log in (`RunAtLoad`).
- **Stops** automatically at logout / shutdown.
- **Restarts** within the session if it crashes (`KeepAlive`).

```bash
UID_NUM=$(id -u)
PLIST="$HOME/Library/LaunchAgents/com.mikail.ai.capture.plist"
launchctl bootout   gui/$UID_NUM "$PLIST"   # stop
launchctl bootstrap gui/$UID_NUM "$PLIST"   # start
tail -f logs/capture.out.log                # logs
./uninstall.sh                              # remove agent (keeps data)
```

---

## Configuration / Yapılandırma

Edit `scripts/start-capture.sh`:

- **Languages**: put space-separated names in `config/languages.conf`
  (e.g. `turkish french english`); defaults to english. (This file is git-ignored.)
- **Retention**: `--retention-days 30` (media auto-deleted; OCR text kept forever).
- **Audio**: off by default. To enable meeting transcription, see
  [`docs/PERMISSIONS.md`](docs/PERMISSIONS.md#optional-audio--meeting-transcription).

---

## Roadmap

- [x] **Phase 1** — Capture layer + auto start/stop + permissions
- [x] **Phase 2** — RAG memory (local vector DB over captured activity)
- [ ] **Phase 3** — MLX LoRA fine-tuning pipeline from your own data
- [ ] **Phase 4** — Local "clone" chat assistant

## License

MIT © Mikail Lekesiz. Built on screenpipe (source-available; free for personal use) and MLX (MIT).
