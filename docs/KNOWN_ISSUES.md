# S.I.M.O.N. Known Issues
**Simon-X Solutions | [OWNER_NAME]**
**Version: 4.4 | Last Updated: March 21, 2026**

---

## Active Issues

### LOW — TOKENIZERS_PARALLELISM Warnings in Log

**Description:** Some subprocess calls may still trigger occasional HuggingFace tokenizer warnings.

**Root Cause:** macOS fork safety and HuggingFace tokenizer parallelism conflict. Both `TOKENIZERS_PARALLELISM=false` and `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` are now set at startup (added in v4.4), which suppresses the majority of these.

**Impact:** None. Purely cosmetic log noise.

**Workaround:** Use the filtered log watch command:
```bash
tail -f ~/Projects/AI-Projects/jarvis/jarvis.log | grep -v TOKENIZERS | grep -v "To disable" | grep -v huggingface
```

---

### LOW — MLX Fast Path Shows DEGRADED in Health Check

**Description:** Health monitor always reports MLX Fast Path as DEGRADED.

**Root Cause:** Intentional. MLX model is not pre-loaded at startup to save 4.5GB RAM. It only loads if HQ is offline for 60+ seconds.

**Impact:** None. This is expected behavior. This tool is now excluded from health escalation alerts.

**Workaround:** None needed.

---

### LOW — Vision Shows DEGRADED in Health Check

**Description:** Health monitor reports "YOLO ready, Moondream still loading" indefinitely.

**Root Cause:** Intentional. Moondream is not pre-loaded at startup to save 3.5GB RAM. Only loads on demand when vision_ask is called and HQ is offline.

**Impact:** None. Vision works correctly — YOLO detects objects, HQ llama3.2-vision handles questions. This tool is now excluded from health escalation alerts.

**Workaround:** None needed.

---

### MEDIUM — PostgreSQL Migration Pending

**Description:** PostgreSQL is installed and configured on simon-hq but the actual data migration from SQLite has not been done.

**Current State:**
- PostgreSQL running on simon-hq:5432
- Database `simon_brain` exists with all tables created
- `simon_db.py` module written and ready
- Mac `config.json` does NOT yet have `simon_db_url`
- `psycopg2-binary` not yet installed on Mac
- 167 contacts and 20 memory facts still in SQLite only

**Why Not Done Yet:** Risk of data loss during migration. SQLite is working fine. Deferred for a dedicated migration session.

**Steps When Ready:**
1. Install: `pip3.11 install psycopg2-binary --break-system-packages`
2. Add to `config.json`: `"simon_db_url": "postgresql://simon:YOUR_DB_PASSWORD@YOUR_HQ_TAILSCALE_IP:5432/simon_brain"`
3. Run migration script to copy SQLite → PostgreSQL
4. Update simon_kb.py to use PostgreSQL for new writes
5. Validate, then deprecate SQLite reads

---

### MEDIUM — Self-Healing Engine Not Deployed

**Description:** A dedicated `simon_healer.py` self-healing registry was planned but not yet built.

**Note:** The health monitor now handles escalation (macOS alerts after 5 minutes sustained DOWN) and basic auto-heal (Mail/Messages reopen). The full healer below would add more sophisticated pattern→fix automation.

**Known Fixes to Implement:**
- Port conflict on 8765 → kill and restart
- YOLO not loaded → call `_get_vision_engine()._load_yolo()`
- KB integrity failure → run `run_maintenance(force=True)`
- Piper TTS missing → fall back to `say -v Daniel`

---

### LOW — Android ADB Reconnects Required After Network Change

**Description:** When the Android phone moves to a different WiFi network or gets a new DHCP IP address, the `adb_host` in config.json will be stale and Android tools will fail.

**Root Cause:** ADB over WiFi targets a specific IP. DHCP leases can change.

**Workaround:**
1. Check your phone's current IP: Settings → About Phone → Status → IP Address
2. Update `config.json` → `"android"` → `"adb_host"`
3. Say "Simon, connect to my phone" to reconnect

**Future Fix:** Reserve a static IP for the phone in your router's DHCP settings using the MAC address.

---

## Resolved Issues

| Issue | Version Fixed | Details |
|---|---|---|
| SIMON listening but not responding | v4.3 | HQ was receiving tools and misrouting to hq_ask |
| Startup hangs for up to 5 minutes | v4.3 | HQ Bridge v2.1 — non-blocking handshake |
| SyntaxError in tool_vision_detect | v4.3 | f-string backslash not allowed in Python 3.11 |
| RAM at ~17GB idle | v4.3 | MLX and Moondream now on-demand only |
| ifconfig not found in shell tools | v4.3 | /usr/sbin:/sbin added to PATH |
| Tool argument mismatch crashes | v4.3 | Sanitizer strips malformed wrapper keys |
| Mail/Messages stay closed | v4.3 | Auto-heal added to health check loop |
| No clean start/stop scripts | v4.3 | start/stop/restart_simon.sh created |
| FastAPI on_event deprecation warning | v4.4 | Migrated to lifespan context manager |
| HQ Bridge circular import warning | v4.4 | Deferred init via `_deferred_init()` async task |
| Duplicate `import os` in jarvis.py | v4.4 | Removed redundant import |
| macOS fork safety log warnings | v4.4 | OBJC_DISABLE_INITIALIZE_FORK_SAFETY added |
| No health escalation alerts | v4.4 | macOS notification after 5 min sustained DOWN |
| No calendar conflict detection | v4.4 | `tool_check_calendar_conflicts()` added |

---

## Roadmap (Not Yet Started)

| Feature | Priority | Notes |
|---|---|---|
| PostgreSQL migration | High | Data persistence and pgvector semantic search |
| Self-healing engine | Medium | Full pattern→fix registry (basic auto-heal already in health monitor) |
| Static IP for Android phone | Medium | Prevents reconnect issues after IP changes |
| OpenClaw multi-agent orchestration | Low | For Simon-X product build for dental clients |
| Ring camera integration | Deferred | python-ring-doorbell library ready to implement |
| SMS/iMessage threading context | Low | Read full thread for richer context |
