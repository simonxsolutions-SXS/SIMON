#!/bin/bash
# EMERGENCY CLEAN RESTART — kills everything, verifies syntax, starts fresh
PYTHON="/opt/homebrew/bin/python3.11"
DIR="$HOME/Projects/AI-Projects/jarvis"

echo "=== EMERGENCY CLEAN RESTART ==="

# Kill every possible SIMON process
echo "[1] Killing all processes..."
pkill -9 -f "jarvis.py" 2>/dev/null
pkill -9 -f "hq_reconnect_watchdog" 2>/dev/null
pkill -9 afplay 2>/dev/null
pkill -9 say 2>/dev/null

# Unload LaunchAgent so it doesn't fight us
echo "[2] Unloading LaunchAgent..."
launchctl unload ~/Library/LaunchAgents/com.simonx.simon.plist 2>/dev/null

# Wait for port to fully clear
sleep 2
lsof -ti tcp:8765 | xargs kill -9 2>/dev/null
sleep 2

# Verify nothing is on the port
if lsof -ti tcp:8765 >/dev/null 2>&1; then
    echo "ERROR: Port 8765 still in use — $(lsof -ti tcp:8765)"
    exit 1
fi
echo "[3] Port 8765 is clear"

# Syntax check
echo "[4] Syntax check..."
"$PYTHON" -m py_compile "$DIR/jarvis.py" && echo "    Syntax OK" || { echo "SYNTAX ERROR — aborting"; exit 1; }

# Clear the log so we see fresh output only
echo "[5] Clearing log..."
> "$DIR/jarvis.log"

# Start SIMON once, directly
echo "[6] Starting SIMON..."
export TOKENIZERS_PARALLELISM=false
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cd "$DIR"
nohup "$PYTHON" jarvis.py >> "$DIR/jarvis.log" 2>&1 &
PID=$!
echo "    PID: $PID"

# Wait for port
for i in $(seq 1 20); do
    sleep 1
    if lsof -ti tcp:8765 >/dev/null 2>&1; then
        echo ""
        echo "✅ SIMON is UP after ${i}s — opening browser"
        open -a "Google Chrome" "http://localhost:8765"
        echo ""
        echo "Live log:"
        tail -f "$DIR/jarvis.log" | grep -v TOKENIZERS | grep -v "To disable" | grep -v huggingface
        exit 0
    fi
    printf "   Waiting... (%d/20)\r" "$i"
done

echo ""
echo "❌ SIMON did not start. Last 30 log lines:"
tail -30 "$DIR/jarvis.log"
