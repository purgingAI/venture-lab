#!/usr/bin/env bash
# CloseLoop control script — foreground / -d (daemon) / stop / status
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID="$DIR/.closeloop.pid"
LOG="$DIR/closeloop.log"
PORT=8805

start_fg(){ exec python3 "$DIR/server.py"; }

start_bg(){
  if [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then
    echo "already running (pid $(cat "$PID"))"; exit 0; fi
  nohup python3 "$DIR/server.py" >"$LOG" 2>&1 &
  echo $! >"$PID"
  sleep 1
  echo "CloseLoop started (pid $(cat "$PID")) → http://localhost:$PORT"
}

stop(){
  if [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then
    kill "$(cat "$PID")" && echo "stopped (pid $(cat "$PID"))"
  else echo "not running"; fi
  rm -f "$PID"
}

status(){
  if [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then
    echo "running (pid $(cat "$PID")) on http://localhost:$PORT"
  else echo "stopped"; fi
}

case "${1:-fg}" in
  ""|fg) start_fg ;;
  -d|daemon) start_bg ;;
  stop) stop ;;
  status) status ;;
  *) echo "usage: ./run.sh [fg|-d|stop|status]"; exit 1 ;;
esac
