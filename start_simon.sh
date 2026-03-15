#!/bin/bash
# ═══════════════════════════════════════════════════════════
#  S.I.M.O.N. v4.2 Launch Script
#  https://github.com/simonxsolutions-SXS/SIMON
#  Usage: bash start_simon.sh
# ═══════════════════════════════════════════════════════════

# Resolve directory relative to this script — works from anywhere
SIMON_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
LOG="$SIMON_DIR/jarvis.log"
PORT=8765
PY="/opt/homebrew/bin/python3.11"

# Check Python exists
if ! command -v "$PY" &>/dev/null; then
    PY=$(which python3.11 2>/dev/null || which python3 2>/dev/null)
    if [ -z "$PY" ]; then
        echo "  ❌ Python 3.11 not found. Install with: brew install python@3.11"
        exit 1
    fi
fi

echo ""
echo "  ╔══════════════════════════════════════════════════════╗"
echo "  ║  S.I.M.O.N. v4.2  |  Systems Intelligence Node      ║"
echo "  ║  Model  : $(python3 -c "import json; c=json.load(open('$SIMON_DIR/config.json')); print(c.get('model','unknown')[:30])" 2>/dev/null || echo 'see config.json')"
echo "  ║  Voice  : Piper TTS — Alan (British, offline)        ║"
echo "  ║  Tools  : Calendar · iMessage · Mail · Reminders     ║"
echo "  ║           Contacts · Shell · System · KB             ║"
echo "  ╚══════════════════════════════════════════════════════╝"
echo ""

# Check config.json exists
if [ ! -f "$SIMON_DIR/config.json" ]; then
    echo "  ❌ config.json not found!"
    echo "     Copy config.example.json → config.json and fill in your details."
    echo "     cp $SIMON_DIR/config.example.json $SIMON_DIR/config.json"
    exit 1
fi

# Check voice model exists
if [ ! -f "$SIMON_DIR/voices/en_GB-alan-medium.onnx" ]; then
    echo "  ⚠️  Voice model not found. Downloading..."
    mkdir -p "$SIMON_DIR/voices"
    curl -L -o "$SIMON_DIR/voices/en_GB-alan-medium.onnx" \
      "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx" && \
    curl -L -o "$SIMON_DIR/voices/en_GB-alan-medium.onnx.json" \
      "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx.json"
fi

# Kill stale process on port
echo "  → Clearing port $PORT..."
lsof -ti tcp:$PORT | xargs kill -9 2>/dev/null || true
sleep 0.5

# Initialize/sync KB
echo "  → Syncing knowledge base..."
"$PY" "$SIMON_DIR/simon_kb.py" sync 2>/dev/null || echo "  ⚠️  KB sync skipped"

# Test TTS
echo "  → Testing Piper TTS..."
"$PY" -c "
from piper.voice import PiperVoice
import wave
v = PiperVoice.load('$SIMON_DIR/voices/en_GB-alan-medium.onnx')
with wave.open('/tmp/simon_boot.wav','wb') as w:
    v.synthesize_wav('S.I.M.O.N. online. All systems ready.', w)
print('TTS_OK')
" 2>/dev/null && afplay /tmp/simon_boot.wav 2>/dev/null && echo "  ✅ TTS: working" || echo "  ⚠️  TTS: check piper install"

# Start SIMON server
echo "  → Starting server..."
cd "$SIMON_DIR"
> "$LOG"
"$PY" jarvis.py >> "$LOG" 2>&1 &
SIMON_PID=$!
echo "  → PID: $SIMON_PID"

# Wait for server to be ready
echo "  → Waiting for server..."
for i in {1..15}; do
    sleep 1
    if curl -s --max-time 1 http://localhost:$PORT/api/status > /dev/null 2>&1; then
        echo "  ✅ Server up at http://localhost:$PORT"
        open -a "Google Chrome" "http://localhost:$PORT" 2>/dev/null || \
            open "http://localhost:$PORT"
        echo ""
        echo "  Say the wake word to activate SIMON."
        echo "  Logs: tail -f $LOG"
        echo ""
        exit 0
    fi
done
echo "  ❌ Server didn't start in 15s — check: tail -20 $LOG"
exit 1
