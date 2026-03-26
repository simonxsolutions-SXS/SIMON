# S.I.M.O.N. Improvements Roadmap
**Simon-X Solutions | [OWNER_NAME]**
**Version: 4.5 | March 21, 2026**

> Research findings, recommended upgrades, mobile access plan, and known weaknesses.
> Prioritized by impact vs effort. Items marked 🔥 are highest priority.

---

## MOBILE ACCESS — USING SIMON ON YOUR PHONE

### Option 1 — PWA via Tailscale (Ready NOW, zero effort) 🔥
SIMON's HUD is already a web interface. Since your Pixel 9a is on Tailscale, you can open it in Chrome right now:

```
http://YOUR_MAC_TAILSCALE_IP:8765
```

On your phone: open Chrome → go to that URL → tap the three-dot menu → **Add to Home Screen**.
This creates a PWA (Progressive Web App) icon on your phone that launches SIMON like a native app.

**What works:** Full voice input (Chrome mic), text chat, all tools.
**Limitation:** Must be on Tailscale (you already are). Response is voice on the Mac's speakers, not phone.

### Option 2 — iOS/Android Shortcuts (Hours to build)
Apple Shortcuts and Android Tasker can call SIMON's REST API directly. One tap on your home screen → "Simon, I'm on my way home" with no app to open. Great for quick hands-free commands while driving.

### Option 4 — Dedicated Mobile View (2-3 days)
A mobile-optimized version of the HUD with larger buttons, swipe gestures, and a simplified UI designed for phone screens. Still lives at the same URL, just detects mobile and switches layouts.

---

## CURRENT WEAKNESSES

### 🔴 Critical
**1. Single point of failure — Mac crashes, everything stops.**
SIMON lives entirely on the Mac. If the Mac sleeps, crashes, or closes the lid, SIMON goes dark. The HUD becomes unreachable, tools stop working, and the Android bridge dies.

Fix: Move the FastAPI core to simon-hq and run the Mac as a thin client (just TTS/mic/camera). This is a major refactor but the right long-term architecture.

**2. API keys stored in plaintext config.json.**
The Ollama Cloud API key, HQ API key, and other credentials sit in `config.json` as plain text. If that file leaks, the keys leak.

Fix: Move secrets to macOS Keychain using `security` CLI tool or Python `keyring` library. ~2 hours to implement.

**3. No authentication on the HUD.**
Anyone on your local network who knows port 8765 can talk to SIMON. On home WiFi this is fine, but if you ever connect to a coffee shop network or work WiFi it's exposed.

Fix: Add a simple token-based auth header check in jarvis.py + a login page on the HUD. ~3 hours.

### 🟡 Medium
**4. No persistent conversation history.**
SIMON forgets every conversation the moment you close the browser tab. There's no "Simon, remember what we talked about last Tuesday" capability.

Fix: Store conversation logs in ChromaDB on simon-hq. Already have the memory endpoints. Just need to write to them after each session. ~4 hours.

**5. ADB drops when you leave home WiFi.**
On home WiFi, ADB uses `YOUR_ANDROID_LOCAL_IP`. When you leave, it fails over to Tailscale `YOUR_ANDROID_TAILSCALE_IP`. But if Tailscale drops momentarily (phone sleep, network switch), ADB disconnects and doesn't auto-recover until the next `_bg_android_monitor` cycle (3 min).

Fix: Add smarter ADB watchdog in android_bridge — detect disconnect on every tool call and immediately attempt reconnect before failing.

**6. LM Studio requires manual start.**
LM Studio doesn't auto-start with simon-hq. If simon-hq reboots, LM Studio is offline until someone opens the GUI and clicks Start Server.

Fix: Use LM Studio's CLI mode or switch to Ollama for serving the same models (Ollama auto-starts). I can migrate Mistral 7B from LM Studio into Ollama on simon-hq — then it auto-starts and SIMON can use it without any manual steps.

**7. No wake word detection.**
SIMON only listens when you click the mic button in the HUD. True "Hey Simon" wake word would make it genuinely ambient.

Fix: **Porcupine** (Picovoice) is the best option — runs on Apple Silicon, free for personal use, ~5ms latency. Custom wake word "Hey Simon" takes 15 minutes to train on their console. This would be a huge quality-of-life upgrade.

