#!/usr/bin/env bash
# ============================================================
# SIMON-X Solutions — simon-hq Full Stack Installer
# Run as: sudo bash simon_hq_install.sh
# ============================================================
set -e
NOVA_USER="simon-hq"
NOVA_HOME="/home/simon-hq"
NOVA_DIR="$NOVA_HOME/simon-hq"
VENV="$NOVA_HOME/nova-venv"

echo "================================================================"
echo "  SIMON-X / NOVA — simon-hq Infrastructure Upgrade"
echo "================================================================"
echo ""

# ── 1. Install packages ──────────────────────────────────────────────────────
echo "[1/8] Installing packages..."
apt-get update -qq
apt-get install -y -qq \
    redis-server nginx \
    postgresql-client \
    python3-psycopg2 \
    logrotate fail2ban \
    jq curl net-tools \
    2>&1 | tail -3
echo "     ✅ Packages installed"

# ── 2. Configure Redis ────────────────────────────────────────────────────────
echo "[2/8] Configuring Redis..."
mkdir -p /etc/redis/redis.conf.d
cat > /etc/redis/redis.conf.d/simonx.conf << 'REDISCONF'
# Simon-X Solutions — Redis config
bind 127.0.0.1
port 6379
maxmemory 256mb
maxmemory-policy allkeys-lru
# Persistence — RDB snapshots + AOF for durability
save 900 1
save 300 10
save 60 10000
appendonly yes
appendfsync everysec
# Session key expiry defaults
# Keys without TTL persist forever (session state)
REDISCONF

# Apply by including in main config if not already included
if ! grep -q "redis.conf.d" /etc/redis/redis.conf 2>/dev/null; then
    echo "include /etc/redis/redis.conf.d/*.conf" >> /etc/redis/redis.conf
fi
systemctl enable redis-server --now
systemctl restart redis-server
sleep 2
redis-cli ping && echo "     ✅ Redis running" || echo "     ⚠️  Redis start issue"

# ── 3. Configure PostgreSQL ───────────────────────────────────────────────────
echo "[3/8] Setting up PostgreSQL simonx database..."
sudo -u postgres psql -f "$NOVA_DIR/setup_postgres_simonx.sql" 2>&1 | tail -5
echo "     ✅ PostgreSQL simonx database ready"

# ── 4. Configure nginx ────────────────────────────────────────────────────────
echo "[4/8] Configuring nginx reverse proxy..."
cat > /etc/nginx/sites-available/simonx << 'NGINXCONF'
# Simon-X Solutions — nginx reverse proxy for NOVA services
# All NOVA endpoints reachable on port 80 with path prefixes
# (bind only on Tailscale interface for security)

server {
    listen YOUR_HQ_TAILSCALE_IP:80;
    server_name simon-hq;

    # NOVA HUD — main GUI
    location / {
        proxy_pass         http://127.0.0.1:3001;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host $host;
        proxy_read_timeout 3600s;
    }

    # Open WebUI
    location /webui/ {
        proxy_pass         http://127.0.0.1:3000/;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host $host;
        proxy_read_timeout 3600s;
    }

    # HQ API
    location /api/ {
        proxy_pass         http://127.0.0.1:8200/;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
    }

    # MCP tools
    location /mcp/ {
        proxy_pass         http://127.0.0.1:8301/;
        proxy_set_header   Host $host;
    }

    # Health check endpoint
    location /health {
        default_type application/json;
        return 200 '{"status":"ok","host":"simon-hq"}';
    }
}
NGINXCONF

ln -sf /etc/nginx/sites-available/simonx /etc/nginx/sites-enabled/simonx
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl enable nginx --now && systemctl restart nginx
echo "     ✅ nginx configured on Tailscale IP :80"

# ── 5. Upgrade systemd services ───────────────────────────────────────────────
echo "[5/8] Upgrading systemd services..."

# Helper to patch a service file
patch_service() {
    local svc="$1"
    local file="/etc/systemd/system/${svc}.service"
    [ -f "$file" ] || return 0
    # Add StartLimitIntervalSec=0 if missing (prevents throttle lock)
    grep -q "StartLimitIntervalSec" "$file" || \
        sed -i '/\[Service\]/a StartLimitIntervalSec=0' "$file"
    # Ensure TimeoutStartSec
    grep -q "TimeoutStartSec" "$file" || \
        sed -i '/Restart=always/a TimeoutStartSec=90' "$file"
    echo "     Patched $svc"
}

