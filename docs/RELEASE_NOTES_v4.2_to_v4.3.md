# S.I.M.O.N. v4.2 → v4.3 Release Notes
**Simon-X Solutions | [OWNER_NAME]**
**Date: March 21, 2026**

---

## Summary

This was a major stability and reliability session. The primary issues resolved:

1. SIMON was listening but not responding to requests — even with HQ online
2. RAM usage was sitting at ~17GB at idle, leaving little headroom
3. SIMON would hang during startup and never launch in the browser
4. No clean way to start/stop/restart the system
5. Several tool argument errors causing silent failures

---

## Bug Fix 1 — "Listening But Not Responding" (Critical)

### Root Cause

`llama3.1:8b` on simon-hq was receiving all 21 tool definitions with every chat request. That model is not fine-tuned for tool calling — it would misroute every request to `hq_ask` instead of the correct tool.

Evidence from the log:
```
[TOOL] hq_ask({"function": "hq_ask", "prompt": "read me my messages"})
[TOOL] hq_ask → This conversation has just started. There are no messages to read yet.
```

Instead of calling `get_recent_messages`, SIMON was calling `hq_ask` with the user's raw sentence as a prompt. That returned garbage, and SIMON went silent.

### Fix

Introduced `_hq_chat_simple()` — a new HQ chat function that sends **NO tools** in the payload. HQ is now used only for pure conversational responses. All tool calls go exclusively to Mistral Large via Ollama Cloud.

New routing logic added:

```python
def _is_conversational(user_msg: str) -> bool:
    """Returns True if message needs no tools — safe to route to HQ."""

def _needs_tools(user_msg: str, sess: Session) -> bool:
    """Returns True if message requires tool execution — must use Cloud."""
```

---

## Architecture Change — Three-Tier LLM Routing

### Before (v4.2)
Every request went to HQ with all tools. HQ misrouted them. Cloud was fallback only.

### After (v4.3)

**Tier 1 — HQ `llama3.1:8b` (conversational, no tools):**
- Greetings, small talk, "good morning", "what time is it", "thank you"
- Response time: ~1 second
- Zero tool misrouting — tools not sent

**Tier 2 — Ollama Cloud `mistral-large-3:675b` (all tool calls):**
- Calendar, iMessage, email, reminders, shell, contacts, vision, memory
- Mistral Large handles tool calling correctly
- Always used when tools are needed

**Tier 3 — MLX `Mistral-7B-Instruct-v0.3-4bit` (emergency only):**
- Only loads if HQ has been offline for 60+ seconds
- Handles simple conversational fallback
- Model is NOT loaded at startup — saves 4.5GB RAM

---

## Bug Fix 2 — HQ Bridge Blocking Startup (5 Minutes)

### Root Cause

`hq_bridge.py` v2.0 ran a startup handshake that retried HQ for up to 5 minutes synchronously. During that window SIMON could not respond to anything — the whole process was blocked.

### Fix — hq_bridge.py v2.1

The handshake now runs as a fire-and-forget background task:
- `asyncio.create_task(_startup_handshake())` — non-blocking
- Each attempt has a 4-second timeout
- SIMON greets the owner in under 3 seconds regardless of HQ
- Background retries continue quietly for up to 10 minutes

Log changed from:
```
[HQ Bridge] Startup handshake attempt 1/10: ...
```
To:
```
[HQ Bridge] Background handshake started (non-blocking)
```

---

## Bug Fix 3 — Python 3.11 Syntax Error (jarvis.py line 640)

### Error
```
SyntaxError: f-string expression part cannot include a backslash
```

### Location
`tool_vision_detect()` in jarvis.py

### Broken Code
```python
return f"Detected in {result['ms']}ms: {', '.join(f'{o[\"label\"]} ({o[\"conf\"]:.0%})' for o in objs[:8])}."
```

### Fixed Code
```python
labels = ", ".join(f"{o['label']} ({o['conf']:.0%})" for o in objs[:8])
return f"Detected in {result['ms']}ms: {labels}."
```

Python 3.11 does not allow backslash-escaped quotes inside f-string expressions. The fix extracts the inner comprehension to a named variable first.

---

## RAM Optimization — 17GB → 10–11GB Idle

### What Was Eating RAM

| Component | RAM | Issue |
|---|---|---|
| MLX Mistral 7B 4-bit | ~4.5 GB | Pre-warmed at startup, rarely needed |
| Moondream2 | ~3.5 GB | Pre-warmed at startup, HQ handles vision |
| DeepFace + TensorFlow | ~1.8 GB | Loaded even when no face ID running |
| macOS baseline | ~3.2 GB | Fixed |
| YOLO26n | ~0.15 GB | Tiny, keep pre-warming |
| Piper TTS | ~0.1 GB | Required, keep |

