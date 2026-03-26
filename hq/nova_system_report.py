#!/usr/bin/env python3
"""
N.O.V.A. / S.I.M.O.N. — Full System Diagnostic Report
=======================================================
Simon-X Solutions | Run from Mac (simonx)

Covers:
  • Mac (SIMON)   — local checks, Tailscale, SIMON API
  • simon-hq      — system stats, all services, ports, firewall, logs
  • Pixel 9a      — ADB over Tailscale, battery, storage, connectivity
  • Connectivity  — full mesh ping matrix between all three nodes

Output: ~/Desktop/NOVA_System_Report_YYYY-MM-DD_HH-MM.html
Usage : python3 nova_system_report.py
"""

import html
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
_cfg_path = Path(__file__).parent / "nova_config.json"
try:
    CFG = json.loads(_cfg_path.read_text())
except Exception:
    CFG = {}

HQ_SSH       = "simon-hq"
HQ_IP        = "YOUR_HQ_TAILSCALE_IP"
MAC_IP       = "YOUR_MAC_TAILSCALE_IP"
ANDROID_IP   = CFG.get("android_ip",   "YOUR_ANDROID_TAILSCALE_IP")
ANDROID_PORT = CFG.get("android_port", 5555)
ADB_TARGET   = f"{ANDROID_IP}:{ANDROID_PORT}"

MAC_API      = f"http://127.0.0.1:8765"
HQ_API       = CFG.get("hq_api_url",  "http://127.0.0.1:8200")  # local, won't work from mac
HQ_WEBUI     = f"http://{HQ_IP}:3000"
HQ_HUD       = f"http://{HQ_IP}:3001"
HQ_MCPO      = f"http://{HQ_IP}:8301"
HQ_CHROMA    = f"http://{HQ_IP}:8100"
HQ_OLLAMA    = f"http://{HQ_IP}:11434"
HQ_API_EXT   = f"http://{HQ_IP}:8200"

REPORT_DIR   = Path.home() / "Desktop"
TS           = datetime.now().strftime("%Y-%m-%d_%H-%M")
REPORT_FILE  = REPORT_DIR / f"NOVA_System_Report_{TS}.html"

# ── Shell helpers ─────────────────────────────────────────────────────────────

def sh(cmd: str, timeout: int = 10) -> tuple[int, str]:
    """Run local shell command. Returns (returncode, combined_output)."""
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 1, f"[timeout after {timeout}s]"
    except Exception as e:
        return 1, f"[error: {e}]"


def ssh(cmd: str, timeout: int = 15) -> tuple[int, str]:
    """Run command on simon-hq via SSH."""
    full = f'ssh -o ConnectTimeout=8 -o BatchMode=yes {HQ_SSH} "{cmd}"'
    return sh(full, timeout=timeout)