### 🟢 Minor
**8. Voice is good but not great.**
Piper with Alan voice is decent but clearly synthetic. Newer TTS models are dramatically better.

Fix: **Kokoro TTS** (82M params, 97ms TTFB) or **MLX-Audio** (runs natively on M5) would sound significantly more natural. Both are open source.

**9. No streaming responses.**
SIMON waits for the full LLM response before speaking. For long answers, there's a dead silence before anything plays.

Fix: Implement streaming from Ollama API + chunked TTS — start speaking the first sentence while the rest is still generating. Cuts perceived latency from ~3s to ~0.5s.

**10. Health escalation only notifies, doesn't self-heal automatically.**
Currently, after 5 minutes of a service being DOWN, SIMON sends a macOS notification. The healer doesn't run automatically.

Fix: Wire `simon_healer.full_repair_run()` directly into the health escalation so it auto-repairs without you having to say "Simon, repair yourself." ~30 min change.

---

## RECOMMENDED UPGRADES — PRIORITIZED

### Phase 1 — Quick Wins (This week)
| Upgrade | Effort | Impact |
|---------|--------|--------|
| Add SIMON PWA to phone home screen | 5 min | High |
| Move Mistral 7B from LM Studio to Ollama | 1 hour | High |
| Auto-trigger healer on health escalation | 30 min | Medium |
| Store API keys in macOS Keychain | 2 hours | High (security) |

### Phase 2 — This Month
| Upgrade | Effort | Impact |
|---------|--------|--------|
| Wake word detection (Porcupine "Hey Simon") | 1 day | Very High |
| Streaming TTS (Kokoro or MLX-Audio) | 1-2 days | High |
| Persistent conversation history via ChromaDB | 4 hours | High |

### Phase 3 — Next Quarter
| Upgrade | Effort | Impact |
|---------|--------|--------|
| Move SIMON core to simon-hq (eliminate single point of failure) | 1-2 weeks | Critical |
| RAG improvements: hybrid search + reranking on ChromaDB | 2-3 days | Medium |
| HUD authentication layer | 3 hours | Medium (security) |
| Mobile-optimized HUD | 2-3 days | Medium |

---

## NEW AI TECH WORTH WATCHING

**Kokoro TTS** — 82M param model, 97ms time-to-first-byte, runs on Apple Silicon. Far better voice quality than Piper. Direct drop-in for our TTS layer.
→ https://github.com/hexgrad/kokoro

**MLX-Audio** — Apple's own framework-based audio models, built for M-series chips. Natively optimized for your M5 MacBook Air.
→ https://github.com/Blaizzy/mlx-audio

**Porcupine Wake Word** — local, private, Apple Silicon native. "Hey Simon" with custom wake word. Free personal tier.
→ https://picovoice.ai/platform/porcupine/

**LangGraph** — best framework for building multi-step agentic workflows. If SIMON ever needs to autonomously chain 5+ actions together (research + summarize + email + calendar), LangGraph handles the state machine.
→ https://github.com/langchain-ai/langgraph

**Mem0** — persistent AI memory layer. Gives SIMON the ability to remember preferences, facts, and patterns across sessions. Plugs into any LLM.
→ https://github.com/mem0ai/mem0

**Hybrid Search + Reranking on ChromaDB** — adding a reranker (like Cohere Rerank or a local cross-encoder) to the knowledge base search gives 10-30% better retrieval accuracy. This matters as the KB grows.

---

## SIMON-HQ CLEANUP (run these manually with sudo)

These services on simon-hq are unnecessary and waste RAM/CPU:

```bash
# SSH into simon-hq first: ssh simon-hq

# ModemManager — no modem, not needed
sudo systemctl disable --now ModemManager

# kerneloops — crash reporter, not needed in production
sudo systemctl disable --now kerneloops

# GNOME Remote Desktop — not using it (using SSH instead)
sudo systemctl disable --now gnome-remote-desktop

# Save the commands for later
sudo systemctl status ModemManager kerneloops gnome-remote-desktop
```

Services to KEEP: `ollama`, `simon-hq-api`, `simon-chroma`, `tailscaled`, `postgresql@16-main`, `nvidia-persistenced`

---

*Last updated: March 21, 2026 — S.I.M.O.N. v4.5*
*Next review: When Phase 1 upgrades are complete*
