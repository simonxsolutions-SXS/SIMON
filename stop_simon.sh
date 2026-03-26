#!/bin/bash
# ============================================================
# S.I.M.O.N. Stop Script — stop_simon.sh
# Simon-X Solutions | [OWNER_NAME]
# Usage: ./stop_simon.sh
# ============================================================

echo ""
echo "  [ SIMON ] Shutting down..."

pkill -f "jarvis.py" 2>/dev/null
pkill -9 afplay 2>/dev/null
pkill -9 say 2>/dev/null
lsof -ti tcp:8765 | xargs kill -9 2>/dev/null

sleep 1

# Confirm
if pgrep -f "jarvis.py" >/dev/null 2>&1; then
    echo "  [ WARN ] jarvis.py still running — forcing kill..."
    pkill -9 -f "jarvis.py" 2>/dev/null
    sleep 1
fi

echo "  [ SIMON ] Offline."
echo ""
