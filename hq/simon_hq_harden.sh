#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# simon-hq Security Hardening Script
# Simon-X Solutions | Run: sudo bash simon_hq_harden.sh
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

RED='\033[0;31m'; YEL='\033[0;33m'; GRN='\033[0;32m'; NC='\033[0m'
ok()  { echo -e "  ${GRN}✓${NC} $1"; }
warn(){ echo -e "  ${YEL}⚠${NC}  $1"; }
err() { echo -e "  ${RED}✗${NC} $1"; }

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  SIMON-HQ SECURITY HARDENING — Simon-X Solutions"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "═══════════════════════════════════════════════════════════════"

# ── 1. Redis requirepass ───────────────────────────────────────────
echo ""
echo "[1/6] Redis — adding requirepass..."
if grep -q "^requirepass" /etc/redis/redis.conf 2>/dev/null; then
    warn "Redis already has requirepass — skipping"
else
    REDIS_PASS=$(openssl rand -hex 32)
    echo "requirepass $REDIS_PASS" >> /etc/redis/redis.conf
    systemctl restart redis-server
    ok "Redis password set"
    # Persist to nova_config.json
    python3 -c "
import json, pathlib
p = pathlib.Path('/home/simon-hq/simon-hq/nova_config.json')
cfg = json.loads(p.read_text()) if p.exists() else {}
cfg['redis_password'] = '${REDIS_PASS}'
p.write_text(json.dumps(cfg, indent=2))
" 2>/dev/null && ok "Password saved to nova_config.json" || warn "Could not save to nova_config.json"
    echo -e "  ${YEL}SAVE THIS:${NC} Redis password = ${REDIS_PASS}"
fi

