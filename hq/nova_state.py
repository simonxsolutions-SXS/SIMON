#!/usr/bin/env python3
"""
N.O.V.A. State Manager — Session Persistence + Database Bridge
==============================================================
Simon-X Solutions

Provides:
  - Redis-backed session state (survives crashes / reconnects)
  - PostgreSQL metrics collector (every 60s via background thread)
  - ChromaDB automated backup
  - Connection event logger

Import and call start() once in nova_hud_server.py or nova_mcp_server.py
"""

import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ── Config ────────────────────────────────────────────────────────────────────
_cfg_candidates = [
    Path(__file__).parent / "nova_config.json",
    Path("/home/simon-hq/simon-hq/nova_config.json"),
]
_cfg = {}
for _p in _cfg_candidates:
    if _p.exists():
        try:
            _cfg = json.loads(_p.read_text())
            break
        except Exception:
            pass

PG_DSN = _cfg.get(
    "pg_dsn",
    "postgresql://simonx_app:YOUR_DB_PASSWORD@localhost/simonx"
)
REDIS_HOST = _cfg.get("redis_host", "127.0.0.1")
REDIS_PORT = _cfg.get("redis_port", 6379)
CHROMA_DATA = Path(_cfg.get("chroma_data", "/home/simon-hq/simon-hq/chroma_data"))
BACKUP_DIR  = Path(_cfg.get("backup_dir",  "/home/simon-hq/backups"))

_redis_client  = None
_pg_conn       = None
_started       = False
_metrics_thread: Optional[threading.Thread] = None
_stop_event    = threading.Event()


# ── Redis ─────────────────────────────────────────────────────────────────────

def _get_redis():
    global _redis_client
    if _redis_client:
        try:
            _redis_client.ping()
            return _redis_client
        except Exception:
            _redis_client = None
    try:
        import redis
        _redis_client = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT,
            decode_responses=True, socket_timeout=3,
            socket_connect_timeout=3,
        )
        _redis_client.ping()
        return _redis_client
    except Exception:
        return None


def session_set(key: str, value: Any, ttl: int = None) -> bool:
    """Store a value in Redis session state."""
    r = _get_redis()
    if not r:
        return False
    try:
        serialized = json.dumps(value)
        if ttl:
            r.setex(f"nova:session:{key}", ttl, serialized)
        else:
            r.set(f"nova:session:{key}", serialized)
        return True
    except Exception:
        return False


def session_get(key: str, default: Any = None) -> Any:
    """Retrieve a value from Redis session state."""
    r = _get_redis()
    if not r:
        return default
    try:
        raw = r.get(f"nova:session:{key}")
        if raw is None:
            return default
        return json.loads(raw)
    except Exception:
        return default


def session_save_checkpoint(data: dict) -> bool:
    """Save a full session checkpoint (dict of key→value pairs)."""
    r = _get_redis()
    if not r:
        # Fallback to file
        try:
            _fallback_file = Path("/tmp/nova_session_checkpoint.json")
            _fallback_file.write_text(json.dumps({
                "ts": datetime.now().isoformat(), "data": data
            }))
            return True
        except Exception:
            return False
    try:
        pipe = r.pipeline()
        for k, v in data.items():
            pipe.set(f"nova:session:{k}", json.dumps(v))
        pipe.set("nova:session:__checkpoint_ts", datetime.now().isoformat())
        pipe.execute()
        return True
    except Exception:
        return False


def session_load_checkpoint() -> dict:
    """Load last session checkpoint from Redis (or file fallback)."""
    r = _get_redis()
    if not r:
        try:
            raw = Path("/tmp/nova_session_checkpoint.json").read_text()
            return json.loads(raw).get("data", {})
        except Exception:
            return {}
    try:
        keys = r.keys("nova:session:*")
        result = {}
        for k in keys:
            short_key = k.replace("nova:session:", "")
            raw = r.get(k)
            if raw:
                try:
                    result[short_key] = json.loads(raw)
                except Exception:
                    result[short_key] = raw
        return result
    except Exception:
        return {}


# ── PostgreSQL ────────────────────────────────────────────────────────────────

