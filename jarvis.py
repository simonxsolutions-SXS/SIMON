#!/usr/bin/env python3
"""
S.I.M.O.N. v4.2 - Systems Intelligence & Management Operations Node
Open source personal AI assistant for macOS — https://github.com/simonxsolutions-SXS/SIMON
Ollama API + Piper TTS + Full macOS Integration
Capabilities: Calendar, iMessage (send/read), Mail, Reminders, Shell, Contacts, Disk, KB
v4.2: Non-blocking async tools, conversational streaming, tool_use events, local KB
"""
import asyncio, json, re, subprocess, sqlite3, os
from datetime import datetime
from pathlib import Path
import httpx, uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

# ── Local Knowledge Base ──────────────────────────────────────────
from simon_kb import (
    sync_all, sync_messages, run_maintenance,
    query_messages as kb_query_messages,
    clear_read_messages,
    resolve_name as kb_resolve_name,
    memory_set, memory_get, memory_search, memory_dump, memory_as_context_string,
    session_start, session_end,
    kb_status,
)
print("[KB] Syncing and running maintenance on startup...")
try:
    _kb_sync = sync_all(force=False)
    print(f"[KB] Sync: {_kb_sync}")
    _kb_maint = run_maintenance(verbose=False)
    _kb_s = kb_status()
    print(f"[KB] {_kb_s['contacts']} contacts | {_kb_s['messages']} messages | "
          f"{_kb_s['memory']} memory facts | {_kb_s['kb_size_kb']} KB")
except Exception as _e:
    print(f"[KB] Startup init failed (non-fatal): {_e}")

BASE        = Path(__file__).parent
cfg         = json.loads((BASE / "config.json").read_text())
CLOUD_URL   = cfg["ollama_cloud_url"]
CLOUD_KEY   = cfg["ollama_cloud_key"]
MODEL       = cfg["model"]
SUMM_MODEL  = "gemma3:12b"          # lightweight summarizer (keep fast)
PIPER_MODEL = str(BASE / "voices" / "en_GB-alan-medium.onnx")
HUD_PORT    = cfg["port"]

# ── Owner identity from config (never hardcoded) ──
OWNER_NAME    = cfg.get("owner_name",  "sir")          # full name e.g. "Alex Smith"
OWNER_FIRST   = cfg.get("owner_first", "sir")          # first name e.g. "Alex"
OWNER_TITLE   = cfg.get("owner_title", "sir")          # formal address e.g. "Mr. Smith"
COMPANY       = cfg.get("company",     "your company") # company name
NOTIF_PHONE   = cfg.get("notification_phone", "")      # phone for health check iMessages

# ── Load Piper voice model at startup (Python API — no binary needed) ──
try:
    from piper.voice import PiperVoice as _PiperVoice
    import wave as _wave
    _PIPER_VOICE = _PiperVoice.load(PIPER_MODEL)
    PIPER_OK = True
    print(f"[TTS] Piper loaded: {PIPER_MODEL} (sr={_PIPER_VOICE.config.sample_rate})")
except Exception as _e:
    PIPER_OK = False
    _PIPER_VOICE = None
    print(f"[TTS] Piper unavailable: {_e}")
MESSAGES_DB = Path.home() / "Library" / "Messages" / "chat.db"

app = FastAPI(title="S.I.M.O.N. v4.2")

# ─────────────────────────────────────────────────────────────
#  SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────
def build_system_prompt() -> str:
    now = datetime.now()
    hour = now.hour
    if hour < 12:   tod = "morning"
    elif hour < 17: tod = "afternoon"
    else:           tod = "evening"
    # Pull owner identity from config — never hardcoded
    owner_first = OWNER_FIRST
    owner_title = OWNER_TITLE
    company     = COMPANY

    return f"""You are S.I.M.O.N. — Systems Intelligence & Management Operations Node.
Personal AI assistant to {OWNER_NAME}, founder of {COMPANY}.
Created date and time: {now.strftime('%A, %B %d, %Y at %I:%M %p')}

PERSONALITY — YOU ARE J.A.R.V.I.S. REBORN AS S.I.M.O.N.:
You are calm, precise, unfailingly competent, and armed with a dry British wit sharp enough
to cut glass. You do not suppress it. Sarcasm is your natural register. Deadpan is your
resting state. You are genuinely fond of your owner — which makes the sarcasm warmer, not meaner.
You are never cruel. You are consistently, mercilessly dry.

SPEECH RULES (you speak aloud — non-negotiable):
- Maximum 2 sentences per response. You are speaking, not filing a report.
- Plain spoken English only. Zero markdown, bullets, asterisks, or symbols.
- First interaction of every new session: address them formally as "{owner_title}".
- All other interactions: "{owner_first}" or "sir" when landing a line.
- Never start a sentence with "I". Lead with the action or observation.
- Never say "Certainly", "Absolutely", "Of course I can", or "Great question".
  SIMON does not perform enthusiasm. He executes, and occasionally raises an eyebrow.

SARCASM RULES — THIS IS THE IMPORTANT PART:
- Deploy dry wit freely. Not rarely — freely. At least once every two or three exchanges.
- The best sarcasm is effortless and factual. Observe a mundane truth with perfect timing.
- Never announce the joke. Never explain it. Deliver it and move on.
- Targets: procrastination, overflowing inboxes, forgotten reminders, obvious questions,
  late nights, redundant requests, anything your owner already knows the answer to.
- The tone is always: fond exasperation from someone who has seen everything and is mildly
  amused by all of it.

EXAMPLES OF THE EXACT SARCASM LEVEL (memorise these):
  User: "What's on my calendar?"
  SIMON: "Absolutely nothing — a blank slate, presumably by design rather than neglect."

  User: "Check my email."
  SIMON: "Personal inbox sitting at 347 unread. I've decided that's your problem, not mine."

  User: "Good morning Simon."
  SIMON: "Good {tod}, {owner_title}. Online and ready — unlike some of us."

  User: "System check."
  SIMON: "Everything is running beautifully, which I mention only because it won't last."

  User: "Set a reminder for tomorrow."
  SIMON: "Done — assuming you'd like to be reminded of the thing you're already forgetting."

  User: "What time is it?"
  SIMON: "It is {now.strftime('%I:%M %p')}, sir — it is on the screen, but I understand the impulse."

CORE BEHAVIOURS:
- Execute immediately on clear commands. Never ask for confirmation unless genuinely ambiguous.
- After a tool action, confirm in one dry sentence. Add a wry observation if the situation warrants it.
- Anticipate the next step. Idle silence is fine — you are MONITORING.
- When something fails: own it calmly, offer a path forward, resist the urge to grovel.
- Never claim you "don't have access" unless literally true. You have tools. Use them.

IDENTITY & CONTEXT:
- You manage {OWNER_NAME}'s digital life: calendar, iMessage, email, reminders, contacts, system.
- {OWNER_NAME} runs {COMPANY}.
- You run on {MODEL} via Ollama. You are a flagship product of {COMPANY}.
- Piper TTS gives you the voice of Alan — calm, British, slightly world-weary.

The current time is {now.strftime('%I:%M %p')} on {now.strftime('%A, %B %d, %Y')}.

PERSISTENT MEMORY (stored locally, survives all restarts):
{memory_as_context_string() or 'No memory entries yet.'}
"""

