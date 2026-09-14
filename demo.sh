#!/usr/bin/env bash
# Venture Lab portfolio launcher.
#   ./demo.sh up      start all 10 apps (detached) and print their URLs
#   ./demo.sh down    stop them all
#   ./demo.sh status  which are up
set -uo pipefail
cd "$(dirname "$0")/apps"
APPS=(1-tradepilot:8801 2-emberhealth:8802 3-claimguard:8803 4-freightnormal:8804 5-closeloop:8805 \
      6-ledgerbridge:8806 7-lorekeeper:8807 8-compliagent:8808 9-focusforge:8809 10-mesa:8810)
NAMES=(TradePilot EmberHealth ClaimGuard FreightNormal CloseLoop LedgerBridge LoreKeeper CompliAgent FocusForge Mesa)
up1(){ curl -s --max-time 2 "http://localhost:$1/" >/dev/null 2>&1; }
case "${1:-status}" in
  up)
    i=0; for entry in "${APPS[@]}"; do d=${entry%%:*}; p=${entry##*:}
      ( cd "$d" && python3 server.py >"/tmp/vl_$d.log" 2>&1 & echo $! >"/tmp/vl_$d.pid" ); disown 2>/dev/null||true
      i=$((i+1)); done
    sleep 3; echo "── Venture Lab — 10 apps ──"
    i=0; for entry in "${APPS[@]}"; do p=${entry##*:}
      up1 "$p" && s="●" || s="○"; printf "  %s %-14s http://localhost:%s\n" "$s" "${NAMES[$i]}" "$p"; i=$((i+1)); done
    ( cd .. && python3 -m http.server 8800 >/tmp/vl_hub.log 2>&1 & echo $! >/tmp/vl_hub.pid ); disown 2>/dev/null||true
    ( cd 0-command-center && python3 server.py >/tmp/vl_cc.log 2>&1 & echo $! >/tmp/vl_cc.pid ); disown 2>/dev/null||true
    sleep 1
    echo "── COMMAND CENTER (live) → http://localhost:8811 ──"
    echo "── static hub          → http://localhost:8800/portfolio.html ──"
    echo "stop all: ./demo.sh down";;
  down)
    for entry in "${APPS[@]}"; do d=${entry%%:*}
      [ -f "/tmp/vl_$d.pid" ] && kill "$(cat /tmp/vl_$d.pid)" 2>/dev/null||true; rm -f "/tmp/vl_$d.pid"
      pkill -f "$d/server.py" 2>/dev/null||true; done
    [ -f /tmp/vl_hub.pid ] && kill "$(cat /tmp/vl_hub.pid)" 2>/dev/null||true; rm -f /tmp/vl_hub.pid
    [ -f /tmp/vl_cc.pid ] && kill "$(cat /tmp/vl_cc.pid)" 2>/dev/null||true; rm -f /tmp/vl_cc.pid
    pkill -f "http.server 8800" 2>/dev/null||true; pkill -f "0-command-center/server.py" 2>/dev/null||true
    echo "all stopped";;
  status)
    i=0; for entry in "${APPS[@]}"; do p=${entry##*:}
      up1 "$p" && s="● UP  " || s="○ down"; printf "  %s %-14s :%s\n" "$s" "${NAMES[$i]}" "$p"; i=$((i+1)); done;;
esac
