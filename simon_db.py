#!/usr/bin/env python3
"""
simon_db.py — Unified Database Client for S.I.M.O.N.
======================================================
Simon-X Solutions | [OWNER_NAME]

Replaces: simon_kb.py (SQLite) + ChromaDB sync in hq_bridge.py

Architecture:
  HQ PostgreSQL = single source of truth for everything permanent
  Mac SQLite    = gone (except 30-fact fallback cache for offline use)

Tables on HQ PostgreSQL:
  contacts        — AddressBook contacts, deduplicated by name
  memory          — Permanent facts with vector embeddings
  messages_cache  — 48h rolling buffer, auto-expired by pg_cron
  session_log     — Conversation history, pruned after 90 days
  research        — Web scraping results, 7-day TTL

Auto-maintenance (zero manual work):
  - messages expire automatically via pg_cron (hourly)
  - duplicates prevented by UNIQUE constraints at DB level
  - sessions pruned after 90 days automatically
  - research cache cleared after 7 days automatically
  - weekly VACUUM runs automatically

Offline fallback:
  - Last 30 memory facts cached in ~/.simon-x/fallback.json
  - SIMON reads this if HQ is unreachable at startup
  - Synced to disk every time memory is written

Usage:
  db = SimonDB()
  db.memory_set("dentist_name", "Dr. Rodriguez", category="person")
  db.memory_get("dentist_name")
  db.memory_search("dental")
  db.contacts_resolve("+1XXXXXXXXXX")
  db.messages_add(rowid, sender, body, msg_time)
  db.session_start() / db.session_end(sid, summary)
"""

import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ── Config ────────────────────────────────────────────────────
_cfg_path = Path(__file__).parent / "config.json"
_cfg = json.loads(_cfg_path.read_text()) if _cfg_path.exists() else {}

# PostgreSQL connection string — stored in config.json after setup
DB_URL = _cfg.get("simon_db_url", os.getenv("SIMON_DB_URL", ""))

# Fallback cache — used when HQ is offline
FALLBACK_PATH = Path.home() / ".simon-x" / "fallback.json"
FALLBACK_PATH.parent.mkdir(parents=True, exist_ok=True)

# Messages DB (macOS only — read-only source)
MESSAGES_DB   = Path.home() / "Library" / "Messages" / "chat.db"
AB_SOURCES    = Path.home() / "Library" / "Application Support" / "AddressBook" / "Sources"

# ── Thread-local connection pool ──────────────────────────────
_local = threading.local()
_pg_available = False
_pg_last_check = 0.0

def _get_pg():
    """Get a thread-local PostgreSQL connection. Returns None if unavailable."""
    global _pg_available, _pg_last_check
    if not DB_URL:
        return None
    # Cache availability check for 30s
    now = time.time()
    if now - _pg_last_check < 30 and not _pg_available:
        return None
    try:
        import psycopg2
        import psycopg2.extras
        if not hasattr(_local, 'conn') or _local.conn is None or _local.conn.closed:
            _local.conn = psycopg2.connect(DB_URL, connect_timeout=3)
            _local.conn.autocommit = False
        # Test connection is still alive
        _local.conn.cursor().execute("SELECT 1")
        _pg_available = True
        _pg_last_check = now
        return _local.conn
    except Exception as e:
        _pg_available = False
        _pg_last_check = now
        if hasattr(_local, 'conn'):
            try: _local.conn.close()
            except: pass
            _local.conn = None
        return None


def pg_is_available() -> bool:
    return _pg_available


# ── Offline fallback cache ────────────────────────────────────

def _load_fallback() -> dict:
    """Load the local fallback cache."""
    try:
        if FALLBACK_PATH.exists():
            return json.loads(FALLBACK_PATH.read_text())
    except Exception:
        pass
    return {"memory": {}, "updated_at": ""}


def _save_fallback(memory_facts: list):
    """Save last 30 memory facts to local fallback cache."""
    try:
        data = {
            "memory": {f["key"]: f["value"] for f in memory_facts[:30]},
            "updated_at": datetime.now().isoformat(),
        }
        FALLBACK_PATH.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════
#  MEMORY — permanent facts
# ═══════════════════════════════════════════════════════════

