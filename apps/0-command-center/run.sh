#!/usr/bin/env bash
# Command Center launcher — foreground / -d (detached) / stop / status
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT=8811
PIDFILE="$HERE/.command-center.pid"
LOG="$HERE/command-center.log"
PY="$(command -v python3)"

is_running(){ [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

case "${1:-}" in
  -d|--daemon|start)
    if is_running; then echo "Already running (pid $(cat "$PIDFILE")) on :$PORT"; exit 0; fi
    nohup "$PY" "$HERE/server.py" >"$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 1
    echo "Command Center started (pid $(cat "$PIDFILE")) → http://localhost:$PORT"
    echo "logs: $LOG"
    ;;
  stop)
    if is_running; then kill "$(cat "$PIDFILE")" && rm -f "$PIDFILE"; echo "Stopped.";
    else echo "Not running."; rm -f "$PIDFILE"; fi
    ;;
  status)
    if is_running; then echo "RUNNING (pid $(cat "$PIDFILE")) on http://localhost:$PORT";
    else echo "STOPPED"; fi
    ;;
  ""|fg|foreground)
    echo "Command Center (foreground) → http://localhost:$PORT   (Ctrl-C to quit)"
    exec "$PY" "$HERE/server.py"
    ;;
  *)
    echo "usage: ./run.sh [ (no arg)=foreground | -d | stop | status ]"; exit 1;;
esac
