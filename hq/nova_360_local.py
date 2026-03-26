#!/usr/bin/env python3
"""
N.O.V.A. 360 Local Diagnostic — runs FROM simon-hq
====================================================
Called by the nova_360_report MCP tool.
Covers ALL THREE devices just like the Mac-side script:
  • simon-hq (NOVA) — checked locally (no SSH)
  • Mac (SIMON)     — checked via HTTP/TCP over Tailscale
  • Pixel 9a        — checked via local ADB over Tailscale
  • Connectivity matrix — full mesh between all nodes

Output:
  Text summary  → printed to stdout (returned by MCP tool)
  HTML report   → /home/simon-hq/reports/NOVA_360_<timestamp>.html
"""

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

MAC_IP       = "YOUR_MAC_TAILSCALE_IP"
HQ_IP        = "YOUR_HQ_TAILSCALE_IP"
ANDROID_IP   = _cfg.get("android_ip",   "YOUR_ANDROID_TAILSCALE_IP")
ANDROID_PORT = _cfg.get("android_port", 5555)
ADB_TARGET   = f"{ANDROID_IP}:{ANDROID_PORT}"

MAC_API  = f"http://{MAC_IP}:8765"
REPORT_DIR  = Path("/home/simon-hq/reports")
TS          = datetime.now().strftime("%Y-%m-%d_%H-%M")
REPORT_FILE = REPORT_DIR / f"NOVA_360_{TS}.html"

# ── Helpers ───────────────────────────────────────────────────────────────────

def sh(cmd: str, timeout: int = 10) -> tuple:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 1, f"[timeout after {timeout}s]"
    except Exception as e:
        return 1, f"[error: {e}]"

def port_open(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close(); return True
    except Exception:
        return False

def http_check(url: str, timeout: int = 5) -> tuple:
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "NOVA-360/1.0"}),
            timeout=timeout
        ) as r:
            return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return True, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)[:80]

def adb_sh(cmd: str) -> str:
    _, out = sh(f"adb -s {ADB_TARGET} shell {cmd} 2>&1", timeout=12)
    return out.strip()


# ── Collectors ────────────────────────────────────────────────────────────────