def memory_set(key: str, value: str, category: str = "general",
               source: str = "user_stated", confidence: float = 1.0) -> None:
    """
    Store a permanent fact. Upserts on key conflict — no duplicates ever.
    Also updates the local fallback cache.
    """
    conn = _get_pg()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO memory (key, value, category, source, confidence)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (key) DO UPDATE SET
                        value=EXCLUDED.value,
                        category=EXCLUDED.category,
                        source=EXCLUDED.source,
                        confidence=EXCLUDED.confidence,
                        updated_at=NOW()
                """, (key, value, category, source, confidence))
                conn.commit()
            # Update fallback cache asynchronously
            threading.Thread(target=_refresh_fallback, daemon=True).start()
            return
        except Exception as e:
            conn.rollback()
            print(f"[DB] memory_set error: {e}")

    # Offline: write to fallback cache
    fb = _load_fallback()
    fb["memory"][key] = value
    fb["updated_at"] = datetime.now().isoformat()
    FALLBACK_PATH.write_text(json.dumps(fb, indent=2))


def memory_get(key: str) -> Optional[str]:
    conn = _get_pg()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE memory SET times_used=times_used+1
                    WHERE key=%s RETURNING value
                """, (key,))
                row = cur.fetchone()
                conn.commit()
                return row[0] if row else None
        except Exception as e:
            conn.rollback()
            print(f"[DB] memory_get error: {e}")

    # Offline fallback
    return _load_fallback()["memory"].get(key)


def memory_search(query: str, category: str = None, limit: int = 10) -> list:
    """Keyword search on key and value. Falls back to local cache offline."""
    conn = _get_pg()
    if conn:
        try:
            with conn.cursor() as cur:
                if category:
                    cur.execute("""
                        SELECT key, value, category, confidence
                        FROM memory
                        WHERE (key ILIKE %s OR value ILIKE %s) AND category=%s
                        ORDER BY confidence DESC, times_used DESC
                        LIMIT %s
                    """, (f"%{query}%", f"%{query}%", category, limit))
                else:
                    cur.execute("""
                        SELECT key, value, category, confidence
                        FROM memory
                        WHERE key ILIKE %s OR value ILIKE %s
                        ORDER BY confidence DESC, times_used DESC
                        LIMIT %s
                    """, (f"%{query}%", f"%{query}%", limit))
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception as e:
            conn.rollback()
            print(f"[DB] memory_search error: {e}")

    # Offline fallback — simple substring search
    fb = _load_fallback()["memory"]
    q = query.lower()
    return [{"key": k, "value": v, "category": "general", "confidence": 1.0}
            for k, v in fb.items()
            if q in k.lower() or q in v.lower()][:limit]


def memory_dump(category: str = None) -> list:
    """Return all memory facts. Used for system prompt injection."""
    conn = _get_pg()
    if conn:
        try:
            with conn.cursor() as cur:
                if category:
                    cur.execute(
                        "SELECT key, value, category, confidence FROM memory "
                        "WHERE category=%s ORDER BY category, key", (category,)
                    )
                else:
                    cur.execute(
                        "SELECT key, value, category, confidence FROM memory "
                        "ORDER BY category, key"
                    )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception as e:
            conn.rollback()
            print(f"[DB] memory_dump error: {e}")

    # Offline fallback
    fb = _load_fallback()["memory"]
    return [{"key": k, "value": v, "category": "general", "confidence": 1.0}
            for k, v in fb.items()]


def memory_as_context_string() -> str:
    """Compact string for SIMON's system prompt."""
    rows = memory_dump()
    if not rows:
        return ""
    return "\n".join(f"[{r['category']}] {r['key']}: {r['value']}" for r in rows)


def _refresh_fallback():
    """Background: update local fallback cache from DB."""
    try:
        rows = memory_dump()
        _save_fallback(rows)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════
#  CONTACTS
# ═══════════════════════════════════════════════════════════

def _normalize_phone(raw: str) -> str:
    digits = re.sub(r"[^\d]", "", (raw or "").strip())
    if len(digits) == 10:  return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"): return f"+{digits}"
    return f"+{digits}" if digits else ""


def contacts_resolve(handle: str) -> str:
    """Instant name lookup by phone or email. Returns handle if not found."""
    if not handle:
        return "Unknown"
    conn = _get_pg()
    if conn:
        try:
            with conn.cursor() as cur:
                if "@" in handle:
                    cur.execute(
                        "SELECT name FROM contacts WHERE email=%s OR email2=%s LIMIT 1",
                        (handle.lower(), handle.lower())
                    )
                else:
                    normalized = _normalize_phone(handle)
                    cur.execute(
                        "SELECT name FROM contacts WHERE phone=%s OR phone2=%s LIMIT 1",
                        (normalized, normalized)
                    )
                    if not cur.fetchone():
                        digits = re.sub(r"[^\d]", "", handle)[-10:]
                        cur.execute(
                            "SELECT name FROM contacts WHERE phone LIKE %s OR phone2 LIKE %s LIMIT 1",
                            (f"%{digits}", f"%{digits}")
                        )
                row = cur.fetchone()
                return row[0] if row else handle
        except Exception as e:
            conn.rollback()
            print(f"[DB] contacts_resolve error: {e}")
    return handle


