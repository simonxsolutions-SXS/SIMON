# S.I.M.O.N. Tools Reference
**Simon-X Solutions | [OWNER_NAME]**
**Version: 4.4 | March 21, 2026**

---

## Core Tools (21)

These are always available regardless of HQ or plugin status.

### Calendar

| Tool | Trigger Phrases | Notes |
|---|---|---|
| `create_calendar_event` | "schedule", "book", "add meeting" | ⚠️ Now checks for conflicts first — warns before creating |
| `get_todays_events` | "what's on my calendar", "today's events" | Returns all events for today |
| `get_upcoming_events` | "upcoming events", "next week" | Default 7 days ahead |

### Messages

| Tool | Trigger Phrases | Notes |
|---|---|---|
| `send_imessage` | "text", "message", "send a message to" | Security-gated, tries iMessage then SMS |
| `get_recent_messages` | "check messages", "any texts", "messages today" | All conversations, default 24h |
| `read_imessages` | "read messages from [person]" | Specific contact only |

### Email

| Tool | Trigger Phrases | Notes |
|---|---|---|
| `send_email` | "send an email", "email [person]" | Via Mail.app, security-gated |
| `get_unread_emails` | "check email", "unread emails" | Default 5, all accounts |

### Reminders

| Tool | Trigger Phrases | Notes |
|---|---|---|
| `create_reminder` | "remind me", "set a reminder" | Optional due date and list |
| `get_reminders` | "check reminders", "pending reminders" | All lists unless specified |

### System

| Tool | Trigger Phrases | Notes |
|---|---|---|
| `run_shell` | "run", "execute", "list files", "system check" | 75-pattern security blocklist |
| `get_system_status` | "system status", "CPU", "RAM", "disk" | Uses top, df |
| `search_contacts` | "find contact", "search for [name]" | Uses Contacts.app |

### Memory

| Tool | Trigger Phrases | Notes |
|---|---|---|
| `remember` | "remember that", "store this", "note that" | Persists across restarts |
| `recall` | "what do you remember", "look up", "recall" | Full-text search |

### Vision

| Tool | Trigger Phrases | Notes |
|---|---|---|
| `vision_detect` | "what do you see", "what's on my desk", "is anyone there" | YOLO26n, 80 classes |
| `vision_ask` | "how many fingers", "what does my screen say", "describe what you see" | Routes to HQ llama3.2-vision, Moondream fallback |
| `vision_identify_person` | "who is there", "do you see me", "who's at the desk" | DeepFace ArcFace, registered faces only |
| `vision_register_face` | "register my face", "learn to recognize [name]" | Takes webcam photo |
| `vision_ocr` | "read the whiteboard", "what does that say", "read my screen" | Routes to vision_ask |
| `vision_close` | "close your eyes", "look away", "turn off camera" | Releases camera handle |

---

## Plugin Tools (22 additional)

### HQ Bridge (7 tools)

| Tool | Description |
|---|---|
| `hq_status` | Check simon-hq health — Ollama, ChromaDB, CPU, RAM, models |
| `hq_ask` | Send a prompt to HQ Ollama for deep analysis or heavy reasoning |
| `hq_web_search` | Search the web via DuckDuckGo on simon-hq |
| `hq_scrape` | Scrape a URL via headless Chromium on simon-hq |
| `hq_memory_store` | Store a document in ChromaDB for semantic search |
| `hq_memory_search` | Semantically search ChromaDB — finds by meaning, not keywords |
| `hq_list_models` | List all Ollama models on simon-hq and which are warm |

### Network Tools (14 tools)

| Tool | Description |
|---|---|
| `get_public_ip` | Get current public IP address |
| `ip_info` | Get geolocation and ISP info for an IP |
| `dns_lookup` | DNS lookup for a hostname |
| `check_port` | Check if a port is open on a host |
| `reverse_dns` | Reverse DNS lookup |
| `scan_common_ports` | Scan common ports on a host |
| `ping_host` | Ping a host, return latency |
| `traceroute` | Trace route to a host |
| `speed_test` | Internet speed test |
| `ssl_cert_check` | Check SSL certificate validity and expiry |
| `whois_lookup` | WHOIS query for a domain |
| `wifi_info` | Current WiFi SSID, signal, channel |
| `local_network_info` | Local network interfaces and addresses |
| `arp_scan` | ARP scan local network (INTERNAL USE ONLY) |

