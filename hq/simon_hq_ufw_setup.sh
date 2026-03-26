#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# simon-hq UFW Firewall Setup
# Simon-X Solutions | Run: sudo bash simon_hq_ufw_setup.sh
#
# POLICY:
#   Default: deny all inbound, allow all outbound
#   Allowed inbound:
#     22    — SSH (Tailscale subnet only for best security)
#     100/8  — All Tailscale traffic (MagicDNS peers)
#     Custom: only Tailscale peers can reach internal services
#
# TAILSCALE NOTE:
#   Tailscale runs on the tailscale0 interface and handles its own
#   encryption. We allow the Tailscale subnet (100.64.0.0/10) for
#   internal service ports and restrict everything else.
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

GRN='\033[0;32m'; YEL='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()  { echo -e "  ${GRN}✓${NC} $1"; }
warn(){ echo -e "  ${YEL}⚠${NC}  $1"; }
err() { echo -e "  ${RED}✗${NC} $1"; }

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  SIMON-HQ UFW FIREWALL SETUP — Simon-X Solutions"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "═══════════════════════════════════════════════════════════════"

# ── Install UFW if needed ────────────────────────────────────────
if ! command -v ufw &>/dev/null; then
    apt-get install -y ufw -qq
    ok "ufw installed"
else
    ok "ufw already installed"
fi

# ── Detect Tailscale IP ──────────────────────────────────────────
TS_IP=$(tailscale ip -4 2>/dev/null | head -1 || echo "")
if [ -z "$TS_IP" ]; then
    warn "Could not detect Tailscale IP — using subnet 100.64.0.0/10"
    TS_NET="100.64.0.0/10"
else
    ok "Tailscale IP: ${TS_IP}"
    TS_NET="100.64.0.0/10"
fi

# ── Reset and configure ──────────────────────────────────────────
echo ""
echo "Configuring UFW rules..."

# Disable first (to avoid locking ourselves out mid-config)
ufw --force disable

# Reset to clean state
ufw --force reset

# Default policies
ufw default deny incoming
ufw default allow outgoing
ufw default deny forward
ok "Default policy: deny incoming, allow outgoing"

# ── SSH (port 22) ────────────────────────────────────────────────
# Allow from Tailscale subnet only — not the whole internet
ufw allow in on tailscale0 to any port 22 proto tcp
ok "SSH allowed from Tailscale interface only"

# ── Tailscale traffic (allow all from TS subnet) ─────────────────
# This covers peer-to-peer traffic on the tailscale0 interface
ufw allow in on tailscale0
ok "All Tailscale (tailscale0) traffic allowed"

# ── Internal services — Tailscale subnet only ─────────────────────
# HQ API (8200) — SIMON Mac connects via Tailscale
ufw allow from "$TS_NET" to any port 8200 proto tcp comment "simon-hq-api (Tailscale only)"
ok "Port 8200 (HQ API) open for Tailscale subnet"

# NOVA HUD (3001) — accessed via browser over Tailscale
ufw allow from "$TS_NET" to any port 3001 proto tcp comment "nova-hud (Tailscale only)"
ok "Port 3001 (NOVA HUD) open for Tailscale subnet"

# Open WebUI (3000) — PWA access via Tailscale
ufw allow from "$TS_NET" to any port 3000 proto tcp comment "nova-webui (Tailscale only)"
ok "Port 3000 (Open WebUI) open for Tailscale subnet"

# ChromaDB (8100) — localhost only, no external access needed
# (Already handled by service binding to 127.0.0.1)

# Ollama (11434) — localhost only
# (Bind to 127.0.0.1 in Ollama config — no external access)

# ── Deny common attack ports explicitly ──────────────────────────
ufw deny 23/tcp    comment "Telnet"
ufw deny 2375/tcp  comment "Docker daemon (unencrypted)"
ufw deny 2376/tcp  comment "Docker TLS"
ok "Common attack ports explicitly denied"

# ── Loopback — always allow ──────────────────────────────────────
ufw allow in on lo
ok "Loopback allowed"

# ── Enable ──────────────────────────────────────────────────────
ufw --force enable
ok "UFW enabled"

# ── Status ──────────────────────────────────────────────────────
echo ""
echo "Current UFW rules:"
ufw status numbered

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo -e "  ${GRN}UFW FIREWALL ACTIVE${NC}"
echo "═══════════════════════════════════════════════════════════════"
echo "  Inbound: DENY all except:"
echo "  ├─ tailscale0 (all Tailscale peer traffic)"
echo "  ├─ Port 22  SSH (Tailscale only)"
echo "  ├─ Port 3000 Open WebUI (Tailscale subnet)"
echo "  ├─ Port 3001 NOVA HUD  (Tailscale subnet)"
echo "  └─ Port 8200 HQ API    (Tailscale subnet)"
echo ""
echo "  Outbound: ALLOW all (needed for Ollama model pulls, etc.)"
echo ""
echo "  Verify: sudo ufw status numbered"
echo "  Test  : from Mac: curl http://YOUR_HQ_TAILSCALE_IP:8200/health"
echo "═══════════════════════════════════════════════════════════════"