def _get_pg():
    global _pg_conn
    try:
        if _pg_conn and not _pg_conn.closed:
            return _pg_conn
    except Exception:
        pass
    try:
        import psycopg2
        _pg_conn = psycopg2.connect(PG_DSN, connect_timeout=5)
        _pg_conn.autocommit = True
        return _pg_conn
    except Exception:
        return None


def log_event(event_type: str, device: str = "simon-hq", detail: str = "") -> bool:
    """Log a connection or system event to PostgreSQL."""
    pg = _get_pg()
    if not pg:
        return False
    try:
        with pg.cursor() as cur:
            cur.execute(
                "INSERT INTO connection_events (event_type, device, detail) VALUES (%s, %s, %s)",
                (event_type, device, detail)
            )
        return True
    except Exception:
        return False


def log_tool_usage(agent: str, tool: str, duration_ms: int, success: bool, error: str = None) -> bool:
    """Log a tool call to PostgreSQL."""
    pg = _get_pg()
    if not pg:
        return False
    try:
        with pg.cursor() as cur:
            cur.execute(
                "INSERT INTO tool_usage (agent, tool_name, duration_ms, success, error_msg) "
                "VALUES (%s, %s, %s, %s, %s)",
                (agent, tool, duration_ms, success, error)
            )
        return True
    except Exception:
        return False


def save_360_report(checks_total: int, checks_pass: int, report_path: str,
                    summary: str, trigger: str = "manual") -> bool:
    """Save a 360 report run to history."""
    pg = _get_pg()
    if not pg:
        return False
    try:
        with pg.cursor() as cur:
            cur.execute(
                "INSERT INTO report_history "
                "(trigger, checks_total, checks_pass, checks_fail, report_path, summary) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (trigger, checks_total, checks_pass,
                 checks_total - checks_pass, report_path, summary)
            )
        return True
    except Exception:
        return False


def _collect_system_snapshot():
    """Collect system metrics and store in PostgreSQL + Redis."""
    try:
        import psutil
        cpu  = psutil.cpu_percent(interval=1)
        mem  = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        load = os.getloadavg()

        ram_used  = round(mem.used / 1e9, 2)
        ram_total = round(mem.total / 1e9, 2)
        disk_used  = round(disk.used / 1e9, 2)
        disk_total = round(disk.total / 1e9, 2)

        # Count running services
        services = ["nova-webui","nova-hud","nova-mcpo",
                    "simon-hq-api","simon-chroma","ollama","tailscaled"]
        ok = 0; fail = 0
        for svc in services:
            rc = subprocess.run(
                ["systemctl", "is-active", svc],
                capture_output=True, text=True
            ).returncode
            if rc == 0: ok += 1
            else: fail += 1

        ts_ip_raw = subprocess.run(
            ["tailscale", "ip", "--4"], capture_output=True, text=True
        ).stdout.strip()

        # Write to PostgreSQL
        pg = _get_pg()
        if pg:
            with pg.cursor() as cur:
                cur.execute(
                    "INSERT INTO system_snapshots "
                    "(cpu_pct, ram_used_gb, ram_total_gb, disk_used_gb, disk_total_gb, "
                    "load_1m, load_5m, load_15m, tailscale_ip, services_ok, services_fail) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (cpu, ram_used, ram_total, disk_used, disk_total,
                     load[0], load[1], load[2], ts_ip_raw, ok, fail)
                )

        # Cache latest snapshot in Redis
        r = _get_redis()
        if r:
            snapshot = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "cpu": cpu, "ram_used": ram_used, "ram_total": ram_total,
                "disk_used": disk_used, "disk_total": disk_total,
                "load_1m": round(load[0], 2),
                "services_ok": ok, "services_fail": fail,
                "tailscale_ip": ts_ip_raw,
            }
            r.setex("nova:latest_snapshot", 120, json.dumps(snapshot))

    except Exception as e:
        pass  # Non-fatal


def _metrics_loop():
    """Background thread: collect metrics every 60s."""
    while not _stop_event.is_set():
        try:
            _collect_system_snapshot()
        except Exception:
            pass
        _stop_event.wait(60)


