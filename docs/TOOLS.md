# Tools Reference — S.I.M.O.N.

> All 14 AI tools available to S.I.M.O.N. — what they do, when the model uses them, and example interactions.

---

## Overview

Tools are called automatically by the LLM when it determines one is needed. You never call tools directly — just speak naturally and SIMON picks the right one.

```
User: "Simon, what's on my calendar this week?"
  → Model selects: get_upcoming_events(days=7)
  → Tool runs, returns events
  → Model generates spoken response
```

---

## Calendar Tools

### `get_todays_events`

Reads all Calendar.app events for today.

**When used:** "What's on my calendar today?" / "What do I have today?" / "Any meetings today?"

**Returns:**
```
Team standup at Saturday, March 14, 2026 at 9:00:00 AM;
Client call at Saturday, March 14, 2026 at 2:00:00 PM;
```

**Example response:**
> "Two things today — standup at nine and a client call at two."

---

### `get_upcoming_events`

Reads Calendar.app events for the next N days.

**Parameters:**
| Param | Type | Default | Description |
|---|---|---|---|
| `days` | int | 7 | How many days ahead to look |

**When used:** "What's coming up this week?" / "Any events in the next 3 days?"

---

### `create_calendar_event`

Creates a new event in Calendar.app.

**Parameters:**
| Param | Type | Required | Description |
|---|---|---|---|
| `title` | string | ✅ | Event name |
| `start` | string | ✅ | e.g. `"March 20, 2026 at 2:00 PM"` |
| `end` | string | ✅ | e.g. `"March 20, 2026 at 3:00 PM"` |
| `calendar` | string | — | Calendar name (default: Personal) |
| `notes` | string | — | Optional notes |

**When used:** "Schedule a meeting with John on Tuesday at 3pm" / "Add dentist appointment Friday at 10"

**Example response:**
> "Done — dentist appointment is on your calendar for Friday at ten."

---

## Messaging Tools

### `get_recent_messages`

Scans **all conversations** for recent messages. iMessage and SMS. Names resolved from contacts cache.

**Parameters:**
| Param | Type | Default | Description |
|---|---|---|---|
| `hours` | int | 24 | How far back to look |
| `limit` | int | 20 | Max messages to return |

**When used:** "Check my messages" / "Any texts today?" / "Did anyone message me?" / "What did I miss?"

**Returns:**
```
[2026-03-14 11:16:58] [iMessage] Jane Smith: Ok that's fine
[2026-03-14 11:16:55] [iMessage] Jane Smith: lol
[2026-03-14 09:15:00] [iMessage] Me: Sounds good
```

**Example response:**
> "Three messages today — Jane said 'ok that's fine' at eleven sixteen, and a health check report came in this morning."

---

### `read_imessages`

Reads messages from a **specific contact**. Use `get_recent_messages` for general checks.

**Parameters:**
| Param | Type | Required | Description |
|---|---|---|---|
| `contact` | string | ✅ | Name or phone number |
| `limit` | int | — | Messages to return (default 10) |

**When used:** "Show me my texts from John" / "What did Sarah say?" / "Read my messages from +13125551234"

---

### `send_imessage`

Sends an iMessage or SMS via Messages.app.

**Parameters:**
| Param | Type | Required | Description |
|---|---|---|---|
| `to` | string | ✅ | Contact name, phone number, or email |
| `message` | string | ✅ | Message text |

**When used:** "Text John I'll be 10 minutes late" / "Send a message to Sarah saying dinner is at 7"

**How it works:**
1. If `to` is a name, looks up phone via Contacts search
2. Normalizes phone to 10-digit format (macOS Messages requirement)
3. Tries iMessage account first, falls back to SMS

**Example response:**
> "Sent."

---

## Email Tools

### `get_unread_emails`

Reads unread messages from Mail.app inboxes.

**Parameters:**
| Param | Type | Default | Description |
|---|---|---|---|
| `limit` | int | 5 | Max emails to return |
| `account` | string | — | Filter by account name |

**When used:** "Check my email" / "Any unread emails?" / "Check my work email"

**Returns:**
```
From: boss@company.com | Subject: Q4 Review tomorrow;
From: noreply@bank.com | Subject: Statement ready;
```

---

### `send_email`

Drafts and sends an email via Mail.app.

**Parameters:**
| Param | Type | Required | Description |
|---|---|---|---|
| `to` | string | ✅ | Recipient email address |
| `subject` | string | ✅ | Subject line |
| `body` | string | ✅ | Email body |
| `account` | string | — | Sending account (optional) |

