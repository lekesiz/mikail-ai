#!/bin/zsh
# Mikail-AI — günlük sağlık anlık görüntüsü (launchd ile 09:13'te çalışır)
# health-history.jsonl'a tek satır JSON ekler. Tamamen yerel, salt-okunur sorgular.
# NOT: `set -e` BİLEREK YOK — DB kilitli/hatalıyken bile (tam o an izleme en kritik)
# kayıt yazılmalı. Tüm sorgular savunmacı (2>/dev/null + ${VAR:-0}); boş AGE => status=degraded.
HOME_DIR="$HOME"
DB="$HOME_DIR/.screenpipe/db.sqlite"
DATA="$HOME_DIR/.screenpipe/data"
HIST="$HOME_DIR/mikail-ai/logs/health-history.jsonl"
DATE=$(date +%Y-%m-%d)

q() { sqlite3 -readonly -cmd ".timeout 4000" "$DB" "$1" 2>/dev/null; }

# Servis durumu
if launchctl print "gui/$(id -u)/com.mikail.ai.capture" >/dev/null 2>&1; then SVC="up"; else SVC="down"; fi

# Metrikler
AGE=$(q "SELECT CAST((julianday('now')-julianday(max(timestamp)))*86400 AS INT) FROM frames;")
FR24=$(q "SELECT count(*) FROM frames WHERE timestamp > datetime('now','-1 day');")
FRT=$(q "SELECT count(*) FROM frames;")
OCR24=$(q "SELECT coalesce(sum(length(text)),0) FROM ocr_text o JOIN frames f ON f.id=o.frame_id WHERE f.timestamp > datetime('now','-1 day');")
UITX=$(q "SELECT count(*) FROM ui_events WHERE event_type='text' AND timestamp > datetime('now','-1 day');")
CLIP=$(q "SELECT count(*) FROM ui_events WHERE event_type='clipboard' AND timestamp > datetime('now','-1 day');")
DBMB=$(( $(stat -f%z "$DB" 2>/dev/null || echo 0) / 1048576 ))
DATAMB=$(du -sm "$DATA" 2>/dev/null | awk '{print $1}')
FREEGB=$(df -g / | awk 'NR==2{print $4}')

# RAG index durumu (varsa)
RAGDB="$HOME_DIR/mikail-ai/rag/index.db"
if [ -f "$RAGDB" ]; then
  RAGCH=$(sqlite3 -readonly "$RAGDB" "SELECT count(*) FROM chunks;" 2>/dev/null || echo 0)
  RAGWM=$(python3 -c "import json;print(json.load(open('$HOME_DIR/mikail-ai/rag/state.json')).get('last_frame_id',0))" 2>/dev/null || echo 0)
else
  RAGCH=0; RAGWM=0
fi

# Durum değerlendirmesi
STATUS="ok"
[ "$SVC" = "down" ] && STATUS="degraded"
[ -z "$AGE" ] && STATUS="degraded"
[ -n "$AGE" ] && [ "$AGE" -gt 600 ] 2>/dev/null && STATUS="stale"

ACT="otomatik gunluk kayit; capture=$SVC; frame_age=${AGE:-NA}s; RAG index chunk=$RAGCH (watermark frame=$RAGWM, gecikme=$(( ${FRT:-0} - ${RAGWM:-0} )) frame)"

python3 - "$DATE" "$STATUS" "${AGE:-0}" "${DBMB:-0}" "${DATAMB:-0}" "${FREEGB:-0}" \
  "${FR24:-0}" "${FRT:-0}" "${OCR24:-0}" "${UITX:-0}" "${CLIP:-0}" "${RAGCH:-0}" "${RAGWM:-0}" "$ACT" "$HIST" <<'PY'
import json, sys
(_, date, status, age, dbmb, datamb, freegb, fr24, frt, ocr24, uitx, clip, ragch, ragwm, act, hist) = sys.argv
rec = {"date": date, "status": status, "frame_age_s": int(age), "db_mb": int(dbmb),
       "data_mb": int(datamb), "disk_free_gb": int(freegb), "frames_24h": int(fr24),
       "frames_total": int(frt), "text_chars_24h": int(ocr24), "ui_text_24h": int(uitx),
       "clipboard_24h": int(clip), "rag_chunks": int(ragch), "rag_watermark": int(ragwm),
       "actions": act}
with open(hist, "a") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
print("✅ saglik kaydi eklendi:", date, status, "frame_age="+age+"s", "rag_chunks="+ragch)
PY
