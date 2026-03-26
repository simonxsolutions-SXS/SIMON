#!/bin/bash
# ============================================================
# S.I.M.O.N. Restart Script — restart_simon.sh
# Simon-X Solutions | [OWNER_NAME]
# Usage: ./restart_simon.sh
# ============================================================

DIR="$(cd "$(dirname "$0")" && pwd)"
"$DIR/stop_simon.sh"
sleep 1
"$DIR/start_simon.sh"