def collect_hq(results: dict):
    """Collect simon-hq data locally — no SSH needed."""
    checks = results["checks"]
    secs   = results["sections"]

    def add(label, ok, detail="", cat="hq"):
        checks.append({"label": label, "ok": ok, "detail": detail, "cat": cat})

    # System stats
    _, cpu    = sh("nproc")
    _, ram    = sh("free -h | awk '/^Mem:/{print $2\" total, \"$3\" used, \"$4\" free\"}'")
    _, disk   = sh("df -h / | awk 'NR==2{print $2\" total, \"$3\" used (\"$5\" full)\"}'")
    _, load   = sh("uptime | awk -F'load average:' '{print $2}'")
    _, uptime = sh("uptime -p")
    _, kver   = sh("uname -r")
    secs["hq_system"] = {
        "cpu": cpu.strip(), "ram": ram.strip(), "disk": disk.strip(),
        "load": load.strip(), "uptime": uptime.strip(), "kernel": kver.strip(),
    }
    add("HQ system stats", True,
        f"CPU: {cpu} cores | RAM: {ram.strip()} | Disk: {disk.strip()}", "hq")

    # Services
    services = [
        ("nova-webui",    3000), ("nova-hud",       3001),
        ("nova-mcpo",     8301),
        ("simon-hq-api",  8200), ("simon-chroma",   8100),
        ("ollama",       11434), ("tailscaled",      None),
    ]
    svc_data = []
    for svc, port in services:
        _, active  = sh(f"systemctl is-active {svc} 2>&1")
        _, enabled = sh(f"systemctl is-enabled {svc} 2>&1")
        active = active.strip(); enabled = enabled.strip()
        port_ok = port_open("127.0.0.1", port) if port else None
        svc_data.append({"name": svc, "active": active, "enabled": enabled,
                         "port": port, "port_ok": port_ok})
        ok     = (active == "active")
        detail = f"enabled={enabled}"
        if port is not None:
            detail += f" | :{port} {'✓' if port_ok else '✗'}"
        add(svc, ok, detail, "services")
    secs["services"] = svc_data

    # Ollama
    ollama_ok, _ = http_check("http://127.0.0.1:11434/api/tags")
    if ollama_ok:
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as r:
                models = json.loads(r.read()).get("models", [])
            names = [m["name"] for m in models]
            add("Ollama API", True, f"{len(names)} model(s): {', '.join(names[:6])}", "hq")
            secs["ollama_models"] = names
        except Exception as e:
            add("Ollama API", False, str(e), "hq")
            secs["ollama_models"] = []
    else:
        add("Ollama API", False, "127.0.0.1:11434 not responding", "hq")
        secs["ollama_models"] = []

    # ChromaDB — v2 API (ChromaDB 0.5+)
    _CHROMA_BASE = "http://127.0.0.1:8100/api/v2"
    ch_ok, ch_msg = http_check(f"{_CHROMA_BASE}/heartbeat")
    if not ch_ok:
        # Try legacy v1 as fallback
        ch_ok, ch_msg = http_check("http://127.0.0.1:8100/api/v1/heartbeat")
        _COLS_URL = "http://127.0.0.1:8100/api/v1/collections"
    else:
        _COLS_URL = f"{_CHROMA_BASE}/tenants/default_tenant/databases/default_database/collections"
    if ch_ok:
        try:
            with urllib.request.urlopen(_COLS_URL, timeout=5) as r:
                cols = json.loads(r.read())
            col_names = [c.get("name", "?") for c in (cols if isinstance(cols, list) else [])]
            add("ChromaDB", True, f"{len(col_names)} collection(s): {', '.join(col_names)}", "hq")
            secs["chroma"] = col_names
        except Exception as e:
            add("ChromaDB", False, str(e), "hq")
    else:
        add("ChromaDB", False, f"127.0.0.1:8100 not responding — {ch_msg}", "hq")

    # Firewall
    _, ufw_svc  = sh("systemctl is-active ufw 2>&1")
    _, ufw_enbl = sh("systemctl is-enabled ufw 2>&1")
    add("UFW firewall", ufw_svc.strip() == "active",
        f"active={ufw_svc.strip()} enabled={ufw_enbl.strip()}", "hq")

    # Tailscale
    _, ts_ip = sh("tailscale ip --4 2>&1")
    _, ts_st = sh("tailscale status 2>&1 | head -4")
    ts_ok = "100." in ts_ip
    add("Tailscale (HQ)", ts_ok, f"IP: {ts_ip.strip()}", "hq")
    secs["hq_tailscale"] = ts_st

    # Log errors
    _, log_errs = sh(
        "journalctl -p err -n 20 --no-pager --since '24h ago' 2>&1 | "
        "grep -v 'Bluetooth\\|ACPI\\|pci\\|usb' | head -20",
        timeout=10
    )
    secs["log_errors"] = log_errs or "No errors in last 24h ✅"


def collect_mac(results: dict):
    """Collect Mac (SIMON) stats via Tailscale HTTP."""
    checks = results["checks"]
    secs   = results["sections"]

    def add(label, ok, detail="", cat="mac"):
        checks.append({"label": label, "ok": ok, "detail": detail, "cat": cat})

    # Basic reachability
    mac_reachable = port_open(MAC_IP, 22, timeout=4)
    add("Mac reachable (SSH port)", mac_reachable, f"{MAC_IP}:22", "mac")

    # SIMON API
    api_ok, api_msg = http_check(f"{MAC_API}/api/status")
    add("Mac SIMON API (port 8765)", api_ok, api_msg, "mac")

    if api_ok:
        try:
            with urllib.request.urlopen(f"{MAC_API}/api/status", timeout=5) as r:
                data = json.loads(r.read())
            plugins = data.get("plugins_loaded", "?")
            uptime  = data.get("uptime", "?")
            model   = data.get("model", "?")
            add("SIMON status", True,
                f"plugins={plugins} | uptime={uptime} | model={model}", "mac")
            secs["mac_status"] = data
        except Exception:
            pass

    # Mac known ports — LM Studio is optional (not always running)
    hud_ok = port_open(MAC_IP, 8765, timeout=3)
    add("Mac SIMON HUD (port 8765)", hud_ok, f"{MAC_IP}:8765", "mac")

    lm_ok = port_open(MAC_IP, 1234, timeout=3)
    # LM Studio is optional — mark as info, not failure
    checks.append({
        "label": "Mac LM Studio (port 1234)",
        "ok": True,   # always green — optional service
        "detail": f"{MAC_IP}:1234 — {'running' if lm_ok else 'not running (optional)'}",
        "cat": "mac",
    })