**When used:** "Email John at john@company.com about the meeting tomorrow"

---

## Reminders Tools

### `get_reminders`

Reads pending reminders from Reminders.app.

**Parameters:**
| Param | Type | Default | Description |
|---|---|---|---|
| `list_name` | string | — | Filter by list (all lists if omitted) |
| `limit` | int | 10 | Max reminders |

**When used:** "What are my reminders?" / "Check my to-do list"

---

### `create_reminder`

Creates a new reminder in Reminders.app.

**Parameters:**
| Param | Type | Required | Description |
|---|---|---|---|
| `title` | string | ✅ | Reminder text |
| `due_date` | string | — | e.g. `"March 20, 2026 at 9:00 AM"` |
| `list_name` | string | — | Reminders list (default: Reminders) |

**When used:** "Remind me to call the dentist tomorrow at 9" / "Set a reminder to submit the report by Friday"

---

## Contacts Tools

### `search_contacts`

Searches macOS Contacts for a person by name.

**Parameters:**
| Param | Type | Required | Description |
|---|---|---|---|
| `query` | string | ✅ | Name or partial name |

**Returns:** Name, all phones, all emails.

**When used:** Primarily used internally by `send_imessage` and `read_imessages` to resolve names to phone numbers.

---

## System Tools

### `get_system_status`

Reports current system health.

**Returns:**
```
CPU: 18.5% | RAM: 14.2GB / 24GB | Disk: 12G used, 911G free (2%)
```

**When used:** "System check" / "How's the machine running?" / "What's my CPU usage?"

**Example response:**
> "Everything is running beautifully — CPU at eighteen percent, fourteen gigabytes of RAM in use. Plenty of runway."

---

### `run_shell`

Runs a safe shell command on the Mac.

**Parameters:**
| Param | Type | Required | Description |
|---|---|---|---|
| `command` | string | ✅ | Shell command to execute |

**Blocked commands (safety):** `rm -rf`, `mkfs`, `dd if=`, `shutdown`, `reboot`, `sudo rm`, `chmod 777`, and others.

**When used:** "What's my IP address?" / "How many files are in my downloads folder?" / "Run the backup script"

---

## Knowledge Base Tools

### `remember`

Stores a fact permanently in the local KB. Survives all restarts.

**Parameters:**
| Param | Type | Required | Description |
|---|---|---|---|
| `key` | string | ✅ | Short identifier e.g. `dentist_name` |
| `value` | string | ✅ | The fact to store |
| `category` | string | — | `person\|preference\|fact\|task\|note` |

**When used:** "Simon, remember that my gym days are Monday, Wednesday, Friday" / "Remember that the client meeting is always on Thursdays"

**Example response:**
> "Remembered — gym days logged under preferences."

---

### `recall`

Searches the local KB memory for stored facts.

**Parameters:**
| Param | Type | Required | Description |
|---|---|---|---|
| `query` | string | ✅ | What to search for |

**When used:** "What do you remember about my trading?" / "Do you know my doctor's name?" / "What have I told you about my schedule?"

**Example response:**
> "Trading — you use your preferred trading platform for options and forex."

---

## Tool Selection Logic

The LLM selects tools based on the tool description strings. Key routing rules:

| If the user says... | Tool selected |
|---|---|
| "check my messages" (no name) | `get_recent_messages` |
| "what did [name] say" | `read_imessages` |
| "text [name]" | `send_imessage` |
| "calendar today" | `get_todays_events` |
| "this week" / "upcoming" | `get_upcoming_events` |
| "remind me" | `create_reminder` |
| "email [address]" | `send_email` |
| "system check" | `get_system_status` |
| "remember that" | `remember` |
| "what do you remember" | `recall` |

---

## Adding Custom Tools

To add a new tool:

1. **Add the tool definition** to the `TOOLS` list in `jarvis.py`:
```python
{
    "type": "function",
    "function": {
        "name": "my_tool",
        "description": "Clear description of when to use this tool.",
        "parameters": {
            "type": "object",
            "properties": {
                "param": {"type": "string", "description": "..."}
            },
            "required": ["param"]
        }
    }
}
```

2. **Implement the function:**
```python
async def tool_my_tool(param: str) -> str:
    # Your implementation
    return "Result"
```

3. **Register in the dispatcher:**
```python
elif name == "my_tool": return await tool_my_tool(**args)
```

The LLM will call it automatically when the description matches the user's intent.