for svc in nova-webui nova-hud nova-mcpo simon-hq-api simon-chroma; do
    patch_service "$svc"
done

# Disable the old ghost service
systemctl disable simon-hq.service 2>/dev/null && echo "     Disabled stale simon-hq.service" || true

# Add Redis + PostgreSQL to nova-mcpo dependency chain
sed -i 's/^After=.*/After=network-online.target ollama.service simon-chroma.service redis.service postgresql.service/' \
    /etc/systemd/system/nova-mcpo.service 2>/dev/null || true

# Add redis service dependency to nova-hud
sed -i 's/^After=network-online.target.*/After=network-online.target simon-hq-api.service simon-chroma.service redis.service/' \
    /etc/systemd/system/nova-hud.service 2>/dev/null || true

systemctl daemon-reload
echo "     ✅ Services upgraded"

# ── 6. ADB reconnect timer ────────────────────────────────────────────────────
echo "[6/8] Installing ADB reconnect watchdog..."
cat > /etc/systemd/system/nova-adb-watchdog.service << 'ADBSVC'
[Unit]
Description=NOVA ADB Reconnect Watchdog (Pixel 9a)
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
Type=oneshot
User=simon-hq
ExecStart=/bin/bash -c '\
    TARGET=$(python3 -c "import json; c=json.load(open(\"/home/simon-hq/simon-hq/nova_config.json\")); print(c[\"android_ip\"]+\":\"+str(c[\"android_port\"]))" 2>/dev/null || echo "YOUR_ANDROID_TAILSCALE_IP:5555"); \
    STATUS=$(adb -s $TARGET get-state 2>&1); \
    if echo "$STATUS" | grep -q "device"; then \
        echo "[ADB watchdog] Pixel 9a connected: $TARGET"; \
    else \
        echo "[ADB watchdog] Reconnecting to $TARGET..."; \
        adb connect $TARGET; \
    fi'
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
ADBSVC

cat > /etc/systemd/system/nova-adb-watchdog.timer << 'ADBTIMER'
[Unit]
Description=NOVA ADB Reconnect — every 5 minutes
Requires=nova-adb-watchdog.service

[Timer]
OnBootSec=60
OnUnitActiveSec=5min
AccuracySec=30

[Install]
WantedBy=timers.target
ADBTIMER

systemctl enable nova-adb-watchdog.timer --now
echo "     ✅ ADB watchdog installed (every 5 min)"

# ── 7. Internet connectivity watchdog ─────────────────────────────────────────
echo "[7/8] Installing internet watchdog..."
cat > /usr/local/bin/nova-net-watchdog.sh << 'NETWATCHDOG'
#!/usr/bin/env bash
# NOVA network watchdog — logs to PostgreSQL and restarts Tailscale if needed
PGCONN="postgresql://simonx_app:YOUR_DB_PASSWORD@localhost/simonx"
LAST_STATE_FILE="/tmp/nova_net_state"
LAST_STATE=$(cat "$LAST_STATE_FILE" 2>/dev/null || echo "unknown")

# Test internet connectivity
if ping -c 1 -W 3 8.8.8.8 > /dev/null 2>&1; then
    CURRENT_STATE="up"
else
    CURRENT_STATE="down"
fi