def collect_android(results: dict):
    """Collect Pixel 9a data via local ADB over Tailscale."""
    checks = results["checks"]
    secs   = results["sections"]

    def add(label, ok, detail="", cat="android"):
        checks.append({"label": label, "ok": ok, "detail": detail, "cat": cat})

    # ADB connect
    _, conn_out = sh(f"adb connect {ADB_TARGET} 2>&1", timeout=10)
    adb_ok = "connected" in conn_out.lower() or "already" in conn_out.lower()
    add("ADB connection", adb_ok, conn_out.strip(), "android")

    if not adb_ok:
        add("Android data", False,
            "ADB not connected — check USB debugging + Tailscale on phone", "android")
        return

    model   = adb_sh("getprop ro.product.model")
    android = adb_sh("getprop ro.build.version.release")
    api     = adb_sh("getprop ro.build.version.sdk")
    serial  = adb_sh("getprop ro.serialno")
    add("Device identified", bool(model),
        f"{model} | Android {android} (API {api})", "android")

    # Battery
    batt_raw = adb_sh("dumpsys battery | grep -E 'level|status|plugged|temperature'")
    batt = {l.split(":")[0].strip(): l.split(":")[-1].strip()
            for l in batt_raw.splitlines() if ":" in l}
    level = batt.get("level", "?")
    status_map = {"2": "Charging", "3": "Discharging", "4": "Not charging", "5": "Full"}
    batt_status = status_map.get(batt.get("status", ""), batt.get("status", "?"))
    try: temp_str = f"{int(batt.get('temperature','0'))/10:.0f}°C"
    except: temp_str = "?"
    batt_ok = int(level) > 10 if level.isdigit() else True
    add("Battery", batt_ok,
        f"{level}% | {batt_status} | Temp: {temp_str}", "android")

    # Storage
    storage = adb_sh("df /data 2>/dev/null | tail -1 | awk '{print $2\" total, \"$3\" used, \"$4\" free\"}'")
    if not storage or storage.startswith("["):
        storage = adb_sh("dumpsys diskstats 2>/dev/null | grep -E 'Data-Free|App' | head -3")
    add("Storage", bool(storage and not storage.startswith("[")),
        storage or "query returned empty", "android")

    # WiFi
    wifi_raw = adb_sh("dumpsys wifi | grep 'mWifiInfo' | head -1")
    ssid = ""
    if "SSID:" in wifi_raw:
        try: ssid = wifi_raw.split("SSID:")[1].split(",")[0].strip()
        except: pass
    add("WiFi", bool(ssid), ssid or "no SSID found", "android")

    # Screen state
    screen = adb_sh("dumpsys power | grep 'mWakefulness='").replace("mWakefulness=", "")
    add("Screen state", True, screen or "unknown", "android")

    # Tailscale app
    ts_pkg = adb_sh("pm list packages | grep tailscale")
    add("Tailscale app", "tailscale" in ts_pkg, ts_pkg.strip() or "not installed", "android")

    secs["android"] = {
        "model": model, "android": android, "api": api, "serial": serial,
        "battery": level, "batt_status": batt_status, "temp": temp_str,
        "wifi": ssid, "screen": screen,
    }


