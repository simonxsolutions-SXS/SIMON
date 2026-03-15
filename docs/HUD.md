# HUD Reference — S.I.M.O.N.

> The browser-based heads-up display. A single HTML file running in Chrome, communicating with the core server over WebSocket.

---

## Overview

The HUD (`hud.html`) is a self-contained, single-file application. No build step, no npm, no dependencies except Google Fonts (can be made offline).

Open at: `http://localhost:8765`

```
┌─────────────────────────────────────────────────────────────────┐
│  S.I.M.O.N. // SIMON-X          11:30:45          ONLINE  USER  │
│  SYSTEMS INTELLIGENCE...        Saturday, March 14, 2026        │
├──────────────┬───────────────────────────────────┬──────────────┤
│ // SYSTEM    │                                   │ // TODAY     │
│ VITALS       │                                   │              │
│ CPU     21%  │         ◉◉◉◉◉◉◉◉◉◉◉             │ // VOICE     │
│ MEMORY  15GB │      ◉◉◉◉◉◉◉◉◉◉◉◉◉◉◉            │ COMMANDS     │
│ DISK  12/911 │    ◉◉◉◉◉◉◉◉◉◉◉◉◉◉◉◉◉◉           │              │
│              │       ◉◉◉◉◉◉◉◉◉◉◉◉◉             │ // ACTIVITY  │
│ // INBOX     │         ◉◉◉◉◉◉◉◉◉               │ LOG          │
│ Work      12 │                                   │              │
│ Personal  45 │         M O N I T O R I N G       │              │
│ Other      3 │         SAY "SIMON" TO ACTIVATE   │              │
│ iCloud     0 │                                   │              │
│              │   ┌────────────────────────────┐  │              │
│ // SYSTEMS   │   │ S.I.M.O.N.                 │  │              │
│ ● Ollama     │   │ Ready for your command...   │  │              │
│ ● MCP Stack  │   └────────────────────────────┘  │              │
│ ● Piper TTS  │                                   │              │
│ ○ iMessage   │                                   │              │
│ // NETWORK   │                                   │              │
│ IP: 10.0.0.1 │                                   │              │
│ // SESSION   │                                   │              │
│ UPTIME 00:05 │                                   │              │
│ LOAD  2.1/.. │                                   │              │
├──────────────┴───────────────────────────────────┴──────────────┤
│  MONITORING  │ Type a command and press Enter...    SEND MUTE CLR│
└─────────────────────────────────────────────────────────────────┘
```

---

## Layout Panels

### Left Panel (System)

| Section | Data | Update Frequency |
|---|---|---|
| System Vitals | CPU %, Memory GB, Disk used/free | Every WebSocket stats push (~5s) |
| Inbox | Unread count per email account | Every 90 seconds |
| Systems | Service status dots | Static (set at startup) |
| Network | Local IP address | Every stats push |
| Session | HUD uptime counter, load average | Uptime: every second; Load: every stats push |
| Wake Sensitivity | Slider for wake word confidence threshold | User-adjustable |

### Center Panel (Brain)

The neural network canvas occupies the full height of the center column.

**52 nodes** arranged in 5 clusters:
```
   Left cluster  Right cluster
      ●●●●●          ●●●●●
   ●●●●●●●●●      ●●●●●●●●●
    ●●●●●●●         ●●●●●●●
      Bottom         Side
       ●●●●●        ●●●●●●
```

Edges connect nodes within ~80px of each other. Animated packets travel along edges at speed proportional to activity level.

**MONITORING** label and wake hint sit directly below the brain. Chat box is below that.

### Right Panel (Intelligence)

| Section | Content |
|---|---|
| Today | Calendar events (loads on startup, Matrix green theme) |
| Voice Commands | Quick reference list of common commands |
| Activity Log | Live console stream — color-coded by severity |

---

## Brain States

The brain canvas responds to S.I.M.O.N.'s current state:

| State | Color | Packet Speed | Glow |
|---|---|---|---|
| `sleeping` | Dark blue (`#004466`) | Very slow | Dim |
| `wake` (monitoring) | Medium blue (`#0077aa`) | Slow | Soft |
| `listening` | Green (`#00bb55`) | Medium | Active |
| `processing` | Gold/amber (`#cc9900`) | Fast | Bright |
| `speaking` | Bright blue (`#2266ee`) | Medium-fast | Pulsing |
| `muted` | Dark red (`#551122`) | Very slow | Dim red |

Word pulse: each spoken word chunk triggers a burst of 8 packets and random node activations, creating a visual "thinking" effect synchronized with speech.

---

## State Machine

```
     ┌──────────────────────────────────────────┐
     │                                          │
     ▼                                          │
  sleeping ──── wake word ────► wake ─────────►─┤
     ▲                           │               │
     │                     any speech            │
     │                           ▼               │
     │                       listening           │
     │                           │               │
     │                      final text           │
     │                           ▼               │
     │                       processing ─────────┤
     │                           │               │
     │                    stream starts          │
     │                           ▼               │
     │                       speaking            │
     │                           │               │
     │                      speech_done          │
     │                    800ms settle           │
     │                           │               │
     │                    conversing?            │
     │                    Yes ───▼ No ──► wake   │
     │                       listening ──────────┘
     │
     └──── kill phrase ──────────────────────────┘
```

