# N.O.V.A. — HQ Mobile Assistant
**Simon-X Solutions | [OWNER_NAME]**
**Version: 1.0 | March 22, 2026**

> N.O.V.A. (Network Operations & Voice Assistant) is the simon-hq counterpart to SIMON.
> Where SIMON lives on your Mac, NOVA lives on simon-hq — always on, always reachable,
> even when your MacBook is in your bag.

---

## What NOVA Is

NOVA runs on simon-hq and provides:

**Open WebUI** (port 3000) — A full ChatGPT-like web interface connected to all your Ollama models and LM Studio. Works as a PWA on your phone. Access via Tailscale from anywhere.

Uses the same NOVA persona: same knowledge of your infrastructure, same routing logic, same access to qwen2.5:7b, mistral, DeepSeek R1 via LM Studio.

---

## Access Points

| Method | URL / App | Requires Tailscale? |
|--------|-----------|---------------------|
| Open WebUI (phone PWA) | `http://YOUR_HQ_TAILSCALE_IP:3000` | Yes |
| Open WebUI (browser) | `http://YOUR_HQ_TAILSCALE_IP:3000` | Yes |

---

## Files on simon-hq

```
/home/simon-hq/simon-hq/
├── nova_config.json         ← NOVA config (URLs, allowed users)
├── start_nova.sh            ← Launch Open WebUI in background
├── simon_brain.md           ← Shared context (used by HQ API)
└── nova.log                 ← Open WebUI logs

/home/simon-hq/nova-venv/   ← Python venv with open-webui + dependencies
/home/simon-hq/nova-data/   ← Open WebUI data (users, settings, chat history)

/tmp/
└── nova-webui.service       ← systemd unit (copy to /etc/systemd/system/)
```

---

## Open WebUI Setup (First Time)

### Step 1 — Verify it installed

```bash
ssh simon-hq
/home/simon-hq/nova-venv/bin/open-webui --version
```

### Step 2 — Start it

```bash
cd /home/simon-hq/simon-hq
./start_nova.sh daemon
```

Logs: `tail -f /home/simon-hq/simon-hq/nova.log`

### Step 3 — Open in browser

On your Pixel 9a or any device on Tailscale:
```
http://YOUR_HQ_TAILSCALE_IP:3000
```

First time: create an admin account (local, not sent anywhere).

### Step 4 — Connect Ollama

Open WebUI auto-detects Ollama at `http://localhost:11434`. All 6 models will appear.

To also connect LM Studio:
- Settings → Connections → Add OpenAI-compatible API
- URL: `http://127.0.0.1:1234/v1`
- API Key: `lm-studio` (any string works)

### Step 5 — Set the NOVA System Prompt

In Open WebUI:
- Click your profile → Settings → System Prompt
- Paste the contents of `/home/simon-hq/simon-hq/simon_brain.md`
- Change "You are S.I.M.O.N." to "You are N.O.V.A."

### Step 6 — Add to Phone Home Screen (PWA)

On Pixel 9a Chrome:
1. Open `http://YOUR_HQ_TAILSCALE_IP:3000`
2. Tap the three-dot menu → "Add to Home Screen"
3. Name it: **NOVA**
4. Done — launches like a native app

### Step 7 — Auto-start on Boot

```bash
# On simon-hq (requires sudo — run this manually):
sudo cp /tmp/nova-webui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable nova-webui
sudo systemctl start nova-webui
sudo systemctl status nova-webui
```

---

## Managing NOVA Services

```bash
# Open WebUI
sudo systemctl status nova-webui
sudo systemctl restart nova-webui
tail -f /home/simon-hq/simon-hq/nova.log
```

---

## Quick Manual Restart (no sudo needed)

```bash
# Kill and restart Open WebUI
pkill -f "open-webui serve"
cd /home/simon-hq/simon-hq && ./start_nova.sh daemon
```

---

## NOVA vs SIMON — Key Differences

| Feature | SIMON (Mac) | NOVA (simon-hq) |
|---------|-------------|-----------------|
| Hardware | M5 MacBook Air | i7 33GB RAM + NVIDIA GPU |
| Voice TTS | Yes (Piper/Reed) | No |
| Microphone | Yes (HUD) | No |
| Web HUD | Yes (port 8765) | Open WebUI (port 3000) |
| Mobile access | Tailscale only | PWA via Tailscale |
| AppleScript tools | Yes (Mail, Messages) | No |
| Android ADB | Yes | No |
| Always-on | No (Mac must be awake) | Yes (server) |
| LLM routing | Cloud + HQ | Local only (Ollama + LM Studio) |
| Shared knowledge | simon_brain.md | simon_brain.md (same file) |

---

## Updating NOVA

### Update the brain (both SIMON and NOVA read this)
Edit `/home/simon-hq/simon-hq/simon_brain.md` on simon-hq.
Then: `curl -X POST http://127.0.0.1:8200/reload_brain`

### Update Open WebUI
```bash
/home/simon-hq/nova-venv/bin/pip install --upgrade open-webui
sudo systemctl restart nova-webui
```

---

*Last updated: March 22, 2026 — NOVA v1.0*
*SIMON v4.5 | Simon-X Solutions*