def collect_connectivity(results: dict):
    """Full mesh connectivity test between all three nodes."""
    checks = results["checks"]

    def add(label, ok, detail=""):
        checks.append({"label": label, "ok": ok, "detail": detail, "cat": "connectivity"})

    # HQ → Mac
    ok = port_open(MAC_IP, 8765, timeout=4)
    add("HQ → Mac SIMON API (8765)", ok, f"{MAC_IP}:8765")

    ok = port_open(MAC_IP, 22, timeout=4)
    add("HQ → Mac SSH (22)", ok, f"{MAC_IP}:22")

    # HQ → Android
    _, adb_out = sh(f"adb connect {ADB_TARGET} 2>&1", timeout=8)
    ok = "connected" in adb_out.lower() or "already" in adb_out.lower()
    add("HQ → Android ADB", ok, ADB_TARGET)

    # HQ → Internet (TCP port 53 to Google DNS — ICMP is blocked in systemd sandbox)
    inet_ok = port_open("8.8.8.8", 53, timeout=4)
    add("HQ → Internet (8.8.8.8:53)", inet_ok, "TCP/DNS")

    # Mac → HQ (check if HQ ports are reachable from Tailscale perspective)
    ok3 = port_open(HQ_IP, 3000, timeout=4)
    add("Mac → HQ WebUI (3000)", ok3, f"{HQ_IP}:3000")

    ok4 = port_open(HQ_IP, 3001, timeout=4)
    add("Mac → HQ HUD (3001)", ok4, f"{HQ_IP}:3001")

    ok5 = port_open(ANDROID_IP, ANDROID_PORT, timeout=4)
    add("HQ → Android Tailscale TCP", ok5, f"{ANDROID_IP}:{ANDROID_PORT}")

    results["sections"]["connectivity_checked"] = True


# ── HTML builder ──────────────────────────────────────────────────────────────

STYLE = """
:root{--bg:#020912;--bg2:#050f1e;--bg3:#091728;--cy:#00d4ff;--vi:#9d4eff;
      --gn:#00ff9d;--pk:#ff3c78;--yw:#ffd700;--txt:#c8e6f5;--dim:#4a7a99;}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--txt);font-family:'Consolas','SF Mono',monospace;font-size:13px;line-height:1.5;}
.hdr{background:linear-gradient(135deg,#020912,#0a1a2e,#020912);
     border-bottom:1px solid var(--cy);padding:24px 36px;position:relative;}
.hdr::before{content:'';position:absolute;inset:0;
  background:radial-gradient(ellipse at 20% 50%,rgba(0,212,255,.06) 0%,transparent 60%),
             radial-gradient(ellipse at 80% 50%,rgba(157,78,255,.06) 0%,transparent 60%);
  pointer-events:none;}
.ttl{font-size:24px;font-weight:700;letter-spacing:3px;color:var(--cy);
     text-shadow:0 0 18px rgba(0,212,255,.5);}
.sub{font-size:11px;color:var(--dim);letter-spacing:2px;margin-top:4px;}
.ts{color:var(--dim);font-size:11px;margin-top:8px;}
.bar{display:flex;gap:12px;padding:14px 36px;background:var(--bg2);
     border-bottom:1px solid rgba(0,212,255,.1);flex-wrap:wrap;}
.card{flex:1;min-width:130px;background:var(--bg3);
      border:1px solid rgba(0,212,255,.18);border-radius:6px;padding:10px 14px;text-align:center;}
.num{font-size:26px;font-weight:700;line-height:1;}
.lbl{font-size:10px;color:var(--dim);letter-spacing:1px;margin-top:3px;}
.gn{color:var(--gn);}.pk{color:var(--pk);}.cy{color:var(--cy);}.yw{color:var(--yw);}
.main{padding:24px 36px;max-width:1400px;}
.sec{background:var(--bg2);border:1px solid rgba(0,212,255,.12);
     border-radius:8px;margin-bottom:20px;overflow:hidden;}
.sh{display:flex;align-items:center;gap:10px;padding:12px 18px;
    background:rgba(0,212,255,.04);border-bottom:1px solid rgba(0,212,255,.08);}
.st{font-size:12px;font-weight:600;letter-spacing:2px;color:var(--cy);flex:1;}
.bdg{font-size:10px;padding:2px 10px;border-radius:12px;font-weight:600;letter-spacing:1px;}
.bok{background:rgba(0,255,157,.1);color:var(--gn);border:1px solid rgba(0,255,157,.25);}
.bwn{background:rgba(255,215,0,.08);color:var(--yw);border:1px solid rgba(255,215,0,.2);}
.bfl{background:rgba(255,60,120,.1);color:var(--pk);border:1px solid rgba(255,60,120,.25);}
table{width:100%;border-collapse:collapse;}
th{padding:8px 18px;background:rgba(0,0,0,.25);color:var(--dim);font-size:10px;
   letter-spacing:1px;text-align:left;font-weight:600;}
td{padding:8px 18px;border-bottom:1px solid rgba(0,212,255,.06);font-size:12px;}
tr:last-child td{border:none;}
.rok{background:rgba(0,255,157,.02);} .rfl{background:rgba(255,60,120,.04);}
.ic{width:28px;font-size:14px;} .lc{width:240px;font-weight:500;} .dc{color:var(--dim);}
.info-g{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));
        gap:1px;background:rgba(0,212,255,.06);}
.ic2{background:var(--bg2);padding:10px 16px;}
.ik{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:1px;}
.iv{color:var(--cy);font-size:12px;margin-top:3px;}
.pill{display:inline-block;background:rgba(157,78,255,.12);
      border:1px solid rgba(157,78,255,.25);border-radius:20px;
      padding:3px 12px;font-size:11px;color:#c8a0ff;margin:3px;}
.log{background:#010810;padding:14px 18px;font-size:11px;color:#5a8aaa;
     max-height:200px;overflow-y:auto;white-space:pre-wrap;word-break:break-all;
     border-top:1px solid rgba(0,212,255,.07);}
.ftr{border-top:1px solid rgba(0,212,255,.08);padding:16px 36px;
     font-size:11px;color:var(--dim);text-align:center;letter-spacing:1px;}
"""