**Total savings: 6–8 GB**

### Changes Made

**MLX:** Module still imports (intent classifier needs it), but the 4.5GB model file only loads if HQ has been offline 60+ seconds. New background task `_bg_mlx_prewarm` polls HQ and triggers load only when needed.

**Moondream:** Removed from `_bg_vision_prewarm`. YOLO only pre-warms now. Moondream loads on demand only when vision_ask is called and HQ is offline.

**DeepFace:** Already technically lazy-loaded, but chain imports were triggering early. Now strictly on-demand — only imports inside `tool_vision_identify_person()` when actually called.

---

## Bug Fix 4 — Tool Argument Sanitizer

Some LLMs wrap arguments in extra layers when returning tool calls. Added sanitizer to `execute_tool()`:

```python
# Handle malformed args: {"function": "x", "args": {...}}
if "args" in args and isinstance(args.get("args"), dict):
    args = args["args"]

# Strip non-parameter keys some LLMs include
for bad_key in ["function", "parameters"]:
    args.pop(bad_key, None)
```

This fixed silent failures where tools received unexpected keyword arguments and crashed.

---

## Bug Fix 5 — ifconfig Not Found

The `local_network_info` tool was failing:
```
Network interface error: [Errno 2] No such file or directory: 'ifconfig'
```

**Cause:** launchd launches SIMON with a minimal PATH that doesn't include `/sbin` or `/usr/sbin`.

**Fix:** Updated PATH in both `jarvis.py` tool shell runner and `com.simonx.simon.plist`:
```
/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
```

---

## New Feature — Auto-Heal for Mail and Messages

Added to `_bg_health_check()`. When the health monitor detects Mail.app or Messages.app as DOWN, it now automatically reopens them without waiting for [OWNER] to notice:

```python
for r in down:
    if "Messages" in r.name:
        subprocess.run(["open", "-a", "Messages"], capture_output=True)
    elif "Mail" in r.name:
        subprocess.run(["open", "-a", "Mail"], capture_output=True)
```

---

## New Feature — Startup/Stop/Restart Scripts

Three scripts added to `~/Projects/AI-Projects/jarvis/`:

### start_simon.sh
1. Kills any running jarvis.py, afplay, say processes
2. Clears port 8765
3. Runs `python3.11 -m py_compile jarvis.py` — syntax check before launch
4. If syntax check fails: prints error, exits cleanly (no hang)
5. Launches SIMON with correct environment variables
6. Waits up to 20 seconds for port 8765 to bind
7. Opens Google Chrome to `http://localhost:8765`

### stop_simon.sh
1. `pkill -f jarvis.py`
2. `pkill -9 afplay`
3. `pkill -9 say`
4. Clears port 8765
5. Confirms process is dead; force-kills if still running

### restart_simon.sh
Calls `stop_simon.sh` then `start_simon.sh` in sequence.

---

## Files Changed in This Session

| File | Version | Change |
|---|---|---|
| `jarvis.py` | v4.2 → v4.3 | Three-tier routing, syntax fix, RAM opts, arg sanitizer, auto-heal, PATH fix |
| `plugins/hq_bridge.py` | v2.0 → v2.1 | Non-blocking startup handshake |
| `LaunchAgents/com.simonx.simon.plist` | — | PATH updated to include `/usr/sbin:/sbin` |
| `start_simon.sh` | NEW | Syntax-check + launch + Chrome |
| `stop_simon.sh` | NEW | Clean shutdown |
| `restart_simon.sh` | NEW | Stop + start |
| `docs/` | NEW | This documentation folder |

---

## What Was NOT Changed

- `simon_kb.py` — SQLite knowledge base, untouched
- `simon_security.py` — security guard, untouched
- `simon_mlx.py` — MLX module, untouched (behavior changed by caller)
- `vision/simon_vision.py` — vision engine, untouched
- `simon_tool_health.py` — health monitor, untouched
- `hud.html` — HUD interface, untouched
- `plugins/network_tools.py` — network tools, untouched
- `plugins/weather.py` — weather plugin, untouched
- `config.json` — configuration, untouched
- PostgreSQL setup on simon-hq — untouched (migration still pending)

---

## Remaining Known Issues

| Issue | Severity | Status |
|---|---|---|
| MLX Fast Path shows DEGRADED in health check | Low | Expected — model intentionally not pre-loaded |
| Moondream shows DEGRADED in health check | Low | Expected — on-demand load by design |
| TOKENIZERS_PARALLELISM warnings flood log | Low | Suppressed in watch commands, harmless |
| PostgreSQL migration SQLite → Postgres pending | Medium | Not started, system runs on SQLite |
| Self-healing engine (simon_healer.py) | Medium | Partially written, not deployed |
| OpenClaw/multi-agent orchestration | Low | Roadmap item for Simon-X product build |
