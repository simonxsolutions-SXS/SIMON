#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# simon-hq Secrets Setup — EnvironmentFile hardening
# Simon-X Solutions | Run: sudo bash simon_hq_secrets_setup.sh
#
# PURPOSE:
#   Moves secrets (HUD token, API key) out of world-readable systemd
#   unit files into a root-owned, chmod 600 EnvironmentFile.
#   Each service gets its own /etc/simon-hq/<service>.env file.
#
# SECURITY:
#   - World-readable unit files (/etc/systemd/system/*.service) should
#     NEVER contain secret values — any user can read them.
#   - EnvironmentFile= with 0600 root:root is the systemd best practice.
#   - This script also generates SIMON_HQ_KEY if not already set.
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

GRN='\033[0;32m'; YEL='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()  { echo -e "  ${GRN}✓${NC} $1"; }
warn(){ echo -e "  ${YEL}⚠${NC}  $1"; }
err() { echo -e "  ${RED}✗${NC} $1"; }

SECRETS_DIR="/etc/simon-hq"
mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  SIMON-HQ SECRETS SETUP — Secure EnvironmentFile migration"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "═══════════════════════════════════════════════════════════════"

# ── 1. nova-hud.service — HUD bearer token ──────────────────────
echo ""
echo "[1/3] nova-hud EnvironmentFile..."

HUD_SVC="/etc/systemd/system/nova-hud.service"
HUD_ENV="${SECRETS_DIR}/nova-hud.env"