# ── 2. HUD token in service environment ───────────────────────────
echo ""
echo "[2/6] HUD authentication token..."
HUD_EXISTING=$(python3 -c "
import json, pathlib
p = pathlib.Path('/home/simon-hq/simon-hq/nova_config.json')
cfg = json.loads(p.read_text()) if p.exists() else {}
print(cfg.get('nova_hud_token', ''))
" 2>/dev/null || echo "")

if [ -n "$HUD_EXISTING" ]; then
    HUD_TOKEN="$HUD_EXISTING"
    warn "HUD token already exists in nova_config.json — reusing"
else
    HUD_TOKEN=$(openssl rand -hex 32)
    python3 -c "
import json, pathlib
p = pathlib.Path('/home/simon-hq/simon-hq/nova_config.json')
cfg = json.loads(p.read_text()) if p.exists() else {}
cfg['nova_hud_token'] = '${HUD_TOKEN}'
p.write_text(json.dumps(cfg, indent=2))
" 2>/dev/null
    ok "HUD token generated and saved to nova_config.json"
    echo -e "  ${YEL}SAVE THIS:${NC} HUD token = ${HUD_TOKEN}"
fi

# Inject into nova-hud.service env block
HUD_SVC="/etc/systemd/system/nova-hud.service"
if [ -f "$HUD_SVC" ]; then
    if grep -q "NOVA_HUD_TOKEN" "$HUD_SVC"; then
        sed -i "s|^Environment=NOVA_HUD_TOKEN=.*|Environment=NOVA_HUD_TOKEN=${HUD_TOKEN}|" "$HUD_SVC"
        ok "Updated NOVA_HUD_TOKEN in nova-hud.service"
    else
        sed -i "/^\[Service\]/a Environment=NOVA_HUD_TOKEN=${HUD_TOKEN}" "$HUD_SVC"
        ok "Injected NOVA_HUD_TOKEN into nova-hud.service"
    fi
fi

# ── 3. Systemd service sandboxing ─────────────────────────────────
echo ""
echo "[3/6] Systemd service sandboxing..."
HARDENING_BLOCK='NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/simon-hq/simon-hq /home/simon-hq/nova-data /home/simon-hq/logs /tmp
RestrictSUIDSGID=yes
LockPersonality=yes
RestrictRealtime=yes'

for svc in nova-mcpo nova-hud simon-hq-api nova-webui; do
    f="/etc/systemd/system/${svc}.service"
    [ -f "$f" ] || { warn "SKIP: ${f} not found"; continue; }
    if grep -q "NoNewPrivileges" "$f"; then
        warn "SKIP ${svc} — already hardened"
        continue
    fi
    # Append hardening after the [Service] header line
    awk '/^\[Service\]/{print; print "'"${HARDENING_BLOCK//$'\n'/\\n}"'"; next}1' "$f" > "${f}.tmp"
    mv "${f}.tmp" "$f"
    ok "Hardened ${svc}"
done
systemctl daemon-reload

# ── 4. Restrict Open WebUI to Tailscale interface only ─────────────
echo ""
echo "[4/6] Restricting nova-webui to Tailscale interface..."
TS_IP=$(tailscale ip -4 2>/dev/null | head -1 || echo "")
if [ -n "$TS_IP" ]; then
    NOVA_WEBUI_SVC="/etc/systemd/system/nova-webui.service"
    if grep -q "\-\-host 0\.0\.0\.0" "$NOVA_WEBUI_SVC" 2>/dev/null; then
        sed -i "s/--host 0\.0\.0\.0/--host ${TS_IP}/" "$NOVA_WEBUI_SVC"
        ok "nova-webui now binds to ${TS_IP} (Tailscale only)"
    else
        warn "nova-webui --host binding not found or already restricted"
    fi
else
    warn "Could not detect Tailscale IP — nova-webui left on 0.0.0.0"
fi
systemctl daemon-reload

# ── 5. SSH hardening ──────────────────────────────────────────────
echo ""
echo "[5/6] SSH hardening..."
SSH_CFG="/etc/ssh/sshd_config"
cp "$SSH_CFG" "${SSH_CFG}.bak.$(date +%Y%m%d%H%M%S)"
ok "Backed up sshd_config"

apply_ssh_setting() {
    local key="$1" val="$2"
    if grep -qE "^#?${key}" "$SSH_CFG"; then
        sed -i -E "s|^#?${key}.*|${key} ${val}|" "$SSH_CFG"
    else
        echo "${key} ${val}" >> "$SSH_CFG"
    fi
}

apply_ssh_setting "MaxAuthTries"         "3"
apply_ssh_setting "LoginGraceTime"       "20"
apply_ssh_setting "PermitRootLogin"      "no"
apply_ssh_setting "X11Forwarding"        "no"
apply_ssh_setting "AllowAgentForwarding" "no"
apply_ssh_setting "AllowTcpForwarding"   "no"
apply_ssh_setting "ClientAliveInterval"  "300"
apply_ssh_setting "ClientAliveCountMax"  "2"
apply_ssh_setting "MaxSessions"          "4"

if sshd -t 2>/dev/null; then
    systemctl reload sshd
    ok "SSH hardened (MaxAuthTries=3, no root login, no X11/agent/TCP forwarding)"
else
    cp "${SSH_CFG}.bak."* "$SSH_CFG" 2>/dev/null || true
    err "sshd config test FAILED — restored backup. Review manually."
fi

# ── 6. fail2ban ───────────────────────────────────────────────────
echo ""
echo "[6/6] Installing and configuring fail2ban..."
if ! command -v fail2ban-server &>/dev/null; then
    apt-get install -y fail2ban -qq
    ok "fail2ban installed"
else
    ok "fail2ban already installed"
fi

cat > /etc/fail2ban/jail.local << 'F2B'
[DEFAULT]
bantime  = 3600
findtime = 600
maxretry = 5
backend  = systemd
ignoreip = 127.0.0.1 ::1 100.0.0.0/8

# SSH — aggressive: 3 failures = 24h ban
[sshd]
enabled  = true
port     = ssh
filter   = sshd
maxretry = 3
findtime = 600
bantime  = 86400

# NOVA HUD — 401 flood protection
[nova-hud-auth]
enabled  = true
port     = 3001
filter   = nova-hud-auth
logpath  = /home/simon-hq/simon-hq/nova-hud.log
maxretry = 10
findtime = 60
bantime  = 300
F2B

# Custom fail2ban filter for HUD 401 responses
mkdir -p /etc/fail2ban/filter.d
cat > /etc/fail2ban/filter.d/nova-hud-auth.conf << 'F2BFILTER'
[Definition]
failregex = .*"(GET|POST|DELETE) /api/.*" 401
ignoreregex =
F2BFILTER

systemctl enable fail2ban
systemctl restart fail2ban
ok "fail2ban configured (SSH: 3 attempts → 24h ban; HUD: 10 attempts → 5min ban)"

# ── Restart all services ──────────────────────────────────────────
echo ""
echo "[+] Restarting NOVA services..."
for svc in nova-mcpo nova-hud simon-hq-api nova-webui; do
    systemctl restart "$svc" 2>/dev/null \
        && ok "Restarted $svc" \
        || warn "$svc restart failed (may not be installed yet)"
    sleep 1
done

# ── Summary ───────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo -e "  ${GRN}SIMON-HQ HARDENING COMPLETE${NC}"
echo "═══════════════════════════════════════════════════════════════"
echo "  Save these in your password manager:"
echo "  ┌─ HUD Token  : ${HUD_TOKEN:-[already existed, check nova_config.json]}"
if [ -n "${REDIS_PASS:-}" ]; then
echo "  ├─ Redis Pass : ${REDIS_PASS}"
fi
echo "  └─ nova_config.json on simon-hq has both values"
echo ""
echo "  Next steps:"
echo "  1. Update HUD JS to send token: Authorization: Bearer <HUD_TOKEN>"
echo "  2. Verify: sudo systemctl status nova-hud nova-mcpo"
echo "  3. Check: sudo fail2ban-client status"
echo "═══════════════════════════════════════════════════════════════"
