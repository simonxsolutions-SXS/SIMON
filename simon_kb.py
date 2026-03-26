#!/usr/bin/env python3
"""
S.I.M.O.N. Knowledge Base — simon_kb.py
Simon-X Solutions | [OWNER_NAME]

100% LOCAL — stored at ~/.simon-x/simon_kb.db — nothing leaves the machine.

DESIGN PHILOSOPHY:
  - messages_cache  : SHORT-LIVED hot buffer only. Auto-expires in 48h.
                      Cleared after SIMON reads it. Never used as permanent storage.
  - contacts        : ONE row per person (name is the key). Phone + email on same row.
                      Deduped on every sync. Persists permanently.
  - memory          : Facts SIMON learns. Permanent. Only grows intentionally.
  - email_senders   : Known senders. Permanent. Deduped by address.
  - session_log     : Brief session summaries. Kept for 30 days, then pruned.

SELF-HEALING:
  - run_maintenance() handles all cleanup automatically.
  - Called on startup and every 6 hours in the background.
  - Deduplicates contacts, expires messages, vacuums, integrity checks.
"""

import sqlite3, re, glob, os
from datetime import datetime, timedelta
from pathlib import Path

KB_PATH     = Path.home() / ".simon-x" / "simon_kb.db"
MESSAGES_DB = Path.home() / "Library" / "Messages" / "chat.db"
AB_SOURCES  = Path.home() / "Library" / "Application Support" / "AddressBook" / "Sources"

# How long message cache rows survive before auto-expiry
MSG_CACHE_TTL_HOURS = 48


# ─────────────────────────────────────────────────────────────
#  SCHEMA  (v2 — one row per contact, proper TTL on messages)
# ─────────────────────────────────────────────────────────────

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA foreign_keys = ON;

