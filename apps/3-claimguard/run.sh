#!/usr/bin/env bash
# ClaimGuard runner — foreground / -d (detached) / stop / status
set -euo pipefail
cd "$(dirname "$0")"

PORT=8803
PIDFILE=".claimguard.pid"
LOG="claimguard.log"

is_running(){ [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

case "${1:-}" in
  -d|--detach|start)
    if is_running; then echo "already running (pid $(cat "$PIDFILE")) on :$PORT"; exit 0; fi
    nohup python3 server.py >"$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 1
    echo "ClaimGuard started (pid $(cat "$PIDFILE")) -> http://localhost:$PORT  (log: $LOG)"
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
