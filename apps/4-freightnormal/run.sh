#!/usr/bin/env bash
# FreightNormal runner — foreground / -d (detached) / stop / status
set -euo pipefail
cd "$(dirname "$0")"

PORT=8804
PIDFILE=".freightnormal.pid"
LOG="freightnormal.log"

is_running(){ [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

case "${1:-}" in
  -d|--detach|start)
    if is_running; then echo "already running (pid $(cat "$PIDFILE")) on :$PORT"; exit 0; fi
    nohup python3 server.py >"$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    # up-check: poll /api/health for a moment
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      if curl -sf "http://localhost:$PORT/api/health" >/dev/null 2>&1; then break; fi
      sleep 0.3
    done
    echo "FreightNormal started (pid $(cat "$PIDFILE")) -> http://localhost:$PORT  (log: $LOG)"
    ;;
  stop)
    if is_running; then kill "$(cat "$PIDFILE")" && echo "stopped"; else echo "not running"; fi
    rm -f "$PIDFILE"
    ;;
  status)
    if is_running; then echo "running (pid $(cat "$PIDFILE")) on :$PORT"; else echo "stopped"; fi
    ;;
  *)
    # foreground
    exec python3 server.py
    ;;
esac