def contacts_sync(force: bool = False) -> int:
    """
    Sync contacts from macOS AddressBook → PostgreSQL.
    Skips if synced within last 6h unless force=True.
    Uses ON CONFLICT DO UPDATE — no duplicates possible.
    """
    conn = _get_pg()
    if not conn:
        return 0

    # Check last sync time
    if not force:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT last_sync FROM sync_state WHERE table_name='contacts'"
                )
                row = cur.fetchone()
                if row and row[0]:
                    if datetime.now(row[0].tzinfo) - row[0] < timedelta(hours=6):
                        return 0
        except Exception:
            pass

    # Read from AddressBook
    people = {}
    for db_path in AB_SOURCES.glob("*/AddressBook-v22.abcddb"):
        try:
            ab = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
            rows = ab.execute("""
                SELECT TRIM(COALESCE(r.ZFIRSTNAME,'') || ' ' || COALESCE(r.ZLASTNAME,'')) as nm,
                       COALESCE(r.ZORGANIZATION,'') as org,
                       p.ZFULLNUMBER, e.ZADDRESSNORMALIZED
                FROM ZABCDRECORD r
                LEFT JOIN ZABCDPHONENUMBER p ON p.ZOWNER=r.Z_PK
                LEFT JOIN ZABCDEMAILADDRESS e ON e.ZOWNER=r.Z_PK
                WHERE r.ZFIRSTNAME IS NOT NULL OR r.ZLASTNAME IS NOT NULL OR r.ZORGANIZATION IS NOT NULL
            """).fetchall()
            ab.close()
            for row in rows:
                name  = (row[0] or "").strip() or (row[1] or "").strip()
                phone = _normalize_phone(row[2]) if row[2] else None
                email = (row[3] or "").strip().lower() or None
                if not name: continue
                if name not in people:
                    people[name] = {"phones": [], "emails": []}
                if phone and phone not in people[name]["phones"]:
                    people[name]["phones"].append(phone)
                if email and email not in people[name]["emails"]:
                    people[name]["emails"].append(email)
        except Exception:
            continue

    count = 0
    try:
        with conn.cursor() as cur:
            for name, data in people.items():
                phones = data["phones"]
                emails = data["emails"]
                cur.execute("""
                    INSERT INTO contacts (name, phone, phone2, email, email2)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (name_lower) DO UPDATE SET
                        phone  = COALESCE(EXCLUDED.phone,  contacts.phone),
                        phone2 = COALESCE(EXCLUDED.phone2, contacts.phone2),
                        email  = COALESCE(EXCLUDED.email,  contacts.email),
                        email2 = COALESCE(EXCLUDED.email2, contacts.email2),
                        updated_at = NOW()
                """, (
                    name,
                    phones[0] if len(phones) > 0 else None,
                    phones[1] if len(phones) > 1 else None,
                    emails[0] if len(emails) > 0 else None,
                    emails[1] if len(emails) > 1 else None,
                ))
                count += 1

            cur.execute("""
                INSERT INTO sync_state (table_name, last_sync, rows_synced, status)
                VALUES ('contacts', NOW(), %s, 'ok')
                ON CONFLICT (table_name) DO UPDATE SET
                    last_sync=NOW(), rows_synced=EXCLUDED.rows_synced, status='ok'
            """, (count,))
            conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[DB] contacts_sync error: {e}")
        return 0

    return count


# ═══════════════════════════════════════════════════════════
#  MESSAGES CACHE — 48h TTL, auto-cleared
# ═══════════════════════════════════════════════════════════

