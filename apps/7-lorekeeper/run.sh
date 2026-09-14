#!/usr/bin/env bash
# LoreKeeper runner — foreground / -d (background) / stop / status
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT=8807
PIDFILE="$HERE/.lorekeeper.pid"
LOG="$HERE/lorekeeper.log"

start_fg(){ exec python3 "$HERE/server.py"; }

start_bg(){
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "Already running (pid $(cat "$PIDFILE")) on :$PORT"; exit 0
  fi
  nohup python3 "$HERE/server.py" >"$LOG" 2>&1 &
  echo $! > "$PIDFILE"
  sleep 1
  echo "LoreKeeper started (pid $(cat "$PIDFILE")) → http://localhost:$PORT  [log: $LOG]"
}

stop(){
  if [ -f "$PIDFILE" ]; then
    kill "$(cat "$PIDFILE")" 2>/dev/null || true
    rm -f "$PIDFILE"; echo "Stopped."
  else echo "Not running (no pidfile)."; fi
}

status(){
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "RUNNING pid $(cat "$PIDFILE") on http://localhost:$PORT"
  else echo "STOPPED"; fi
}

case "${1:-}" in
  -d|--daemon|background) start_bg ;;
  stop) stop ;;
  status) status ;;
  ""|fg|foreground) start_fg ;;
  *) echo "usage: ./run.sh [ | -d | stop | status ]"; exit 1 ;;
esac