# ── ChromaDB Backup ───────────────────────────────────────────────────────────

def backup_chromadb(keep_days: int = 7) -> str:
    """
    Create a timestamped snapshot of the ChromaDB data directory.
    Returns status message.
    """
    if not CHROMA_DATA.exists():
        return f"[Backup] ChromaDB data not found at {CHROMA_DATA}"

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts  = datetime.now().strftime("%Y-%m-%d_%H-%M")
    dst = BACKUP_DIR / f"chromadb_{ts}"

    try:
        shutil.copytree(CHROMA_DATA, dst)
        size = sum(f.stat().st_size for f in dst.rglob("*") if f.is_file())

        # Remove old backups beyond keep_days
        cutoff = time.time() - (keep_days * 86400)
        removed = 0
        for old in BACKUP_DIR.glob("chromadb_*"):
            if old.is_dir() and old.stat().st_mtime < cutoff:
                shutil.rmtree(old)
                removed += 1

        msg = (f"[Backup] ChromaDB → {dst} "
               f"({size/1024:.0f} KB) | {removed} old backup(s) pruned")
        log_event("chromadb_backup", "simon-hq", msg)
        return msg
    except Exception as e:
        return f"[Backup ERROR] {e}"


def backup_postgres(keep_days: int = 7) -> str:
    """Dump the simonx PostgreSQL database to the backup directory."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts  = datetime.now().strftime("%Y-%m-%d_%H-%M")
    dst = BACKUP_DIR / f"postgres_simonx_{ts}.sql.gz"

    try:
        result = subprocess.run(
            f"pg_dump -U simonx_app -h localhost simonx | gzip > {dst}",
            shell=True, capture_output=True, text=True, timeout=60,
            env={**os.environ, "PGPASSWORD": os.getenv("PGPASSWORD", "YOUR_DB_PASSWORD")}
        )
        if result.returncode != 0:
            return f"[Backup ERROR] pg_dump failed: {result.stderr[:200]}"

        size = dst.stat().st_size

        # Prune old dumps
        cutoff = time.time() - (keep_days * 86400)
        removed = 0
        for old in BACKUP_DIR.glob("postgres_simonx_*.sql.gz"):
            if old.stat().st_mtime < cutoff:
                old.unlink()
                removed += 1

        msg = f"[Backup] PostgreSQL → {dst} ({size/1024:.0f} KB) | {removed} old dump(s) pruned"
        log_event("postgres_backup", "simon-hq", msg)
        return msg
    except Exception as e:
        return f"[Backup ERROR] {e}"


# ── Startup ───────────────────────────────────────────────────────────────────

def start(collect_metrics: bool = True) -> dict:
    """
    Initialize the state manager. Call once at service startup.
    Returns status dict.
    """
    global _started
    if _started:
        return {"status": "already_started"}

    status = {"redis": False, "postgres": False, "metrics_thread": False}

    # Redis
    r = _get_redis()
    if r:
        status["redis"] = True
        r.incr("nova:boot_count")
        session_set("last_boot", datetime.now().isoformat())
        session_set("service_status", "starting")

    # PostgreSQL
    pg = _get_pg()
    if pg:
        status["postgres"] = True
        log_event("service_start", "simon-hq", "NOVA state manager initialized")

    # Metrics thread
    if collect_metrics:
        global _metrics_thread
        _stop_event.clear()
        _metrics_thread = threading.Thread(
            target=_metrics_loop, daemon=True, name="nova-metrics"
        )
        _metrics_thread.start()
        status["metrics_thread"] = True

    _started = True
    return status


def stop():
    """Graceful shutdown — stop metrics thread, flush state."""
    _stop_event.set()
    r = _get_redis()
    if r:
        session_set("service_status", "stopped")
        session_set("last_stop", datetime.now().isoformat())
    log_event("service_stop", "simon-hq", "NOVA state manager stopped")


# ── Standalone backup runner ──────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    print("[NOVA State] Running backup...")
    print(backup_chromadb())
    print(backup_postgres())
    print("[NOVA State] Done.")