**Key behavior: 800ms settle window**

After TTS finishes playing (`speech_done`), the HUD stays in `speaking` state for 800ms. During this window, any speech results from the SR engine are discarded. This prevents SIMON's own voice from being picked up by the microphone and queued as a command.

After 800ms, state switches to `listening` (waveform turns green) — signaling SIMON is waiting for your response.

---

## Activity Log

The Activity Log in the right panel is a real console interceptor. Every `console.log`, `console.info`, `console.warn`, and `console.error` call is routed to the HUD in addition to the browser console.

**Color coding:**

| Level | Color | Used for |
|---|---|---|
| `sys` | Bright cyan `#00ffe7` | SIMON internal events (WS connect, SR status, tool calls) |
| `info` | Cyan `#00d4ff` | Periodic vitals snapshots, system info |
| `warn` | Gold `#ffd060` | Non-critical warnings |
| `error` | Red `#ff3355` | Errors and unhandled promise rejections |
| `log` | Matrix green `#00ff41` | General application logs |

**Vitals snapshot** (every 30 seconds):
```
10:30:18 [INFO] vitals │ CPU:18.5% MEM:14.2/24GB DISK:12G LOAD:2.1 / 2.3 / 2.4
```

**Unhandled errors are caught automatically:**
```js
window.addEventListener('error', e => _writeLog('error', [e.message]));
window.addEventListener('unhandledrejection', e => _writeLog('error', [String(e.reason)]));
```

Maximum 200 entries — oldest are pruned automatically.

---

## Voice System

### Wake Word

Default wake word: **"Simon"**

The SR engine runs continuously when the tab is focused. When "Simon" is detected with confidence ≥ `wakeThresh` (default 0.55), SIMON enters conversation mode.

**Wake sensitivity slider:** Adjust in the left panel under `// SYSTEMS`.
- Sensitive (left): fires on low-confidence detections — more responsive, more false positives
- Strict (right): requires high confidence — fewer false positives, may miss quiet speech

### Conversation Mode

Once activated, SIMON stays in conversation mode for 90 seconds of idle time. You can speak freely without saying "Simon" again between turns.

**Kill phrases** (exit conversation mode):
- "Give me a second"
- "Stand by"
- "That's all"
- "Go to sleep"
- "Stop listening"
- "Goodbye Simon"
- "Goodnight Simon"

### Tab Focus Requirement

Chrome's Web Speech API pauses when the tab loses focus. The HUD handles this gracefully:
- When the tab goes to background: SR stops (no abort spam)
- When the tab comes back to focus: SR resumes automatically within 400ms
- Activity log shows: `Tab focused — resuming mic`

---

## Processing Overlay

When SIMON is working on a request, an overlay appears above the chat box:

```
        ◎  (spinning ring)
    ⚡ GET TODAYS EVENTS...   (updates per tool)
    ████████░░░░░░░░░░░░░░   (animated progress bar)
```

**States:**
- `THINKING` — model is generating (before any tool call)
- `⚡ TOOL NAME` — a specific tool is running (updates per tool)
- Hidden — as soon as the first response chunk streams in

---

## Bottom Bar Controls

| Control | Function |
|---|---|
| Status pill (left) | Current state: MONITORING / 🔴 LISTENING / ⚡ PROCESSING / 🔊 SPEAKING / 🔇 MUTED |
| Text input | Type commands manually and press Enter |
| SEND | Submit typed command |
| MUTE / UNMUTE | Toggle voice input. Click brain canvas for same effect. |
| CLEAR | Clear chat history and reset conversation context |

---

## WebSocket Protocol

The HUD communicates with the core server over a persistent WebSocket at `ws://localhost:8765/ws/{sessionId}`.

See [API Reference](API.md) for full protocol documentation.

---

## Customization

### Changing the Voice

1. Download a Piper voice model from [HuggingFace](https://huggingface.co/rhasspy/piper-voices)
2. Place `.onnx` and `.onnx.json` in the `voices/` directory
3. Update `PIPER_MODEL` in `jarvis.py`:
```python
PIPER_MODEL = str(BASE / "voices" / "your-voice-model.onnx")
```

### Adjusting Wake Word Threshold

In the HUD: use the Wake Sensitivity slider.

In code (`hud.html`):
```js
let wakeThresh = 0.55;  // 0.0 = accept everything, 1.0 = require perfect confidence
```

### Making the HUD Offline

Replace the Google Fonts import with locally downloaded fonts:
```html
<!-- Replace this: -->
@import url('https://fonts.googleapis.com/css2?family=Orbitron...');

<!-- With this (after downloading fonts): -->
@font-face { font-family: 'Orbitron'; src: url('fonts/Orbitron.woff2'); }
```
