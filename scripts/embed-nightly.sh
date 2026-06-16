#!/bin/zsh
# Mikail-AI — Phase 2 gece RAG batch (launchd ile her gece çalışır)
# Watermark'tan artımlı gömme. Ollama yereldeyse çalışır; değilse güvenli atlar.
HOME_DIR="$HOME"
RAG="$HOME_DIR/mikail-ai"
PY="$RAG/.venv/bin/python"
LOG="$RAG/logs/embed-nightly.log"
TS=$(date "+%Y-%m-%d %H:%M:%S")

echo "[$TS] embed-nightly başlıyor" >> "$LOG"

# Ollama API yereldeyse devam et; değilse atla (watermark ilerlemez, ertesi gün toparlar)
if ! curl -s --max-time 5 http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "[$TS] UYARI: Ollama API (11434) yanıt vermiyor — bu koşu atlandı." >> "$LOG"
  exit 0
fi

"$PY" "$RAG/rag/embed_daily.py" >> "$LOG" 2>&1
echo "[$TS] embed-nightly bitti (exit $?)" >> "$LOG"