def http_check(url: str, timeout: int = 5) -> tuple[bool, str]:
    """GET a URL and return (ok, status_line)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NOVA-Diagnostics/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return True, f"HTTP {e.code}"   # server replied = it's up
    except Exception as e:
        return False, str(e)[:80]


def port_open(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


# ── Data collectors ───────────────────────────────────────────────────────────

def collect_mac() -> dict:
    print("  → Mac (SIMON)...")
    d: dict = {"name": "Mac (SIMON)", "ip": MAC_IP, "checks": []}
    add = lambda label, ok, detail="": d["checks"].append({"label": label, "ok": ok, "detail": detail})

    # Tailscale
    rc, out = sh("tailscale status 2>&1 | head -5")
    ts_ok = rc == 0 and "100." in out
    add("Tailscale running", ts_ok, out.splitlines()[0] if out else "")

    rc2, myip = sh("tailscale ip --4 2>&1")
    add("Tailscale IP", rc2 == 0, myip.strip())

    # SIMON API
    api_ok, api_msg = http_check(f"{MAC_API}/api/status", timeout=4)
    add("SIMON API (port 8765)", api_ok, api_msg)

    # HQ reachable from Mac
    hq_ping_ok = port_open(HQ_IP, 22, timeout=4)
    add("simon-hq reachable (SSH)", hq_ping_ok, HQ_IP)

    # Mac disk
    rc3, disk = sh("df -h / | awk 'NR==2{print $2\" total, \"$3\" used, \"$5\" full\"}'")
    add("Mac disk (/)", rc3 == 0, disk)

    # Mac RAM
    rc4, ram = sh("vm_stat | awk '/Pages free/{f=$3} /Pages active/{a=$3} END{printf \"free ~%.1f GB\", (f+0)*4096/1073741824}'")
    add("Mac RAM", rc4 == 0, ram)

    return d


def collect_hq() -> dict:
    print("  → simon-hq...")
    d: dict = {"name": "simon-hq (NOVA)", "ip": HQ_IP, "checks": [], "sections": {}}

    rc_ssh, _ = ssh("echo ok", timeout=8)
    ssh_ok = rc_ssh == 0
    d["checks"].append({"label": "SSH connectivity", "ok": ssh_ok, "detail": HQ_SSH})
    if not ssh_ok:
        d["checks"].append({"label": "All HQ checks", "ok": False, "detail": "SSH failed — skipping"})
        return d

    def add(label, ok, detail=""):
        d["checks"].append({"label": label, "ok": ok, "detail": detail})

    # ── System stats ──
    _, cpu    = ssh("nproc")
    _, ram    = ssh("free -h | awk '/^Mem:/{print $2\" total, \"$3\" used, \"$4\" free\"}'")
    _, disk   = ssh("df -h / | awk 'NR==2{print $2\" total, \"$3\" used (\"$5\" full)\"}'")
    _, load   = ssh("uptime | awk -F'load average:' '{print $2}'")
    _, uptime = ssh("uptime -p")
    _, kver   = ssh("uname -r")
    d["sections"]["system"] = {
        "cpu": cpu.strip(), "ram": ram.strip(), "disk": disk.strip(),
        "load": load.strip(), "uptime": uptime.strip(), "kernel": kver.strip(),
    }

    # ── Services ──
    services = [
        ("nova-webui",    3000), ("nova-hud",       3001),
        ("nova-mcpo",     8301),
        ("simon-hq-api",  8200), ("simon-chroma",   8100),
        ("ollama",       11434), ("tailscaled",      None),
    ]
    svc_rows = []
    for svc, port in services:
        _, active  = ssh(f"systemctl is-active {svc} 2>&1")
        _, enabled = ssh(f"systemctl is-enabled {svc} 2>&1")
        active = active.strip(); enabled = enabled.strip()
        port_ok = port_open(HQ_IP, port) if port else None
        svc_rows.append({
            "name": svc, "active": active, "enabled": enabled,
            "port": port, "port_ok": port_ok,
        })
        ok = (active == "active")
        detail = f"enabled={enabled}" + (f" | port {port}: {'open' if port_ok else 'closed'}" if port else "")
        add(f"Service: {svc}", ok, detail)
    d["sections"]["services"] = svc_rows

    # ── Ports ──
    # External ports (should be reachable from Mac over Tailscale)
    ext_ports = [22, 3000, 3001, 8200]
    # Internal-only ports (localhost-bound by design — check via SSH)
    int_ports  = [8100, 8301, 11434]

    ext_results = {p: port_open(HQ_IP, p) for p in ext_ports}
    for p, ok in ext_results.items():
        add(f"Port {p} accessible (external)", ok,
            f"{HQ_IP}:{p} — {'OPEN' if ok else 'CLOSED'}")

    for p in int_ports:
        _, nc_out = ssh(f"nc -zv 127.0.0.1 {p} 2>&1 || echo 'closed'", timeout=6)
        ok = "succeeded" in nc_out.lower() or "open" in nc_out.lower() or (
             "refused" not in nc_out.lower() and "closed" not in nc_out.lower() and nc_out.strip() != "")
        # Fallback: try curl
        if not ok:
            _, curl_out = ssh(f"curl -s --max-time 2 http://127.0.0.1:{p}/ 2>&1 | head -1", timeout=6)
            ok = bool(curl_out.strip()) and "refused" not in curl_out.lower()
        add(f"Port {p} (localhost-only, internal)", ok,
            f"127.0.0.1:{p} — {'ACTIVE' if ok else 'NOT RESPONDING'} (intentionally not exposed externally)")

    d["sections"]["ports"] = {**ext_results, **{p: None for p in int_ports}}

    # ── Firewall ──
    # Check UFW via systemctl (no sudo needed) + iptables rule count
    _, ufw_svc  = ssh("systemctl is-active ufw 2>&1")
    _, ufw_enbl = ssh("systemctl is-enabled ufw 2>&1")
    _, ipt_cnt  = ssh("iptables -L INPUT --line-numbers 2>/dev/null | wc -l || echo 'N/A'")
    ufw_running = ufw_svc.strip() == "active"
    ufw_detail  = f"Service: {ufw_svc.strip()} | Enabled: {ufw_enbl.strip()} | iptables INPUT rules: {ipt_cnt.strip()}"
    add("UFW firewall", ufw_running, ufw_detail)
    d["sections"]["ufw"] = ufw_detail

    # ── Tailscale ──
    _, ts_status = ssh("tailscale status 2>&1")
    ts_ok = "100." in ts_status
    add("Tailscale (HQ)", ts_ok, ts_status.splitlines()[0] if ts_status else "")
    d["sections"]["tailscale"] = ts_status

    # ── Ollama (check locally from HQ via SSH) ──
    _, ollama_raw = ssh("curl -s --max-time 5 http://127.0.0.1:11434/api/tags 2>&1", timeout=10)
    ollama_ok = ollama_raw.strip().startswith("{") or '"models"' in ollama_raw
    add("Ollama API (localhost)", ollama_ok, "127.0.0.1:11434" + (" — responding" if ollama_ok else " — no response"))
    if ollama_ok:
        try:
            data = json.loads(ollama_raw)
            models = data.get("models", [])
            model_list = [f"{m['name']} ({m.get('size',0)/1e9:.1f}GB)" for m in models]
            d["sections"]["ollama_models"] = model_list
            add("Ollama models", True, f"{len(models)} model(s): " + ", ".join(m['name'] for m in models[:6]))
        except Exception as e:
            d["sections"]["ollama_models"] = []
            add("Ollama models", False, f"JSON parse error: {e}")
    else:
        d["sections"]["ollama_models"] = []

    # ── ChromaDB — v2 API (ChromaDB 0.5+), fallback to v1 ──
    _chroma_v2_hb  = "http://127.0.0.1:8100/api/v2/heartbeat"
    _chroma_v2_col = "http://127.0.0.1:8100/api/v2/tenants/default_tenant/databases/default_database/collections"
    _chroma_v1_hb  = "http://127.0.0.1:8100/api/v1/heartbeat"
    _chroma_v1_col = "http://127.0.0.1:8100/api/v1/collections"
    _, chroma_raw = ssh(f"curl -s --max-time 5 '{_chroma_v2_hb}' 2>&1", timeout=10)
    chroma_ok = "nanosecond" in chroma_raw or '"heartbeat"' in chroma_raw
    _cols_url = _chroma_v2_col
    if not chroma_ok:
        _, chroma_raw = ssh(f"curl -s --max-time 5 '{_chroma_v1_hb}' 2>&1", timeout=10)
        chroma_ok = "nanosecond" in chroma_raw or '"heartbeat"' in chroma_raw
        _cols_url = _chroma_v1_col
    add("ChromaDB (localhost)", chroma_ok, "127.0.0.1:8100" + (" — healthy" if chroma_ok else " — not responding"))
    if chroma_ok:
        _, cols_raw = ssh(f"curl -s --max-time 5 '{_cols_url}' 2>&1", timeout=10)
        try:
            cols = json.loads(cols_raw)
            col_names = [c.get("name","?") for c in (cols if isinstance(cols, list) else [])]
            add("ChromaDB collections", True, f"{len(col_names)} collection(s): {', '.join(col_names)}")
            d["sections"]["chroma_collections"] = col_names
        except Exception as e:
            add("ChromaDB collections", False, str(e))

    # ── HUD + WebUI reachable (external check from Mac) ──
    webui_ok, webui_msg = http_check(HQ_WEBUI)
    add("Open WebUI (port 3000)", webui_ok, f"{HQ_WEBUI} — {webui_msg}")
    hud_ok, hud_msg = http_check(HQ_HUD)
    add("NOVA HUD (port 3001)", hud_ok, f"{HQ_HUD} — {hud_msg}")

    # nova-mcpo: internal only — check via SSH
    _, mcpo_raw = ssh("curl -s --max-time 5 http://127.0.0.1:8301/openapi.json 2>&1 | head -1", timeout=10)
    mcpo_ok = '"openapi"' in mcpo_raw or '"paths"' in mcpo_raw or mcpo_raw.strip().startswith("{")
    add("nova-mcpo tools API (localhost:8301)", mcpo_ok,
        "127.0.0.1:8301 — " + ("OpenAPI schema served" if mcpo_ok else f"not responding: {mcpo_raw[:60]}"))

    # ── System logs — recent errors ──
    _, errors = ssh(
        "journalctl -p err -n 30 --no-pager --since '24h ago' 2>&1 | "
        "grep -v 'Bluetooth\\|ACPI\\|pci\\|usb\\|kernel:' | head -25",
        timeout=12,
    )
    d["sections"]["log_errors"] = errors

    # ── Disk usage breakdown ──
    _, du = ssh("df -h 2>/dev/null | grep -v tmpfs | grep -v udev")
    d["sections"]["df"] = du

    # ── Recent service logs ──
    svc_logs = {}
    for svc, _ in services[:6]:
        _, log = ssh(f"journalctl -u {svc} -n 8 --no-pager 2>&1 | tail -8", timeout=8)
        svc_logs[svc] = log
    d["sections"]["svc_logs"] = svc_logs

    return d


def collect_android() -> dict:
    print("  → Pixel 9a (Android)...")
    d: dict = {"name": "Pixel 9a", "ip": ANDROID_IP, "checks": [], "sections": {}}

    def add(label, ok, detail=""):
        d["checks"].append({"label": label, "ok": ok, "detail": detail})

    # Connect via HQ's ADB (ADB is installed on simon-hq)
    rc, conn_out = ssh(f"adb connect {ADB_TARGET} 2>&1", timeout=12)
    connected = "connected" in conn_out.lower() or "already" in conn_out.lower()
    add("ADB connection (via HQ)", connected, conn_out.strip())

    if not connected:
        add("Android data", False, "ADB not connected — ensure USB debugging + Tailscale active on phone")
        return d

    def adb(cmd):
        _, out = ssh(f"adb -s {ADB_TARGET} shell {cmd} 2>&1", timeout=12)
        return out.strip()

    # Device info
    model   = adb("getprop ro.product.model")
    android = adb("getprop ro.build.version.release")
    api     = adb("getprop ro.build.version.sdk")
    serial  = adb("getprop ro.serialno")
    ok_info = bool(model and model != "")
    add("Device identified", ok_info, f"{model} | Android {android} (API {api})")
    d["sections"]["device"] = {
        "model": model, "android": android, "api": api, "serial": serial
    }

    # Battery
    battery_raw = adb("dumpsys battery | grep -E 'level|status|plugged|temperature'")
    battery_lines = {l.split(":")[0].strip(): l.split(":")[1].strip()
                     for l in battery_raw.splitlines() if ":" in l}
    level = battery_lines.get("level", "?")
    status_map = {"1": "Unknown", "2": "Charging", "3": "Discharging", "4": "Not charging", "5": "Full"}
    batt_status = status_map.get(battery_lines.get("status", ""), battery_lines.get("status", "?"))
    plugged_map = {"0": "Not plugged", "1": "AC", "2": "USB", "4": "Wireless"}
    plugged = plugged_map.get(battery_lines.get("plugged", ""), "?")
    temp_raw = battery_lines.get("temperature", "?")
    try:
        temp_c = int(temp_raw) / 10
        temp_str = f"{temp_c}°C"
    except Exception:
        temp_str = temp_raw
    batt_ok = int(level) > 10 if level.isdigit() else False
    add("Battery", batt_ok, f"{level}% | {batt_status} | {plugged} | Temp: {temp_str}")
    d["sections"]["battery"] = {"level": level, "status": batt_status, "plugged": plugged, "temp": temp_str}

    # Storage — try /data first, fall back to df summary
    storage = adb("df /data 2>/dev/null | tail -1 | awk '{print $2\" total, \"$3\" used, \"$4\" free\"}'")
    if not storage or storage.startswith("["):
        storage = adb("df 2>/dev/null | grep -E '/data|/storage' | head -3 | awk '{print $1\": \"$2\" total, \"$3\" used\"}'")
    if not storage or storage.startswith("["):
        storage = adb("dumpsys diskstats 2>/dev/null | grep -E 'Data-Free|Cache-Free|App' | head -5")
    add("Storage", bool(storage and not storage.startswith("[")), storage or "ADB storage query returned empty")

    # Screen state
    screen_raw = adb("dumpsys power | grep 'mWakefulness='")
    screen_state = screen_raw.replace("mWakefulness=", "").strip()
    add("Screen state", True, screen_state or "unknown")

    # WiFi
    wifi_raw = adb("dumpsys wifi | grep 'mWifiInfo' | head -1")
    ssid_match = ""
    if "SSID:" in wifi_raw:
        try:
            ssid_match = wifi_raw.split("SSID:")[1].split(",")[0].strip()
        except Exception:
            ssid_match = "parsing error"
    add("WiFi", bool(ssid_match), ssid_match or "no WiFi info")

    # Tailscale on phone
    ts_pkg = adb("pm list packages | grep tailscale")
    ts_installed = "tailscale" in ts_pkg
    add("Tailscale app installed", ts_installed, ts_pkg.strip() if ts_installed else "not found")

    # ADB features
    d["sections"]["storage"] = storage
    d["sections"]["screen"]  = screen_state
    d["sections"]["wifi"]    = ssid_match

    return d


def collect_connectivity() -> dict:
    """Test the full mesh: Mac↔HQ, Mac↔Android, HQ↔Android."""
    print("  → Connectivity matrix...")
    nodes = {
        "Mac": MAC_IP,
        "simon-hq": HQ_IP,
        "Pixel 9a": ANDROID_IP,
    }
    matrix = []

    # Mac → HQ
    ok = port_open(HQ_IP, 22, timeout=4)
    matrix.append({"from": "Mac", "to": "simon-hq", "method": "TCP:22 (SSH)", "ok": ok})

    ok = port_open(HQ_IP, 3001, timeout=4)
    matrix.append({"from": "Mac", "to": "simon-hq", "method": "TCP:3001 (HUD)", "ok": ok})

    ok = port_open(HQ_IP, 3000, timeout=4)
    matrix.append({"from": "Mac", "to": "simon-hq", "method": "TCP:3000 (WebUI)", "ok": ok})

    # Mac → Android (direct Tailscale)
    ok = port_open(ANDROID_IP, ANDROID_PORT, timeout=4)
    matrix.append({"from": "Mac", "to": "Pixel 9a", "method": f"TCP:{ANDROID_PORT} (ADB)", "ok": ok})

    # HQ → Mac
    rc, out = ssh(f"nc -zw3 {MAC_IP} 8765 2>&1 && echo open || echo closed", timeout=8)
    ok = "open" in out
    matrix.append({"from": "simon-hq", "to": "Mac", "method": "TCP:8765 (SIMON API)", "ok": ok})

    # HQ → Android
    rc2, out2 = ssh(f"adb connect {ADB_TARGET} 2>&1", timeout=10)
    ok2 = "connected" in out2.lower() or "already" in out2.lower()
    matrix.append({"from": "simon-hq", "to": "Pixel 9a", "method": f"ADB:{ANDROID_PORT}", "ok": ok2})

    # HQ → internet
    rc3, _ = ssh("ping -c 2 -W 2 8.8.8.8 2>&1", timeout=8)
    matrix.append({"from": "simon-hq", "to": "Internet (8.8.8.8)", "method": "ICMP ping", "ok": rc3 == 0})

    # Mac → internet
    rc4, _ = sh("ping -c 2 -W 2 8.8.8.8 2>&1", timeout=8)
    matrix.append({"from": "Mac", "to": "Internet (8.8.8.8)", "method": "ICMP ping", "ok": rc4 == 0})

    return {"matrix": matrix}


# ── HTML report builder ───────────────────────────────────────────────────────

STYLE = """
:root {
  --bg:    #020912;
  --bg2:   #050f1e;
  --bg3:   #091728;
  --cy:    #00d4ff;
  --vi:    #9d4eff;
  --gn:    #00ff9d;
  --pk:    #ff3c78;
  --yw:    #ffd700;
  --txt:   #c8e6f5;
  --dim:   #4a7a99;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--txt);
  font-family: 'Consolas', 'SF Mono', 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.55;
}
a { color: var(--cy); text-decoration: none; }