-- ONE ROW PER PERSON. Name is the dedup key.
-- Phone and email live on the same row so no duplicates.
CREATE TABLE IF NOT EXISTS contacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,   -- dedup key
    phone      TEXT,                   -- primary phone (E.164)
    phone2     TEXT,                   -- secondary phone
    email      TEXT,                   -- primary email
    email2     TEXT,                   -- secondary email
    importance INTEGER DEFAULT 0,      -- 0=normal 1=important 2=vip
    synced_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_con_name  ON contacts(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_con_phone ON contacts(phone);
CREATE INDEX IF NOT EXISTS idx_con_email ON contacts(email);

-- SHORT-LIVED CACHE ONLY. Rows expire after 48h.
-- Cleared by SIMON after he reads what he needs.
-- msg_rowid deduplicates against chat.db.
CREATE TABLE IF NOT EXISTS messages_cache (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_rowid     INTEGER UNIQUE,     -- dedup key from chat.db
    sender_name   TEXT,              -- resolved name (fast lookup)
    sender_handle TEXT,              -- raw phone/email
    is_from_me    INTEGER,
    service       TEXT,
    text          TEXT,
    msg_time      TEXT,              -- ISO local datetime
    expires_at    TEXT NOT NULL,     -- auto-expire timestamp
    read_by_simon INTEGER DEFAULT 0  -- 1 = SIMON has read this, safe to clear
);
CREATE INDEX IF NOT EXISTS idx_msg_time    ON messages_cache(msg_time DESC);
CREATE INDEX IF NOT EXISTS idx_msg_expires ON messages_cache(expires_at);
CREATE INDEX IF NOT EXISTS idx_msg_rowid   ON messages_cache(msg_rowid);

-- Known email senders. Deduped by address.
CREATE TABLE IF NOT EXISTS email_senders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    address    TEXT UNIQUE NOT NULL,
    name       TEXT,
    domain     TEXT,
    importance INTEGER DEFAULT 0,
    first_seen TEXT,
    last_seen  TEXT,
    msg_count  INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sender_addr ON email_senders(address);

-- PERMANENT memory. Survives all restarts.
CREATE TABLE IF NOT EXISTS memory (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT UNIQUE NOT NULL,
    value      TEXT NOT NULL,
    category   TEXT DEFAULT 'general',
    source     TEXT DEFAULT 'user_stated',
    confidence REAL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    times_used INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mem_key ON memory(key);
CREATE INDEX IF NOT EXISTS idx_mem_cat ON memory(category);

-- Session summaries — pruned after 30 days.
CREATE TABLE IF NOT EXISTS session_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at   TEXT,
    summary    TEXT,
    tool_calls INTEGER DEFAULT 0,
    msg_count  INTEGER DEFAULT 0
);

-- Sync schedule tracker.
CREATE TABLE IF NOT EXISTS sync_state (
    table_name TEXT PRIMARY KEY,
    last_sync  TEXT,
    rows_synced INTEGER DEFAULT 0,
    status     TEXT DEFAULT 'ok'
);
"""


# ─────────────────────────────────────────────────────────────
#  CONNECTION
# ─────────────────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    KB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(KB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ─────────────────────────────────────────────────────────────
#  PHONE NORMALIZATION
# ─────────────────────────────────────────────────────────────

def _normalize_phone(raw: str) -> str:
    digits = re.sub(r"[^\d]", "", (raw or "").strip())
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return f"+{digits}" if digits else ""


# ─────────────────────────────────────────────────────────────
#  CONTACTS SYNC  (one row per person, no duplicates)
# ─────────────────────────────────────────────────────────────

def sync_contacts(force: bool = False) -> int:
    """
    Read all contacts from AddressBook source DBs directly — no AppleScript.
    ONE row per person. Phone and email merged onto the same row.
    Skips if synced within last 6h unless force=True.
    Returns count of new/updated rows.
    """
    conn = get_conn()
    cur  = conn.cursor()

    if not force:
        row = cur.execute(
            "SELECT last_sync FROM sync_state WHERE table_name='contacts'"
        ).fetchone()
        if row and row["last_sync"]:
            if datetime.now() - datetime.fromisoformat(row["last_sync"]) < timedelta(hours=6):
                conn.close()
                return 0

    # Gather all contacts from all AddressBook source DBs
    # Key: name → {"phones": [...], "emails": [...]}
    people: dict[str, dict] = {}

    source_dbs = list(AB_SOURCES.glob("*/AddressBook-v22.abcddb"))
    for db_path in source_dbs:
        try:
            ab = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
            rows = ab.execute("""
                SELECT
                    TRIM(COALESCE(r.ZFIRSTNAME,'') || ' ' || COALESCE(r.ZLASTNAME,'')) as full_name,
                    COALESCE(r.ZORGANIZATION,'') as org,
                    p.ZFULLNUMBER  as phone,
                    e.ZADDRESSNORMALIZED as email
                FROM ZABCDRECORD r
                LEFT JOIN ZABCDPHONENUMBER  p ON p.ZOWNER = r.Z_PK
                LEFT JOIN ZABCDEMAILADDRESS e ON e.ZOWNER = r.Z_PK
                WHERE r.ZFIRSTNAME IS NOT NULL
                   OR r.ZLASTNAME  IS NOT NULL
                   OR r.ZORGANIZATION IS NOT NULL
            """).fetchall()
            ab.close()
        except Exception:
            continue

        for row in rows:
            name  = (row[0] or "").strip() or (row[1] or "").strip()
            phone = _normalize_phone(row[2]) if row[2] else None
            email = (row[3] or "").strip().lower() or None
            if not name:
                continue
            if name not in people:
                people[name] = {"phones": [], "emails": []}
            if phone and phone not in people[name]["phones"]:
                people[name]["phones"].append(phone)
            if email and email not in people[name]["emails"]:
                people[name]["emails"].append(email)

    now   = datetime.now().isoformat()
    count = 0

    for name, data in people.items():
        phones = data["phones"]
        emails = data["emails"]
        p1 = phones[0] if len(phones) > 0 else None
        p2 = phones[1] if len(phones) > 1 else None
        e1 = emails[0] if len(emails) > 0 else None
        e2 = emails[1] if len(emails) > 1 else None

        existing = cur.execute(
            "SELECT id FROM contacts WHERE name=?", (name,)
        ).fetchone()

        if existing:
            # Update — merge in any new phones/emails
            cur.execute("""
                UPDATE contacts SET
                    phone=COALESCE(?,phone), phone2=COALESCE(?,phone2),
                    email=COALESCE(?,email), email2=COALESCE(?,email2),
                    synced_at=?
                WHERE name=?
            """, (p1, p2, e1, e2, now, name))
        else:
            cur.execute("""
                INSERT INTO contacts (name, phone, phone2, email, email2, synced_at)
                VALUES (?,?,?,?,?,?)
            """, (name, p1, p2, e1, e2, now))
            count += 1

    cur.execute("""
        INSERT INTO sync_state (table_name, last_sync, rows_synced, status)
        VALUES ('contacts',?,?,'ok')
        ON CONFLICT(table_name) DO UPDATE SET
            last_sync=excluded.last_sync, rows_synced=excluded.rows_synced, status='ok'
    """, (now, count))

    conn.commit()
    conn.close()
    return count


def resolve_name(handle: str, conn: sqlite3.Connection = None) -> str:
    """
    Instant sub-millisecond name lookup from the local contacts cache.
    Tries primary phone, then secondary phone, then last-10-digits match.
    """
    if not handle:
        return "Unknown"
    close = conn is None
    if close:
        conn = get_conn()

    if "@" in handle:
        normalized = handle.lower().strip()
        row = conn.execute(
            "SELECT name FROM contacts WHERE email=? OR email2=? LIMIT 1",
            (normalized, normalized)
        ).fetchone()
    else:
        normalized = _normalize_phone(handle)
        row = conn.execute(
            "SELECT name FROM contacts WHERE phone=? OR phone2=? LIMIT 1",
            (normalized, normalized)
        ).fetchone()
        if not row:
            digits = re.sub(r"[^\d]", "", handle)[-10:]
            row = conn.execute(
                "SELECT name FROM contacts WHERE phone LIKE ? OR phone2 LIKE ? LIMIT 1",
                (f"%{digits}", f"%{digits}")
            ).fetchone()

    if close:
        conn.close()
    return row["name"] if row else handle


# ─────────────────────────────────────────────────────────────
#  MESSAGES — SHORT-LIVED CACHE WITH TTL
# ─────────────────────────────────────────────────────────────

def sync_messages(hours_back: int = 48) -> int:
    """
    Pull new messages from chat.db WAL into the local cache.
    Each row gets an expires_at = now + MSG_CACHE_TTL_HOURS.
    Only adds rows not already cached (msg_rowid is the dedup key).
    """
    if not MESSAGES_DB.exists():
        return 0

    kb_conn  = get_conn()
    msg_uri  = f"file:{MESSAGES_DB}?mode=ro"

    try:
        msg_conn = sqlite3.connect(msg_uri, uri=True, timeout=5)
        msg_conn.row_factory = sqlite3.Row
    except Exception as e:
        kb_conn.close()
        raise RuntimeError(f"Cannot open Messages DB: {e}")

    cutoff_dt   = datetime.now() - timedelta(hours=hours_back)
    apple_epoch = datetime(2001, 1, 1)
    cutoff_ns   = int((cutoff_dt - apple_epoch).total_seconds()) * 1_000_000_000

    rows = msg_conn.execute("""
        SELECT
            m.ROWID as msg_rowid,
            COALESCE(h.id, c.chat_identifier, '') as sender_handle,
            m.text, m.is_from_me, m.service,
            datetime(m.date/1000000000 + 978307200, 'unixepoch', 'localtime') as msg_time
        FROM message m
        LEFT JOIN handle h ON m.handle_id = h.ROWID
        LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        LEFT JOIN chat c ON c.ROWID = cmj.chat_id
        WHERE m.date >= ? AND m.text IS NOT NULL AND m.text != ''
        GROUP BY m.ROWID ORDER BY m.date DESC
    """, (cutoff_ns,)).fetchall()
    msg_conn.close()

    now        = datetime.now()
    expires_at = (now + timedelta(hours=MSG_CACHE_TTL_HOURS)).isoformat()
    now_str    = now.isoformat()
    count      = 0
    cur        = kb_conn.cursor()

    for row in rows:
        if cur.execute("SELECT id FROM messages_cache WHERE msg_rowid=?",
                       (row["msg_rowid"],)).fetchone():
            continue

        sender_name = "[OWNER]" if row["is_from_me"] else resolve_name(row["sender_handle"], kb_conn)

        cur.execute("""
            INSERT OR IGNORE INTO messages_cache
                (msg_rowid, sender_name, sender_handle, is_from_me,
                 service, text, msg_time, expires_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (row["msg_rowid"], sender_name, row["sender_handle"],
              row["is_from_me"], row["service"], row["text"],
              row["msg_time"], expires_at))
        count += 1

    cur.execute("""
        INSERT INTO sync_state (table_name, last_sync, rows_synced, status)
        VALUES ('messages',?,?,'ok')
        ON CONFLICT(table_name) DO UPDATE SET
            last_sync=excluded.last_sync, rows_synced=excluded.rows_synced, status='ok'
    """, (now_str, count))

    kb_conn.commit()
    kb_conn.close()
    return count


def query_messages(hours: int = 24, limit: int = 30,
                   contact: str = None, mark_read: bool = True) -> list:
    """
    Query the message cache. Optionally marks returned rows as read.
    Expired rows are never returned — maintenance clears them later.
    """
    conn = get_conn()
    now  = datetime.now().isoformat()
    base = "msg_time >= datetime('now', ?, 'localtime') AND expires_at > ?"
    params_base = (f"-{hours} hours", now)

    if contact:
        rows = conn.execute(f"""
            SELECT sender_name, sender_handle, is_from_me, service, text, msg_time, id
            FROM messages_cache
            WHERE {base} AND (sender_name LIKE ? OR sender_handle LIKE ?)
            ORDER BY msg_time DESC LIMIT ?
        """, (*params_base, f"%{contact}%", f"%{contact}%", limit)).fetchall()
    else:
        rows = conn.execute(f"""
            SELECT sender_name, sender_handle, is_from_me, service, text, msg_time, id
            FROM messages_cache
            WHERE {base}
            ORDER BY msg_time DESC LIMIT ?
        """, (*params_base, limit)).fetchall()

    result = [dict(r) for r in rows]

    if mark_read and result:
        ids = [r["id"] for r in result]
        conn.execute(
            f"UPDATE messages_cache SET read_by_simon=1 WHERE id IN ({','.join('?'*len(ids))})",
            ids
        )
        conn.commit()

    conn.close()
    return result


def clear_read_messages() -> int:
    """Delete all message cache rows that SIMON has already read."""
    conn = get_conn()
    cur  = conn.execute("DELETE FROM messages_cache WHERE read_by_simon=1")
    n    = cur.rowcount
    conn.commit()
    conn.close()
    return n


# ─────────────────────────────────────────────────────────────
#  SELF-HEALING MAINTENANCE
# ─────────────────────────────────────────────────────────────

def run_maintenance(verbose: bool = False) -> dict:
    """
    Full self-healing maintenance pass. Safe to call any time.
    Runs automatically on startup and every 6h in background.

    Operations (in order):
      1. Integrity check — abort if DB is corrupt
      2. Expire old message cache rows (TTL exceeded)
      3. Clear read message cache rows (SIMON already consumed them)
      4. Deduplicate contacts (merge any stragglers with same name)
      5. Prune session log (keep last 30 days only)
      6. Prune email_senders if > 5000 rows (drop lowest importance/oldest)
      7. VACUUM — reclaim free pages, defrag the file
    """
    conn    = get_conn()
    now     = datetime.now().isoformat()
    report  = {}

    def log(msg):
        if verbose:
            print(f"[KB MAINTENANCE] {msg}")

    # 1. INTEGRITY CHECK
    ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
    report["integrity"] = ok
    if ok != "ok":
        conn.close()
        raise RuntimeError(f"KB integrity check failed: {ok}")
    log("Integrity: ok")

    # 2. EXPIRE TTL-exceeded messages
    cur = conn.execute(
        "DELETE FROM messages_cache WHERE expires_at <= ?", (now,)
    )
    report["messages_expired"] = cur.rowcount
    log(f"Expired messages: {cur.rowcount}")

    # 3. CLEAR already-read messages
    cur = conn.execute("DELETE FROM messages_cache WHERE read_by_simon=1")
    report["messages_cleared"] = cur.rowcount
    log(f"Cleared read messages: {cur.rowcount}")

    # 4. DEDUPLICATE CONTACTS
    # Find any duplicate names (can happen if sync runs before schema migration)
    dup_names = conn.execute("""
        SELECT name FROM contacts GROUP BY name HAVING COUNT(*) > 1
    """).fetchall()
    deduped = 0
    for row in dup_names:
        name = row["name"]
        # Keep the row with the most data, delete the rest
        all_rows = conn.execute("""
            SELECT id, phone, phone2, email, email2
            FROM contacts WHERE name=? ORDER BY
                (phone IS NOT NULL) + (email IS NOT NULL) +
                (phone2 IS NOT NULL) + (email2 IS NOT NULL) DESC
        """, (name,)).fetchall()
        if len(all_rows) < 2:
            continue
        keeper = all_rows[0]
        # Merge phones/emails from all duplicates into keeper
        phones = set(filter(None, [r["phone"] for r in all_rows] + [r["phone2"] for r in all_rows]))
        emails = set(filter(None, [r["email"] for r in all_rows] + [r["email2"] for r in all_rows]))
        phones = list(phones)
        emails = list(emails)
        conn.execute("""
            UPDATE contacts SET
                phone=?, phone2=?, email=?, email2=?, synced_at=?
            WHERE id=?
        """, (
            phones[0] if len(phones) > 0 else None,
            phones[1] if len(phones) > 1 else None,
            emails[0] if len(emails) > 0 else None,
            emails[1] if len(emails) > 1 else None,
            now, keeper["id"]
        ))
        # Delete all other duplicates
        other_ids = [r["id"] for r in all_rows[1:]]
        conn.execute(
            f"DELETE FROM contacts WHERE id IN ({','.join('?'*len(other_ids))})",
            other_ids
        )
        deduped += len(other_ids)

    report["contacts_deduped"] = deduped
    log(f"Contacts deduped: {deduped} duplicates removed")

    # 5. PRUNE SESSION LOG (keep 30 days)
    cutoff_sessions = (datetime.now() - timedelta(days=30)).isoformat()
    cur = conn.execute(
        "DELETE FROM session_log WHERE started_at < ?", (cutoff_sessions,)
    )
    report["sessions_pruned"] = cur.rowcount
    log(f"Old sessions pruned: {cur.rowcount}")

    # 6. PRUNE EMAIL SENDERS if bloated (keep top 5000 by importance + recency)
    sender_count = conn.execute("SELECT COUNT(*) FROM email_senders").fetchone()[0]
    if sender_count > 5000:
        conn.execute("""
            DELETE FROM email_senders WHERE id NOT IN (
                SELECT id FROM email_senders
                ORDER BY importance DESC, last_seen DESC LIMIT 5000
            )
        """)
        report["senders_pruned"] = sender_count - 5000
        log(f"Email senders pruned: {sender_count - 5000} removed")
    else:
        report["senders_pruned"] = 0

    conn.commit()
    conn.close()

    # 7. VACUUM — must run outside transaction, reclaims free pages
    conn2 = sqlite3.connect(str(KB_PATH), timeout=10)
    conn2.execute("VACUUM")
    conn2.close()
    report["vacuumed"] = True
    log("VACUUM complete")

    # Report final sizes
    s = kb_status()
    report["final_size_kb"] = s["kb_size_kb"]
    report["contacts"]      = s["contacts"]
    report["messages"]      = s["messages"]
    report["memory"]        = s["memory"]
    log(f"Final: {s['kb_size_kb']}KB | {s['contacts']} contacts | {s['messages']} messages | {s['memory']} memory")

    return report


# ─────────────────────────────────────────────────────────────
#  MEMORY — permanent facts
# ─────────────────────────────────────────────────────────────

def memory_set(key: str, value: str, category: str = "general",
               source: str = "user_stated", confidence: float = 1.0) -> None:
    conn = get_conn()
    now  = datetime.now().isoformat()
    conn.execute("""
        INSERT INTO memory (key, value, category, source, confidence, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(key) DO UPDATE SET
            value=excluded.value, category=excluded.category,
            source=excluded.source, confidence=excluded.confidence,
            updated_at=excluded.updated_at
    """, (key, value, category, source, confidence, now, now))
    conn.commit()
    conn.close()


def memory_get(key: str) -> str | None:
    conn = get_conn()
    row  = conn.execute("SELECT value FROM memory WHERE key=?", (key,)).fetchone()
    if row:
        conn.execute("UPDATE memory SET times_used=times_used+1 WHERE key=?", (key,))
        conn.commit()
    conn.close()
    return row["value"] if row else None


def memory_search(query: str, category: str = None, limit: int = 10) -> list:
    conn = get_conn()
    if category:
        rows = conn.execute("""
            SELECT key, value, category, confidence FROM memory
            WHERE (key LIKE ? OR value LIKE ?) AND category=?
            ORDER BY confidence DESC, times_used DESC LIMIT ?
        """, (f"%{query}%", f"%{query}%", category, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT key, value, category, confidence FROM memory
            WHERE key LIKE ? OR value LIKE ?
            ORDER BY confidence DESC, times_used DESC LIMIT ?
        """, (f"%{query}%", f"%{query}%", limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def memory_dump(category: str = None) -> list:
    conn = get_conn()
    if category:
        rows = conn.execute(
            "SELECT key, value, category, confidence FROM memory "
            "WHERE category=? ORDER BY key", (category,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT key, value, category, confidence FROM memory "
            "ORDER BY category, key"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def memory_delete(key: str) -> bool:
    conn = get_conn()
    cur  = conn.execute("DELETE FROM memory WHERE key=?", (key,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def memory_as_context_string() -> str:
    """Compact string injected into SIMON's system prompt every session."""
    rows = memory_dump()
    if not rows:
        return ""
    return "\n".join(f"[{r['category']}] {r['key']}: {r['value']}" for r in rows)


# ─────────────────────────────────────────────────────────────
#  EMAIL SENDERS
# ─────────────────────────────────────────────────────────────

def upsert_email_sender(address: str, name: str = None, importance: int = 0) -> None:
    conn   = get_conn()
    now    = datetime.now().isoformat()
    domain = address.split("@")[-1].lower() if "@" in address else ""
    conn.execute("""
        INSERT INTO email_senders (address, name, domain, importance, first_seen, last_seen, msg_count)
        VALUES (?,?,?,?,?,?,1)
        ON CONFLICT(address) DO UPDATE SET
            name=COALESCE(excluded.name, name), last_seen=excluded.last_seen,
            msg_count=msg_count+1, importance=MAX(importance, excluded.importance)
    """, (address.lower(), name, domain, importance, now, now))
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────
#  SESSION LOG
# ─────────────────────────────────────────────────────────────

def session_start() -> int:
    conn = get_conn()
    cur  = conn.execute("INSERT INTO session_log (started_at) VALUES (?)",
                        (datetime.now().isoformat(),))
    sid  = cur.lastrowid
    conn.commit()
    conn.close()
    return sid


def session_end(session_id: int, summary: str,
                tool_calls: int = 0, msg_count: int = 0) -> None:
    conn = get_conn()
    conn.execute("""
        UPDATE session_log SET ended_at=?, summary=?, tool_calls=?, msg_count=?
        WHERE id=?
    """, (datetime.now().isoformat(), summary, tool_calls, msg_count, session_id))
    conn.commit()
    conn.close()


def get_recent_sessions(limit: int = 3) -> list:
    conn = get_conn()
    rows = conn.execute("""
        SELECT started_at, summary FROM session_log
        WHERE summary IS NOT NULL ORDER BY started_at DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────
#  STATUS & SYNC
# ─────────────────────────────────────────────────────────────

def kb_status() -> dict:
    conn = get_conn()
    s = {
        "kb_path":    str(KB_PATH),
        "kb_size_kb": round(KB_PATH.stat().st_size / 1024, 1) if KB_PATH.exists() else 0,
        "contacts":   conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],
        "messages":   conn.execute("SELECT COUNT(*) FROM messages_cache").fetchone()[0],
        "memory":     conn.execute("SELECT COUNT(*) FROM memory").fetchone()[0],
        "sessions":   conn.execute("SELECT COUNT(*) FROM session_log").fetchone()[0],
        "senders":    conn.execute("SELECT COUNT(*) FROM email_senders").fetchone()[0],
        "sync":       {},
    }
    for row in conn.execute("SELECT * FROM sync_state").fetchall():
        s["sync"][row["table_name"]] = {
            "last_sync": row["last_sync"], "rows": row["rows_synced"]
        }
    conn.close()
    return s


def sync_all(force: bool = False) -> dict:
    results = {}
    try:
        n = sync_contacts(force=force)
        results["contacts"] = f"{n} upserted"
    except Exception as e:
        results["contacts"] = f"ERROR: {e}"
    try:
        n = sync_messages(hours_back=48)
        results["messages"] = f"{n} new"
    except Exception as e:
        results["messages"] = f"ERROR: {e}"
    return results


# ─────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "init":
        get_conn().close()
        print(f"✅ KB initialized — {KB_PATH}")

    elif cmd == "sync":
        force = "--force" in sys.argv
        r = sync_all(force=force)
        for k, v in r.items():
            print(f"  {k:12} {v}")
        s = kb_status()
        print(f"\n  {s['contacts']} contacts | {s['messages']} messages | "
              f"{s['memory']} memory | {s['kb_size_kb']} KB")

    elif cmd == "maintain":
        verbose = "--verbose" in sys.argv or "-v" in sys.argv
        print("Running self-healing maintenance...")
        r = run_maintenance(verbose=verbose)
        print(f"\n  Integrity:       {r['integrity']}")
        print(f"  Msgs expired:    {r['messages_expired']}")
        print(f"  Msgs cleared:    {r['messages_cleared']}")
        print(f"  Contacts deduped:{r['contacts_deduped']} duplicates removed")
        print(f"  Sessions pruned: {r['sessions_pruned']}")
        print(f"  Vacuumed:        {r['vacuumed']}")
        print(f"\n  Final: {r['final_size_kb']} KB | {r['contacts']} contacts | "
              f"{r['messages']} messages | {r['memory']} memory facts")

    elif cmd == "status":
        s = kb_status()
        print(f"\n  S.I.M.O.N. Knowledge Base — 100% LOCAL on this Mac")
        print(f"  Path:     {s['kb_path']}")
        print(f"  Size:     {s['kb_size_kb']} KB")
        print(f"  Contacts: {s['contacts']} (one row per person, no duplicates)")
        print(f"  Messages: {s['messages']} (48h TTL cache, auto-expires)")
        print(f"  Memory:   {s['memory']} permanent facts")
        print(f"  Sessions: {s['sessions']}")
        for t, i in s["sync"].items():
            print(f"  [{t}] last sync: {i['last_sync']}")

    elif cmd == "memory":
        sub = sys.argv[2] if len(sys.argv) > 2 else "list"
        if sub == "set" and len(sys.argv) >= 5:
            cat = sys.argv[5] if len(sys.argv) > 5 else "general"
            memory_set(sys.argv[3], sys.argv[4], category=cat)
            print(f"✅ [{cat}] {sys.argv[3]} = {sys.argv[4]}")
        elif sub == "get" and len(sys.argv) >= 4:
            print(memory_get(sys.argv[3]) or "(not found)")
        elif sub == "delete" and len(sys.argv) >= 4:
            print("✅ Deleted" if memory_delete(sys.argv[3]) else "Not found")
        elif sub == "search" and len(sys.argv) >= 4:
            for r in memory_search(sys.argv[3]):
                print(f"  [{r['category']:12}] {r['key']:30} = {r['value'][:60]}")
        else:
            rows = memory_dump()
            if not rows:
                print("  (no memory stored yet)")
            for r in rows:
                print(f"  [{r['category']:12}] {r['key']:30} = {r['value'][:60]}")

    elif cmd == "messages":
        hours = int(sys.argv[2]) if len(sys.argv) > 2 else 24
        msgs  = query_messages(hours=hours, mark_read=False)  # CLI peek — don't mark read
        print(f"\n  Messages in last {hours}h ({len(msgs)} from cache)\n")
        for m in reversed(msgs):
            sender = "[OWNER]" if m["is_from_me"] else m["sender_name"]
            print(f"  {m['msg_time']}  {sender:20}  {str(m['text'])[:55]}")

    elif cmd == "contacts":
        q    = sys.argv[2] if len(sys.argv) > 2 else ""
        conn = get_conn()
        rows = conn.execute(
            "SELECT name, phone, email FROM contacts "
            "WHERE name LIKE ? OR phone LIKE ? ORDER BY name LIMIT 30",
            (f"%{q}%", f"%{q}%")
        ).fetchall() if q else conn.execute(
            "SELECT name, phone, email FROM contacts ORDER BY name LIMIT 30"
        ).fetchall()
        conn.close()
        for r in rows:
            print(f"  {r['name']:30} {str(r['phone'] or ''):20} {str(r['email'] or '')}")

    elif cmd == "clear-messages":
        n = clear_read_messages()
        print(f"✅ Cleared {n} read message cache rows")

    else:
        print("Commands: init | sync [--force] | maintain [-v] | status | "
              "memory [set|get|delete|search|list] | messages [hours] | "
              "contacts [query] | clear-messages")
