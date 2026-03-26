#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# simon-hq CVE Version Check + Remediation
# Simon-X Solutions | Run: bash simon_hq_cve_check.sh
# (No sudo required for check; sudo needed for upgrades)
# ═══════════════════════════════════════════════════════════════════

GRN='\033[0;32m'; YEL='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()  { echo -e "  ${GRN}✓${NC} $1"; }
warn(){ echo -e "  ${YEL}⚠${NC}  $1"; }
crit(){ echo -e "  ${RED}✗ CRITICAL${NC} $1"; }

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  SIMON-HQ CVE VERSION CHECK — Simon-X Solutions"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "═══════════════════════════════════════════════════════════════"
echo ""

NEEDS_ACTION=0

# ── 1. Ollama — CVE-2025-63389 (CVSS 9.8, RCE) ─────────────────
#     CVE-2024-37032 / Probllama (CVSS 9.4, path traversal)
#     Fixed in: 0.6.5+ (CVE-2025-63389), 0.1.34+ (Probllama)
echo "[1/4] Ollama version check..."
OLLAMA_VER=$(ollama --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' | head -1 || echo "")
if [ -z "$OLLAMA_VER" ]; then
    warn "Ollama not found or not in PATH"
else
    echo "  Installed: ollama $OLLAMA_VER"
    # Compare versions (semver comparison)
    OLLAMA_MAJOR=$(echo "$OLLAMA_VER" | cut -d. -f1)
    OLLAMA_MINOR=$(echo "$OLLAMA_VER" | cut -d. -f2)
    OLLAMA_PATCH=$(echo "$OLLAMA_VER" | cut -d. -f3)

    # CVE-2024-37032 fixed in 0.1.34
    if [ "$OLLAMA_MAJOR" -eq 0 ] && [ "$OLLAMA_MINOR" -eq 1 ] && [ "$OLLAMA_PATCH" -lt 34 ]; then
        crit "CVE-2024-37032 (Probllama, CVSS 9.4): path traversal in model pull"
        echo "         UPGRADE: curl -fsSL https://ollama.com/install.sh | sh"
        NEEDS_ACTION=1
    elif [ "$OLLAMA_MAJOR" -eq 0 ] && [ "$OLLAMA_MINOR" -lt 6 ]; then
        crit "CVE-2025-63389 (CVSS 9.8): RCE via model manifest. Version $OLLAMA_VER is vulnerable!"
        echo "         UPGRADE: curl -fsSL https://ollama.com/install.sh | sh"
        NEEDS_ACTION=1
    elif [ "$OLLAMA_MAJOR" -eq 0 ] && [ "$OLLAMA_MINOR" -eq 6 ] && [ "$OLLAMA_PATCH" -lt 5 ]; then
        crit "CVE-2025-63389 (CVSS 9.8): RCE via model manifest. Version $OLLAMA_VER is vulnerable!"
        echo "         UPGRADE: curl -fsSL https://ollama.com/install.sh | sh"
        NEEDS_ACTION=1
    else
        ok "Ollama $OLLAMA_VER — above CVE-2025-63389 threshold (0.6.5+)"
    fi
fi

# ── 2. Open WebUI — CVE-2025-64496 (CVSS 9.1, JWT theft) ────────
#     Fixed in: 0.6.10+
echo ""
echo "[2/4] Open WebUI version check..."
WEBUI_VER=$(/home/simon-hq/nova-venv/bin/open-webui --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' | head -1 || echo "")
if [ -z "$WEBUI_VER" ]; then
    # Try pip show
    WEBUI_VER=$(/home/simon-hq/nova-venv/bin/pip show open-webui 2>/dev/null | grep -oP '(?<=Version: )\S+' || echo "")
fi
if [ -z "$WEBUI_VER" ]; then
    warn "Open WebUI version could not be determined"
else
    echo "  Installed: open-webui $WEBUI_VER"
    OW_MAJOR=$(echo "$WEBUI_VER" | cut -d. -f1)
    OW_MINOR=$(echo "$WEBUI_VER" | cut -d. -f2)
    OW_PATCH=$(echo "$WEBUI_VER" | cut -d. -f3)
    if [ "$OW_MAJOR" -eq 0 ] && [ "$OW_MINOR" -lt 6 ]; then
        crit "CVE-2025-64496 (CVSS 9.1): JWT theft / privilege escalation. Vulnerable!"
        echo "         UPGRADE: /home/simon-hq/nova-venv/bin/pip install --upgrade open-webui"
        echo "                  sudo systemctl restart nova-webui"
        NEEDS_ACTION=1
    elif [ "$OW_MAJOR" -eq 0 ] && [ "$OW_MINOR" -eq 6 ] && [ "$OW_PATCH" -lt 10 ]; then
        crit "CVE-2025-64496 (CVSS 9.1): JWT theft / privilege escalation. Vulnerable!"
        echo "         UPGRADE: /home/simon-hq/nova-venv/bin/pip install --upgrade open-webui"
        echo "                  sudo systemctl restart nova-webui"
        NEEDS_ACTION=1
    else
        ok "Open WebUI $WEBUI_VER — above CVE-2025-64496 threshold (0.6.10+)"
    fi
fi

# ── 3. Redis — CVE-2025-49844 (CVSS 9.3, Lua sandbox escape) ───
#     Fixed in: Redis 7.2.8 / 7.4.3 / 8.0.2
echo ""
echo "[3/4] Redis version check..."
REDIS_VER=$(redis-server --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' | head -1 || echo "")
if [ -z "$REDIS_VER" ]; then
    warn "Redis not found or not in PATH"
else
    echo "  Installed: redis $REDIS_VER"
    R_MAJOR=$(echo "$REDIS_VER" | cut -d. -f1)
    R_MINOR=$(echo "$REDIS_VER" | cut -d. -f2)
    R_PATCH=$(echo "$REDIS_VER" | cut -d. -f3)
    REDIS_VULN=false
    if [ "$R_MAJOR" -eq 7 ] && [ "$R_MINOR" -eq 2 ] && [ "$R_PATCH" -lt 8 ]; then REDIS_VULN=true; fi
    if [ "$R_MAJOR" -eq 7 ] && [ "$R_MINOR" -eq 4 ] && [ "$R_PATCH" -lt 3 ]; then REDIS_VULN=true; fi
    if [ "$R_MAJOR" -eq 8 ] && [ "$R_MINOR" -eq 0 ] && [ "$R_PATCH" -lt 2 ]; then REDIS_VULN=true; fi
    if [ "$REDIS_VULN" = true ]; then
        crit "CVE-2025-49844 (CVSS 9.3): Lua sandbox escape in EVAL. Vulnerable!"
        echo "         UPGRADE: sudo apt-get update && sudo apt-get install -y redis"
        NEEDS_ACTION=1
    else
        ok "Redis $REDIS_VER — above known CVE threshold"
    fi
fi

# ── 4. Python packages in nova-venv ─────────────────────────────
echo ""
echo "[4/4] Python dependency audit (pip-audit)..."
if /home/simon-hq/nova-venv/bin/pip show pip-audit &>/dev/null 2>&1; then
    /home/simon-hq/nova-venv/bin/pip-audit 2>/dev/null | head -30 || warn "pip-audit returned errors"
else
    warn "pip-audit not installed — install with:"
    echo "         /home/simon-hq/nova-venv/bin/pip install pip-audit"
    echo "         Then run: /home/simon-hq/nova-venv/bin/pip-audit"
fi

# ── Summary ──────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════"
if [ "$NEEDS_ACTION" -eq 0 ]; then
    echo -e "  ${GRN}ALL CHECKED COMPONENTS APPEAR UP TO DATE${NC}"
else
    echo -e "  ${RED}ACTION REQUIRED — see UPGRADE commands above${NC}"
    echo "  After upgrades: sudo systemctl restart nova-webui ollama redis-server"
fi
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "  Additional hardening reminders:"
echo "  1. Ensure Ollama binds to 127.0.0.1 only:"
echo "     OLLAMA_HOST=127.0.0.1:11434 in /etc/systemd/system/ollama.service"
echo "  2. Redis requirepass should be set (check /etc/redis/redis.conf)"
echo "  3. Run pip-audit monthly: /home/simon-hq/nova-venv/bin/pip-audit"
echo "═══════════════════════════════════════════════════════════════"