/* ── Header ── */
.header {
  background: linear-gradient(135deg, #020912 0%, #0a1a2e 50%, #020912 100%);
  border-bottom: 1px solid var(--cy);
  padding: 28px 40px 20px;
  position: relative;
  overflow: hidden;
}
.header::before {
  content: '';
  position: absolute; inset: 0;
  background: radial-gradient(ellipse at 20% 50%, rgba(0,212,255,0.06) 0%, transparent 60%),
              radial-gradient(ellipse at 80% 50%, rgba(157,78,255,0.06) 0%, transparent 60%);
  pointer-events: none;
}
.header-title {
  font-size: 26px;
  font-weight: 700;
  letter-spacing: 4px;
  color: var(--cy);
  text-shadow: 0 0 20px rgba(0,212,255,0.5);
}
.header-sub {
  font-size: 11px;
  color: var(--dim);
  letter-spacing: 2px;
  margin-top: 4px;
}
.header-meta {
  position: absolute; right: 40px; top: 28px;
  text-align: right;
  font-size: 11px;
  color: var(--dim);
}
.header-meta .ts { color: var(--cy); font-size: 13px; }

/* ── Summary bar ── */
.summary-bar {
  display: flex;
  gap: 16px;
  padding: 16px 40px;
  background: var(--bg2);
  border-bottom: 1px solid rgba(0,212,255,0.15);
  flex-wrap: wrap;
}
.summary-card {
  flex: 1; min-width: 140px;
  background: var(--bg3);
  border: 1px solid rgba(0,212,255,0.2);
  border-radius: 6px;
  padding: 12px 16px;
  text-align: center;
}
.summary-card .num {
  font-size: 28px;
  font-weight: 700;
  line-height: 1;
}
.summary-card .lbl {
  font-size: 10px;
  color: var(--dim);
  letter-spacing: 1px;
  margin-top: 4px;
}
.num.green  { color: var(--gn); text-shadow: 0 0 12px rgba(0,255,157,0.4); }
.num.red    { color: var(--pk); text-shadow: 0 0 12px rgba(255,60,120,0.4); }
.num.yellow { color: var(--yw); }
.num.cyan   { color: var(--cy); }

/* ── Layout ── */
.main { padding: 28px 40px; max-width: 1400px; }

/* ── Section ── */
.section {
  background: var(--bg2);
  border: 1px solid rgba(0,212,255,0.15);
  border-radius: 8px;
  margin-bottom: 24px;
  overflow: hidden;
}
.section-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 20px;
  background: rgba(0,212,255,0.05);
  border-bottom: 1px solid rgba(0,212,255,0.12);
  cursor: pointer;
  user-select: none;
}
.section-icon { font-size: 16px; }
.section-title {
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 2px;
  color: var(--cy);
  flex: 1;
}
.section-badge {
  font-size: 10px;
  padding: 2px 10px;
  border-radius: 12px;
  font-weight: 600;
  letter-spacing: 1px;
}
.badge-ok   { background: rgba(0,255,157,0.15); color: var(--gn); border: 1px solid rgba(0,255,157,0.3); }
.badge-warn { background: rgba(255,215,0,0.1);  color: var(--yw); border: 1px solid rgba(255,215,0,0.3); }
.badge-fail { background: rgba(255,60,120,0.15); color: var(--pk); border: 1px solid rgba(255,60,120,0.3); }
.section-body { padding: 0; }