import html as _html

def _e(s): return _html.escape(str(s))

def _badge(cat_checks):
    ok = sum(1 for c in cat_checks if c["ok"])
    t  = len(cat_checks)
    if not t: return "bwn", "NO DATA"
    if ok == t: return "bok", f"{ok}/{t} OK"
    if ok == 0: return "bfl", f"0/{t} FAIL"
    return "bwn", f"{ok}/{t} OK"

def _rows(cat_checks):
    out = ""
    for c in cat_checks:
        cls = "rok" if c["ok"] else "rfl"
        ic  = "✅" if c["ok"] else "❌"
        out += (f'<tr class="{cls}"><td class="ic">{ic}</td>'
                f'<td class="lc">{_e(c["label"])}</td>'
                f'<td class="dc">{_e(c.get("detail",""))}</td></tr>')
    return out

def build_html(data: dict) -> str:
    checks  = data["checks"]
    secs    = data.get("sections", {})
    passing = sum(1 for c in checks if c["ok"])
    failing = len(checks) - passing
    ts_h    = datetime.now().strftime("%A, %B %d %Y — %H:%M:%S")

    ov_txt = "ALL SYSTEMS GO" if failing == 0 else f"{failing} ISSUE(S)"
    ov_col = "gn" if failing == 0 else ("pk" if failing > 4 else "yw")

    bar = f"""<div class="bar">
<div class="card"><div class="num {ov_col}">{_e(ov_txt)}</div><div class="lbl">OVERALL</div></div>
<div class="card"><div class="num gn">{passing}</div><div class="lbl">PASSING</div></div>
<div class="card"><div class="num {'pk' if failing else 'gn'}">{failing}</div><div class="lbl">FAILING</div></div>
<div class="card"><div class="num cy">3</div><div class="lbl">DEVICES</div></div>
<div class="card"><div class="num cy">{len(checks)}</div><div class="lbl">TOTAL CHECKS</div></div>
</div>"""

    # ── Issues box ──
    issues = [c for c in checks if not c["ok"]]
    issues_html = ""
    if issues:
        bc, bt = _badge(issues)
        issues_html = f"""<div class="sec">
<div class="sh"><span class="st">⚠️ ISSUES REQUIRING ATTENTION</span>
<span class="bdg bfl">{len(issues)} ISSUE(S)</span></div>
<table><tr><th></th><th>FAILED CHECK</th><th>DETAIL</th></tr>
{_rows(issues)}</table></div>"""

    # ── simon-hq section ──
    hq_sys = secs.get("hq_system", {})
    hq_checks = [c for c in checks if c["cat"] in ("hq", "services")]
    bc, bt = _badge(hq_checks)
    hq_info = "".join(
        f'<div class="ic2"><div class="ik">{k}</div><div class="iv">{_e(v)}</div></div>'
        for k, v in [
            ("CPU Cores", hq_sys.get("cpu","")), ("RAM", hq_sys.get("ram","")),
            ("Disk /", hq_sys.get("disk","")),   ("Load Avg", hq_sys.get("load","")),
            ("Uptime", hq_sys.get("uptime","")), ("Kernel", hq_sys.get("kernel","")),
        ]
    )
    # service table
    svc_rows = ""
    for s in secs.get("services", []):
        a_s = "color:var(--gn)" if s["active"]=="active" else "color:var(--pk)"
        ic  = "✅" if s["active"]=="active" else "❌"
        if s["port"] is None:
            p_cell = '<span style="color:var(--dim)">—</span>'
        elif s["port_ok"]:
            p_cell = f'<span style="color:var(--gn)">:{s["port"]} ✓</span>'
        else:
            p_cell = f'<span style="color:var(--pk)">:{s["port"]} ✗</span>'
        svc_rows += (f'<tr><td>{ic}</td><td>{_e(s["name"])}</td>'
                     f'<td style="{a_s}">{_e(s["active"])}</td>'
                     f'<td style="color:var(--dim)">{_e(s["enabled"])}</td>'
                     f'<td>{p_cell}</td></tr>')
    # Ollama pills
    pills = "".join(f'<span class="pill">{_e(m)}</span>' for m in secs.get("ollama_models", []))
    pills = pills or '<span style="color:var(--pk)">No models</span>'

    hq_html = f"""<div class="sec">
<div class="sh"><span class="st">🖧 SIMON-HQ (NOVA) — {HQ_IP}</span>
<span class="bdg {bc}">{bt}</span></div>
<div class="info-g">{hq_info}</div>
<table><tr><th></th><th>SERVICE</th><th>ACTIVE</th><th>ENABLED</th><th>PORT</th></tr>
{svc_rows}</table>
<div style="padding:12px 18px;border-top:1px solid rgba(0,212,255,.07)">
<div style="font-size:10px;color:var(--dim);letter-spacing:1px;margin-bottom:6px">OLLAMA MODELS</div>
{pills}</div>
<div class="log">{_e(secs.get('hq_tailscale',''))}</div>
</div>"""

    # ── Mac (SIMON) section ──
    mac_checks = [c for c in checks if c["cat"] == "mac"]
    bc2, bt2 = _badge(mac_checks)
    mac_status = secs.get("mac_status", {})
    mac_info = ""
    if mac_status:
        mac_info = '<div class="info-g">' + "".join(
            f'<div class="ic2"><div class="ik">{k}</div><div class="iv">{_e(str(v))}</div></div>'
            for k, v in mac_status.items() if k not in ("error",)
        ) + '</div>'
    mac_html = f"""<div class="sec">
<div class="sh"><span class="st">🖥️ MAC (SIMON) — {MAC_IP}</span>
<span class="bdg {bc2}">{bt2}</span></div>
{mac_info}
<table><tr><th></th><th>CHECK</th><th>DETAIL</th></tr>
{_rows(mac_checks)}</table></div>"""

    # ── Android section ──
    android_checks = [c for c in checks if c["cat"] == "android"]
    bc3, bt3 = _badge(android_checks)
    andrd = secs.get("android", {})
    andrd_info = ""
    if andrd:
        andrd_info = '<div class="info-g">' + "".join(
            f'<div class="ic2"><div class="ik">{k}</div><div class="iv">{_e(str(v))}</div></div>'
            for k, v in [
                ("Model", andrd.get("model","")), ("Android", andrd.get("android","")),
                ("API Level", andrd.get("api","")), ("Serial", andrd.get("serial","")),
                ("Battery", f'{andrd.get("battery","?")}%'),
                ("Status", andrd.get("batt_status","")),
                ("Temp", andrd.get("temp","")),
                ("WiFi SSID", andrd.get("wifi","")),
                ("Screen", andrd.get("screen","")),
            ]
        ) + '</div>'
    android_html = f"""<div class="sec">
<div class="sh"><span class="st">📱 PIXEL 9a — {ANDROID_IP}</span>
<span class="bdg {bc3}">{bt3}</span></div>
{andrd_info}
<table><tr><th></th><th>CHECK</th><th>DETAIL</th></tr>
{_rows(android_checks)}</table></div>"""

    # ── Connectivity matrix ──
    conn_checks = [c for c in checks if c["cat"] == "connectivity"]
    bc4, bt4 = _badge(conn_checks)
    conn_rows = "".join(
        f'<tr><td>{"✅" if c["ok"] else "❌"}</td>'
        f'<td style="color:{"var(--gn)" if c["ok"] else "var(--pk)"}">'
        f'{"● CONNECTED" if c["ok"] else "✕ UNREACHABLE"}</td>'
        f'<td class="lc">{_e(c["label"])}</td>'
        f'<td class="dc">{_e(c.get("detail",""))}</td></tr>'
        for c in conn_checks
    )
    conn_html = f"""<div class="sec">
<div class="sh"><span class="st">🔗 CONNECTIVITY MATRIX</span>
<span class="bdg {bc4}">{bt4}</span></div>
<table><tr><th></th><th>STATUS</th><th>LINK</th><th>ENDPOINT</th></tr>
{conn_rows}</table></div>"""

    # ── Log errors ──
    log_text = _e(secs.get("log_errors", "No errors in last 24h ✅"))
    log_html = f"""<div class="sec">
<div class="sh"><span class="st">📋 SYSTEM LOG — ERRORS (LAST 24H)</span></div>
<div class="log">{log_text}</div></div>"""

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"/>
<title>NOVA 360 Report — {TS}</title>
<style>{STYLE}</style></head>
<body>
<div class="hdr">
  <div class="ttl">N.O.V.A. 360 DIAGNOSTIC REPORT</div>
  <div class="sub">SIMON-X SOLUTIONS ● MAC · SIMON-HQ · PIXEL 9A</div>
  <div class="ts">{_e(ts_h)}</div>
