-- ============================================================
-- SIMON-X Solutions — PostgreSQL Schema
-- simon-hq | Database: simonx
-- ============================================================

-- Create database user (run as postgres superuser)
-- psql -U postgres -f setup_postgres_simonx.sql

\c postgres

-- Drop and recreate for clean setup
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'simonx';
DROP DATABASE IF EXISTS simonx;
DROP ROLE IF EXISTS simonx_app;

CREATE ROLE simonx_app WITH LOGIN PASSWORD 'YOUR_DB_PASSWORD';
CREATE DATABASE simonx OWNER simonx_app ENCODING 'UTF8';

\c simonx

-- Grant schema privileges
GRANT ALL ON SCHEMA public TO simonx_app;

-- ── Service Metrics History ──────────────────────────────────────────────────
CREATE TABLE service_metrics (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    service     TEXT NOT NULL,
    active      BOOLEAN NOT NULL,
    cpu_pct     REAL,
    mem_mb      REAL,
    restarts    INT DEFAULT 0
);
CREATE INDEX idx_svc_metrics_ts      ON service_metrics(ts DESC);
CREATE INDEX idx_svc_metrics_service ON service_metrics(service, ts DESC);

-- ── System Snapshots ──────────────────────────────────────────────────────────
CREATE TABLE system_snapshots (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cpu_pct     REAL,
    ram_used_gb REAL,
    ram_total_gb REAL,
    disk_used_gb REAL,
    disk_total_gb REAL,
    load_1m     REAL,
    load_5m     REAL,
    load_15m    REAL,
    tailscale_ip TEXT,
    services_ok  INT,
    services_fail INT
);
CREATE INDEX idx_snapshots_ts ON system_snapshots(ts DESC);

-- ── Connection Events ─────────────────────────────────────────────────────────
CREATE TABLE connection_events (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type  TEXT NOT NULL,  -- 'internet_lost','internet_restored','adb_connect','adb_disconnect','tailscale_up','tailscale_down','service_restart'
    device      TEXT,           -- 'simon-hq','mac','pixel9a','internet'
    detail      TEXT,
    resolved_at TIMESTAMPTZ
);
CREATE INDEX idx_conn_events_ts   ON connection_events(ts DESC);
CREATE INDEX idx_conn_events_type ON connection_events(event_type, ts DESC);

-- ── Session State ─────────────────────────────────────────────────────────────
-- Survives crashes and reconnects — last known good state
CREATE TABLE session_state (
    key         TEXT PRIMARY KEY,
    value       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ttl_seconds INT DEFAULT NULL  -- NULL = persist forever
);

-- ── Audit Log ────────────────────────────────────────────────────────────────
CREATE TABLE audit_log (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor       TEXT NOT NULL DEFAULT 'system',  -- 'simon','nova','system'
    action      TEXT NOT NULL,
    target      TEXT,
    detail      JSONB,
    ip          TEXT,
    success     BOOLEAN DEFAULT TRUE
);
CREATE INDEX idx_audit_ts    ON audit_log(ts DESC);
CREATE INDEX idx_audit_actor ON audit_log(actor, ts DESC);

-- ── Tool Usage Metrics ────────────────────────────────────────────────────────
CREATE TABLE tool_usage (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    agent       TEXT NOT NULL,   -- 'simon','nova'
    tool_name   TEXT NOT NULL,
    duration_ms INT,
    success     BOOLEAN,
    error_msg   TEXT
);
CREATE INDEX idx_tool_usage_ts   ON tool_usage(ts DESC);
CREATE INDEX idx_tool_usage_tool ON tool_usage(tool_name, ts DESC);

-- ── 360 Report History ────────────────────────────────────────────────────────
CREATE TABLE report_history (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    trigger     TEXT DEFAULT 'manual',  -- 'manual','scheduled','api'
    checks_total INT,
    checks_pass  INT,
    checks_fail  INT,
    report_path  TEXT,
    summary      TEXT
);
CREATE INDEX idx_report_history_ts ON report_history(ts DESC);

-- ── Seed initial session state ────────────────────────────────────────────────
INSERT INTO session_state (key, value) VALUES
  ('nova_boot_count', '0'),
  ('simon_boot_count', '0'),
  ('last_360_report', 'null'),
  ('system_version', '"1.0.0"'),
  ('setup_complete', 'true');

-- ── Views ──────────────────────────────────────────────────────────────────────
CREATE VIEW v_service_health AS
SELECT
    service,
    bool_and(active)               AS currently_active,
    avg(cpu_pct)                   AS avg_cpu_24h,
    avg(mem_mb)                    AS avg_mem_mb_24h,
    max(restarts)                  AS max_restarts_24h,
    count(*)                       AS sample_count,
    max(ts)                        AS last_seen
FROM service_metrics
WHERE ts > NOW() - INTERVAL '24 hours'
GROUP BY service;

CREATE VIEW v_daily_summary AS
SELECT
    date_trunc('hour', ts)         AS hour,
    avg(cpu_pct)                   AS avg_cpu,
    avg(ram_used_gb)               AS avg_ram_gb,
    avg(load_1m)                   AS avg_load,
    max(services_fail)             AS max_failures
FROM system_snapshots
WHERE ts > NOW() - INTERVAL '7 days'
GROUP BY 1 ORDER BY 1 DESC;

-- Permissions
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO simonx_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO simonx_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO simonx_app;

\echo 'simonx database setup complete.'
