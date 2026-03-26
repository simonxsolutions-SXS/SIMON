# S.I.M.O.N. Network Configuration
**Simon-X Solutions | [OWNER_NAME]**
**Version: 4.3 | Last Updated: March 21, 2026**

---

## Hosts

| Host | Role | Tailscale IP | LAN IP | OS | Hardware |
|---|---|---|---|---|---|
| Your-MacBook | Mac (SIMON voice/tools) | YOUR_MAC_TAILSCALE_IP | DHCP | macOS | M5 MacBook Air 24GB RAM 1TB |
| simon-hq | HQ (LLM/memory/vision) | YOUR_HQ_TAILSCALE_IP | YOUR_HQ_LOCAL_IP | Ubuntu 24.04 LTS | i7, 33.4GB RAM, 982GB disk |

---

## VPN

**Provider:** Tailscale
**Network:** Simon-X private tailnet

Both machines are always connected via Tailscale. All SIMON → HQ communication uses Tailscale IPs (100.x.x.x) for security. LAN IP is available as fallback but not used in config.

**Check Tailscale status:**
```bash
# On Mac
tailscale status

# On HQ
sudo tailscale status

# Ping test Mac → HQ
tailscale ping YOUR_HQ_TAILSCALE_IP
```

---

## Ports

### Mac (Your-MacBook)

| Port | Service | Bound To | Purpose |
|---|---|---|---|
| 8765 | SIMON FastAPI | 0.0.0.0 | HUD, WebSocket, REST API |

### simon-hq

| Port | Service | Bound To | Purpose |
|---|---|---|---|
| 8200 | HQ FastAPI API | 0.0.0.0 | LLM chat, vision, memory, web |
| 8100 | ChromaDB | 0.0.0.0 | Vector memory store |
| 11434 | Ollama | 127.0.0.1 | LLM inference (local only) |
| 5432 | PostgreSQL | 127.0.0.1 | Relational DB (local only) |

Note: Ollama and PostgreSQL are bound to localhost on HQ. The HQ API (`:8200`) proxies requests to them. Direct external access to `:11434` or `:5432` requires SSH tunnel.

---

## SSH Access

```bash
# SSH to simon-hq
ssh user@YOUR_HQ_TAILSCALE_IP

# SSH with port forward (e.g., to access Ollama directly)
ssh -L 11434:localhost:11434 user@YOUR_HQ_TAILSCALE_IP
```

---

## API Keys and Authentication

### Ollama Cloud (Mac → Cloud)

Used by jarvis.py to send all tool-requiring requests to Mistral Large.

```
URL:   https://api.ollama.com
Key:   stored in config.json → ollama_cloud_key
Model: mistral-large-3:675b
```

### HQ API Key (Mac → simon-hq)

Used by jarvis.py to authenticate requests to the HQ API.

```
URL:   http://YOUR_HQ_TAILSCALE_IP:8200
Key:   stored in config.json → hq_api_key
```

The HQ API validates this key on every request. Without it, all HQ requests return 401.

---

## Health Check Endpoints

### SIMON (Mac)
```bash
curl http://localhost:8765/api/status
```
Returns: CPU, RAM, disk, IP, load average, time.

### HQ API
```bash
curl http://YOUR_HQ_TAILSCALE_IP:8200/health
```
Returns: Ollama status, ChromaDB status, CPU, RAM, warm models, uptime.

### Ollama (HQ — local only or via tunnel)
```bash
# On HQ directly
curl http://localhost:11434/api/tags
curl http://localhost:11434/api/ps   # loaded models

# From Mac via tunnel
ssh -L 11434:localhost:11434 user@YOUR_HQ_TAILSCALE_IP -N &
curl http://localhost:11434/api/ps
```

### ChromaDB (HQ)
```bash
curl http://YOUR_HQ_TAILSCALE_IP:8100/api/v1/heartbeat
```

---

## PostgreSQL (simon-hq)

```
Host:     localhost (on HQ) / YOUR_HQ_TAILSCALE_IP (via Tailscale, port must be forwarded)
Port:     5432
Database: simon_brain
User:     simon
Password: YOUR_DB_PASSWORD
```

**Connect from HQ:**
```bash
psql -U simon -d simon_brain
```

**Connect from Mac (via SSH tunnel):**
```bash
ssh -L 5432:localhost:5432 user@YOUR_HQ_TAILSCALE_IP -N &
psql -h localhost -U simon -d simon_brain
```

**Tables:**
- `contacts` — 167 contacts (migration pending)
- `memory` — 20 memory facts (migration pending)
- `messages_cache` — message history
- `session_log` — SIMON session records
- `research` — web research results
- `sync_state` — KB sync tracking

---

## systemd Services on simon-hq

```bash
# Status of all SIMON services
sudo systemctl status simon-hq-api simon-chroma ollama postgresql

# Individual controls
sudo systemctl start|stop|restart simon-hq-api
sudo systemctl start|stop|restart simon-chroma
sudo systemctl start|stop|restart ollama
sudo systemctl start|stop|restart postgresql

# Enable/disable autostart
sudo systemctl enable simon-hq-api
sudo systemctl disable simon-hq-api

# View live logs
sudo journalctl -u simon-hq-api -f
sudo journalctl -u simon-chroma -f
```

---

## Firewall

simon-hq uses UFW. The following ports are open:

```bash
# Check UFW status
sudo ufw status

# Expected open ports
22     (SSH)
8200   (HQ API — Tailscale only recommended)
8100   (ChromaDB — Tailscale only recommended)
```

Ollama (11434) and PostgreSQL (5432) are NOT open in UFW — they're localhost-only by design and accessed only via the HQ API proxy.

---

## Bandwidth Notes

**Normal operation (HQ online):**
- Mac → HQ: ~2–5KB per conversational request
- Mac → Cloud: ~10–50KB per tool request (includes tool definitions)

**Vision requests:**
- Mac → HQ: ~200–400KB per frame (base64 encoded JPEG)

**KB sync (every 5 min):**
- Mac → HQ ChromaDB: ~5–20KB (20 memory facts)