if [ "$CURRENT_STATE" != "$LAST_STATE" ]; then
    echo "$CURRENT_STATE" > "$LAST_STATE_FILE"
    TS=$(date -u +"%Y-%m-%d %H:%M:%S")

    if [ "$CURRENT_STATE" == "up" ]; then
        echo "[$TS] Internet RESTORED — restarting Tailscale and ADB..."
        systemctl restart tailscaled tailscale-up 2>/dev/null || true
        sleep 3
        # Reconnect ADB
        ANDROID_IP=$(python3 -c "import json; c=json.load(open('/home/simon-hq/simon-hq/nova_config.json')); print(c.get('android_ip','YOUR_ANDROID_TAILSCALE_IP')+':'+str(c.get('android_port',5555)))" 2>/dev/null || echo "YOUR_ANDROID_TAILSCALE_IP:5555")
        su -c "adb connect $ANDROID_IP" simon-hq 2>/dev/null || true
        # Log to PostgreSQL
        psql "$PGCONN" -c "INSERT INTO connection_events (event_type, device, detail) VALUES ('internet_restored', 'simon-hq', 'Internet back online — services refreshed')" 2>/dev/null || true
        # Update Redis session state
        redis-cli set nova:net_status "up" EX 600 > /dev/null 2>&1 || true
    else
        echo "[$TS] Internet LOST — logging event..."
        psql "$PGCONN" -c "INSERT INTO connection_events (event_type, device, detail) VALUES ('internet_lost', 'simon-hq', 'Connectivity check failed to 8.8.8.8')" 2>/dev/null || true
        redis-cli set nova:net_status "down" EX 600 > /dev/null 2>&1 || true
    fi
fi

# Always check Tailscale
TS_IP=$(tailscale ip -4 2>/dev/null || echo "")
if [ -z "$TS_IP" ] && [ "$CURRENT_STATE" == "up" ]; then
    echo "Tailscale down despite internet — forcing reconnect..."
    tailscale up --accept-routes --accept-dns=false 2>/dev/null || true
fi
NETWATCHDOG
chmod +x /usr/local/bin/nova-net-watchdog.sh

cat > /etc/systemd/system/nova-net-watchdog.service << 'NETSVC'
[Unit]
Description=NOVA Network Watchdog
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/nova-net-watchdog.sh
StandardOutput=journal
StandardError=journal
NETSVC

cat > /etc/systemd/system/nova-net-watchdog.timer << 'NETTIMER'
[Unit]
Description=NOVA Network Watchdog — every 2 minutes

[Timer]
OnBootSec=30
OnUnitActiveSec=2min
AccuracySec=15

[Install]
WantedBy=timers.target
NETTIMER

systemctl enable nova-net-watchdog.timer --now
echo "     ✅ Internet watchdog installed (every 2 min)"

# ── 8. Logrotate for NOVA logs ────────────────────────────────────────────────
echo "[8/8] Configuring log rotation..."
cat > /etc/logrotate.d/simonx << 'LOGROTATE'
/home/simon-hq/simon-hq/*.log /var/log/simonx/*.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
    sharedscripts
    postrotate
        systemctl reload nova-hud nova-mcpo simon-hq-api 2>/dev/null || true
    endscript
}
LOGROTATE
mkdir -p /var/log/simonx
chown simon-hq:simon-hq /var/log/simonx
echo "     ✅ Logrotate configured (14 days)"

# ── Install Python deps in nova-venv ──────────────────────────────────────────
echo ""
echo "[+] Installing Python packages in nova-venv..."
sudo -u simon-hq "$VENV/bin/pip" install -q \
    psycopg2-binary redis hiredis \
    2>&1 | tail -3
echo "     ✅ psycopg2 + redis client installed in nova-venv"

# ── Reload everything ─────────────────────────────────────────────────────────
echo ""
echo "[+] Reloading and restarting all services..."
systemctl daemon-reload
systemctl restart redis-server postgresql nginx 2>/dev/null || true
for svc in simon-chroma simon-hq-api nova-mcpo nova-hud nova-webui; do
    systemctl restart "$svc" 2>/dev/null && echo "     ↻ $svc" || echo "     ⚠  $svc restart failed"
    sleep 1
done

echo ""
echo "================================================================"
echo "  ✅ SIMON-X simon-hq Infrastructure Upgrade COMPLETE"
echo "================================================================"
echo ""
echo "  New services:"
echo "    • Redis        127.0.0.1:6379"
echo "    • nginx        ${TAILSCALE_IP:-YOUR_HQ_TAILSCALE_IP}:80 (proxy to all NOVA services)"
echo "    • PostgreSQL   127.0.0.1:5432 → simonx database"
echo ""
echo "  New timers:"
echo "    • nova-adb-watchdog.timer   (every 5 min — ADB reconnect)"
echo "    • nova-net-watchdog.timer   (every 2 min — internet monitor)"
echo ""
echo "  Run: systemctl list-timers | grep nova"
echo "================================================================"