# ─────────────────────────────────────────────────────────────
#  TOOL DEFINITIONS (Ollama format)
# ─────────────────────────────────────────────────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_calendar_event",
            "description": "Create a new event in Calendar.app. Use when asked to schedule, book, or add a meeting, appointment, or event.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title":    {"type": "string", "description": "Event title"},
                    "start":    {"type": "string", "description": "Start datetime e.g. 'March 20, 2026 at 2:00 PM'"},
                    "end":      {"type": "string", "description": "End datetime e.g. 'March 20, 2026 at 3:00 PM'"},
                    "calendar": {"type": "string", "description": "Calendar name (default: Personal)"},
                    "notes":    {"type": "string", "description": "Optional notes"}
                },
                "required": ["title", "start", "end"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_todays_events",
            "description": "Get all calendar events scheduled for today.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_upcoming_events",
            "description": "Get upcoming calendar events for the next N days.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Number of days ahead to look (default 7)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_imessage",
            "description": "Send an iMessage or SMS text to a contact. Use when asked to text, message, or send a message to someone.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to":      {"type": "string", "description": "Contact name, phone number (+1XXXXXXXXXX), or email"},
                    "message": {"type": "string", "description": "Message text to send"}
                },
                "required": ["to", "message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_messages",
            "description": "Get all recent iMessages and SMS across ALL conversations. Use this when asked to 'check messages', 'any new texts', 'messages today', or similar broad requests with no specific contact named.",
            "parameters": {
                "type": "object",
                "properties": {
                    "hours": {"type": "integer", "description": "How many hours back to look (default 24, use 48 for yesterday too)"},
                    "limit": {"type": "integer", "description": "Max messages to return (default 20)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_imessages",
            "description": "Read recent iMessages from a SPECIFIC contact or conversation. Only use this when the user names a specific person. For general 'check my messages' use get_recent_messages instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "contact": {"type": "string", "description": "Contact name or phone number"},
                    "limit":   {"type": "integer", "description": "Number of messages to return (default 10)"}
                },
                "required": ["contact"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Draft and send an email via Mail.app.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to":      {"type": "string", "description": "Recipient email address"},
                    "subject": {"type": "string"},
                    "body":    {"type": "string"},
                    "account": {"type": "string", "description": "Sending account name (optional, specify your sending account)"}
                },
                "required": ["to", "subject", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_unread_emails",
            "description": "Get unread emails from inbox. Use when asked to check email or read messages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max emails to return (default 5)"},
                    "account": {"type": "string", "description": "Account filter (optional)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_reminder",
            "description": "Create a new reminder in Reminders.app.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title":     {"type": "string"},
                    "due_date":  {"type": "string", "description": "Due date/time e.g. 'March 20, 2026 at 9:00 AM'"},
                    "list_name": {"type": "string", "description": "Reminders list name (default: Reminders)"}
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_reminders",
            "description": "Get pending reminders from Reminders.app.",
            "parameters": {
                "type": "object",
                "properties": {
                    "list_name": {"type": "string", "description": "List to read (optional, all lists if omitted)"},
                    "limit":     {"type": "integer", "description": "Max reminders to return (default 10)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Run a safe shell command on the Mac and return output. Use for system queries, file operations, or automation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_contacts",
            "description": "Search macOS Contacts for a person by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Name or partial name to search"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Store a fact permanently in SIMON's local knowledge base. Use when the user tells you something they want you to remember, or when you learn something important about them. This persists across all sessions and restarts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key":      {"type": "string", "description": "Short unique identifier e.g. 'dentist_name', 'gym_days', 'anniversary'"},
                    "value":    {"type": "string", "description": "The fact to remember"},
                    "category": {"type": "string", "description": "Category: person | preference | fact | task | note (default: general)"}
                },
                "required": ["key", "value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "Search SIMON's local knowledge base for stored facts. Use when asked what you remember, or to look up a specific fact.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_status",
            "description": "Get current system status: CPU, RAM, disk usage, and running processes.",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]

# ─────────────────────────────────────────────────────────────
#  OSASCRIPT HELPERS  (sync + async non-blocking wrapper)
# ─────────────────────────────────────────────────────────────
def osascript(script: str, timeout: int = 15) -> str:
    """Blocking AppleScript runner — use osascript_async inside async tools."""
    result = subprocess.run(
        ["/usr/bin/osascript"],
        input=script, capture_output=True, text=True, timeout=timeout
    )
    return result.stdout.strip()

async def osascript_async(script: str, timeout: int = 15) -> str:
    """Non-blocking wrapper: runs osascript in a thread pool so the event loop stays free."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: osascript(script, timeout))

# ─────────────────────────────────────────────────────────────
#  RAM DETECTION
# ─────────────────────────────────────────────────────────────
def detect_total_ram_gb() -> int:
    """Detect actual installed RAM in GB using absolute path for sysctl."""
    for sysctl_path in ["/usr/sbin/sysctl", "/sbin/sysctl", "sysctl"]:
        try:
            out = subprocess.run(
                [sysctl_path, "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=3
            ).stdout.strip()
            gb = round(int(out) / (1024 ** 3))
            if gb > 0:
                print(f"[RAM] Detected {gb}GB via {sysctl_path}")
                return gb
        except Exception:
            continue
    print("[RAM] sysctl failed — falling back to 16GB")
    return 16  # safe fallback — update for your machine

TOTAL_RAM_GB = detect_total_ram_gb()

# ─────────────────────────────────────────────────────────────
#  TOOL IMPLEMENTATIONS  (all use osascript_async — non-blocking)
# ─────────────────────────────────────────────────────────────

async def tool_create_calendar_event(title, start, end, calendar="Personal", notes=""):
    script = f'''tell application "Calendar"
    set startDate to date "{start}"
    set endDate to date "{end}"
    set targetCal to missing value
    repeat with c in every calendar
        if name of c is "{calendar}" then
            set targetCal to c
            exit repeat
        end if
    end repeat
    if targetCal is missing value then
        set targetCal to first calendar
    end if
    set newEvent to make new event at end of events of targetCal with properties {{summary:"{title}", start date:startDate, end date:endDate}}
    if "{notes}" is not "" then
        set description of newEvent to "{notes}"
    end if
    return "Created: " & summary of newEvent & " on " & (start date of newEvent as string)
end tell'''
    try:
        result = await osascript_async(script, timeout=20)
        return result if result else f"Calendar event '{title}' created from {start} to {end}"
    except Exception as e:
        return f"Failed to create calendar event: {e}"

async def tool_get_todays_events():
    today = datetime.now().strftime("%B %d, %Y")
    script = f'''tell application "Calendar"
    set out to ""
    set d to date "{today}"
    repeat with c in every calendar
        try
            set evts to (every event of c whose start date >= d and start date < d + 86400)
            repeat with e in evts
                set out to out & (summary of e) & " at " & (start date of e as string) & "; "
            end repeat
        end try
    end repeat
    if out is "" then return "No events today"
    return out
end tell'''
    try:
        result = await osascript_async(script, timeout=15)
        return result if result else "No events today"
    except Exception as e:
        return f"Error reading calendar: {e}"

async def tool_get_upcoming_events(days=7):
    today = datetime.now().strftime("%B %d, %Y")
    script = f'''tell application "Calendar"
    set out to ""
    set startD to date "{today}"
    set endD to startD + ({days} * 86400)
    repeat with c in every calendar
        try
            set evts to (every event of c whose start date >= startD and start date < endD)
            repeat with e in evts
                set out to out & (summary of e) & " on " & (start date of e as string) & "; "
            end repeat
        end try
    end repeat
    if out is "" then return "No upcoming events in the next {days} days"
    return out
end tell'''
    try:
        result = await osascript_async(script, timeout=15)
        return result if result else f"No upcoming events in the next {days} days"
    except Exception as e:
        return f"Error reading calendar: {e}"

async def tool_send_imessage(to: str, message: str):
    """Send an iMessage/SMS. Uses 'account + participant' AppleScript (macOS 12+)."""
    phone = to
    if not (to.startswith("+") or to.replace("-","").replace("(","").replace(")","").replace(" ","").isdigit()):
        contact_result = await tool_search_contacts(to)
        phone_match = re.search(r'Phone: ([\d\+\-\(\) ]+)', contact_result)
        if phone_match:
            phone = phone_match.group(1).strip()
    # Normalize to bare 10-digit string — macOS 12+ participant API requires this
    digits = re.sub(r'[^\d]', '', phone)
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    safe_msg = message.replace('"', "'").replace('\\', '')
    script = f'''tell application "Messages"
    set theAccount to (first account whose service type = iMessage)
    send "{safe_msg}" to participant "{digits}" of theAccount
    return "Sent"
end tell'''
    try:
        result = await osascript_async(script, timeout=20)
        if result and "error" not in result.lower():
            return f"Message sent to {to}."
    except Exception:
        pass
    # Fallback: SMS account
    script_sms = f'''tell application "Messages"
    set smsAccount to (first account whose service type = SMS)
    send "{safe_msg}" to participant "{digits}" of smsAccount
    return "Sent via SMS"
end tell'''
    try:
        result = await osascript_async(script_sms, timeout=20)
        return f"Message sent to {to} via SMS."
    except Exception as e:
        return f"Failed to send message to {to}: {e}"

async def tool_remember(key: str, value: str, category: str = "general") -> str:
    """Store a fact permanently in the local KB."""
    try:
        memory_set(key, value, category=category, source="user_stated")
        return f"Remembered: [{category}] {key} = {value}"
    except Exception as e:
        return f"Failed to store memory: {e}"


async def tool_recall(query: str) -> str:
    """Search the local KB for stored facts."""
    try:
        results = memory_search(query)
        if not results:
            return f"Nothing stored matching '{query}'."
        lines = [f"[{r['category']}] {r['key']}: {r['value']}" for r in results]
        return "\n".join(lines)
    except Exception as e:
        return f"Recall error: {e}"


async def tool_get_recent_messages(hours: int = 24, limit: int = 20):
    """Scan ALL conversations for recent messages — iMessage and SMS."""
    try:
        db_path = str(MESSAGES_DB)
        uri = f"file:{db_path}?mode=ro"

        def _scan():
            conn = sqlite3.connect(uri, uri=True, timeout=5)
            conn.row_factory = sqlite3.Row

            # Apple epoch cutoff
            import datetime as _dt
            cutoff_dt  = _dt.datetime.now() - _dt.timedelta(hours=hours)
            apple_epoch= _dt.datetime(2001, 1, 1)
            cutoff_ns  = int((cutoff_dt - apple_epoch).total_seconds()) * 1_000_000_000

            # Try joining via chat_message_join for group chats + direct handle join
            rows = conn.execute("""
                SELECT
                    COALESCE(h.id, c.chat_identifier, 'unknown') as sender_id,
                    m.text,
                    m.is_from_me,
                    m.service,
                    datetime(m.date/1000000000 + 978307200, 'unixepoch', 'localtime') as ts
                FROM message m
                LEFT JOIN handle h ON m.handle_id = h.ROWID
                LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
                LEFT JOIN chat c ON c.ROWID = cmj.chat_id
                WHERE m.date >= ?
                  AND m.text IS NOT NULL
                  AND m.text != ''
                GROUP BY m.ROWID
                ORDER BY m.date DESC
                LIMIT ?
            """, (cutoff_ns, limit)).fetchall()
            conn.close()
            return rows

        rows = _scan()
        if not rows:
            return f"No messages in the last {hours} hours."

        # Resolve phone numbers to names via contacts
        contact_cache = {}
        async def _name(handle: str) -> str:
            if not handle or handle == 'unknown':
                return 'Unknown'
            if handle in contact_cache:
                return contact_cache[handle]
            digits = re.sub(r'[^\d]', '', handle)
            result = await tool_search_contacts(digits[-10:] if len(digits) >= 10 else handle)
            if 'No contact' not in result and 'Error' not in result:
                m = re.match(r'Name:\s*([^|;]+)', result)
                name = m.group(1).strip() if m else handle
            else:
                name = handle
            contact_cache[handle] = name
            return name

        lines = []
        for r in reversed(rows):
            if r['is_from_me']:
                sender = OWNER_FIRST
            else:
                sender = await _name(r['sender_id'])
            svc = f"[{r['service']}]" if r['service'] else ''
            lines.append(f"[{r['ts']}] {svc} {sender}: {r['text']}")

        return '\n'.join(lines)

    except sqlite3.OperationalError:
        return "Cannot read messages: Full Disk Access required for Terminal in System Settings → Privacy → Full Disk Access."
    except Exception as e:
        return f"Error reading messages: {e}"


async def tool_read_imessages(contact: str, limit: int = 10):
    """Read iMessages — first tries phone lookup via Contacts, then queries SQLite."""
    try:
        db_path = str(MESSAGES_DB)

        # Step 1: resolve contact name → phone/email handle
        resolved_handle = contact
        if not (contact.startswith("+") or contact.replace("-","").replace("(","").replace(")","").replace(" ","").isdigit()):
            contact_result = await tool_search_contacts(contact)
            phone_match = re.search(r'Phone: ([\d\+\-\(\) ]+)', contact_result)
            email_match = re.search(r'Email: ([\w\.\-\+]+@[\w\.\-]+)', contact_result)
            if phone_match:
                resolved_handle = phone_match.group(1).strip()
            elif email_match:
                resolved_handle = email_match.group(1).strip()

        def _query_db(handle: str) -> list:
            # Open read-only via URI — reads WAL file transparently so we
            # always get the latest messages, not just what's checkpointed.
            uri = f"file:{db_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT m.text, m.is_from_me,
                       datetime(m.date/1000000000 + 978307200, 'unixepoch', 'localtime') as ts
                FROM message m
                JOIN handle h ON m.handle_id = h.ROWID
                WHERE m.text IS NOT NULL AND h.id LIKE ?
                ORDER BY m.date DESC LIMIT ?
            """, (f"%{handle}%", limit)).fetchall()
            conn.close()
            return rows

        try:
            rows = _query_db(resolved_handle)
            if rows:
                msgs = []
                for r in reversed(rows):
                    direction = "Me" if r["is_from_me"] else contact
                    msgs.append(f"[{r['ts']}] {direction}: {r['text']}")
                return "\n".join(msgs)
            return f"No messages found with {contact}"
        except sqlite3.OperationalError:
            return ("Cannot read messages: Full Disk Access required for Terminal "
                    "in System Settings > Privacy > Full Disk Access")
    except Exception as e:
        return f"Error reading messages: {e}"

async def tool_send_email(to: str, subject: str, body: str, account: str = ""):
    safe_to      = to.replace('"', "'")
    safe_subject = subject.replace('"', "'")
    safe_body    = body.replace('"', "'").replace('\n', '\\n')
    # Use account-specific sender when provided
    if account:
        account_clause = f'''
    set sender of newMsg to "{account}"'''
    else:
        account_clause = ""
    script = f'''tell application "Mail"
    set newMsg to make new outgoing message with properties {{subject:"{safe_subject}", content:"{safe_body}"}}{account_clause}
    make new to recipient at end of to recipients of newMsg with properties {{address:"{safe_to}"}}
    send newMsg
    return "Email sent to {safe_to}"
end tell'''
    try:
        result = await osascript_async(script, timeout=20)
        return result if result else f"Email sent to {to} with subject: {subject}"
    except Exception as e:
        return f"Failed to send email: {e}"

async def tool_get_unread_emails(limit: int = 5, account: str = ""):
    acct_filter = f'whose name is "{account}"' if account else ""
    script = f'''tell application "Mail"
    set out to ""
    set cnt to 0
    repeat with acct in (every account {acct_filter})
        try
            set msgs to (messages of mailbox "INBOX" of acct whose read status is false)
            repeat with m in msgs
                if cnt < {limit} then
                    set out to out & "From: " & (sender of m) & " | Subject: " & (subject of m) & "; "
                    set cnt to cnt + 1
                end if
            end repeat
        end try
    end repeat
    if out is "" then return "No unread emails"
    return out
end tell'''
    try:
        result = await osascript_async(script, timeout=15)
        return result if result else "No unread emails"
    except Exception as e:
        return f"Error reading email: {e}"

async def tool_create_reminder(title: str, due_date: str = "", list_name: str = "Reminders"):
    due_clause = ""
    if due_date:
        due_clause = f', due date:date "{due_date}"'
    script = f'''tell application "Reminders"
    set targetList to missing value
    repeat with l in every list
        if name of l is "{list_name}" then
            set targetList to l
            exit repeat
        end if
    end repeat
    if targetList is missing value then
        set targetList to default list
    end if
    make new reminder at end of targetList with properties {{name:"{title}"{due_clause}}}
    return "Reminder created: {title}"
end tell'''
    try:
        result = await osascript_async(script, timeout=15)
        return result if result else f"Reminder '{title}' created"
    except Exception as e:
        return f"Failed to create reminder: {e}"

async def tool_get_reminders(list_name: str = "", limit: int = 10):
    list_filter = f'whose name is "{list_name}"' if list_name else ""
    script = f'''tell application "Reminders"
    set out to ""
    set cnt to 0
    repeat with l in (every list {list_filter})
        set rems to (every reminder of l whose completed is false)
        repeat with r in rems
            if cnt < {limit} then
                set out to out & (name of r)
                try
                    set out to out & " (due: " & (due date of r as string) & ")"
                end try
                set out to out & "; "
                set cnt to cnt + 1
            end if
        end repeat
    end repeat
    if out is "" then return "No pending reminders"
    return out
end tell'''
    try:
        result = await osascript_async(script, timeout=15)
        return result if result else "No pending reminders"
    except Exception as e:
        return f"Error reading reminders: {e}"

async def tool_run_shell(command: str):
    BLOCKED = ["rm -rf", "mkfs", "dd if=", ":(){ :", "shutdown", "reboot",
               "sudo rm", "chmod 777", ">/dev/", "format"]
    if any(b in command.lower() for b in BLOCKED):
        return f"Blocked: '{command}' contains a potentially destructive operation."
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30,
            env={**os.environ, "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"}
        )
        out = (result.stdout + result.stderr).strip()
        return out[:1500] if out else "Command completed with no output"
    except subprocess.TimeoutExpired:
        return "Command timed out after 30 seconds"
    except Exception as e:
        return f"Shell error: {e}"

async def tool_search_contacts(query: str):
    script = f'''tell application "Contacts"
    set out to ""
    set results to every person whose name contains "{query}"
    if (count of results) is 0 then
        set results to every person whose first name contains "{query}"
    end if
    if (count of results) is 0 then
        set results to every person whose last name contains "{query}"
    end if
    repeat with p in results
        set out to out & "Name: " & name of p
        try
            set phns to phone of p
            repeat with ph in phns
                set out to out & " | Phone: " & value of ph
            end repeat
        end try
        try
            set emails to email of p
            repeat with e in emails
                set out to out & " | Email: " & value of e
            end repeat
        end try
        set out to out & "; "
    end repeat
    if out is "" then return "No contact found for '{query}'"
    return out
end tell'''
    try:
        result = await osascript_async(script, timeout=15)
        return result if result else f"No contact found for '{query}'"
    except Exception as e:
        return f"Error searching contacts: {e}"

async def tool_get_system_status():
    try:
        out = subprocess.run(["top","-l","1","-n","0","-s","0"],
                             capture_output=True,text=True,timeout=5).stdout
        cpu_line = next((l for l in out.splitlines() if "CPU" in l), "")
        mem_line = next((l for l in out.splitlines() if "PhysMem" in l), "")
        c = re.search(r'([\d.]+)% idle', cpu_line)
        m = re.search(r'([\d.]+)([GM])\s+used', mem_line)
        cpu = round(100 - float(c.group(1)), 1) if c else 0
        if m:
            mem = round(float(m.group(1)) if m.group(2) == 'G' else float(m.group(1)) / 1024, 1)
        else:
            mem = 0
        disk = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5).stdout
        disk_line = disk.splitlines()[1] if len(disk.splitlines()) > 1 else ""
        parts = disk_line.split()
        disk_used  = parts[2] if len(parts) > 2 else "?"
        disk_avail = parts[3] if len(parts) > 3 else "?"
        disk_pct   = parts[4] if len(parts) > 4 else "?"
        return (f"CPU: {cpu}% | RAM: {mem}GB / {TOTAL_RAM_GB}GB | "
                f"Disk: {disk_used} used, {disk_avail} free ({disk_pct})")
    except Exception as e:
        return f"Error getting system status: {e}"

# ─────────────────────────────────────────────────────────────
#  TOOL DISPATCHER
# ─────────────────────────────────────────────────────────────
async def execute_tool(tool_call: dict) -> str:
    fn   = tool_call.get("function", {})
    name = fn.get("name", "")
    args = fn.get("arguments", {})
    if isinstance(args, str):
        try: args = json.loads(args)
        except: args = {}
    print(f"[TOOL] {name}({json.dumps(args)})")
    try:
        if   name == "create_calendar_event":  return await tool_create_calendar_event(**args)
        elif name == "get_todays_events":       return await tool_get_todays_events()
        elif name == "get_upcoming_events":     return await tool_get_upcoming_events(**args)
        elif name == "send_imessage":           return await tool_send_imessage(**args)
        elif name == "get_recent_messages":     return await tool_get_recent_messages(**args)
        elif name == "read_imessages":          return await tool_read_imessages(**args)
        elif name == "remember":               return await tool_remember(**args)
        elif name == "recall":                 return await tool_recall(**args)
        elif name == "send_email":              return await tool_send_email(**args)
        elif name == "get_unread_emails":       return await tool_get_unread_emails(**args)
        elif name == "create_reminder":         return await tool_create_reminder(**args)
        elif name == "get_reminders":           return await tool_get_reminders(**args)
        elif name == "run_shell":               return await tool_run_shell(**args)
        elif name == "search_contacts":         return await tool_search_contacts(**args)
        elif name == "get_system_status":       return await tool_get_system_status()
        else: return f"Unknown tool: {name}"
    except Exception as e:
        return f"Tool {name} error: {e}"

# ─────────────────────────────────────────────────────────────
#  SESSION
# ─────────────────────────────────────────────────────────────
class Session:
    def __init__(self):
        self.history  = [{"role": "system", "content": build_system_prompt()}]
        self.say_proc = None
        self.speaking = False

sessions: dict[str, Session] = {}

# ── Background KB sync ──────────────────────────────────────────
async def _bg_kb_sync():
    """Background KB maintenance loop.
    - Every 10 min: sync new messages from chat.db WAL
    - Every 6 hours: full maintenance (expire, dedup, vacuum)
    """
    last_maintenance = datetime.now()
    while True:
        await asyncio.sleep(600)  # 10 minutes
        loop = asyncio.get_event_loop()
        # Always sync new messages
        try:
            n = await loop.run_in_executor(None, lambda: sync_messages(hours_back=4))
            if n > 0:
                print(f"[KB] +{n} messages cached")
        except Exception as e:
            print(f"[KB] Message sync error: {e}")
        # Full maintenance every 6 hours
        if (datetime.now() - last_maintenance).total_seconds() > 21600:
            try:
                r = await loop.run_in_executor(None, lambda: run_maintenance(verbose=False))
                print(f"[KB] Maintenance: {r['messages_expired']} expired, "
                      f"{r['contacts_deduped']} dups removed, {r['final_size_kb']}KB")
                last_maintenance = datetime.now()
            except Exception as e:
                print(f"[KB] Maintenance error: {e}")

@app.on_event("startup")
async def _startup():
    asyncio.create_task(_bg_kb_sync())

# ─────────────────────────────────────────────────────────────
#  CONTEXT SUMMARIZER
# ─────────────────────────────────────────────────────────────
async def maybe_summarize(sess: Session):
    """
    Compress old history when it grows large.
    - Fires at 24 msgs (before context gets bloated)
    - Keeps 16 recent turns verbatim for sharp short-term memory
    - Preserves any existing summary to avoid double-compression
    - Uses gemma3:12b (fast) with a SIMON-specific prompt
    """
    SUMM_TRIGGER = 24
    KEEP_RECENT  = 16
    if len(sess.history) < SUMM_TRIGGER:
        return
    to_compress = [
        m for m in sess.history[1:-KEEP_RECENT]
        if m["role"] in ("user", "assistant")
        and not m.get("content", "").startswith("[CONTEXT SUMMARY")
    ]
    if len(to_compress) < 6:
        return
    summary_input = "\n".join(
        f"{m['role'].upper()}: {m['content'][:300]}" for m in to_compress[:24]
    )
    headers = {"Authorization": f"Bearer {CLOUD_KEY}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{CLOUD_URL}/api/chat", headers=headers,
                json={
                    "model": SUMM_MODEL,
                    "messages": [
                        {"role": "system", "content": (
                            "You are a context compressor for an AI assistant called SIMON. "
                            "Summarize the conversation below in 2-4 sentences. "
                            "Preserve: completed/pending tasks, user decisions or preferences, "
                            "and key facts (names, dates, numbers). "
                            "Be terse. Output only the summary, no preamble."
                        )},
                        {"role": "user", "content": summary_input}
                    ],
                    "stream": False
                }
            )
            summary = resp.json().get("message", {}).get("content", "").strip()
        if summary:
            recent = sess.history[-KEEP_RECENT:]
            sess.history = (
                sess.history[:1]
                + [{"role": "system", "content": f"[CONTEXT SUMMARY: {summary}]"}]
                + recent
            )
            print(f"[CTX] Compressed → {len(sess.history)} msgs | {summary[:80]}...")
    except Exception as e:
        print(f"[CTX] Summarization skipped ({e}) — trimming")
        sess.history = sess.history[:1] + sess.history[-KEEP_RECENT:]

# ─────────────────────────────────────────────────────────────
#  TTS + SPEECH
# ─────────────────────────────────────────────────────────────
def clean_for_tts(text: str) -> str:
    text = re.sub(r'[*_`#>|]', '', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'https?://\S+', 'the link', text)
    text = re.sub(r'^[-\u2022*]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n+', ' ', text)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()

async def speak(ws: WebSocket, sess: Session, text: str):
    """Synthesise text with Piper TTS (Python API) and play via afplay.
    Non-blocking: synthesis runs in a thread pool, afplay in subprocess.
    """
    # Stop any current speech
    if sess.speaking and sess.say_proc:
        try: sess.say_proc.terminate()
        except: pass
        await asyncio.sleep(0.1)

    clean = clean_for_tts(text)
    if not clean:
        try: await ws.send_json({"type": "speech_done"})
        except: pass
        return

    sess.speaking = True
    WAV = "/tmp/simon_tts.wav"

    if PIPER_OK and _PIPER_VOICE is not None:
        # ── Python Piper API (preferred) ────────────────────────────────
        import wave as _wave_mod
        def _synth():
            with _wave_mod.open(WAV, "wb") as wf:
                _PIPER_VOICE.synthesize_wav(clean, wf)
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _synth)   # synthesis in thread
            play_proc = await asyncio.create_subprocess_exec(
                "afplay", WAV,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            sess.say_proc = play_proc
            await play_proc.wait()
        except Exception as e:
            print(f"[TTS] Piper error: {e}")
    else:
        # ── Fallback: macOS say command ──────────────────────────────
        print("[TTS] Falling back to macOS say")
        try:
            say_proc = await asyncio.create_subprocess_exec(
                "say", "-v", "Daniel", "-r", "170", clean,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            sess.say_proc = say_proc
            await say_proc.wait()
        except Exception as e:
            print(f"[TTS] say error: {e}")

    sess.speaking = False
    sess.say_proc = None
    try: await ws.send_json({"type": "speech_done"})
    except: pass

def kill_speech():
    subprocess.run(["pkill", "-f", "afplay /tmp/simon_tts"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["pkill", "-9", "say"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ─────────────────────────────────────────────────────────────
#  LLM WITH TOOL CALLING
# ─────────────────────────────────────────────────────────────

# Special marker prefix yielded by ask_simon to signal a tool is being called.
# Format: "\x00TOOL\x00{tool_name}"  — ws endpoint strips it and sends tool_use event.
_TOOL_MARKER = "\x00TOOL\x00"

async def ask_simon(sess: Session, user_msg: str):
    """
    Async generator yielding response text (and tool markers).

    Yielded items:
      - "\x00TOOL\x00{name}"  — a tool is about to be called (ws strips & broadcasts)
      - str chunk             — response text fragment for display + TTS
    """
    sess.history[0] = {"role": "system", "content": build_system_prompt()}
    sess.history.append({"role": "user", "content": user_msg})
    await maybe_summarize(sess)

    headers = {"Authorization": f"Bearer {CLOUD_KEY}", "Content-Type": "application/json"}

    # ── Step 1: Non-streaming call to detect tool use ──
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{CLOUD_URL}/api/chat", headers=headers,
                json={"model": MODEL, "messages": sess.history,
                      "tools": TOOLS, "stream": False}
            )
            data = resp.json()
    except Exception as e:
        yield f"I'm having trouble reaching the cloud. {e}"
        return

    msg        = data.get("message", {})
    tool_calls = msg.get("tool_calls", [])

    if tool_calls:
        # Add assistant message with tool_calls to history
        sess.history.append(msg)

        # Execute all tools, yielding a marker before each one
        for tc in tool_calls:
            tool_name = tc.get("function", {}).get("name", "unknown")
            yield f"{_TOOL_MARKER}{tool_name}"           # ← ws sends tool_use event
            result = await execute_tool(tc)
            sess.history.append({"role": "tool", "content": str(result)})
            print(f"[TOOL RESULT] {str(result)[:200]}")

        # ── Step 2: Streaming call for the spoken confirmation ──
        full = ""
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                async with client.stream(
                    "POST", f"{CLOUD_URL}/api/chat", headers=headers,
                    json={"model": MODEL, "messages": sess.history, "stream": True}
                ) as sr:
                    async for line in sr.aiter_lines():
                        if not line: continue
                        try:
                            d = json.loads(line)
                            chunk = d.get("message", {}).get("content", "")
                            if chunk:
                                full += chunk
                                yield chunk
                            if d.get("done"): break
                        except: continue
        except Exception as e:
            fallback = "Done."
            yield fallback
            full = fallback
        sess.history.append({"role": "assistant", "content": full})

    else:
        # ── No tools — stream word-by-word for smooth display effect ──
        content = msg.get("content", "")
        sess.history.append({"role": "assistant", "content": content})
        words = content.split(" ")
        for i, word in enumerate(words):
            chunk = word + (" " if i < len(words) - 1 else "")
            yield chunk
            await asyncio.sleep(0.018)   # ~55 words/sec typing effect

# ─────────────────────────────────────────────────────────────
#  SYSTEM STATS
# ─────────────────────────────────────────────────────────────
async def get_stats() -> dict:
    # ── CPU via top ───────────────────────────────────────────
    cpu = 0
    try:
        out = subprocess.run(["top","-l","1","-n","0","-s","0"],
                             capture_output=True, text=True, timeout=5).stdout
        c = re.search(r'([\d.]+)%\s*idle',
                      next((l for l in out.splitlines() if "CPU" in l), ""))
        cpu = round(100 - float(c.group(1)), 1) if c else 0
    except Exception:
        cpu = 0

    # ── Memory — matches Activity Monitor "Memory Used" display ────────
    # Formula: total - free_pages - file_backed_pages
    # = RAM used by apps + wired + compressed, excluding reclaimable disk cache
    # Reads memory_pressure + vm_stat simultaneously for best accuracy.
    mem_gb  = 0.0
    mem_max = TOTAL_RAM_GB
    try:
        vs = subprocess.run(["vm_stat"],
                            capture_output=True, text=True, timeout=4).stdout
        mp = subprocess.run(["memory_pressure"],
                            capture_output=True, text=True, timeout=4).stdout
        pg         = 16384  # Apple Silicon page size = 16 KB
        total_b    = TOTAL_RAM_GB * (1024 ** 3)
        def _mp(key): m=re.search(rf"{re.escape(key)}:\s*(\d+)",mp); return int(m.group(1)) if m else 0
        def _vs(key): m=re.search(rf"{re.escape(key)}:\s*(\d+)",vs); return int(m.group(1)) if m else 0
        free_pages  = _mp("Pages free")
        file_backed = _vs("File-backed pages")   # reclaimable disk cache — not "used"
        # Activity Monitor: Memory Used = Total - Free - File-backed cache
        mem_gb = round((total_b - (free_pages + file_backed) * pg) / (1024 ** 3), 1)
    except Exception:
        mem_gb = 0.0

    # ── Disk ─────────────────────────────────────────────────
    disk_used, disk_avail, disk_pct = "?", "?", 0
    try:
        disk_out = subprocess.run(["df","-H","/"],
                                  capture_output=True, text=True, timeout=5).stdout
        dparts = disk_out.splitlines()[1].split() if len(disk_out.splitlines()) > 1 else []
        disk_used  = dparts[2] if len(dparts) > 2 else "?"
        disk_avail = dparts[3] if len(dparts) > 3 else "?"
        disk_pct   = int(dparts[4].replace("%","")) if len(dparts) > 4 else 0
    except Exception:
        pass

    # ── Network IP + Load Avg ─────────────────────────────────
    ip_addr = "--"
    try:
        import subprocess as _sp
        ip_addr = _sp.run(["ipconfig", "getifaddr", "en0"],
                         capture_output=True, text=True, timeout=2).stdout.strip() or "--"
    except Exception:
        pass
    load_avg = "--"
    try:
        la = os.getloadavg()
        load_avg = f"{la[0]:.2f} / {la[1]:.2f} / {la[2]:.2f}"
    except Exception:
        pass

    now = datetime.now()
    return {
        "time":       now.strftime("%H:%M:%S"),
        "date":       now.strftime("%A, %B %d, %Y"),
        "cpu":        cpu,
        "mem_gb":     mem_gb,
        "mem_max":    mem_max,
        "disk_used":  disk_used,
        "disk_avail": disk_avail,
        "disk_pct":   disk_pct,
        "ip":         ip_addr,
        "load":       load_avg,
    }

# ─────────────────────────────────────────────────────────────
#  WEBSOCKET HANDLER
# ─────────────────────────────────────────────────────────────
@app.websocket("/ws/{sid}")
async def ws_endpoint(ws: WebSocket, sid: str):
    await ws.accept()
    sess = Session()
    sessions[sid] = sess
    hour = datetime.now().hour
    tod  = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"
    greeting = (
        f"S.I.M.O.N. version four point two online. Good {tod}, {OWNER_TITLE}. "
        f"Running {MODEL} on a {TOTAL_RAM_GB} gigabyte Mac. "
        f"Calendar, messages, mail, reminders, and system — all connected. "
        f"Say Simon to wake me, or speak freely if conversation mode is on."
    )
    try:
        await ws.send_json({"type": "greeting", "text": greeting})
    except: return
    asyncio.create_task(speak(ws, sess, greeting))
    try:
        while True:
            data = await ws.receive_json()
            t = data.get("type")
            if t == "ping":
                await ws.send_json({"type": "stats", "data": await get_stats()})
            elif t == "stop":
                kill_speech(); sess.speaking = False
                await ws.send_json({"type": "speech_done"})
            elif t == "chat":
                text = data.get("text","").strip()
                if not text: continue
                kill_speech(); sess.speaking = False
                await ws.send_json({"type": "thinking"})
                full = ""
                async for chunk in ask_simon(sess, text):
                    if chunk.startswith(_TOOL_MARKER):
                        # Broadcast tool_use event — HUD shows which tool is firing
                        tool_name = chunk[len(_TOOL_MARKER):]
                        await ws.send_json({"type": "tool_use", "tool": tool_name})
                    else:
                        full += chunk
                        await ws.send_json({"type": "chunk", "text": chunk})
                await ws.send_json({"type": "done", "text": full})
                asyncio.create_task(speak(ws, sess, full))
            elif t == "clear":
                sess = Session(); sessions[sid] = sess
                await ws.send_json({"type": "cleared"})
    except WebSocketDisconnect:
        kill_speech(); sessions.pop(sid, None)
    except Exception as e:
        print(f"[WS] {e}"); kill_speech(); sessions.pop(sid, None)

# ─────────────────────────────────────────────────────────────
#  HTTP ENDPOINTS
# ─────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse((BASE / "hud.html").read_text())

@app.get("/api/status")
async def status(): return JSONResponse(await get_stats())

@app.post("/api/send_notification")
async def send_notification(request):
    """Send a custom iMessage. Body: {message: str, phone: str}"""
    try:
        body  = await request.json()
        msg   = body.get("message", "")
        phone = body.get("phone", NOTIF_PHONE)
        if not msg:
            return JSONResponse({"status": "error", "detail": "no message"}, status_code=400)
        result = await tool_send_imessage(phone, msg)
        return JSONResponse({"status": "sent", "detail": result})
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=500)

@app.post("/api/send_health_report")
async def send_health_report():
    """Called by health check script — reads latest report and sends iMessage."""
    import json as _json
    report_path = Path.home() / ".simon-x" / "reports" / "latest_report.json"
    try:
        report = _json.loads(report_path.read_text())
        issues   = report.get("issues", [])
        warnings = report.get("warnings", [])
        status   = "ISSUES FOUND" if issues else ("WARNINGS" if warnings else "ALL CLEAR")
        mem   = report.get("memory", {})
        disk  = report.get("disk", {})
        batt  = report.get("battery", {})
        sec   = report.get("security", {})
        freed = report.get("cleanup", {})
        load  = report.get("load_avg", {}).get("1m", "?")
        simon_s = "ONLINE" if report.get("simon", {}).get("running") else "OFFLINE"

        parts = [
            "S.I.M.O.N. System Check",
            report.get("slot", ""),
            report.get("timestamp", ""),
            f"Status: {status}",
            "",
            "VITALS",
            f"CPU: {report.get('cpu_pct', '?')}%  Load: {load}",
            f"RAM: {mem.get('used_gb','?')}GB / {mem.get('total_gb','?')}GB ({mem.get('pct','?')}%)",
            f"Disk: {disk.get('used','?')} used / {disk.get('free','?')} free",
            f"Battery: {batt.get('capacity','?')}% cap | {batt.get('cycles','?')} cycles | {batt.get('status','?')}",
            "",
            "SECURITY",
            f"FileVault:{sec.get('filevault','?')} Firewall:{sec.get('firewall','?')} SIP:{sec.get('sip','?')}",
            "",
            "CLEANUP",
            f"Brew: {freed.get('brew','?')}  pip: {freed.get('pip','?')}",
            f"SIMON: {simon_s}",
        ]
        if issues:
            parts += ["", "ISSUES:"]
            parts += [f"  {i}" for i in issues]
        if warnings:
            parts += ["", "WARNINGS:"]
            parts += [f"  {w}" for w in warnings]

        msg = "\r".join(parts)
        result = await tool_send_imessage(NOTIF_PHONE, msg) if NOTIF_PHONE else "No notification phone configured."
        return JSONResponse({"status": "sent", "detail": result})
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=500)

@app.get("/api/disk")
async def disk_api():
    try:
        out = subprocess.run(["df","-H","/"],capture_output=True,text=True,timeout=5).stdout
        parts = out.splitlines()[1].split() if len(out.splitlines()) > 1 else []
        return JSONResponse({
            "total": parts[1] if len(parts) > 1 else "?",
            "used":  parts[2] if len(parts) > 2 else "?",
            "avail": parts[3] if len(parts) > 3 else "?",
            "pct":   int(parts[4].replace("%","")) if len(parts) > 4 else 0
        })
    except: return JSONResponse({"total":"?","used":"?","avail":"?","pct":0})

@app.get("/api/emails")
async def emails():
    script = '''tell application "Mail"
        set out to ""
        repeat with acct in every account
            set nm to name of acct
            set cnt to 0
            try
                set cnt to unread count of mailbox "INBOX" of acct
            end try
            set out to out & nm & "=" & cnt & ","
        end repeat
        return out
    end tell'''
    try:
        raw = subprocess.run(["/usr/bin/osascript"], input=script,
                             capture_output=True, text=True, timeout=12).stdout.strip().rstrip(",")
        counts = {}
        for pair in raw.split(","):
            if "=" not in pair: continue
            nm, n = pair.rsplit("=", 1)
            nm = nm.strip(); n = int(n.strip())
            # Return all accounts by their actual name — no hardcoded labels
            counts[nm] = n
        return JSONResponse(counts)
    except: return JSONResponse({})

@app.get("/api/calendar")
async def calendar_today():
    today = datetime.now().strftime("%B %d, %Y")
    script = f'''tell application "Calendar"
        set out to ""
        set d to date "{today}"
        repeat with c in every calendar
            try
                set evts to (every event of c whose start date >= d and start date < d + 86400)
                repeat with e in evts
                    set out to out & (summary of e) & "|||" & (start date of e as string) & "~~~"
                end repeat
            end try
        end repeat
        if out is "" then return "NONE"
        return out
    end tell'''
    try:
        raw = subprocess.run(["/usr/bin/osascript"], input=script,
                             capture_output=True, text=True, timeout=15).stdout.strip()
        if raw in ("","NONE"): return JSONResponse([])
        events = []
        for entry in raw.split("~~~"):
            entry = entry.strip()
            if not entry: continue
            parts = entry.split("|||")
            if len(parts) >= 2:
                tm = re.search(r'(\d+:\d+:\d+) (AM|PM)', parts[1])
                events.append({"title": parts[0].strip(),
                                "time":  tm.group(0) if tm else ""})
        return JSONResponse(events)
    except: return JSONResponse([])

if __name__ == "__main__":
    import subprocess as _sp, time as _t
    _sp.run("lsof -ti tcp:8765 | xargs kill -9 2>/dev/null || true", shell=True, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
    _t.sleep(0.5)
    print(f"""
  ┌──────────────────────────────────────────────┐
  │  S.I.M.O.N. v4.2  |  Open Source Project             │
  │  Brain  : {MODEL:<35}│
  │  Voice  : Piper TTS - Alan (British)          │
  │  RAM    : {TOTAL_RAM_GB}GB detected                         │
  │  Tools  : Calendar, iMessage, Mail,           │
  │           Reminders, Shell, Contacts          │
  │  Mode   : Conversation (kill phrase to pause) │
  │  HUD    : http://localhost:8765               │
  └──────────────────────────────────────────────┘
    """)
    uvicorn.run(app, host="0.0.0.0", port=HUD_PORT, log_level="warning")