/* ── Check rows ── */
.check-table { width: 100%; border-collapse: collapse; }
.check-table tr { border-bottom: 1px solid rgba(0,212,255,0.07); }
.check-table tr:last-child { border-bottom: none; }
.check-table td { padding: 9px 20px; vertical-align: middle; }
.check-icon { width: 32px; font-size: 14px; }
.check-label { width: 280px; font-weight: 500; color: var(--txt); }
.check-detail { color: var(--dim); font-size: 12px; word-break: break-word; }
.check-ok   .check-icon::before { content: '✅'; }
.check-fail .check-icon::before { content: '❌'; }
.check-warn .check-icon::before { content: '⚠️'; }
.check-ok   { background: rgba(0,255,157,0.02); }
.check-fail { background: rgba(255,60,120,0.04); }

/* ── Grid of info ── */
.info-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 1px;
  background: rgba(0,212,255,0.08);
}
.info-cell {
  background: var(--bg2);
  padding: 12px 18px;
}
.info-cell .key { font-size: 10px; color: var(--dim); letter-spacing: 1px; text-transform: uppercase; margin-bottom: 4px; }
.info-cell .val { color: var(--cy); font-size: 13px; }

/* ── Connectivity matrix ── */
.matrix-table { width: 100%; border-collapse: collapse; }
.matrix-table th {
  padding: 10px 20px;
  background: rgba(0,0,0,0.3);
  color: var(--dim);
  font-size: 10px;
  letter-spacing: 1px;
  text-align: left;
  font-weight: 600;
}
.matrix-table td { padding: 9px 20px; border-bottom: 1px solid rgba(0,212,255,0.07); font-size: 12px; }
.matrix-table tr:last-child td { border-bottom: none; }
.conn-ok   { color: var(--gn); font-weight: 600; }
.conn-fail { color: var(--pk); font-weight: 600; }

