#!/bin/zsh
# Mikail-AI — quick health/status check
UID_NUM=$(id -u)
LABEL="com.mikail.ai.capture"

echo "=== launchd agent ==="
launchctl print "gui/$UID_NUM/$LABEL" 2>/dev/null | grep -E "state =|pid =|last exit" | head -3 \
  || echo "agent not loaded"

echo "=== port 3030 ==="
if lsof -nP -iTCP:3030 -sTCP:LISTEN >/dev/null 2>&1; then echo "listening ✅"; else echo "not listening ❌"; fi

echo "=== /health ==="
curl -s --max-time 4 http://localhost:3030/health 2>/dev/null | python3 -c "import sys,json
try:
  d=json.load(sys.stdin)
  print('status      :', d.get('status'))
  print('monitors    :', d.get('monitors'))
  ui=d.get('ui_recorder',{})
  print('ui_recorder :', 'mode='+str(ui.get('mode')), 'clipboard='+str(ui.get('clipboard_capture')), 'input_tap='+str(ui.get('input_tap_running')))
except Exception:
  print('(no response — service may still be starting or blocked on permissions)')"

DB="$HOME/.screenpipe/db.sqlite"
if [ -f "$DB" ]; then
  echo "=== captured so far ==="
  echo "frames : $(sqlite3 "$DB" 'SELECT count(*) FROM frames;' 2>/dev/null)"
  echo "ocr    : $(sqlite3 "$DB" 'SELECT count(*) FROM ocr_text;' 2>/dev/null)"
  echo "ui_evt : $(sqlite3 "$DB" 'SELECT count(*) FROM ui_events;' 2>/dev/null)"
fi
