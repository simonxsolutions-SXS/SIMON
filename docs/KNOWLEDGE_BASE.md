# Knowledge Base — S.I.M.O.N.

> The local SQLite database that gives S.I.M.O.N. instant memory, contact resolution, and message caching. 100% on-device — nothing leaves your Mac.

---

## Overview

```
~/.simon-x/simon_kb.db   (SQLite 3, WAL mode)

┌──────────────────────────────────────────────────────────┐
│  TABLE          │  PURPOSE              │  LIFETIME       │
├──────────────────────────────────────────────────────────┤
│  contacts       │  Name → phone/email   │  Permanent      │
│  messages_cache │  Recent texts         │  48h TTL        │
│  memory         │  Facts about you      │  Permanent      │
│  email_senders  │  Known senders        │  Permanent      │
│  session_log    │  Session summaries    │  30-day rolling │
│  sync_state     │  Sync timestamps      │  Internal       │
└──────────────────────────────────────────────────────────┘
```

**Core principle:** Only `memory` and `contacts` grow permanently. Everything else expires automatically.

---

## Tables

### contacts

One row per person — name is the `UNIQUE` key. Phone and email live on the same row. No duplicate rows for the same person.

```sql
CREATE TABLE contacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,   -- dedup key
    phone      TEXT,                   -- primary phone (E.164: +1XXXXXXXXXX)
    phone2     TEXT,                   -- secondary phone
    email      TEXT,                   -- primary email (lowercase)
    email2     TEXT,                   -- secondary email
    importance INTEGER DEFAULT 0,      -- 0=normal 1=important 2=vip
    synced_at  TEXT NOT NULL
);
```

**Data source:** AddressBook SQLite files at `~/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb`

**Sync frequency:** On startup + every 6 hours (skipped if synced within last 6h)

**Example rows:**
```
name          phone          phone2        email
──────────────────────────────────────────────────────
John Smith    +13125551234   +13125555678  john@email.com
Sarah Jones   +17735559876   NULL          sarah@work.com
```

---

### messages_cache

Short-lived buffer. Every row expires 48 hours after it was cached. Rows are marked `read_by_simon=1` when SIMON reads them and are cleared on the next maintenance pass.

```sql
CREATE TABLE messages_cache (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_rowid     INTEGER UNIQUE,     -- dedup key from chat.db
    sender_name   TEXT,              -- resolved from contacts (pre-cached)
    sender_handle TEXT,              -- raw phone/email
    is_from_me    INTEGER,           -- 1=sent by you
    service       TEXT,              -- 'iMessage' or 'SMS'
    text          TEXT,
    msg_time      TEXT,              -- ISO local datetime
    expires_at    TEXT NOT NULL,     -- auto-expire timestamp (48h from sync)
    read_by_simon INTEGER DEFAULT 0  -- 1 = SIMON consumed this, safe to clear
);
```

**Data source:** `~/Library/Messages/chat.db` (WAL mode, read-only)

**Why 48h TTL?** Messages are informational context. Once SIMON has read and responded, the cached row serves no purpose. Treating the cache as ephemeral keeps the DB small and prevents it accumulating months of texts.

**Lifecycle:**
```
chat.db WAL  →  sync_messages()  →  messages_cache (expires_at = now+48h)
                                              │
                      query_messages() ───────┤
                      (mark_read=True)        │ read_by_simon=1
                                              │
                      run_maintenance() ──────┘ DELETE WHERE read_by_simon=1
                                                DELETE WHERE expires_at <= now
```

---

### memory

Permanent key-value store for facts SIMON learns about you. Survives all restarts. Only grows when you explicitly tell SIMON something or SIMON infers something with high confidence.

```sql
CREATE TABLE memory (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT UNIQUE NOT NULL,   -- e.g. 'wife_name', 'gym_days'
    value      TEXT NOT NULL,
    category   TEXT DEFAULT 'general', -- person | preference | fact | task | note
    source     TEXT DEFAULT 'user_stated',
    confidence REAL DEFAULT 1.0,       -- 0.0–1.0
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    times_used INTEGER DEFAULT 0       -- access counter
);
```

**Categories:**

| Category | Use for |
|---|---|
| `person` | Contact facts — wife's name, doctor, key relationships |
| `preference` | How you like things — trading platform, languages, habits |
| `fact` | Objective facts — machine specs, company name |
| `task` | Ongoing tasks or projects |
| `note` | Anything else |

**Memory is injected into every system prompt:**
```
PERSISTENT MEMORY (stored locally, survives all restarts):
[person] wife_contact: Jane
[preference] trading_platform: your preferred platform
[fact] machine_specs: MacBook Air, 16GB RAM, 512GB
```

**Via voice:**
> "Simon, remember that my dentist is Dr. Patel on Michigan Avenue"

**Via CLI:**
```bash
python3.11 simon_kb.py memory set "dentist" "Dr. Patel, Michigan Ave" person
python3.11 simon_kb.py memory get "dentist"
python3.11 simon_kb.py memory list
python3.11 simon_kb.py memory search "doctor"
python3.11 simon_kb.py memory delete "dentist"
```

---

### email_senders

Known email senders indexed by address. Used for quick name resolution and spam filtering.

