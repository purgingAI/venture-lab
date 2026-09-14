#!/usr/bin/env bash
# CompliAgent launcher.  ./run.sh (fg) · ./run.sh -d (detached) · ./run.sh stop · ./run.sh status
set -uo pipefail
cd "$(dirname "$0")"; PY="$(command -v python3)"; PORT=8808; PIDF="/tmp/compliagent.pid"; LOG="/tmp/compliagent.log"
up(){ curl -s --max-time 3 "http://localhost:$PORT/api/health" >/dev/null 2>&1; }
case "${1:-}" in
  -d) "$PY" server.py >>"$LOG" 2>&1 & echo $! >"$PIDF"; disown||true; sleep 2;
      up && echo "UP → http://localhost:$PORT  (logs:$LOG, stop:./run.sh stop)" || echo "starting… check $LOG";;
  stop) [ -f "$PIDF" ] && kill "$(cat "$PIDF")" 2>/dev/null||true; pkill -f "8-compliagent/server.py" 2>/dev/null||true; rm -f "$PIDF"; echo stopped;;
  status) up && echo "UP → http://localhost:$PORT" || echo DOWN;;
  *) exec "$PY" server.py;;
esac