# Extract existing token if present in the unit file
HUD_TOKEN_EXISTING=$(grep -oP '(?<=NOVA_HUD_TOKEN=)[^\s]+' "$HUD_SVC" 2>/dev/null || true)
HUD_TOKEN_IN_CONFIG=$(python3 -c "
import json, pathlib
p = pathlib.Path('/home/simon-hq/simon-hq/nova_config.json')
cfg = json.loads(p.read_text()) if p.exists() else {}
print(cfg.get('nova_hud_token', ''))
" 2>/dev/null || echo "")

if [ -n "$HUD_TOKEN_EXISTING" ]; then
    HUD_TOKEN="$HUD_TOKEN_EXISTING"
    warn "Using existing HUD token from unit file"
elif [ -n "$HUD_TOKEN_IN_CONFIG" ]; then
    HUD_TOKEN="$HUD_TOKEN_IN_CONFIG"
    warn "Using existing HUD token from nova_config.json"
else
    HUD_TOKEN=$(openssl rand -hex 32)
    ok "Generated new HUD token"
fi

# Write EnvironmentFile (chmod 600, root only)
cat > "$HUD_ENV" << EOF
NOVA_HUD_TOKEN=${HUD_TOKEN}
EOF
chmod 600 "$HUD_ENV"
chown root:root "$HUD_ENV"
ok "Created ${HUD_ENV} (chmod 600, root:root)"

# Remove the inline Environment= from the unit file, add EnvironmentFile= instead
if [ -f "$HUD_SVC" ]; then
    # Remove any existing inline NOVA_HUD_TOKEN= line
    sed -i '/^Environment=NOVA_HUD_TOKEN=/d' "$HUD_SVC"
    # Add EnvironmentFile directive under [Service] if not already there
    if ! grep -q "EnvironmentFile=${HUD_ENV}" "$HUD_SVC"; then
        sed -i "/^\[Service\]/a EnvironmentFile=${HUD_ENV}" "$HUD_SVC"
    fi
    ok "Updated nova-hud.service → EnvironmentFile=${HUD_ENV}"
else
    warn "nova-hud.service not found — skipping unit update"
fi

# ── 2. simon-hq-api.service — HQ API key ────────────────────────
echo ""
echo "[2/3] simon-hq-api EnvironmentFile..."

API_SVC="/etc/systemd/system/simon-hq-api.service"
API_ENV="${SECRETS_DIR}/simon-hq-api.env"

# Extract existing key or generate a new one
API_KEY_EXISTING=$(grep -oP '(?<=SIMON_HQ_KEY=)[^\s]+' "$API_SVC" 2>/dev/null || true)
HQ_API_KEY_IN_CONFIG=$(python3 -c "
import json, pathlib
p = pathlib.Path('/home/simon-hq/simon-hq/nova_config.json')
cfg = json.loads(p.read_text()) if p.exists() else {}
print(cfg.get('hq_api_key', ''))
" 2>/dev/null || echo "")

NEED_NEW_KEY=false
if [ -n "$API_KEY_EXISTING" ] && [ "$API_KEY_EXISTING" != "simon-hq-key-changeme" ]; then
    API_KEY="$API_KEY_EXISTING"
    warn "Using existing SIMON_HQ_KEY from unit file"
elif [ -n "$HQ_API_KEY_IN_CONFIG" ] && [ "$HQ_API_KEY_IN_CONFIG" != "simon-hq-key-changeme" ]; then
    API_KEY="$HQ_API_KEY_IN_CONFIG"
    warn "Using existing SIMON_HQ_KEY from nova_config.json"
else
    API_KEY=$(openssl rand -hex 32)
    NEED_NEW_KEY=true
    ok "Generated new SIMON_HQ_KEY (old was default placeholder)"
fi

cat > "$API_ENV" << EOF
SIMON_HQ_KEY=${API_KEY}
HQ_API_HOST=YOUR_HQ_TAILSCALE_IP
EOF
chmod 600 "$API_ENV"
chown root:root "$API_ENV"
ok "Created ${API_ENV} (chmod 600, root:root)"

if [ -f "$API_SVC" ]; then
    sed -i '/^Environment=SIMON_HQ_KEY=/d' "$API_SVC"
    if ! grep -q "EnvironmentFile=${API_ENV}" "$API_SVC"; then
        sed -i "/^\[Service\]/a EnvironmentFile=${API_ENV}" "$API_SVC"
    fi
    ok "Updated simon-hq-api.service → EnvironmentFile=${API_ENV}"
else
    warn "simon-hq-api.service not found"
    # Create a stub for reference
    cat > "$API_SVC" << SVCEOF
[Unit]
Description=S.I.M.O.N. HQ API
After=network.target ollama.service

[Service]
Type=simple
User=simon-hq
WorkingDirectory=/home/simon-hq/simon-hq
EnvironmentFile=${API_ENV}
ExecStart=/home/simon-hq/nova-venv/bin/python3 /home/simon-hq/simon-hq/hq_api_v2_main.py
Restart=always
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/simon-hq/simon-hq /home/simon-hq/nova-data /home/simon-hq/logs /tmp

[Install]
WantedBy=multi-user.target
SVCEOF
    chmod 644 "$API_SVC"
    ok "Created stub simon-hq-api.service"
fi

# If we generated a new key, update nova_config.json so Mac SIMON picks it up
if [ "$NEED_NEW_KEY" = true ]; then
    python3 -c "
import json, pathlib
p = pathlib.Path('/home/simon-hq/simon-hq/nova_config.json')
cfg = json.loads(p.read_text()) if p.exists() else {}
cfg['hq_api_key'] = '${API_KEY}'
p.write_text(json.dumps(cfg, indent=2))
" 2>/dev/null && ok "Updated hq_api_key in nova_config.json" \
    || warn "Could not update nova_config.json — set manually"
fi

# Save HUD token to nova_config.json too
python3 -c "
import json, pathlib
p = pathlib.Path('/home/simon-hq/simon-hq/nova_config.json')
cfg = json.loads(p.read_text()) if p.exists() else {}
cfg['nova_hud_token'] = '${HUD_TOKEN}'
p.write_text(json.dumps(cfg, indent=2))
" 2>/dev/null && ok "Updated nova_hud_token in nova_config.json" \
    || warn "Could not update nova_config.json"

# ── 3. Reload and restart ────────────────────────────────────────
echo ""
echo "[3/3] Reloading systemd and restarting services..."
systemctl daemon-reload

for svc in nova-hud simon-hq-api; do
    if systemctl list-unit-files "${svc}.service" &>/dev/null; then
        systemctl restart "$svc" 2>/dev/null \
            && ok "Restarted $svc" \
            || warn "$svc restart failed (check journalctl -u $svc)"
        sleep 1
    else
        warn "$svc not found — skipping"
    fi
done

# ── Summary ──────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo -e "  ${GRN}SECRETS MIGRATION COMPLETE${NC}"
echo "═══════════════════════════════════════════════════════════════"
echo "  Secrets are now in chmod-600 EnvironmentFiles:"
echo "  ├─ ${HUD_ENV}       ← NOVA_HUD_TOKEN"
echo "  └─ ${API_ENV}   ← SIMON_HQ_KEY + HQ_API_HOST"
echo ""
if [ "$NEED_NEW_KEY" = true ]; then
echo -e "  ${YEL}ACTION REQUIRED:${NC} New API key was generated."
echo "  Update Mac SIMON config.json → hq_api_key with this value:"
echo "  ${API_KEY}"
echo ""
fi
echo "  Next: ls -la ${SECRETS_DIR}  (should show -rw------- root root)"
echo "  Verify: sudo systemctl status nova-hud simon-hq-api"
echo "═══════════════════════════════════════════════════════════════"
