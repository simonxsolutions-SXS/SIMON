#!/bin/bash
# ============================================================
# S.I.M.O.N. Startup Script — start_simon.sh  (v4.4)
# Simon-X Solutions | [OWNER_NAME]
# ============================================================

JARVIS_DIR="$HOME/Projects/AI-Projects/jarvis"
PYTHON="/opt/homebrew/bin/python3.11"
LOG="$JARVIS_DIR/jarvis.log"
PORT=8765
CHROME="/Applications/Google Chrome.app"
BOOT_MODE="${1:-}"   # Pass "boot" when launched from launchd

if [ "$BOOT_MODE" != "boot" ]; then
    echo ""
    echo "  ╔══════════════════════════════════════╗"
    echo "  ║   S.I.M.O.N. Startup Script  v4.4   ║"
    echo "  ║   Simon-X Solutions                   ║"
    echo "  ╚══════════════════════════════════════╝"
    echo ""
fi

# ── 1. Kill any existing SIMON processes ─────────────────────
[ "$BOOT_MODE" != "boot" ] && echo "  [1/5] Clearing existing processes..."
pkill -9 -f "jarvis.py" 2>/dev/null
pkill -9 -f "hq_reconnect_watchdog.py" 2>/dev/null
pkill -9 afplay 2>/dev/null
pkill -9 say 2>/dev/null
sleep 1
lsof -ti tcp:$PORT | xargs kill -9 2>/dev/null
sleep 1
[ "$BOOT_MODE" != "boot" ] && echo "        Done."

# ── 2. Rotate log if over 20MB ───────────────────────────────
if [ -f "$LOG" ] && [ $(wc -c < "$LOG") -gt 20971520 ]; then
    mv "$LOG" "$LOG.bak.$(date +%Y%m%d)"
    echo "[Log] Rotated — was over 20MB"
fi

# ── 3. Syntax check ───────────────────────────────────────────
[ "$BOOT_MODE" != "boot" ] && echo "  [3/5] Checking jarvis.py for errors..."
SYNTAX_ERR=$("$PYTHON" -m py_compile "$JARVIS_DIR/jarvis.py" 2>&1)
if [ $? -ne 0 ]; then
    echo "  ❌ SYNTAX ERROR in jarvis.py: $SYNTAX_ERR"
    exit 1
fi
[ "$BOOT_MODE" != "boot" ] && echo "        Syntax OK."

# ── 4. Launch SIMON ───────────────────────────────────────────
[ "$BOOT_MODE" != "boot" ] && echo "  [4/5] Starting S.I.M.O.N...."
export TOKENIZERS_PARALLELISM=false
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cd "$JARVIS_DIR"
nohup "$PYTHON" jarvis.py >> "$LOG" 2>&1 &
SIMON_PID=$!

# ── 5. Wait for port bind ────────────────────────────────────
READY=0
for i in $(seq 1 25); do
    sleep 1
    if lsof -ti tcp:$PORT >/dev/null 2>&1; then
        READY=1
        [ "$BOOT_MODE" != "boot" ] && echo "" && echo "  ✅ S.I.M.O.N. online — PID $SIMON_PID (${i}s)"
        break
    fi
    [ "$BOOT_MODE" != "boot" ] && printf "     Waiting... (%d/25)\r" "$i"
done

if [ $READY -eq 0 ]; then
    echo "  ⚠️  SIMON did not start within 25s — check: tail -f $LOG"
    exit 1
fi

# ── Open browser (interactive mode only) ─────────────────────
if [ "$BOOT_MODE" != "boot" ]; then
    echo "  [5/5] Opening HUD..."
    if [ -d "$CHROME" ]; then
        open -a "Google Chrome" "http://localhost:$PORT"
    else
        open "http://localhost:$PORT"
    fi
    echo ""
    echo "  Log:  tail -f $LOG | grep -v TOKENIZERS | grep -v huggingface"
    echo ""
fi