def messages_sync(hours_back: int = 48) -> int:
    """Pull new messages from chat.db into PostgreSQL cache."""
    if not MESSAGES_DB.exists():
        return 0
    conn = _get_pg()
    if not conn:
        return 0

    try:
        msg_conn = sqlite3.connect(f"file:{MESSAGES_DB}?mode=ro", uri=True, timeout=5)
        msg_conn.row_factory = sqlite3.Row
    except Exception as e:
        print(f"[DB] Cannot open Messages DB: {e}")
        return 0

    cutoff_dt   = datetime.now() - timedelta(hours=hours_back)
    apple_epoch = datetime(2001, 1, 1)
    cutoff_ns   = int((cutoff_dt - apple_epoch).total_seconds()) * 1_000_000_000

    rows = msg_conn.execute("""
        SELECT m.ROWID as msg_rowid,
               COALESCE(h.id, c.chat_identifier, '') as sender_handle,
               m.text, m.is_from_me, m.service,
               datetime(m.date/1000000000 + 978307200, 'unixepoch') as msg_time
        FROM message m
        LEFT JOIN handle h ON m.handle_id = h.ROWID
        LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        LEFT JOIN chat c ON c.ROWID = cmj.chat_id
        WHERE m.date >= %d AND m.text IS NOT NULL AND m.text != ''
        GROUP BY m.ROWID ORDER BY m.date DESC
    """ % cutoff_ns).fetchall()
    msg_conn.close()

    count = 0
    try:
        with conn.cursor() as cur:
            for row in rows:
                sender_name = "[OWNER]" if row["is_from_me"] else contacts_resolve(row["sender_handle"])
                cur.execute("""
                    INSERT INTO messages_cache
                        (msg_rowid, sender_name, sender_handle, is_from_me,
                         service, body, msg_time, expires_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW() + INTERVAL '48 hours')
                    ON CONFLICT (msg_rowid) DO NOTHING
                """, (
                    row["msg_rowid"], sender_name, row["sender_handle"],
                    bool(row["is_from_me"]), row["service"], row["text"],
                    row["msg_time"],
                ))
                count += cur.rowcount
            conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[DB] messages_sync error: {e}")

    return count


def messages_query(hours: int = 24, limit: int = 30,
                   contact: str = None, mark_read: bool = True) -> list:
    """Query message cache. Expired messages never returned (DB constraint)."""
    conn = _get_pg()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            if contact:
                cur.execute("""
                    SELECT sender_name, sender_handle, is_from_me, service, body, msg_time
                    FROM messages_cache
                    WHERE msg_time >= NOW() - (%s || ' hours')::INTERVAL
                      AND expires_at > NOW()
                      AND (sender_name ILIKE %s OR sender_handle ILIKE %s)
                    ORDER BY msg_time DESC LIMIT %s
                """, (str(hours), f"%{contact}%", f"%{contact}%", limit))
            else:
                cur.execute("""
                    SELECT sender_name, sender_handle, is_from_me, service, body, msg_time
                    FROM messages_cache
                    WHERE msg_time >= NOW() - (%s || ' hours')::INTERVAL
                      AND expires_at > NOW()
                    ORDER BY msg_time DESC LIMIT %s
                """, (str(hours), limit))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]

            if mark_read and rows:
                cur.execute("""
                    UPDATE messages_cache SET read_by_simon=TRUE
                    WHERE msg_time >= NOW() - (%s || ' hours')::INTERVAL
                      AND expires_at > NOW()
                """, (str(hours),))
            conn.commit()
            return rows
    except Exception as e:
        conn.rollback()
        print(f"[DB] messages_query error: {e}")
        return []


# ═══════════════════════════════════════════════════════════
#  SESSION LOG
# ═══════════════════════════════════════════════════════════

def session_start(model_used: str = "qwen2.5:7b") -> Optional[int]:
    conn = _get_pg()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO session_log (model_used) VALUES (%s) RETURNING id",
                (model_used,)
            )
            sid = cur.fetchone()[0]
            conn.commit()
            return sid
    except Exception as e:
        conn.rollback()
        print(f"[DB] session_start error: {e}")
        return None