```sql
CREATE TABLE email_senders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    address    TEXT UNIQUE NOT NULL,
    name       TEXT,
    domain     TEXT,
    importance INTEGER DEFAULT 0,  -- 0=normal 1=known 2=vip 3=junk
    first_seen TEXT,
    last_seen  TEXT,
    msg_count  INTEGER DEFAULT 0
);
```

Pruned to 5,000 rows max (lowest importance + oldest dropped first).

---

### session_log

Brief summaries of SIMON sessions for continuity. Rolling 30-day window.

```sql
CREATE TABLE session_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at   TEXT,
    summary    TEXT,       -- 2-3 sentence recap
    tool_calls INTEGER DEFAULT 0,
    msg_count  INTEGER DEFAULT 0
);
```

---

## Self-Healing Maintenance

`run_maintenance()` runs automatically on startup and every 6 hours. It is safe to call any time.

**What it does (in order):**

```
1. INTEGRITY CHECK
   └─ PRAGMA integrity_check
   └─ Aborts if DB is corrupt — protects data

2. EXPIRE TTL-exceeded messages
   └─ DELETE FROM messages_cache WHERE expires_at <= now

3. CLEAR read messages
   └─ DELETE FROM messages_cache WHERE read_by_simon = 1

4. DEDUPLICATE CONTACTS
   └─ Find any duplicate names
   └─ Merge phones/emails from all duplicates into one row
   └─ Delete extras

5. PRUNE SESSION LOG
   └─ DELETE WHERE started_at < (now - 30 days)

6. PRUNE EMAIL SENDERS
   └─ Only if > 5,000 rows
   └─ Keep top 5,000 by importance DESC, last_seen DESC

7. VACUUM
   └─ Reclaims free pages
   └─ Defragments the file
   └─ Runs outside transaction (required by SQLite)
```

**Run manually:**
```bash
python3.11 simon_kb.py maintain            # run maintenance
python3.11 simon_kb.py maintain --verbose  # with step-by-step output
```

**Example verbose output:**
```
Running self-healing maintenance...
[KB MAINTENANCE] Integrity: ok
[KB MAINTENANCE] Expired messages: 8
[KB MAINTENANCE] Cleared read messages: 5
[KB MAINTENANCE] Contacts deduped: 0 duplicates removed
[KB MAINTENANCE] Old sessions pruned: 3
[KB MAINTENANCE] VACUUM complete
[KB MAINTENANCE] Final: 96.0KB | 142 contacts | 0 messages | 5 memory
```

---

## Sync Schedule

| Event | What syncs | How often |
|---|---|---|
| Startup | Contacts + messages | Every start (contacts skipped if < 6h old) |
| Background task | Messages only | Every 10 minutes |
| Background task | Full maintenance | Every 6 hours |
| Manual | Everything | `simon_kb.py sync --force` |

---

## Performance

All reads from the KB are SQLite indexed queries — typically under 1ms.

| Query | Method | Speed |
|---|---|---|
| `resolve_name("+13125551234")` | `WHERE phone=?` index | <0.5ms |
| `query_messages(hours=24)` | `WHERE msg_time >= ?` index | <2ms |
| `memory_get("wife_name")` | `WHERE key=?` unique index | <0.5ms |
| `memory_search("doctor")` | `WHERE key LIKE ?` | <3ms |

Compare to the previous approach:
- Contact name lookup via AppleScript: **250ms per call**
- Contact name lookup via KB: **<1ms** — 250× faster

---

## CLI Reference

```bash
# Status overview
python3.11 simon_kb.py status

# Sync everything
python3.11 simon_kb.py sync
python3.11 simon_kb.py sync --force    # ignore 6h cooldown

# Self-healing maintenance
python3.11 simon_kb.py maintain
python3.11 simon_kb.py maintain -v     # verbose

# Memory management
python3.11 simon_kb.py memory list
python3.11 simon_kb.py memory set KEY VALUE [category]
python3.11 simon_kb.py memory get KEY
python3.11 simon_kb.py memory search QUERY
python3.11 simon_kb.py memory delete KEY

# Browse cached messages
python3.11 simon_kb.py messages 24     # last 24 hours
python3.11 simon_kb.py messages 48     # last 48 hours

# Search contacts cache
python3.11 simon_kb.py contacts
python3.11 simon_kb.py contacts Smith

# Clear read message cache manually
python3.11 simon_kb.py clear-messages
```

---

## DB Location & Backup

```
~/.simon-x/simon_kb.db      ← main database
~/.simon-x/simon_kb.db-wal  ← WAL file (auto-managed)
~/.simon-x/simon_kb.db-shm  ← shared memory file (auto-managed)
```

**Backup:**
```bash
# Safe backup (SQLite online backup API via CLI)
sqlite3 ~/.simon-x/simon_kb.db ".backup ~/.simon-x/simon_kb_backup.db"
```

**Reset (start fresh):**
```bash
rm ~/.simon-x/simon_kb.db*
python3.11 simon_kb.py init
python3.11 simon_kb.py sync --force
```

> ⚠️ Reset deletes all `memory` entries. Export them first:
> ```bash
> python3.11 simon_kb.py memory list > my_memory_backup.txt
> ```
