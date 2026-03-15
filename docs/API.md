# API Reference — S.I.M.O.N.

> REST endpoints and WebSocket protocol for the S.I.M.O.N. core server.

---

## Server

| Property | Value |
|---|---|
| Host | `localhost` |
| Port | `8765` (configurable in `config.json`) |
| Base URL | `http://localhost:8765` |
| WebSocket | `ws://localhost:8765/ws/{sessionId}` |

---

## REST Endpoints

### `GET /api/status`

Returns current system vitals.

**Response:**
```json
{
  "time": "11:30:45",
  "date": "Saturday, March 14, 2026",
  "cpu": 18.5,
  "mem_gb": 14.2,
  "mem_max": 16,
  "disk_used": "12G",
  "disk_avail": "450G",
  "disk_pct": 2,
  "ip": "10.0.0.X",
  "load": "2.10 / 2.30 / 2.40"
}
```

**Memory calculation:** Uses `vm_stat` + `memory_pressure`:
```
Memory Used = Total RAM − Free Pages − File-backed Pages
```
This matches Activity Monitor's "Memory Used" display exactly. File-backed pages (reclaimable disk cache) are excluded — they are not "used" by any app.

---

### `GET /api/calendar`

Returns today's calendar events formatted for the HUD.

**Response:**
```json
[
  {"time": "9:00 AM", "title": "Team Standup"},
  {"time": "2:00 PM", "title": "Client Call"}
]
```

Returns `[]` if no events today.

---

### `GET /api/emails`

Returns unread email counts per account.

**Response:**
```json
{
  "Work": 12,
  "Personal": 45,
  "iCloud": 0
}
```

> Account names are returned as-is from Mail.app — they match whatever your accounts are named in Mail preferences.

---

### `POST /api/send_health_report`

Triggers the health check system to read the latest report and send it as an iMessage to the configured phone number.

**Response:**
```json
{"status": "sent"}
```

---

### `POST /api/send_notification`

Send a custom iMessage.

**Request body:**
```json
{
  "message": "Custom notification text",
  "phone": "+13125551234"
}
```

**Response:**
```json
{"status": "sent"}
```

---

## WebSocket Protocol

### Connection

```
ws://localhost:8765/ws/{sessionId}
```

`sessionId` can be any unique string. The HUD uses `Date.now()` as the ID:
```js
const WS_URL = `ws://${location.host}/ws/${Date.now()}`;
```

### Client → Server Messages

#### `chat`
Send a user message to SIMON.

```json
{"type": "chat", "text": "What's on my calendar today?"}
```

#### `ping`
Keep-alive. Sent every 5 seconds.

```json
{"type": "ping"}
```

#### `clear`
Clear the conversation history and reset context.

```json
{"type": "clear"}
```

---

### Server → Client Messages

#### `greeting`
Sent once on connection. The startup message SIMON speaks.

```json
{
  "type": "greeting",
  "text": "S.I.M.O.N. version four point two online. Good morning..."
}
```

#### `stats`
System vitals update. Sent every ~5 seconds.

```json
{
  "type": "stats",
  "data": {
    "time": "11:30:45",
    "date": "Saturday, March 14, 2026",
    "cpu": 18.5,
    "mem_gb": 14.2,
    "mem_max": 16,
    "disk_used": "12G",
    "disk_avail": "450G",
    "disk_pct": 2,
    "ip": "10.0.0.X",
    "load": "2.10 / 2.30 / 2.40"
  }
}
```

#### `thinking`
SIMON has received the message and is generating a response.

```json
{"type": "thinking"}
```

HUD: activates processing overlay with "THINKING" label.

#### `tool_use`
A tool is being called. Updates the processing overlay label.

```json
{"type": "tool_use", "tool": "get_todays_events"}
```

HUD: overlay updates to `⚡ GET TODAYS EVENTS`.

#### `chunk`
A streaming response chunk.

```json
{"type": "chunk", "text": "Nothing scheduled"}
```

Multiple chunks are sent sequentially. The HUD concatenates them.

#### `done`
Response generation complete.

```json
{"type": "done", "text": "Nothing scheduled for today — a rare commodity."}
```

HUD: processing overlay hides. Brain enters `speaking` state.

#### `speech_done`
TTS playback has finished.

```json
{"type": "speech_done"}
```

HUD: starts 800ms settle timer, then enters `listening` state.

#### `cleared`
Session history has been cleared (response to `clear` message).

```json
{"type": "cleared"}
```

---

## Message Flow Diagram

```
HUD                          Server                         LLM / Tools
 │                              │                               │
 │── {type:"chat", text:"..."}─►│                               │
 │                              │── POST /api/chat (no stream) ─►│
 │◄── {type:"thinking"} ────────│                               │
 │                              │◄── {tool_calls: [...]} ───────│
 │◄── {type:"tool_use",...} ────│                               │
 │                              │── execute_tool() ─────────────►│
 │                              │◄── tool result ───────────────│
 │                              │── POST /api/chat (stream) ───►│
 │◄── {type:"chunk", text} ─────│◄── streaming chunks ──────────│
 │◄── {type:"chunk", text} ─────│                               │
 │◄── {type:"chunk", text} ─────│                               │
 │◄── {type:"done", text} ──────│◄── done                       │
 │                              │── Piper TTS ──────────────────►│
 │                              │   afplay WAV                  │
 │◄── {type:"speech_done"} ─────│                               │
 │    (800ms settle)            │                               │
 │    → listening state         │                               │
```

---

## Error Handling

The server never crashes the WebSocket on errors. Tool failures return a string describing what went wrong, which SIMON speaks naturally:

```python
# Tool error handling pattern
async def execute_tool(tool_call: dict) -> str:
    try:
        return await tool_implementation(**args)
    except Exception as e:
        return f"Tool {name} error: {e}"
```

SIMON will say something like:
> "I'm afraid I wasn't able to reach the calendar. Shall I try again?"

---

## Building an Integration

To build a custom client that connects to S.I.M.O.N.:

```python
import asyncio, json, websockets

async def simon_client():
    uri = "ws://localhost:8765/ws/my-custom-client"
    
    async with websockets.connect(uri) as ws:
        # Wait for greeting
        greeting = json.loads(await ws.recv())
        print(f"SIMON: {greeting['text']}")
        
        # Send a command
        await ws.send(json.dumps({"type": "chat", "text": "System check"}))
        
        # Collect response
        response_parts = []
        async for message in ws:
            msg = json.loads(message)
            
            if msg["type"] == "thinking":
                print("[Processing...]")
            elif msg["type"] == "tool_use":
                print(f"[Running tool: {msg['tool']}]")
            elif msg["type"] == "chunk":
                response_parts.append(msg["text"])
                print(msg["text"], end="", flush=True)
            elif msg["type"] == "done":
                print()
                break
            elif msg["type"] == "speech_done":
                break  # ready for next command

asyncio.run(simon_client())
```