/* ── Service table ── */
.svc-table { width: 100%; border-collapse: collapse; }
.svc-table th { padding: 10px 20px; background: rgba(0,0,0,0.3); color: var(--dim); font-size: 10px; letter-spacing: 1px; text-align: left; font-weight: 600; }
.svc-table td { padding: 9px 20px; border-bottom: 1px solid rgba(0,212,255,0.07); font-size: 12px; }
.svc-table tr:last-child td { border-bottom: none; }
.svc-active   { color: var(--gn); }
.svc-inactive { color: var(--pk); }
.port-open    { color: var(--gn); }
.port-closed  { color: var(--pk); }
.port-na      { color: var(--dim); }

/* ── Log box ── */
.log-box {
  background: #010810;
  border-top: 1px solid rgba(0,212,255,0.1);
  padding: 16px 20px;
  font-size: 11px;
  color: #5a8aaa;
  max-height: 280px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
.log-box::-webkit-scrollbar { width: 6px; }
.log-box::-webkit-scrollbar-track { background: transparent; }
.log-box::-webkit-scrollbar-thumb { background: rgba(0,212,255,0.2); border-radius: 3px; }

/* ── Model pills ── */
.pill {
  display: inline-block;
  background: rgba(157,78,255,0.15);
  border: 1px solid rgba(157,78,255,0.3);
  border-radius: 20px;
  padding: 3px 12px;
  font-size: 11px;
  color: #c8a0ff;
  margin: 4px;
}

/* ── Footer ── */
.footer {
  border-top: 1px solid rgba(0,212,255,0.1);
  padding: 20px 40px;
  font-size: 11px;
  color: var(--dim);
  text-align: center;
  letter-spacing: 1px;
}
"""

def section_badge(checks):
    total = len(checks)
    if total == 0:
        return "badge-warn", "NO DATA"
    ok = sum(1 for c in checks if c["ok"])
    if ok == total:
        return "badge-ok", f"{ok}/{total} OK"
    elif ok == 0:
        return "badge-fail", f"0/{total} FAIL"
    else:
        return "badge-warn", f"{ok}/{total} OK"


def render_checks(checks: list) -> str:
    rows = []
    for c in checks:
        cls = "check-ok" if c["ok"] else "check-fail"
        lbl = html.escape(c["label"])
        det = html.escape(c.get("detail", ""))
        rows.append(
            f'<tr class="{cls}">'
            f'<td class="check-icon"></td>'
            f'<td class="check-label">{lbl}</td>'
            f'<td class="check-detail">{det}</td>'
            f'</tr>'
        )
    return f'<table class="check-table">{"".join(rows)}</table>'


def render_device_section(dev: dict) -> str:
    badge_cls, badge_txt = section_badge(dev["checks"])
    ok_count = sum(1 for c in dev["checks"] if c["ok"])
    icon = "🖥️" if "Mac" in dev["name"] else ("🖧" if "simon" in dev["name"] else "📱")
    body = render_checks(dev["checks"])

    # Extra sections
    extra = ""
    secs = dev.get("sections", {})

    if "system" in secs:
        s = secs["system"]
        extra += '<div class="info-grid">'
        for k, v in [("CPU Cores", s["cpu"]), ("RAM", s["ram"]),
                     ("Disk /", s["disk"]), ("Load Avg", s["load"]),
                     ("Uptime", s["uptime"]), ("Kernel", s["kernel"])]:
            extra += f'<div class="info-cell"><div class="key">{k}</div><div class="val">{html.escape(v)}</div></div>'
        extra += '</div>'

    if "services" in secs:
        rows = []
        for s in secs["services"]:
            a_cls = "svc-active" if s["active"] == "active" else "svc-inactive"
            if s["port"] is None:
                port_cell = '<span class="port-na">—</span>'
            elif s["port_ok"]:
                port_cell = f'<span class="port-open">:{s["port"]} open</span>'
            else:
                port_cell = f'<span class="port-closed">:{s["port"]} closed</span>'
            rows.append(
                f'<tr><td>{html.escape(s["name"])}</td>'
                f'<td class="{a_cls}">{s["active"]}</td>'
                f'<td style="color:var(--dim)">{s["enabled"]}</td>'
                f'<td>{port_cell}</td></tr>'
            )
        extra += (
            '<table class="svc-table">'
            '<tr><th>SERVICE</th><th>ACTIVE</th><th>ENABLED</th><th>PORT</th></tr>'
            + "".join(rows) + '</table>'
        )

    if "ollama_models" in secs:
        pills = "".join(f'<span class="pill">{html.escape(m)}</span>' for m in secs["ollama_models"])
        extra += f'<div style="padding:14px 20px">{pills}</div>'

    if "tailscale" in secs:
        ts = html.escape(secs["tailscale"])
        extra += f'<div class="log-box">{ts}</div>'

    if "df" in secs:
        extra += f'<div class="log-box">{html.escape(secs["df"])}</div>'

    if "log_errors" in secs:
        log = secs["log_errors"].strip()
        log_html = html.escape(log) if log else "No errors in last 24h ✅"
        extra += f'<div style="padding:10px 20px;font-size:11px;color:var(--dim);border-top:1px solid rgba(0,212,255,0.1)">SYSTEM LOG — ERRORS (24H)</div>'
        extra += f'<div class="log-box">{log_html}</div>'

    if "svc_logs" in secs:
        for svc, log in secs["svc_logs"].items():
            if log.strip():
                extra += f'<div style="padding:8px 20px 4px;font-size:10px;color:var(--vi);border-top:1px solid rgba(0,212,255,0.07);letter-spacing:1px">LOG: {svc.upper()}</div>'
                extra += f'<div class="log-box" style="max-height:140px">{html.escape(log)}</div>'

    if "battery" in secs:
        b = secs["battery"]
        extra += '<div class="info-grid">'
        for k, v in [("Battery Level", f'{b["level"]}%'), ("Status", b["status"]),
                     ("Plugged", b["plugged"]), ("Temp", b["temp"])]:
            extra += f'<div class="info-cell"><div class="key">{k}</div><div class="val">{html.escape(str(v))}</div></div>'
        extra += '</div>'

    if "device" in secs:
        d2 = secs["device"]
        extra += '<div class="info-grid">'
        for k, v in [("Model", d2["model"]), ("Android", d2["android"]),
                     ("API Level", d2["api"]), ("Serial", d2["serial"])]:
            extra += f'<div class="info-cell"><div class="key">{k}</div><div class="val">{html.escape(str(v))}</div></div>'
        extra += '</div>'

    return f"""
<div class="section">
  <div class="section-header">
    <span class="section-icon">{icon}</span>
    <span class="section-title">{html.escape(dev["name"])} — {html.escape(dev["ip"])}</span>
    <span class="section-badge {badge_cls}">{badge_txt}</span>
  </div>
  <div class="section-body">
    {body}
    {extra}
  </div>
</div>"""


def render_connectivity(conn: dict) -> str:
    matrix = conn["matrix"]
    ok_count = sum(1 for r in matrix if r["ok"])
    badge_cls = "badge-ok" if ok_count == len(matrix) else ("badge-warn" if ok_count > 0 else "badge-fail")
    badge_txt = f"{ok_count}/{len(matrix)} OK"
    rows = []
    for r in matrix:
        cls = "conn-ok" if r["ok"] else "conn-fail"
        status = "● CONNECTED" if r["ok"] else "✕ UNREACHABLE"
        rows.append(
            f'<tr><td>{html.escape(r["from"])}</td>'
            f'<td style="color:var(--dim)">→</td>'
            f'<td>{html.escape(r["to"])}</td>'
            f'<td style="color:var(--dim)">{html.escape(r["method"])}</td>'
            f'<td class="{cls}">{status}</td></tr>'
        )
    return f"""
<div class="section">
  <div class="section-header">
    <span class="section-icon">🔗</span>
    <span class="section-title">CONNECTIVITY MATRIX</span>
    <span class="section-badge {badge_cls}">{badge_txt}</span>
  </div>
  <div class="section-body">
    <table class="matrix-table">
      <tr><th>FROM</th><th></th><th>TO</th><th>METHOD</th><th>STATUS</th></tr>
      {"".join(rows)}
    </table>
  </div>
</div>"""


def build_report(mac: dict, hq: dict, android: dict, conn: dict) -> str:
    all_checks = mac["checks"] + hq["checks"] + android["checks"]
    total = len(all_checks)
    passing = sum(1 for c in all_checks if c["ok"])
    failing = total - passing
    conn_total = len(conn["matrix"])
    conn_ok = sum(1 for r in conn["matrix"] if r["ok"])

    ts_human = datetime.now().strftime("%A, %B %d %Y — %H:%M:%S")
    overall = "HEALTHY" if failing == 0 else (f"{failing} ISSUES DETECTED" if failing < 5 else "DEGRADED")
    overall_color = "green" if failing == 0 else ("yellow" if failing < 5 else "red")

    summary_bar = f"""
<div class="summary-bar">
  <div class="summary-card">
    <div class="num {overall_color}">{overall}</div>
    <div class="lbl">OVERALL STATUS</div>
  </div>
  <div class="summary-card">
    <div class="num green">{passing}</div>
    <div class="lbl">CHECKS PASSING</div>
  </div>
  <div class="summary-card">
    <div class="num {'red' if failing else 'green'}">{failing}</div>
    <div class="lbl">CHECKS FAILING</div>
  </div>
  <div class="summary-card">
    <div class="num cyan">{conn_ok}/{conn_total}</div>
    <div class="lbl">CONNECTIONS OK</div>
  </div>
  <div class="summary-card">
    <div class="num cyan">3</div>
    <div class="lbl">DEVICES SCANNED</div>
  </div>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>NOVA System Report — {TS}</title>
<style>{STYLE}</style>
</head>
<body>
<div class="header">
  <div class="header-title">N.O.V.A. SYSTEM DIAGNOSTIC REPORT</div>
  <div class="header-sub">SIMON-X SOLUTIONS ● FULL STACK HEALTH CHECK ● ALL DEVICES</div>
  <div class="header-meta">
    <div class="ts">{ts_human}</div>
    <div>Mac (SIMON) · simon-hq (NOVA) · Pixel 9a</div>
  </div>
</div>
{summary_bar}
<div class="main">
  {render_device_section(mac)}
  {render_device_section(hq)}
  {render_device_section(android)}
  {render_connectivity(conn)}
</div>
<div class="footer">
  N.O.V.A. / S.I.M.O.N. ● Simon-X Solutions ● Generated {ts_human} ● {total} checks ● {passing} passing ● {failing} failing
</div>
</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print("  N.O.V.A. SYSTEM DIAGNOSTIC REPORT")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")
    print("Collecting data from all devices...\n")

    t0 = time.time()
    mac     = collect_mac()
    hq      = collect_hq()
    android = collect_android()
    conn    = collect_connectivity()
    elapsed = time.time() - t0

    print(f"\nBuilding HTML report...")
    report = build_report(mac, hq, android, conn)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(report, encoding="utf-8")

    # Summary to console
    all_checks = mac["checks"] + hq["checks"] + android["checks"]
    passing = sum(1 for c in all_checks if c["ok"])
    failing = len(all_checks) - passing
    conn_ok = sum(1 for r in conn["matrix"] if r["ok"])

    print(f"\n{'='*60}")
    print(f"  REPORT COMPLETE — {elapsed:.1f}s")
    print(f"  {passing} passing | {failing} failing | {conn_ok}/{len(conn['matrix'])} connections OK")
    print(f"  Saved: {REPORT_FILE}")
    print(f"{'='*60}\n")

    if failing:
        print("ISSUES FOUND:")
        for c in all_checks:
            if not c["ok"]:
                print(f"  ✗ {c['label']}: {c.get('detail','')}")
        print()

    # Open report in browser on Mac
    rc, _ = sh(f"open '{REPORT_FILE}'")
    if rc == 0:
        print("  ↑ Report opened in your default browser.\n")

    return 0 if failing == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
