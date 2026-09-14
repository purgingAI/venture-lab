#!/usr/bin/env bash
# LedgerBridge control script — foreground / -d (detached) / stop / status
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT=8806
PIDFILE="$DIR/.ledgerbridge.pid"
LOGFILE="$DIR/ledgerbridge.log"

running() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

case "${1:-}" in
  -d|--detach|start)
    if running; then echo "Already running (pid $(cat "$PIDFILE")) on :$PORT"; exit 0; fi
    nohup python3 "$DIR/server.py" > "$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 1
    echo "LedgerBridge started (pid $(cat "$PIDFILE")) → http://localhost:$PORT  [log: $LOGFILE]"
    ;;
  stop)
    if running; then kill "$(cat "$PIDFILE")" && rm -f "$PIDFILE"; echo "Stopped.";
    else echo "Not running."; rm -f "$PIDFILE"; fi
    ;;
  status)
    if running; then echo "RUNNING (pid $(cat "$PIDFILE")) on http://localhost:$PORT";
    else echo "STOPPED"; fi
    ;;
  ""|fg|foreground)
    echo "LedgerBridge (foreground) → http://localhost:$PORT   (Ctrl-C to stop)"
    exec python3 "$DIR/server.py"
    ;;
  *)
    echo "Usage: ./run.sh [foreground | -d | stop | status]"; exit 1
    ;;
esac