### Weather (1 tool)

| Tool | Description |
|---|---|
| `get_weather` | Current weather and forecast for a location |

---

## Security Restrictions

### Shell Blocklist (75 patterns)
The following are permanently blocked regardless of who asks:
- Reading credential files: `~/.ssh`, `config.json`, `.env`, Keychain
- Privilege escalation: `sudo -s`, `su root`, `sudo bash`
- Destructive disk: `rm -rf /`, `mkfs`, `dd`, `diskutil erase`
- Network capture: `tcpdump`, `wireshark`, `tshark`
- Pipe to shell: `eval`, `| bash`, `| sh`

### Outbound Send Scanning
Before any iMessage or email is sent, content is scanned for:
- Passwords and passphrases
- API keys and tokens
- Social Security Numbers
- Credit card and bank account numbers
- Private SSH/TLS keys

If detected, the send is blocked and [OWNER] is notified.

### Network Tool Output
ARP scan results, port scan results, and full network topology are for internal use only. Raw output is never forwarded externally. Summary only when speaking ("three open ports on that host").

---

## Tool Argument Format

Tools receive arguments from Mistral Large in standard Ollama format:

```json
{
  "function": {
    "name": "get_recent_messages",
    "arguments": {
      "hours": 24,
      "limit": 20
    }
  }
}
```

The argument sanitizer in `execute_tool()` also handles the malformed wrapper format some LLMs produce:
```json
{
  "function": "get_recent_messages",
  "args": {"hours": 24}
}
```

---

## Plugin: Android Bridge (14 tools) — v1.0

Requires ADB over WiFi setup. See `ANDROID_SETUP.md` for one-time pairing instructions.
All tools require the phone to be on the same WiFi network as the Mac.

### Connection & Status

| Tool | Trigger Phrases | Notes |
|---|---|---|
| `android_connect` | "connect to my phone", "reconnect Android" | Auto-reconnects; also called by other tools on failure |
| `android_status` | "phone battery", "phone status", "check my phone" | Battery %, charging state, WiFi SSID, ADB state |
| `android_device_info` | "phone info", "what Android version", "phone model" | Model, OS version, carrier, storage |

### SMS / Messaging

| Tool | Trigger Phrases | Notes |
|---|---|---|
| `android_read_sms` | "read my texts", "any new texts", "check my messages" | Up to 50 messages; supports name/number filter |
| `android_send_sms` | "send a text to X", "text X saying Y" | Security-gated; always confirm before sending |

### Calls

| Tool | Trigger Phrases | Notes |
|---|---|---|
| `android_call_log` | "recent calls", "missed calls", "who called me" | Filter by type: missed/incoming/outgoing/all |
| `android_make_call` | "call X", "dial X", "phone X" | Confirm number before dialing |
| `android_end_call` | "end call", "hang up", "reject call" | Sends KEYCODE_ENDCALL |

### Contacts

| Tool | Trigger Phrases | Notes |
|---|---|---|
| `android_contacts_search` | "search contacts for X", "what's X's number" | Searches by name or partial number |

### Device Control

| Tool | Trigger Phrases | Notes |
|---|---|---|
| `android_notifications` | "phone notifications", "what's on my phone" | Active notifications, skips system packages |
| `android_open_app` | "open X on my phone", "launch X" | 50+ app names pre-mapped; falls back to package search |
| `android_screenshot` | "screenshot my phone", "what's on my phone screen" | Saved as PNG in jarvis folder |
| `android_get_location` | "where is my phone", "phone GPS" | Requires Location Services enabled |
| `android_list_apps` | "what apps are on my phone", "is X installed" | Third-party apps only; supports filter |

### Proactive Monitoring (background, no voice trigger needed)

| Event | Action | Interval |
|---|---|---|
| New missed call | macOS notification: "📞 Missed Call — [name] called your Android Phone" | Every 3 min |
| Battery ≤ 20% | macOS notification: "🟡 Low Battery — [X]%" | Once per charge cycle |
| Battery ≤ 10% | macOS notification: "🔴 Critical Battery — [X]%" | Once per charge cycle |