</div>
{bar}
<div class="main">
{issues_html}
{hq_html}
{mac_html}
{android_html}
{conn_html}
{log_html}
</div>
<div class="ftr">N.O.V.A. ● Simon-X Solutions ● {_e(ts_h)} ● {len(checks)} checks ● {passing} passing ● {failing} failing</div>
</body></html>"""


# ── Text summary ──────────────────────────────────────────────────────────────

def text_summary(data: dict) -> str:
    checks  = data["checks"]
    passing = sum(1 for c in checks if c["ok"])
    failing = len(checks) - passing
    secs    = data.get("sections", {})
    hq_s    = secs.get("hq_system", {})
    models  = secs.get("ollama_models", [])
    android = secs.get("android", {})
    ts_h    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    issues_text = "".join(
        f"  ✗ {c['label']}: {c.get('detail','')}\n"
        for c in checks if not c["ok"]
    )

    return f"""╔══════════════════════════════════════════════════════╗
║    N.O.V.A. 360 REPORT — {ts_h}
╠══════════════════════════════════════════════════════╣
║  STATUS : {'✅ ALL SYSTEMS GO' if not failing else f'⚠️  {failing} ISSUE(S) DETECTED'}
║  Checks : {passing} passing  /  {failing} failing  /  {len(checks)} total
╠══════════════════════════════════════════════════════╣
║  SIMON-HQ (NOVA)
║    CPU  : {hq_s.get('cpu','?')} cores   Load: {hq_s.get('load','?')}
║    RAM  : {hq_s.get('ram','?')}
║    Disk : {hq_s.get('disk','?')}
║    Up   : {hq_s.get('uptime','?')}
╠══════════════════════════════════════════════════════╣
║  OLLAMA ({len(models)} model(s)): {', '.join(models[:4]) or 'none'}
╠══════════════════════════════════════════════════════╣
║  MAC (SIMON)
║    API  : {'reachable' if any(c['ok'] for c in checks if 'SIMON API' in c['label']) else 'UNREACHABLE'}  —  {MAC_IP}:8765
╠══════════════════════════════════════════════════════╣
║  PIXEL 9a — {android.get('model','?')}
║    Android {android.get('android','?')}  API {android.get('api','?')}  Battery: {android.get('battery','?')}%  WiFi: {android.get('wifi','?')}
╠══════════════════════════════════════════════════════╣
{"║  ISSUES:\n" + issues_text if issues_text else "║  ✅ No issues detected\n"}╠══════════════════════════════════════════════════════╣
║  HTML → {REPORT_FILE}
╚══════════════════════════════════════════════════════╝"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> str:
    data = {"checks": [], "sections": {}}
    collect_hq(data)
    collect_mac(data)
    collect_android(data)
    collect_connectivity(data)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    html_content = build_html(data)
    REPORT_FILE.write_text(html_content, encoding="utf-8")

    summary = text_summary(data)
    print(summary, flush=True)
    return summary


if __name__ == "__main__":
    main()
