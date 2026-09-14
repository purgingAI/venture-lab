#!/usr/bin/env bash
# TradePilot launcher.  Usage:
#   ./run.sh          run in foreground
#   ./run.sh -d       run detached (logs -> /tmp/tradepilot.log, pid -> /tmp/tradepilot.pid)
#   ./run.sh stop     stop the detached server
#   ./run.sh status   show status
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT=8801
PIDFILE=/tmp/tradepilot.pid
LOGFILE=/tmp/tradepilot.log
PY=python3

case "${1:-}" in
  -d|--detached)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "already running (pid $(cat "$PIDFILE")) on :$PORT"; exit 0
    fi
    nohup "$PY" "$DIR/server.py" >"$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 1
    echo "TradePilot detached (pid $(cat "$PIDFILE")) -> http://localhost:$PORT"
    echo "logs: $LOGFILE"
    ;;
  stop)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      kill "$(cat "$PIDFILE")" && rm -f "$PIDFILE"
      echo "stopped."
    else
      echo "not running."; rm -f "$PIDFILE" 2>/dev/null || true
    fi
    ;;
  status)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "running (pid $(cat "$PIDFILE")) on http://localhost:$PORT"
    else
      echo "not running."
    fi
    ;;
  *)
    echo "TradePilot -> http://localhost:$PORT  (Ctrl-C to stop)"
    exec "$PY" "$DIR/server.py"
    ;;
esac