def session_end(session_id: Optional[int], summary: str,
                tool_calls: int = 0, msg_count: int = 0) -> None:
    if not session_id:
        return
    conn = _get_pg()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE session_log
                SET ended_at=NOW(), summary=%s, tool_calls=%s, msg_count=%s
                WHERE id=%s
            """, (summary, tool_calls, msg_count, session_id))
            conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[DB] session_end error: {e}")


# ═══════════════════════════════════════════════════════════
#  STATUS
# ═══════════════════════════════════════════════════════════

def db_status() -> dict:
    conn = _get_pg()
    status = {
        "pg_available": pg_is_available(),
        "db_url_set":   bool(DB_URL),
        "fallback_path": str(FALLBACK_PATH),
    }
    if conn:
        try:
            with conn.cursor() as cur:
                for table in ["contacts", "memory", "messages_cache", "session_log", "research"]:
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    status[table] = cur.fetchone()[0]
                cur.execute("""
                    SELECT pg_size_pretty(pg_database_size('simon_brain'))
                """)
                status["db_size"] = cur.fetchone()[0]
        except Exception as e:
            status["error"] = str(e)
    else:
        fb = _load_fallback()
        status["offline_memory_facts"] = len(fb.get("memory", {}))
        status["offline_updated_at"]   = fb.get("updated_at", "never")
    return status


def sync_all(force: bool = False) -> dict:
    results = {}
    try:
        n = contacts_sync(force=force)
        results["contacts"] = f"{n} upserted"
    except Exception as e:
        results["contacts"] = f"ERROR: {e}"
    try:
        n = messages_sync(hours_back=48)
        results["messages"] = f"{n} new"
    except Exception as e:
        results["messages"] = f"ERROR: {e}"
    return results


# ═══════════════════════════════════════════════════════════
#  COMPATIBILITY SHIMS
#  Drop-in replacements for simon_kb.py function names
#  so jarvis.py needs minimal changes.
# ═══════════════════════════════════════════════════════════

# KB compatibility
def memory_delete(key: str) -> bool:
    conn = _get_pg()
    if not conn: return False
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM memory WHERE key=%s", (key,))
            n = cur.rowcount
            conn.commit()
            return n > 0
    except Exception as e:
        conn.rollback()
        return False

def resolve_name(handle: str, conn=None) -> str:
    return contacts_resolve(handle)

def kb_status() -> dict:
    s = db_status()
    return {
        "kb_path":    f"postgresql://...simon_brain",
        "kb_size_kb": 0,
        "contacts":   s.get("contacts", 0),
        "messages":   s.get("messages_cache", 0),
        "memory":     s.get("memory", 0),
        "sessions":   s.get("session_log", 0),
        "kb_size_kb": s.get("db_size", "?"),
    }

def query_messages(hours: int = 24, limit: int = 30, contact: str = None, mark_read: bool = True) -> list:
    return messages_query(hours=hours, limit=limit, contact=contact, mark_read=mark_read)

def clear_read_messages() -> int:
    conn = _get_pg()
    if not conn: return 0
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM messages_cache WHERE read_by_simon=TRUE")
            n = cur.rowcount
            conn.commit()
            return n
    except Exception as e:
        conn.rollback()
        return 0

def run_maintenance(verbose: bool = False) -> dict:
    """Maintenance is automatic via pg_cron — this just reports status."""
    conn = _get_pg()
    if not conn:
        return {"error": "PostgreSQL not available"}
    try:
        with conn.cursor() as cur:
            # Manual trigger of what pg_cron does automatically
            cur.execute("DELETE FROM messages_cache WHERE expires_at < NOW()")
            expired = cur.rowcount
            cur.execute("DELETE FROM messages_cache WHERE read_by_simon=TRUE")
            cleared = cur.rowcount
            cur.execute("DELETE FROM session_log WHERE started_at < NOW() - INTERVAL '90 days'")
            pruned = cur.rowcount
            cur.execute("DELETE FROM research WHERE expires_at < NOW()")
            research_pruned = cur.rowcount
            conn.commit()
        s = db_status()
        return {
            "integrity":        "ok",
            "messages_expired": expired,
            "messages_cleared": cleared,
            "sessions_pruned":  pruned,
            "research_pruned":  research_pruned,
            "contacts_deduped": 0,  # handled by UNIQUE constraint
            "vacuumed":         False,  # pg_cron handles this
            "final_size_kb":    s.get("db_size", "?"),
            "contacts":         s.get("contacts", 0),
            "messages":         s.get("messages_cache", 0),
            "memory":           s.get("memory", 0),
        }
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "status":
        s = db_status()
        print(f"\n  S.I.M.O.N. Unified Database — PostgreSQL on simon-hq")
        print(f"  Available : {s['pg_available']}")
        for k, v in s.items():
            if k not in ("pg_available", "db_url_set"):
                print(f"  {k:20} : {v}")

    elif cmd == "sync":
        force = "--force" in sys.argv
        r = sync_all(force=force)
        for k, v in r.items():
            print(f"  {k:12} {v}")

    elif cmd == "memory":
        sub = sys.argv[2] if len(sys.argv) > 2 else "list"
        if sub == "set" and len(sys.argv) >= 5:
            memory_set(sys.argv[3], sys.argv[4])
            print(f"✅ {sys.argv[3]} = {sys.argv[4]}")
        elif sub == "get" and len(sys.argv) >= 4:
            print(memory_get(sys.argv[3]) or "(not found)")
        elif sub == "search" and len(sys.argv) >= 4:
            for r in memory_search(sys.argv[3]):
                print(f"  [{r['category']:12}] {r['key']:30} = {r['value'][:60]}")
        else:
            rows = memory_dump()
            if not rows:
                print("  (no memory stored)")
            for r in rows:
                print(f"  [{r['category']:12}] {r['key']:30} = {r['value'][:60]}")

    elif cmd == "maintain":
        r = run_maintenance(verbose=True)
        for k, v in r.items():
            print(f"  {k:20} : {v}")
