#!/usr/bin/env python3
"""
N.O.V.A. HUD Server v4.0 — simon-hq:3001 | Simon-X Solutions
==============================================================
Holographic AI interface with full PTY terminal, file tree,
live system data, and NOVA chat.
"""

import asyncio, collections, fcntl, hmac, json, mimetypes, os, pty, secrets, select, struct
import subprocess, termios, time
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── 2FA integration ───────────────────────────────────────────────────────────
try:
    from nova_2fa import require_admin_auth, get_2fa_status
    _2FA_AVAILABLE = True
except ImportError:
    _2FA_AVAILABLE = False
    def require_admin_auth(token, action):
        # Fail-SECURE: module missing = block admin ops
        return False, "🔐 nova_2fa.py not found — deploy it and restart nova-hud"

import chromadb, httpx, psutil, uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── Config ────────────────────────────────────────────────────────────────────
_cfg = {}
try:
    _cfg = json.loads((Path(__file__).parent / "nova_config.json").read_text())
except Exception:
    pass

OLLAMA_URL   = _cfg.get("ollama_hq_url", "http://127.0.0.1:11434")
HQ_API_URL   = _cfg.get("hq_api_url",    "http://127.0.0.1:8200")
HQ_API_KEY   = _cfg.get("hq_api_key",    "")
MAC_URL      = _cfg.get("mac_simon_url",  "http://YOUR_MAC_TAILSCALE_IP:8765")
ANDROID_IP   = _cfg.get("android_ip",    "YOUR_ANDROID_TAILSCALE_IP")
ANDROID_PORT = _cfg.get("android_port",  5555)
HQ_MODEL     = _cfg.get("hq_model",      "qwen2.5:7b")
ADB_TARGET   = f"{ANDROID_IP}:{ANDROID_PORT}"
NOVA_USER    = "simon-hq"
NOVA_HOME    = f"/home/{NOVA_USER}"

SERVICES = ["nova-webui","nova-mcpo","nova-hud",
            "simon-hq-api","simon-chroma","ollama","tailscaled"]

# ── HUD authentication ─────────────────────────────────────────────────────────
# Read token from env var (preferred) or config file.
# If neither is set, a random session token is generated at startup and printed once.
HUD_TOKEN: str = (
    os.environ.get("NOVA_HUD_TOKEN")
    or _cfg.get("nova_hud_token", "")
    or secrets.token_hex(32)
)
if not (os.environ.get("NOVA_HUD_TOKEN") or _cfg.get("nova_hud_token")):
    print(f"\n[NOVA HUD] ⚠️  No NOVA_HUD_TOKEN set — using ephemeral token this session.")
    print(f"[NOVA HUD]    Set NOVA_HUD_TOKEN={HUD_TOKEN} in the service env to persist.\n")

def _token_valid(t: str) -> bool:
    """Constant-time token comparison — safe against timing attacks."""
    if not t or not HUD_TOKEN:
        return False
    return hmac.compare_digest(t.encode(), HUD_TOKEN.encode())

# IP-based rate limiting for /api/admin/verify (10 attempts / 60s per IP)
_verify_ip_attempts: dict = collections.defaultdict(list)
_VERIFY_MAX = 10
_VERIFY_WIN = 60  # seconds

def _verify_rate_ok(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _verify_ip_attempts[ip] if now - t < _VERIFY_WIN]
    _verify_ip_attempts[ip] = recent
    if len(recent) >= _VERIFY_MAX:
        return False
    recent.append(now)
    return True

# ── App + CORS ─────────────────────────────────────────────────────────────────
# Use MagicDNS hostname when available; fall back to raw Tailscale IP
_NOVA_TS_HOST = _cfg.get("nova_tailscale_host", _cfg.get("nova_tailscale_ip", "YOUR_HQ_TAILSCALE_IP"))
_NOVA_TS_IP   = _cfg.get("nova_tailscale_ip", "YOUR_HQ_TAILSCALE_IP")
_MAC_TS_IP    = _cfg.get("mac_tailscale_ip",  "YOUR_MAC_TAILSCALE_IP")
_ALLOWED_ORIGINS = [
    f"http://{_NOVA_TS_IP}:3001",   f"http://{_NOVA_TS_IP}:8765",
    f"http://{_NOVA_TS_HOST}:3001", f"http://{_NOVA_TS_HOST}:8765",
    f"http://{_MAC_TS_IP}:8765",    f"http://{_MAC_TS_IP}:3001",
    "http://127.0.0.1:3001",        "http://127.0.0.1:8765",
    "http://localhost:3001",        "http://localhost:8765",
]

app = FastAPI(title="NOVA HUD")
app.add_middleware(CORSMiddleware,
                   allow_origins=_ALLOWED_ORIGINS,
                   allow_methods=["GET", "POST", "DELETE"],
                   allow_headers=["Authorization", "Content-Type"])

@app.middleware("http")
async def hud_auth_middleware(request: Request, call_next):
    """Require HUD token on all /api/* routes. WebSocket /ws/* checked separately."""
    if request.url.path.startswith("/api/"):
        auth  = request.headers.get("authorization", "")
        bearer = auth.replace("Bearer ", "").replace("bearer ", "").strip()
        query_t = request.query_params.get("token", "")
        if not _token_valid(bearer or query_t):
            return JSONResponse({"error": "Unauthorized — valid HUD token required"},
                                status_code=401)
    return await call_next(request)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """
    Add HTTP security response headers to every response.
    These headers defend against XSS, clickjacking, MIME sniffing,
    and information leakage.  Accessible only over Tailscale (no HTTPS
    needed on LAN, but headers still provide defence-in-depth).
    """
    response = await call_next(request)
    # Prevent MIME-type sniffing
    response.headers["X-Content-Type-Options"] = "nosniff"
    # Block the page from being embedded in iframes (clickjacking)
    response.headers["X-Frame-Options"] = "DENY"
    # Disable legacy XSS auditor (replaced by CSP below)
    response.headers["X-XSS-Protection"] = "0"
    # Referrer: don't leak the HUD URL to any external requests
    response.headers["Referrer-Policy"] = "no-referrer"
    # CSP: allow same-origin scripts/styles only; no inline scripts from external sources.
    # 'unsafe-inline' is required for the HUD's inline JS — tighten further with a nonce
    # if the HTML is refactored to use external JS files.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self' ws: wss:; "
        "frame-ancestors 'none';"
    )
    # Permissions Policy — disable features the HUD doesn't need
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    )
    # Remove server version banner (MutableHeaders uses __delitem__, not pop)
    try:
        del response.headers["server"]
    except KeyError:
        pass
    return response


# ── Shell helper ──────────────────────────────────────────────────────────────
def _sh(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception as e:
        return f"[err:{e}]"

import re as _re2
_ADB_INJECT_RE = _re2.compile(r'[;&|`$<>()\{\}\[\]]|&&|\|\|')

def _adb(cmd: str) -> str:
    """Run ADB shell command — shell=False to prevent injection."""
    if _ADB_INJECT_RE.search(cmd):
        return "[BLOCKED] ADB command contains shell metacharacters."
    try:
        r = subprocess.run(
            ["adb", "-s", ADB_TARGET, "shell", cmd],
            shell=False, capture_output=True, text=True, timeout=10
        )
        return (r.stdout + r.stderr).strip()
    except Exception as e:
        return f"[err:{e}]"

# ── Data collectors ───────────────────────────────────────────────────────────
def collect_system():
    c = psutil.cpu_percent(interval=0.2)
    r = psutil.virtual_memory()
    d = psutil.disk_usage("/")
    l = psutil.getloadavg()
    u = int(time.time() - psutil.boot_time())
    h, rem = divmod(u, 3600); m, _ = divmod(rem, 60)
    return dict(cpu_pct=round(c,1), ram_pct=round(r.percent,1),
                ram_used_gb=round(r.used/1e9,1), ram_total_gb=round(r.total/1e9,1),
                disk_pct=round(d.percent,1), disk_used_gb=round(d.used/1e9,1),
                disk_total_gb=round(d.total/1e9,1),
                load_1m=round(l[0],2), uptime=f"{h}h {m}m",
                hostname=_sh("hostname"), kernel=_sh("uname -r"))

def collect_services():
    return {s: dict(active=_sh(f"systemctl is-active {s} 2>&1"),
                    enabled=_sh(f"systemctl is-enabled {s} 2>&1"))
            for s in SERVICES}

async def collect_ollama():
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get(f"{OLLAMA_URL}/api/tags")
        ms = r.json().get("models", [])
        return dict(online=True, count=len(ms),
                    models=[dict(name=m["name"],
                                 size_gb=round(m.get("size",0)/1e9,1)) for m in ms])
    except Exception as e:
        return dict(online=False, count=0, models=[], error=str(e))

async def collect_mac():
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get(f"{MAC_URL}/api/status")
        d = r.json()
        return dict(online=True, cpu=d.get("cpu"), mem_gb=d.get("mem_gb"),
                    ip=d.get("ip"), time=d.get("time"))
    except:
        return dict(online=False)

async def collect_chroma():
    try:
        cl = chromadb.HttpClient(host="127.0.0.1", port=8100)
        cols = cl.list_collections()
        return dict(online=True, collections=len(cols),
                    total_docs=sum(c.count() for c in cols),
                    names=[c.name for c in cols])
    except:
        return dict(online=False, collections=0, total_docs=0)

def collect_android():
    if not _sh("which adb"):
        return dict(connected=False, error="adb not installed")
    _sh(f"adb connect {ADB_TARGET}", 6)
    devs = _sh("adb devices")
    if ADB_TARGET not in devs or "offline" in devs:
        return dict(connected=False, error=f"Device not reachable: {ADB_TARGET}")
    bat = _adb("dumpsys battery")
    lvl  = next((l.split(":")[-1].strip() for l in bat.splitlines() if "level:" in l), "")
    stat = next((l.split(":")[-1].strip() for l in bat.splitlines() if "status:" in l), "")
    plug = next((l.split(":")[-1].strip() for l in bat.splitlines() if "plugged:" in l), "")
    screen = _adb("dumpsys power | grep mWakefulness")
    sm = {"2":"Charging","3":"Discharging","4":"Not Charging","5":"Full"}
    pm = {"0":"Battery","1":"AC","2":"USB","4":"Wireless"}
    return dict(connected=True,
                model=_adb("getprop ro.product.model").strip(),
                android=_adb("getprop ro.build.version.release").strip(),
                api=_adb("getprop ro.build.version.sdk").strip(),
                battery_pct=int(lvl) if lvl.isdigit() else 0,
                battery_status=sm.get(stat, stat), battery_plug=pm.get(plug, plug),
                screen="Awake" if "Awake" in screen else "Asleep",
                wifi=_adb("dumpsys wifi | grep 'SSID' | head -1").strip())

def collect_tailscale():
    raw = _sh("tailscale status --json 2>/dev/null", 5)
    try:
        d = json.loads(raw)
        return dict(online=True, self_ip=d.get("TailscaleIPs",["?"])[0],
                    peers=[dict(hostname=v.get("HostName",""), ips=v.get("TailscaleIPs",[]),
                                os=v.get("OS",""), online=v.get("Online",False))
                           for v in d.get("Peer",{}).values()])
    except:
        return dict(online=False, peers=[], raw=_sh("tailscale status 2>/dev/null")[:300])

# ── HTTP API ──────────────────────────────────────────────────────────────────
@app.get("/api/data")
async def api_data():
    sys_d = collect_system(); svc_d = collect_services()
    android = collect_android(); ts = collect_tailscale()
    ol, mac, ch = await asyncio.gather(collect_ollama(), collect_mac(), collect_chroma())
    return JSONResponse(dict(ts=datetime.now().isoformat(),
                             system=sys_d, services=svc_d, ollama=ol,
                             mac=mac, chroma=ch, android=android, tailscale=ts))

class ChatMsg(BaseModel):
    prompt: str; model: str = ""; system: str = ""

@app.post("/api/chat")
async def api_chat(msg: ChatMsg):
    model  = msg.model or HQ_MODEL
    syspmt = msg.system or ("You are N.O.V.A., the AI brain of simon-hq built by "
                            "Simon-X Solutions. Direct, technical, precise. "
                            "Use tools to get live data. Answer concisely.")
    async def stream():
        payload = dict(model=model, prompt=msg.prompt, system=syspmt,
                       stream=True, options=dict(temperature=0.3, num_predict=1024))
        try:
            async with httpx.AsyncClient(timeout=120) as c:
                async with c.stream("POST", f"{OLLAMA_URL}/api/generate",
                                    json=payload) as resp:
                    async for line in resp.aiter_lines():
                        if line:
                            try:
                                chunk = json.loads(line)
                                t = chunk.get("response","")
                                if t: yield t
                                if chunk.get("done"): break
                            except: pass
        except Exception as e:
            yield f"\n[Error: {e}]"
    return StreamingResponse(stream(), media_type="text/plain")

@app.get("/api/files")
async def api_files(path: str = NOVA_HOME):
    try:
        p = Path(path).resolve()
        entries = []
        for item in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            try:
                st = item.stat()
                entries.append(dict(name=item.name, is_dir=item.is_dir(),
                                    size=st.st_size,
                                    modified=datetime.fromtimestamp(st.st_mtime).strftime("%m/%d %H:%M"),
                                    path=str(item)))
            except: pass
        return JSONResponse(dict(path=str(p), entries=entries[:100]))
    except Exception as e:
        return JSONResponse(dict(path=path, entries=[], error=str(e)))

@app.get("/api/file/content")
async def api_file_content(path: str):
    try:
        p = Path(path).resolve()  # resolve() collapses all ../ traversal attempts
        safe = [Path(NOVA_HOME).resolve(), Path("/etc").resolve(),
                Path("/var/log").resolve(), Path("/tmp").resolve()]
        if not any(str(p).startswith(str(s)) for s in safe):
            return JSONResponse(dict(error="Path not allowed"), status_code=403)
        content = p.read_text(errors="replace")
        lines = content.splitlines()
        truncated = len(lines) > 500
        return JSONResponse(dict(content="\n".join(lines[:500]),
                                 lines=len(lines), truncated=truncated,
                                 size=p.stat().st_size))
    except Exception as e:
        return JSONResponse(dict(error=str(e)), status_code=400)

class FileCreate(BaseModel):
    path: str; content: str = ""; is_dir: bool = False

@app.post("/api/file/create")
async def api_file_create(req: FileCreate):
    try:
        p = Path(req.path)
        if not str(p).startswith(NOVA_HOME) and not str(p).startswith("/tmp"):
            return JSONResponse(dict(error="Only allowed under home or /tmp"), status_code=403)
        if req.is_dir:
            p.mkdir(parents=True, exist_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(req.content)
        return JSONResponse(dict(ok=True, path=str(p)))
    except Exception as e:
        return JSONResponse(dict(error=str(e)), status_code=400)

class FileDelete(BaseModel):
    path: str
    totp_token: str = ""

@app.post("/api/file/delete")
async def api_file_delete(req: FileDelete):
    # 2FA gate — file deletion is an admin action
    ok, msg = require_admin_auth(req.totp_token, "hud_delete_file")
    if not ok:
        return JSONResponse(dict(error=f"2FA required: {msg}", require_2fa=True), status_code=401)
    try:
        p = Path(req.path)
        if not str(p).startswith(NOVA_HOME) and not str(p).startswith("/tmp"):
            return JSONResponse(dict(error="Not allowed"), status_code=403)
        import shutil
        if p.is_dir(): shutil.rmtree(p)
        else: p.unlink()
        return JSONResponse(dict(ok=True))
    except Exception as e:
        return JSONResponse(dict(error=str(e)), status_code=400)


class AdminVerify(BaseModel):
    token: str
    action: str = "hud_admin"

@app.post("/api/admin/verify")
async def api_admin_verify(req: AdminVerify, request: Request):
    """Verify a TOTP token for an admin action. Used by HUD modal."""
    client_ip = request.client.host if request.client else "unknown"
    if not _verify_rate_ok(client_ip):
        return JSONResponse(
            dict(ok=False, message=f"Too many verification attempts from {client_ip}. Wait 60s."),
            status_code=429)
    ok, msg = require_admin_auth(req.token, req.action)
    return JSONResponse(dict(ok=ok, message=msg))

@app.get("/api/admin/2fa-status")
async def api_2fa_status():
    """Return 2FA configuration status for the HUD security panel."""
    if not _2FA_AVAILABLE:
        return JSONResponse(dict(configured=False, available=False))
    status = get_2fa_status()
    status["available"] = True
    return JSONResponse(status)

@app.get("/api/file/download")
async def api_file_download(path: str):
    p = Path(path)
    if not p.exists() or not p.is_file():
        return JSONResponse(dict(error="Not found"), status_code=404)
    return FileResponse(str(p), filename=p.name)

@app.post("/api/file/upload")
async def api_file_upload(dest: str = NOVA_HOME, file: UploadFile = File(...)):
    try:
        # Resolve the destination to prevent path traversal via ../
        base = Path(NOVA_HOME).resolve()
        safe_name = Path(file.filename or "upload").name  # strip any path component from filename
        p = (Path(dest) / safe_name).resolve()
        if not str(p).startswith(str(base)):
            return JSONResponse(dict(error="Upload destination outside home directory"),
                                status_code=403)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(await file.read())
        return JSONResponse(dict(ok=True, path=str(p), size=p.stat().st_size))
    except Exception as e:
        return JSONResponse(dict(error=str(e)), status_code=400)

@app.get("/api/adb/connect")
async def api_adb_connect():
    return JSONResponse(dict(result=_sh(f"adb connect {ADB_TARGET}", 8)))

# ── WebSocket PTY Terminal ────────────────────────────────────────────────────
@app.websocket("/ws/terminal")
async def ws_terminal(ws: WebSocket):
    # Auth check BEFORE accepting — reject unauthenticated connections at handshake
    ws_token = ws.query_params.get("token", "")
    if not _token_valid(ws_token):
        await ws.close(code=4401)
        return
    await ws.accept()
    master_fd, slave_fd = pty.openpty()
    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    env = {**os.environ,
           "TERM": "xterm-256color", "HOME": NOVA_HOME,
           "USER": NOVA_USER, "SHELL": "/bin/bash",
           "PS1": r"\[\033[38;5;51m\]nova\[\033[0m\]@\[\033[38;5;99m\]simon-hq\[\033[0m\]:\[\033[38;5;39m\]\w\[\033[0m\]\$ "}
    proc = subprocess.Popen(
        ["/bin/bash", "--login", "-i"],
        stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
        preexec_fn=os.setsid, env=env, close_fds=True
    )
    os.close(slave_fd)
    loop = asyncio.get_event_loop()

    async def pty_to_ws():
        while proc.poll() is None:
            try:
                r, _, _ = await loop.run_in_executor(
                    None, lambda: select.select([master_fd], [], [], 0.04))
                if r:
                    data = os.read(master_fd, 4096)
                    if data:
                        await ws.send_bytes(data)
            except (OSError, RuntimeError):
                break

    async def ws_to_pty():
        while True:
            try:
                msg = await ws.receive()
                if "bytes" in msg:
                    os.write(master_fd, msg["bytes"])
                elif "text" in msg:
                    try:
                        cmd = json.loads(msg["text"])
                        if cmd.get("type") == "resize":
                            fcntl.ioctl(master_fd, termios.TIOCSWINSZ,
                                        struct.pack("HHHH",
                                                    cmd.get("rows", 24),
                                                    cmd.get("cols", 80), 0, 0))
                    except (json.JSONDecodeError, KeyError):
                        os.write(master_fd, msg["text"].encode())
            except (WebSocketDisconnect, RuntimeError):
                break

    try:
        await asyncio.gather(pty_to_ws(), ws_to_pty())
    finally:
        try: proc.terminate()
        except: pass
        try: os.close(master_fd)
        except: pass

# ── HUD HTML ──────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def hud():
    # Inject session token into the page so the JS client can auth all API calls.
    # The main page itself has no auth requirement — it's the API layer that's gated.
    return HUD_HTML.replace("__HUD_TOKEN_PLACEHOLDER__", HUD_TOKEN)

HUD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>N.O.V.A. — Simon-X Solutions</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/xterm/5.3.0/xterm.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/xterm/5.3.0/xterm.min.js"></script>
<script src="https://unpkg.com/@xterm/addon-fit@0.10.0/lib/addon-fit.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css">
<style>
:root{
  --bg:    #020912;
  --bg1:   #050e22;
  --bg2:   #07132a;
  --bg3:   #091830;
  --br:    #0c2244;
  --brh:   #1a4480;
  --brc:   rgba(0,212,255,.15);
  --cy:    #00d4ff;
  --cyd:   #006688;
  --cyg:   #00f5ff;
  --vi:    #9d4eff;
  --vid:   #5520aa;
  --vig:   #bf8fff;
  --gn:    #00ff9d;
  --gnd:   #006640;
  --yw:    #ffd700;
  --pk:    #ff3c78;
  --tx:    #b8d4f0;
  --td:    #3a6080;
  --tm:    #152030;
  --mono:  'Courier New',monospace;
  --sans:  'Segoe UI',system-ui,sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:var(--bg);color:var(--tx);font-family:var(--mono);font-size:13px;overflow:hidden}

/* scanlines */
body::after{content:'';position:fixed;inset:0;background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,212,255,.012) 2px,rgba(0,212,255,.012) 4px);pointer-events:none;z-index:9999}

/* layout */
.shell{display:flex;flex-direction:column;height:100vh}
header{display:flex;align-items:center;justify-content:space-between;padding:8px 18px;border-bottom:1px solid var(--brh);background:linear-gradient(90deg,#030d20,#020912 60%,#030d20);flex-shrink:0;position:relative}
header::after{content:'';position:absolute;bottom:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,var(--cy),transparent)}
.logo{display:flex;align-items:center;gap:12px}
.logo-ring{width:34px;height:34px;border:2px solid var(--cy);border-radius:50%;display:flex;align-items:center;justify-content:center;box-shadow:0 0 16px var(--cy),inset 0 0 8px rgba(0,212,255,.1);animation:rpulse 4s ease-in-out infinite}
@keyframes rpulse{0%,100%{box-shadow:0 0 14px var(--cy),inset 0 0 8px rgba(0,212,255,.1)}50%{box-shadow:0 0 28px var(--cyg),0 0 50px rgba(0,212,255,.2),inset 0 0 12px rgba(0,212,255,.15)}}
.logo-ring svg{width:16px;height:16px;fill:var(--cy)}
.logo-text h1{font-size:16px;font-weight:bold;letter-spacing:6px;color:var(--cy);text-shadow:0 0 20px var(--cy)}
.logo-text p{font-size:9px;color:var(--cyd);letter-spacing:3px;margin-top:1px}
.hdr-r{display:flex;align-items:center;gap:18px}
.status-pill{display:flex;align-items:center;gap:6px;padding:4px 10px;border:1px solid var(--brh);border-radius:20px;background:rgba(0,212,255,.04)}
.sdot{width:7px;height:7px;border-radius:50%;background:var(--gn);box-shadow:0 0 6px var(--gn);animation:blink 2s step-start infinite}
.sdot.warn{background:var(--yw);box-shadow:0 0 6px var(--yw);animation:none}
.sdot.err{background:var(--pk);box-shadow:0 0 6px var(--pk)}
@keyframes blink{50%{opacity:.2}}
.status-lbl{font-size:9px;letter-spacing:2px;color:var(--td)}
.clock{font-size:20px;font-weight:bold;color:var(--cy);letter-spacing:2px;text-shadow:0 0 12px rgba(0,212,255,.5);font-variant-numeric:tabular-nums}
.spin{width:12px;height:12px;border:2px solid var(--br);border-top-color:var(--cy);border-radius:50%;animation:sp 1s linear infinite;opacity:0;transition:opacity .2s}
.spin.on{opacity:1}
@keyframes sp{to{transform:rotate(360deg)}}

/* tabs */
.tabs{display:flex;border-bottom:1px solid var(--br);background:var(--bg1);flex-shrink:0;position:relative}
.tabs::after{content:'';position:absolute;bottom:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,var(--vi),transparent);opacity:.3}
.tab{padding:10px 20px;font-family:var(--mono);font-size:10px;letter-spacing:3px;color:var(--td);cursor:pointer;border-bottom:2px solid transparent;transition:all .2s;text-transform:uppercase;white-space:nowrap;user-select:none}
.tab:hover{color:var(--cy);background:rgba(0,212,255,.03)}
.tab.active{color:var(--cy);border-bottom-color:var(--cy);background:rgba(0,212,255,.05);text-shadow:0 0 10px rgba(0,212,255,.4)}

/* panels */
.panels{flex:1;overflow:hidden;position:relative}
.panel{position:absolute;inset:0;display:none;flex-direction:column;overflow:hidden}
.panel.active{display:flex}

/* ─── DASHBOARD ─── */
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;padding:12px;overflow-y:auto;height:100%}
@media(max-width:1100px){.grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:640px){.grid{grid-template-columns:1fr}}
.s2{grid-column:span 2}.s4{grid-column:span 4}

.card{background:var(--bg1);border:1px solid var(--br);border-radius:6px;overflow:hidden;transition:border-color .3s,box-shadow .3s}
.card:hover{border-color:var(--brh);box-shadow:0 0 20px rgba(0,212,255,.05)}
.ch{display:flex;align-items:center;justify-content:space-between;padding:8px 13px;background:linear-gradient(90deg,rgba(0,212,255,.06),transparent);border-bottom:1px solid var(--br)}
.ct{font-size:10px;letter-spacing:3px;color:var(--cy);text-transform:uppercase;font-weight:bold}
.bpill{font-size:9px;padding:2px 8px;border-radius:10px;letter-spacing:1px;font-weight:bold}
.bpill.ok{background:rgba(0,255,157,.1);color:var(--gn);border:1px solid var(--gnd)}
.bpill.off{background:rgba(255,60,120,.08);color:var(--pk);border:1px solid rgba(255,60,120,.3)}
.bpill.unk{background:rgba(255,215,0,.08);color:var(--yw);border:1px solid rgba(255,215,0,.3)}
.bpill.def{background:rgba(0,212,255,.07);color:var(--cyd);border:1px solid var(--br)}
.body{padding:11px 13px}

/* gauges */
.gr{display:flex;align-items:center;gap:9px;margin-bottom:8px}
.gl{width:40px;font-size:10px;color:var(--td);letter-spacing:1px}
.gbar{flex:1;height:8px;background:var(--bg3);border:1px solid var(--br);border-radius:4px;overflow:hidden}
.gf{height:100%;border-radius:4px;transition:width 1s cubic-bezier(.4,0,.2,1)}
.gf.lo{background:linear-gradient(90deg,var(--cyd),var(--cy));box-shadow:0 0 6px rgba(0,212,255,.4)}
.gf.md{background:linear-gradient(90deg,#806600,var(--yw));box-shadow:0 0 6px rgba(255,215,0,.4)}
.gf.hi{background:linear-gradient(90deg,#880020,var(--pk));box-shadow:0 0 6px rgba(255,60,120,.5);animation:cp .6s ease infinite}
@keyframes cp{50%{opacity:.6}}
.gv{width:36px;text-align:right;font-size:11px;font-weight:bold}

.sg{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-top:10px}
.sb{background:var(--bg2);border:1px solid var(--br);border-radius:4px;padding:9px;text-align:center}
.sv{font-size:21px;font-weight:bold;color:var(--cy);text-shadow:0 0 12px rgba(0,212,255,.4)}
.sl{font-size:9px;color:var(--td);letter-spacing:2px;margin-top:3px}

.srow{display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--br);font-size:11px}
.srow:last-child{border-bottom:none}
.sn{flex:1;letter-spacing:1px}
.stag{font-size:9px;padding:2px 7px;border-radius:10px;letter-spacing:1px;font-weight:bold}
.stag.active{background:rgba(0,255,157,.1);color:var(--gn);border:1px solid var(--gnd)}
.stag.inactive,.stag.failed{background:rgba(255,60,120,.08);color:var(--pk);border:1px solid rgba(255,60,120,.3)}
.stag.checking,.stag.activating{background:rgba(255,215,0,.08);color:var(--yw);border:1px solid rgba(255,215,0,.3)}

.pr{display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--br);font-size:11px}
.pr:last-child{border-bottom:none}
.pdot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.pdot.on{background:var(--gn);box-shadow:0 0 5px var(--gn)}
.pdot.off{background:var(--tm)}
.pip{color:var(--vi);width:115px;font-size:10px;font-weight:bold}
.pn{flex:1}.pos{color:var(--td);font-size:10px}

.ag{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-bottom:9px}
.ab{background:var(--bg2);border:1px solid var(--br);border-radius:4px;padding:8px;text-align:center}
.av{font-size:17px;font-weight:bold;color:var(--cy)}
.al{font-size:9px;color:var(--td);letter-spacing:1px;margin-top:2px}
.bbar{width:100%;height:12px;background:var(--bg3);border:1px solid var(--br);border-radius:6px;overflow:hidden;margin-top:6px;position:relative}
.bfill{height:100%;transition:width 1s;border-radius:6px}
.bfill.hi{background:linear-gradient(90deg,var(--gnd),var(--gn))}
.bfill.md{background:linear-gradient(90deg,#806600,var(--yw))}
.bfill.lo{background:linear-gradient(90deg,#880020,var(--pk));animation:cp .8s ease infinite}
.blbl{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:9px;color:#fff;font-weight:bold;text-shadow:0 1px 3px #000}
.ainfo{font-size:10px;color:var(--td);margin-top:8px;line-height:1.9}
.ainfo span{color:var(--tx)}

.mrow{display:flex;align-items:center;justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--br);font-size:11px}
.mrow:last-child{border-bottom:none}
.mtag{font-size:9px;padding:2px 6px;border-radius:10px;background:rgba(0,255,157,.1);color:var(--gn);border:1px solid var(--gnd)}
.mkv{color:var(--td);font-size:10px}

.feed{height:100px;overflow-y:auto;font-size:10px;line-height:1.7;color:var(--td)}
.feed::-webkit-scrollbar{width:3px}
.feed::-webkit-scrollbar-thumb{background:var(--brh)}
.le{padding:1px 0;border-bottom:1px solid var(--bg2)}
.le .ts{color:var(--tm);margin-right:6px}
.ok{color:var(--gn)}.er{color:var(--pk)}.in{color:var(--cy)}.wn{color:var(--yw)}

.acts{display:flex;gap:7px;flex-wrap:wrap}
.btn{padding:6px 14px;border:1px solid var(--brh);background:rgba(0,212,255,.05);color:var(--cy);font-family:var(--mono);font-size:10px;letter-spacing:2px;cursor:pointer;border-radius:4px;text-transform:uppercase;transition:all .2s}
.btn:hover{background:rgba(0,212,255,.12);border-color:var(--cy);box-shadow:0 0 12px rgba(0,212,255,.2)}
.btn.vi{border-color:var(--vid);color:var(--vi);background:rgba(157,78,255,.05)}
.btn.vi:hover{background:rgba(157,78,255,.12);border-color:var(--vi);box-shadow:0 0 12px rgba(157,78,255,.2)}

/* ─── CHAT ─── */
.chat-wrap{display:flex;flex-direction:column;flex:1;overflow:hidden}
.chat-bar{display:flex;align-items:center;gap:12px;padding:8px 16px;border-bottom:1px solid var(--br);background:var(--bg1);flex-shrink:0}
.chat-bar label{font-size:10px;color:var(--td);letter-spacing:2px}
.msel{background:var(--bg2);border:1px solid var(--brh);color:var(--tx);font-family:var(--mono);font-size:11px;padding:5px 9px;border-radius:4px;outline:none}
.msel:focus{border-color:var(--cy)}
.chat-msgs{flex:1;overflow-y:auto;padding:14px 18px;display:flex;flex-direction:column;gap:12px}
.chat-msgs::-webkit-scrollbar{width:4px}
.chat-msgs::-webkit-scrollbar-thumb{background:var(--brh)}
.msg{max-width:78%;padding:10px 14px;border-radius:6px;font-size:12px;line-height:1.65;white-space:pre-wrap;word-break:break-word}
.msg.user{background:rgba(157,78,255,.1);border:1px solid var(--vid);align-self:flex-end;color:var(--tx)}
.msg.nova{background:var(--bg2);border:1px solid var(--brh);align-self:flex-start;color:var(--tx)}
.nova-lbl{font-size:9px;color:var(--cy);letter-spacing:3px;margin-bottom:6px;font-weight:bold}
.typing{display:flex;gap:4px;align-items:center;padding:4px 0}
.typing span{width:6px;height:6px;background:var(--cy);border-radius:50%;animation:td 1.4s ease-in-out infinite}
.typing span:nth-child(2){animation-delay:.2s}
.typing span:nth-child(3){animation-delay:.4s}
@keyframes td{0%,60%,100%{transform:translateY(0)}30%{transform:translateY(-7px)}}
.chat-input-row{display:flex;gap:9px;padding:10px 16px;border-top:1px solid var(--brh);background:var(--bg1);flex-shrink:0}
.chat-inp{flex:1;background:var(--bg2);border:1px solid var(--brh);color:var(--tx);font-family:var(--mono);font-size:12px;padding:10px 13px;border-radius:4px;resize:none;outline:none;height:42px;max-height:140px;transition:border-color .2s}
.chat-inp:focus{border-color:var(--cy);box-shadow:0 0 10px rgba(0,212,255,.1)}
.send-btn{padding:10px 20px;background:rgba(0,212,255,.1);border:1px solid var(--cy);color:var(--cy);font-family:var(--mono);font-size:11px;letter-spacing:2px;cursor:pointer;border-radius:4px;transition:all .2s;align-self:flex-end;white-space:nowrap}
.send-btn:hover{background:rgba(0,212,255,.2);box-shadow:0 0 14px rgba(0,212,255,.3)}

/* ─── TERMINAL ─── */
.term-wrap{display:flex;flex-direction:column;flex:1;overflow:hidden;background:#010608}
.term-bar{display:flex;align-items:center;gap:10px;padding:7px 14px;border-bottom:1px solid var(--br);background:var(--bg1);flex-shrink:0}
.term-cwd{font-size:10px;color:var(--vi);letter-spacing:1px;flex:1;font-family:var(--mono)}
.xterm-container{flex:1;padding:8px;overflow:hidden;position:relative}
.xterm-container .xterm{height:100%}
.term-status{font-size:9px;color:var(--td);letter-spacing:1px;padding:4px 14px;border-top:1px solid var(--br);background:var(--bg1);flex-shrink:0}

/* ─── FILES ─── */
.files-wrap{display:flex;flex:1;overflow:hidden}
.file-tree{width:260px;flex-shrink:0;border-right:1px solid var(--br);display:flex;flex-direction:column;background:var(--bg1)}
.tree-bar{display:flex;align-items:center;gap:7px;padding:8px 12px;border-bottom:1px solid var(--br);flex-shrink:0}
.tree-path{font-size:10px;color:var(--cy);letter-spacing:1px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:var(--mono)}
.tree-list{flex:1;overflow-y:auto}
.tree-list::-webkit-scrollbar{width:3px}
.tree-list::-webkit-scrollbar-thumb{background:var(--brh)}
.tree-item{display:flex;align-items:center;gap:8px;padding:6px 12px;font-size:11px;cursor:pointer;transition:background .15s;border-bottom:1px solid rgba(0,212,255,.03);letter-spacing:.5px}
.tree-item:hover{background:rgba(0,212,255,.06)}
.tree-item.selected{background:rgba(0,212,255,.1);border-left:2px solid var(--cy)}
.tree-item .icon{width:14px;text-align:center;font-size:12px;flex-shrink:0}
.tree-item .name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tree-item .name.dir{color:var(--cy)}
.tree-item .meta{font-size:9px;color:var(--tm)}
.file-preview{flex:1;display:flex;flex-direction:column;overflow:hidden}
.preview-bar{display:flex;align-items:center;gap:9px;padding:8px 14px;border-bottom:1px solid var(--br);background:var(--bg1);flex-shrink:0}
.preview-fname{font-size:11px;color:var(--cy);flex:1;font-family:var(--mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.preview-body{flex:1;overflow:auto;padding:14px;font-family:var(--mono);font-size:12px;line-height:1.65;color:var(--tx);white-space:pre-wrap;word-break:break-word}
.preview-body::-webkit-scrollbar{width:5px}
.preview-body::-webkit-scrollbar-thumb{background:var(--brh)}
.preview-empty{display:flex;align-items:center;justify-content:center;height:100%;color:var(--td);font-size:12px;letter-spacing:2px;flex-direction:column;gap:12px}
.preview-empty .icon{font-size:40px;opacity:.3}

/* context menu */
.ctxmenu{position:fixed;background:var(--bg2);border:1px solid var(--brh);border-radius:4px;padding:4px 0;z-index:1000;min-width:140px;box-shadow:0 8px 24px rgba(0,0,0,.4)}
.ctxmenu div{padding:7px 14px;font-size:11px;cursor:pointer;letter-spacing:1px;transition:background .15s}
.ctxmenu div:hover{background:rgba(0,212,255,.1);color:var(--cy)}
.ctxmenu div.danger:hover{background:rgba(255,60,120,.1);color:var(--pk)}
.ctxmenu .sep{height:1px;background:var(--br);margin:3px 0;padding:0}

/* modal */
.modal-bg{position:fixed;inset:0;background:rgba(2,9,18,.85);display:none;align-items:center;justify-content:center;z-index:2000;backdrop-filter:blur(4px)}
.modal-bg.open{display:flex}
.modal{background:var(--bg2);border:1px solid var(--brh);border-radius:6px;padding:24px;min-width:320px;box-shadow:0 0 40px rgba(0,212,255,.1)}
.modal h3{font-size:13px;letter-spacing:3px;color:var(--cy);margin-bottom:16px}
.modal input,.modal textarea{width:100%;background:var(--bg1);border:1px solid var(--brh);color:var(--tx);font-family:var(--mono);font-size:12px;padding:8px 11px;border-radius:4px;outline:none;margin-bottom:12px}
.modal input:focus,.modal textarea:focus{border-color:var(--cy)}
.modal-btns{display:flex;justify-content:flex-end;gap:8px}

.redline{height:1px;background:linear-gradient(90deg,transparent,var(--cy),transparent);flex-shrink:0;opacity:.4}
footer{display:flex;justify-content:space-between;padding:5px 18px;font-size:9px;color:var(--tm);letter-spacing:2px;background:var(--bg1);border-top:1px solid var(--br);flex-shrink:0}
</style>
</head>
<body>
<div class="shell">

<header>
  <div class="logo">
    <div class="logo-ring">
      <svg viewBox="0 0 24 24"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>
    </div>
    <div class="logo-text"><h1>N.O.V.A.</h1><p>NETWORK OPERATIONS &amp; VOICE ASSISTANT — SIMON-HQ</p></div>
  </div>
  <div class="hdr-r">
    <div class="status-pill"><div class="sdot" id="d-main"></div><span class="status-lbl" id="lbl-main">CONNECTING</span></div>
    <div class="spin" id="spinner"></div>
    <div class="clock" id="clock">--:--:--</div>
  </div>
</header>

<div class="tabs">
  <div class="tab active" onclick="showTab('dash',this)">◈ DASHBOARD</div>
  <div class="tab" onclick="showTab('chat',this)">◉ NOVA CHAT</div>
  <div class="tab" onclick="showTab('term',this)" id="tab-term">⌨ TERMINAL</div>
  <div class="tab" onclick="showTab('files',this)" id="tab-files">⬡ FILES</div>
</div>

<div class="panels">

<!-- ════════ DASHBOARD ════════ -->
<div class="panel active" id="panel-dash">
<div class="grid">
  <div class="card s2">
    <div class="ch"><span class="ct">◈ System</span><span class="bpill def" id="b-up">UP --</span></div>
    <div class="body">
      <div class="gr"><span class="gl">CPU</span><div class="gbar"><div class="gf lo" id="g-cpu" style="width:0"></div></div><span class="gv" id="v-cpu">--%</span></div>
      <div class="gr"><span class="gl">RAM</span><div class="gbar"><div class="gf lo" id="g-ram" style="width:0"></div></div><span class="gv" id="v-ram">--%</span></div>
      <div class="gr"><span class="gl">DISK</span><div class="gbar"><div class="gf lo" id="g-disk" style="width:0"></div></div><span class="gv" id="v-disk">--%</span></div>
      <div class="gr"><span class="gl">LOAD</span><div class="gbar"><div class="gf lo" id="g-load" style="width:0"></div></div><span class="gv" id="v-load">-</span></div>
      <div class="sg">
        <div class="sb"><div class="sv" id="sv-ru">--</div><div class="sl">GB RAM USED</div></div>
        <div class="sb"><div class="sv" id="sv-rt">--</div><div class="sl">GB RAM TOTAL</div></div>
        <div class="sb"><div class="sv" id="sv-du">--</div><div class="sl">GB DISK USED</div></div>
        <div class="sb"><div class="sv" id="sv-dt">--</div><div class="sl">GB DISK TOTAL</div></div>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="ch"><span class="ct">◉ Services</span><span class="bpill def" id="b-svc">-- / 8</span></div>
    <div class="body" id="svc-list"></div>
  </div>

  <div class="card">
    <div class="ch"><span class="ct">⬡ Tailscale</span><span class="bpill ok" id="b-ts">VPN</span></div>
    <div class="body" id="ts-list"></div>
  </div>

  <div class="card s2">
    <div class="ch"><span class="ct">◈ Pixel 9a</span><span class="bpill def" id="b-adb">ADB --</span></div>
    <div class="body" id="android-body"><div style="color:var(--td);font-size:10px">connecting...</div></div>
  </div>

  <div class="card">
    <div class="ch"><span class="ct">◉ Mac SIMON</span><span class="bpill def" id="b-mac">--</span></div>
    <div class="body" id="mac-body"></div>
  </div>

  <div class="card">
    <div class="ch"><span class="ct">⬡ ChromaDB Memory</span><span class="bpill def" id="b-ch">--</span></div>
    <div class="body">
      <div class="sg">
        <div class="sb"><div class="sv" id="sv-cols">--</div><div class="sl">COLLECTIONS</div></div>
        <div class="sb"><div class="sv" id="sv-docs">--</div><div class="sl">TOTAL DOCS</div></div>
      </div>
      <div id="ch-names" style="margin-top:8px;font-size:10px;color:var(--td)"></div>
    </div>
  </div>

  <div class="card s2">
    <div class="ch"><span class="ct">◈ Ollama Models</span><span class="bpill def" id="b-ol">--</span></div>
    <div class="body" id="models-list"></div>
  </div>

  <div class="card s4">
    <div class="ch"><span class="ct">⬡ Event Feed</span>
      <div style="display:flex;gap:7px;align-items:center">
        <span class="bpill def" id="b-feed">0 events</span>
        <button class="btn" onclick="clearFeed()">CLEAR</button>
        <button class="btn" onclick="refresh()">↺ REFRESH</button>
      </div>
    </div>
    <div class="body"><div class="feed" id="feed"></div></div>
  </div>

  <div class="card s4">
    <div class="ch"><span class="ct">⚙ Quick Actions</span></div>
    <div class="body">
      <div class="acts">
        <button class="btn" onclick="switchToTab('chat')">NOVA CHAT</button>
        <button class="btn" onclick="switchToTab('term')">TERMINAL</button>
        <button class="btn" onclick="switchToTab('files')">FILES</button>
        <button class="btn vi" onclick="window.open('http://YOUR_HQ_TAILSCALE_IP:3000','_blank')">OPEN WEBUI ↗</button>
<button class="btn" onclick="doAdbReconnect()">RECONNECT ADB</button>
        <button class="btn" onclick="refresh()">REFRESH ALL</button>
      </div>
    </div>
  </div>
</div>
</div>

<!-- ════════ CHAT ════════ -->
<div class="panel" id="panel-chat">
<div class="chat-wrap">
  <div class="chat-bar">
    <label>MODEL</label>
    <select class="msel" id="chat-model">
      <option value="qwen2.5:7b">qwen2.5:7b — balanced</option>
      <option value="llama3.2:3b">llama3.2:3b — fast</option>
      <option value="mistral:latest">mistral — general</option>
      <option value="phi3:mini">phi3:mini — compact</option>
      <option value="llama3.2-vision:11b">llama3.2-vision — vision</option>
    </select>
    <button class="btn" onclick="clearChat()">CLEAR</button>
    <span style="font-size:9px;color:var(--td);letter-spacing:2px;margin-left:auto">LOCAL LLM · SIMON-HQ</span>
  </div>
  <div class="chat-msgs" id="chat-msgs">
    <div class="msg nova"><div class="nova-lbl">N.O.V.A.</div>Systems online. I am NOVA — the AI intelligence of simon-hq, built by Simon-X Solutions. I have live access to this system's tools, Android bridge, Mac SIMON, ChromaDB memory, and Ollama models. What do you need?</div>
  </div>
  <div class="chat-input-row">
    <textarea class="chat-inp" id="chat-inp" placeholder="Ask NOVA... (Enter to send · Shift+Enter for newline)" rows="1"></textarea>
    <button class="send-btn" id="send-btn" onclick="sendChat()">SEND ▶</button>
  </div>
</div>
</div>

<!-- ════════ TERMINAL ════════ -->
<div class="panel" id="panel-term">
<div class="term-wrap">
  <div class="term-bar">
    <span style="font-size:10px;color:var(--cy);letter-spacing:1px">◈ NOVA TERMINAL</span>
    <span class="term-cwd" id="term-cwd">simon-hq — Full PTY bash shell</span>
    <button class="btn" onclick="termReconnect()">↺ RECONNECT</button>
    <button class="btn" onclick="termClear()">CLEAR</button>
    <span style="font-size:9px;color:var(--td);letter-spacing:1px" id="term-status">CONNECTING...</span>
  </div>
  <div class="xterm-container" id="xterm-container"></div>
  <div class="term-status">Full PTY · xterm-256color · vim · nano · htop · adb · ollama · libreoffice · all Linux tools</div>
</div>
</div>

<!-- ════════ FILES ════════ -->
<div class="panel" id="panel-files">
<div class="files-wrap">
  <div class="file-tree">
    <div class="tree-bar">
      <span class="tree-path" id="tree-path">/home/simon-hq</span>
      <button class="btn" style="padding:3px 8px;font-size:9px" onclick="treeNav('/home/simon-hq')">⌂</button>
      <button class="btn" style="padding:3px 8px;font-size:9px" onclick="treeUp()">↑</button>
    </div>
    <div class="tree-list" id="tree-list"></div>
    <div style="padding:8px 12px;border-top:1px solid var(--br);display:flex;gap:6px;flex-shrink:0">
      <button class="btn" style="flex:1;padding:5px;font-size:9px" onclick="openNewFile()">+ FILE</button>
      <button class="btn" style="flex:1;padding:5px;font-size:9px" onclick="openNewDir()">+ DIR</button>
    </div>
  </div>
  <div class="file-preview">
    <div class="preview-bar" id="preview-bar" style="display:none">
      <span class="preview-fname" id="preview-fname">--</span>
      <button class="btn" style="padding:4px 9px;font-size:9px" onclick="editCurrentFile()">EDIT</button>
      <button class="btn" style="padding:4px 9px;font-size:9px" onclick="downloadCurrentFile()">↓ DL</button>
      <button class="btn vi" style="padding:4px 9px;font-size:9px" onclick="deleteCurrentFile()">DELETE</button>
    </div>
    <div id="preview-body-wrap" style="flex:1;overflow:hidden;display:flex;flex-direction:column">
      <div class="preview-empty" id="preview-empty">
        <span class="icon">⬡</span>
        <span style="letter-spacing:3px;font-size:10px">SELECT A FILE TO PREVIEW</span>
      </div>
      <pre class="preview-body" id="preview-body" style="display:none"></pre>
    </div>
  </div>
</div>
</div>

</div><!-- /panels -->

<div class="redline"></div>
<footer>
  <span>N.O.V.A. HUD v4.0 · Simon-X Solutions</span>
  <span id="ft-host">simon-hq · YOUR_HQ_TAILSCALE_IP · UFW + TAILSCALE</span>
  <span id="ft-ts">--</span>
</footer>
</div><!-- /shell -->

<!-- Context menu -->
<div class="ctxmenu" id="ctxmenu" style="display:none">
  <div onclick="ctxOpen()">Open / Preview</div>
  <div onclick="ctxEdit()">Edit in Terminal</div>
  <div onclick="ctxDownload()">Download</div>
  <div class="sep"></div>
  <div onclick="openRenameFile()">Rename</div>
  <div class="danger" onclick="ctxDelete()">Delete</div>
</div>

<!-- Modal -->
<div class="modal-bg" id="modal">
  <div class="modal">
    <h3 id="modal-title">NEW FILE</h3>
    <input id="modal-input" type="text" placeholder="filename">
    <textarea id="modal-content" style="height:80px;resize:none;display:none" placeholder="content (optional)"></textarea>
    <div class="modal-btns">
      <button class="btn" onclick="closeModal()">CANCEL</button>
      <button class="btn vi" id="modal-ok" onclick="modalOk()">CREATE</button>
    </div>
  </div>
</div>

<script>
// ── HUD Auth — token injected at render time ──────────────────────────────
const HUD_TOKEN = '__HUD_TOKEN_PLACEHOLDER__';

/**
 * Authenticated fetch wrapper — automatically adds Authorization header
 * to every /api/* call so the server middleware accepts it.
 */
async function _apiFetch(url, opts = {}) {
  opts.headers = Object.assign({ 'Authorization': 'Bearer ' + HUD_TOKEN },
                                opts.headers || {});
  return fetch(url, opts);
}

const REFRESH = 10000;
let feedN = 0, chatBusy = false;
let termWS = null, xterm = null, fitAddon = null;
let treeCwd = '/home/simon-hq', ctxTarget = null, currentFile = null;
let modalAction = null;

// ── Clock ──────────────────────────────────────────────────────────────────
setInterval(() => document.getElementById('clock').textContent =
  new Date().toTimeString().slice(0,8), 1000);

// ── Tabs ──────────────────────────────────────────────────────────────────
function showTab(name, el) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('panel-'+name).classList.add('active');
  if (name === 'term' && !xterm) initTerminal();
  else if (name === 'term' && fitAddon) setTimeout(() => fitAddon.fit(), 50);
  if (name === 'files') treeNav(treeCwd);
}
function switchToTab(name) {
  const tabs = document.querySelectorAll('.tab');
  const map = {dash:0, chat:1, term:2, files:3};
  showTab(name, tabs[map[name]]);
}

// ── Feed ──────────────────────────────────────────────────────────────────
function log(msg, t='in') {
  const f = document.getElementById('feed');
  const d = document.createElement('div'); d.className = 'le';
  d.innerHTML = `<span class="ts">[${new Date().toTimeString().slice(0,8)}]</span><span class="${t}">${msg}</span>`;
  f.prepend(d);
  if (f.children.length > 400) f.lastChild.remove();
  document.getElementById('b-feed').textContent = ++feedN + ' events';
}
function clearFeed() { document.getElementById('feed').innerHTML = ''; feedN = 0; }

// ── Badge / gauge helpers ──────────────────────────────────────────────────
function badge(id, text, cls='def') {
  const el = document.getElementById(id); if (!el) return;
  el.textContent = text; el.className = 'bpill ' + cls;
}
function gauge(gid, vid, pct, label) {
  const g = document.getElementById(gid); if (!g) return;
  g.style.width = Math.min(pct,100)+'%';
  g.className = 'gf ' + (pct>90?'hi':pct>70?'md':'lo');
  const v = document.getElementById(vid);
  if (v) v.textContent = label || Math.round(pct)+'%';
}

// ── Data refresh ──────────────────────────────────────────────────────────
async function refresh() {
  document.getElementById('spinner').classList.add('on');
  try {
    const r = await _apiFetch('/api/data');
    const d = await r.json();
    renderSystem(d.system); renderServices(d.services);
    renderTailscale(d.tailscale); renderAndroid(d.android);
    renderMac(d.mac); renderChroma(d.chroma); renderOllama(d.ollama);
    document.getElementById('d-main').className = 'sdot';
    document.getElementById('lbl-main').textContent = 'ONLINE';
    document.getElementById('ft-ts').textContent = 'SYNC: '+new Date().toTimeString().slice(0,8);
  } catch(e) {
    log('Data fetch failed: '+e.message,'er');
    document.getElementById('d-main').className = 'sdot err';
    document.getElementById('lbl-main').textContent = 'ERROR';
  }
  document.getElementById('spinner').classList.remove('on');
}

function renderSystem(s) {
  if (!s) return;
  gauge('g-cpu','v-cpu', s.cpu_pct);
  gauge('g-ram','v-ram', s.ram_pct);
  gauge('g-disk','v-disk', s.disk_pct);
  gauge('g-load','v-load', Math.min(s.load_1m/8*100,100), s.load_1m.toString());
  document.getElementById('sv-ru').textContent = s.ram_used_gb;
  document.getElementById('sv-rt').textContent = s.ram_total_gb;
  document.getElementById('sv-du').textContent = s.disk_used_gb;
  document.getElementById('sv-dt').textContent = s.disk_total_gb;
  document.getElementById('b-up').textContent = 'UP '+s.uptime;
  log(`System · CPU ${s.cpu_pct}% · RAM ${s.ram_pct}% · Load ${s.load_1m}`,'in');
}

function renderServices(sv) {
  if (!sv) return;
  const keys = Object.keys(sv);
  const active = keys.filter(k => sv[k].active==='active').length;
  badge('b-svc', active+'/'+keys.length, active===keys.length?'ok':'unk');
  document.getElementById('svc-list').innerHTML = keys.map(k => {
    const st = sv[k].active, cls = st==='active'?'active':st==='inactive'?'inactive':'checking';
    return `<div class="srow"><span class="sn">${k}</span><span class="stag ${cls}">${st.toUpperCase()}</span></div>`;
  }).join('');
}

function renderTailscale(ts) {
  if (!ts) return;
  const peers = ts.peers||[], on = peers.filter(p=>p.online).length;
  badge('b-ts', on+' peers online', ts.online?'ok':'off');
  let h = ts.self_ip ? `<div class="pr"><div class="pdot on"></div><span class="pip">${ts.self_ip}</span><span class="pn">simon-hq <span style="color:var(--cy)">[HQ]</span></span><span class="pos">linux</span></div>` : '';
  peers.forEach(p => {
    h += `<div class="pr"><div class="pdot ${p.online?'on':'off'}"></div><span class="pip">${p.ips[0]||''}</span><span class="pn">${p.hostname}</span><span class="pos">${p.os}</span></div>`;
  });
  document.getElementById('ts-list').innerHTML = h || '<div style="color:var(--td);font-size:10px">no peers</div>';
}

function renderAndroid(a) {
  const b = document.getElementById('android-body');
  if (!a||!a.connected) {
    badge('b-adb','ADB OFFLINE','off');
    b.innerHTML = `<div style="color:var(--pk);font-size:11px">⚠ ${a?.error||'Not reachable'}</div><div style="margin-top:8px"><button class="btn" onclick="doAdbReconnect()">↺ RECONNECT</button></div>`;
    log('Android offline: '+(a?.error||''),'er'); return;
  }
  badge('b-adb','CONNECTED','ok');
  const pct=a.battery_pct||0, bc=pct>50?'hi':pct>20?'md':'lo';
  b.innerHTML=`<div class="ag">
    <div class="ab"><div class="av">${pct}%</div><div class="al">BATTERY</div></div>
    <div class="ab"><div class="av">${a.battery_status||'--'}</div><div class="al">STATUS</div></div>
    <div class="ab"><div class="av">${a.android||'--'}</div><div class="al">ANDROID</div></div>
  </div>
  <div class="bbar"><div class="bfill ${bc}" style="width:${pct}%"></div><div class="blbl">${a.battery_plug||''} · ${pct}%</div></div>
  <div class="ainfo">MODEL: <span>${a.model||'--'}</span> · API: <span>${a.api||'--'}</span> · SCREEN: <span>${a.screen||'--'}</span></div>`;
  log(`Android · ${a.model} · bat ${pct}% (${a.battery_status})`,'ok');
}

function renderMac(m) {
  const b = document.getElementById('mac-body');
  if (!m||!m.online) {
    badge('b-mac','OFFLINE','off');
    b.innerHTML='<div style="color:var(--pk);font-size:11px">⚠ SIMON offline</div>'; return;
  }
  badge('b-mac','SIMON ONLINE','ok');
  b.innerHTML=`<div class="mrow"><span class="mkv">CPU</span><span>${m.cpu||'--'}%</span></div>
  <div class="mrow"><span class="mkv">RAM</span><span>${m.mem_gb||'--'} GB</span></div>
  <div class="mrow"><span class="mkv">IP</span><span>${m.ip||'--'}</span></div>
  <div class="mrow"><span class="mkv">TIME</span><span>${m.time||'--'}</span></div>`;
  log('Mac SIMON online · CPU '+m.cpu+'%','ok');
}

function renderChroma(c) {
  badge('b-ch', c?.online?c.total_docs+' docs':'OFFLINE', c?.online?'ok':'off');
  document.getElementById('sv-cols').textContent = c?.online?c.collections:'!';
  document.getElementById('sv-docs').textContent = c?.online?c.total_docs:'!';
  if (c?.names) document.getElementById('ch-names').innerHTML =
    c.names.map(n=>`<div style="padding:2px 0;border-bottom:1px solid var(--br);color:var(--vi)">◉ ${n}</div>`).join('');
}

function renderOllama(o) {
  badge('b-ol', o?.online?o.count+' models':'OFFLINE', o?.online?'ok':'off');
  document.getElementById('models-list').innerHTML = o?.online
    ? (o.models||[]).map(m=>`<div class="mrow"><span>${m.name.split(':')[0]}</span><span style="color:var(--td);font-size:10px">${m.name.split(':')[1]||'latest'}</span><span style="color:var(--td);font-size:10px">${m.size_gb}GB</span><span class="mtag">READY</span></div>`).join('')
    : '<div style="color:var(--pk);font-size:10px">Ollama unreachable</div>';
}

async function doAdbReconnect() {
  log('Reconnecting ADB...','wn');
  const r = await _apiFetch('/api/adb/connect');
  const d = await r.json();
  log('ADB: '+d.result, d.result?.includes('connected')?'ok':'er');
  setTimeout(refresh, 1500);
}

// ── CHAT ──────────────────────────────────────────────────────────────────
function clearChat() {
  document.getElementById('chat-msgs').innerHTML =
    '<div class="msg nova"><div class="nova-lbl">N.O.V.A.</div>Chat cleared. Ready.</div>';
}

async function sendChat() {
  if (chatBusy) return;
  const inp = document.getElementById('chat-inp');
  const prompt = inp.value.trim(); if (!prompt) return;
  inp.value = ''; chatBusy = true;
  document.getElementById('send-btn').textContent = '...';

  const msgs = document.getElementById('chat-msgs');
  const ub = document.createElement('div'); ub.className='msg user';
  ub.textContent = prompt; msgs.appendChild(ub);

  const typing = document.createElement('div'); typing.className='msg nova';
  typing.innerHTML='<div class="nova-lbl">N.O.V.A.</div><div class="typing"><span></span><span></span><span></span></div>';
  msgs.appendChild(typing); msgs.scrollTop=msgs.scrollHeight;

  const model = document.getElementById('chat-model').value;
  try {
    const resp = await _apiFetch('/api/chat',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({prompt,model})});
    const reader=resp.body.getReader(), dec=new TextDecoder();
    let text='';
    const rb=document.createElement('div'); rb.className='msg nova';
    rb.innerHTML='<div class="nova-lbl">N.O.V.A.</div><span id="ns"></span>';
    msgs.replaceChild(rb,typing);
    const ns=rb.querySelector('#ns');
    while(true){
      const{done,value}=await reader.read(); if(done)break;
      text+=dec.decode(value,{stream:true}); ns.textContent=text;
      msgs.scrollTop=msgs.scrollHeight;
    }
  } catch(e) {
    typing.innerHTML=`<div class="nova-lbl">N.O.V.A.</div><span style="color:var(--pk)">Error: ${e.message}</span>`;
  }
  chatBusy=false;
  document.getElementById('send-btn').textContent='SEND ▶';
  msgs.scrollTop=msgs.scrollHeight;
}

document.getElementById('chat-inp').addEventListener('keydown', e => {
  if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendChat();}
});

// ── TERMINAL (xterm.js + WebSocket PTY) ───────────────────────────────────
function initTerminal() {
  const container = document.getElementById('xterm-container');
  xterm = new Terminal({
    theme: {
      background:'#010608', foreground:'#c0e4c0',
      cursor:'#00d4ff', cursorAccent:'#020912',
      selectionBackground:'rgba(0,212,255,.2)',
      black:'#0a0f18',  red:'#ff3c78',
      green:'#00ff9d',  yellow:'#ffd700',
      blue:'#00d4ff',   magenta:'#9d4eff',
      cyan:'#00f0ff',   white:'#c0dcf0',
      brightBlack:'#1a3050', brightRed:'#ff6090',
      brightGreen:'#40ffb0', brightYellow:'#ffe040',
      brightBlue:'#40e0ff',  brightMagenta:'#bf8fff',
      brightCyan:'#40f8ff',  brightWhite:'#e0f0ff',
    },
    fontFamily:"'Cascadia Code','Fira Code','Courier New',monospace",
    fontSize:13, lineHeight:1.3, cursorBlink:true,
    allowTransparency:true, scrollback:5000,
  });

  fitAddon = new FitAddon.FitAddon();
  xterm.loadAddon(fitAddon);
  xterm.open(container);
  setTimeout(() => fitAddon.fit(), 100);

  window.addEventListener('resize', () => { if(fitAddon) fitAddon.fit(); });

  termConnect();

  xterm.onResize(({cols,rows}) => {
    if(termWS && termWS.readyState===WebSocket.OPEN)
      termWS.send(JSON.stringify({type:'resize',cols,rows}));
  });
}

function termConnect() {
  const proto = location.protocol==='https:'?'wss':'ws';
  const url = `${proto}://${location.host}/ws/terminal?token=${encodeURIComponent(HUD_TOKEN)}`;
  termWS = new WebSocket(url);
  termWS.binaryType = 'arraybuffer';

  termWS.onopen = () => {
    document.getElementById('term-status').textContent = 'CONNECTED · bash · xterm-256color';
    document.getElementById('term-status').style.color = 'var(--gn)';
    if(fitAddon) {
      fitAddon.fit();
      const {cols,rows} = xterm;
      termWS.send(JSON.stringify({type:'resize',cols,rows}));
    }
  };
  termWS.onmessage = e => {
    if(e.data instanceof ArrayBuffer) xterm.write(new Uint8Array(e.data));
    else xterm.write(e.data);
  };
  termWS.onclose = () => {
    document.getElementById('term-status').textContent = 'DISCONNECTED — click RECONNECT';
    document.getElementById('term-status').style.color = 'var(--pk)';
    xterm.write('\r\n\x1b[31m[Terminal disconnected. Click RECONNECT to restore.]\x1b[0m\r\n');
  };
  termWS.onerror = () => xterm.write('\r\n\x1b[31m[WebSocket error]\x1b[0m\r\n');

  xterm.onData(data => {
    if(termWS && termWS.readyState===WebSocket.OPEN)
      termWS.send(new TextEncoder().encode(data));
  });
}

function termReconnect() {
  if(termWS) termWS.close();
  if(xterm) { xterm.clear(); termConnect(); }
  else initTerminal();
}
function termClear() { if(xterm) xterm.clear(); }

// ── FILES ─────────────────────────────────────────────────────────────────
async function treeNav(path) {
  treeCwd = path;
  document.getElementById('tree-path').textContent = path;
  const r = await _apiFetch('/api/files?path='+encodeURIComponent(path));
  const d = await r.json();
  const list = document.getElementById('tree-list');
  if(d.error){ list.innerHTML=`<div style="padding:10px;color:var(--pk);font-size:10px">${d.error}</div>`; return; }
  let h = '';
  d.entries.forEach(f => {
    const sz = f.is_dir ? '' : fmtSize(f.size);
    h += `<div class="tree-item" data-path="${f.path}" data-dir="${f.is_dir}"
      onclick="treeClick('${f.path}',${f.is_dir})"
      oncontextmenu="showCtx(event,'${f.path}',${f.is_dir})">
      <span class="icon">${f.is_dir?'📁':'📄'}</span>
      <span class="name ${f.is_dir?'dir':''}">${f.name}</span>
      <span class="meta">${sz||f.modified}</span>
    </div>`;
  });
  list.innerHTML = h || '<div style="padding:10px;color:var(--td);font-size:10px">empty</div>';
}

function treeUp() {
  const parts = treeCwd.split('/').filter(Boolean);
  if(parts.length>1) treeNav('/'+parts.slice(0,-1).join('/'));
  else treeNav('/');
}

function treeClick(path, isDir) {
  document.querySelectorAll('.tree-item').forEach(i=>i.classList.remove('selected'));
  const el = document.querySelector(`.tree-item[data-path="${path}"]`);
  if(el) el.classList.add('selected');
  if(isDir) { treeNav(path); return; }
  previewFile(path);
}

async function previewFile(path) {
  currentFile = path;
  document.getElementById('preview-empty').style.display='none';
  document.getElementById('preview-bar').style.display='flex';
  document.getElementById('preview-fname').textContent=path.split('/').pop();
  const body = document.getElementById('preview-body');
  body.style.display='block'; body.textContent='loading...';
  try {
    const r = await _apiFetch('/api/file/content?path='+encodeURIComponent(path));
    const d = await r.json();
    if(d.error){ body.textContent='Error: '+d.error; return; }
    body.textContent = d.content+(d.truncated?'\n\n[... truncated at 500 lines]':'');
    try { hljs.highlightElement(body); } catch(e){}
    if(d.truncated) log('File truncated at 500 lines · '+path,'wn');
  } catch(e){ body.textContent='Error: '+e.message; }
}

function fmtSize(b){
  if(b<1024)return b+'B'; if(b<1048576)return(b/1024).toFixed(1)+'K';
  if(b<1073741824)return(b/1048576).toFixed(1)+'M'; return(b/1073741824).toFixed(1)+'G';
}

// Context menu
function showCtx(e, path, isDir){
  e.preventDefault(); ctxTarget={path,isDir};
  const m=document.getElementById('ctxmenu');
  m.style.display='block'; m.style.left=e.clientX+'px'; m.style.top=e.clientY+'px';
}
document.addEventListener('click',()=>document.getElementById('ctxmenu').style.display='none');
function ctxOpen(){ if(ctxTarget) treeClick(ctxTarget.path,ctxTarget.isDir); }
function ctxEdit(){ if(ctxTarget&&!ctxTarget.isDir) { switchToTab('term'); } }
function ctxDownload(){ if(ctxTarget&&!ctxTarget.isDir) window.open('/api/file/download?path='+encodeURIComponent(ctxTarget.path)); }
function ctxDelete(){ if(ctxTarget) deleteFile(ctxTarget.path); }
function downloadCurrentFile(){ if(currentFile) window.open('/api/file/download?path='+encodeURIComponent(currentFile)); }
async function deleteCurrentFile(){
  if(!currentFile||!confirm('Delete '+currentFile.split('/').pop()+'?')) return;
  deleteFile(currentFile);
}
async function deleteFile(path){
  const r=await _apiFetch('/api/file/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});
  const d=await r.json();
  if(d.ok){ log('Deleted: '+path,'wn'); treeNav(treeCwd); resetPreview(); }
  else log('Delete failed: '+d.error,'er');
}
function resetPreview(){
  currentFile=null;
  document.getElementById('preview-empty').style.display='flex';
  document.getElementById('preview-bar').style.display='none';
  document.getElementById('preview-body').style.display='none';
}
function editCurrentFile(){
  if(!currentFile) return;
  switchToTab('term');
  setTimeout(()=>{ if(xterm&&termWS&&termWS.readyState===WebSocket.OPEN)
    termWS.send(new TextEncoder().encode(`nano "${currentFile}"\r`)); },300);
}

// Modal
function openNewFile(){
  modalAction='file';
  document.getElementById('modal-title').textContent='NEW FILE';
  document.getElementById('modal-input').placeholder='filename.txt';
  document.getElementById('modal-content').style.display='block';
  document.getElementById('modal').classList.add('open');
  document.getElementById('modal-input').focus();
}
function openNewDir(){
  modalAction='dir';
  document.getElementById('modal-title').textContent='NEW DIRECTORY';
  document.getElementById('modal-input').placeholder='dirname';
  document.getElementById('modal-content').style.display='none';
  document.getElementById('modal').classList.add('open');
  document.getElementById('modal-input').focus();
}
function openRenameFile(){
  if(!ctxTarget) return;
  modalAction='rename';
  document.getElementById('modal-title').textContent='RENAME';
  document.getElementById('modal-input').value=ctxTarget.path.split('/').pop();
  document.getElementById('modal-content').style.display='none';
  document.getElementById('modal').classList.add('open');
}
function closeModal(){
  document.getElementById('modal').classList.remove('open');
  document.getElementById('modal-input').value='';
  document.getElementById('modal-content').value='';
}
async function modalOk(){
  const name=document.getElementById('modal-input').value.trim(); if(!name) return;
  if(modalAction==='file'){
    const content=document.getElementById('modal-content').value;
    const path=treeCwd+'/'+name;
    const r=await _apiFetch('/api/file/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path,content,is_dir:false})});
    const d=await r.json();
    if(d.ok){log('Created: '+path,'ok');treeNav(treeCwd);}else log('Error: '+d.error,'er');
  } else if(modalAction==='dir'){
    const path=treeCwd+'/'+name;
    const r=await _apiFetch('/api/file/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path,is_dir:true})});
    const d=await r.json();
    if(d.ok){log('Created dir: '+path,'ok');treeNav(treeCwd);}else log('Error: '+d.error,'er');
  }
  closeModal();
}
document.getElementById('modal-input').addEventListener('keydown',e=>{if(e.key==='Enter')modalOk();});

// Boot
refresh();
setInterval(refresh, REFRESH);
treeNav('/home/simon-hq');

// ── 2FA Modal ─────────────────────────────────────────────────────────────────
// Usage: nova2fa.require('hud_delete_file', 'Delete file?').then(token => { ... })
//        Returns a Promise that resolves with the verified TOTP token,
//        or rejects if the user cancels or fails verification.

const nova2fa = (() => {
  let _resolve, _reject, _action, _pendingCallback;

  // Inject modal HTML once
  const _html = `
  <div id="fa-overlay" style="display:none;position:fixed;inset:0;background:rgba(2,9,18,0.92);
       z-index:9999;display:flex;align-items:center;justify-content:center;">
    <div style="background:#0a1628;border:1px solid #00d4ff;border-radius:12px;padding:32px 40px;
                min-width:340px;max-width:420px;box-shadow:0 0 40px rgba(0,212,255,0.2);">
      <div style="text-align:center;margin-bottom:20px;">
        <div style="font-size:2rem;margin-bottom:8px;">🔐</div>
        <div style="color:#00d4ff;font-size:1.1rem;font-weight:700;letter-spacing:1px;">ADMIN VERIFICATION</div>
        <div id="fa-action-label" style="color:#7a8fa6;font-size:0.82rem;margin-top:6px;"></div>
      </div>
      <div style="color:#c9d6e3;font-size:0.88rem;margin-bottom:16px;text-align:center;">
        Enter your 6-digit <span style="color:#00ff9d;">Google Authenticator</span> code
      </div>
      <input id="fa-input" type="text" inputmode="numeric" maxlength="6" placeholder="000000"
             style="width:100%;padding:12px;font-size:1.5rem;letter-spacing:8px;text-align:center;
                    background:#020912;border:1px solid #00d4ff;border-radius:8px;color:#00d4ff;
                    outline:none;box-sizing:border-box;" />
      <div id="fa-error" style="color:#ff3c78;font-size:0.8rem;margin-top:8px;min-height:18px;text-align:center;"></div>
      <div style="display:flex;gap:12px;margin-top:20px;">
        <button id="fa-cancel" onclick="nova2fa._cancel()"
                style="flex:1;padding:10px;background:transparent;border:1px solid #3a4a5a;
                       border-radius:6px;color:#7a8fa6;cursor:pointer;font-size:0.9rem;">
          Cancel
        </button>
        <button id="fa-submit" onclick="nova2fa._submit()"
                style="flex:2;padding:10px;background:linear-gradient(135deg,#00d4ff22,#9d4eff22);
                       border:1px solid #00d4ff;border-radius:6px;color:#00d4ff;cursor:pointer;
                       font-size:0.9rem;font-weight:600;">
          Verify ↵
        </button>
      </div>
    </div>
  </div>`;
  document.body.insertAdjacentHTML('beforeend', _html);

  const _overlay = () => document.getElementById('fa-overlay');
  const _input   = () => document.getElementById('fa-input');
  const _errEl   = () => document.getElementById('fa-error');

  document.getElementById('fa-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') nova2fa._submit();
    if (e.key === 'Escape') nova2fa._cancel();
  });

  async function _submit() {
    const token = _input().value.trim();
    if (token.length !== 6 || !/^\d{6}$/.test(token)) {
      _errEl().textContent = 'Enter a 6-digit code.';
      return;
    }
    document.getElementById('fa-submit').textContent = 'Verifying…';
    document.getElementById('fa-submit').disabled = true;
    try {
      const res = await _apiFetch('/api/admin/verify', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({token, action: _action})
      });
      const data = await res.json();
      if (data.ok) {
        _overlay().style.display = 'none';
        _resolve(token);
      } else {
        _errEl().textContent = data.message || 'Invalid token. Try again.';
        _input().value = '';
        _input().focus();
        document.getElementById('fa-submit').textContent = 'Verify ↵';
        document.getElementById('fa-submit').disabled = false;
      }
    } catch(err) {
      _errEl().textContent = 'Verification failed — server error.';
      document.getElementById('fa-submit').textContent = 'Verify ↵';
      document.getElementById('fa-submit').disabled = false;
    }
  }

  function _cancel() {
    _overlay().style.display = 'none';
    _reject(new Error('2FA cancelled'));
  }

  function require(action, label='Admin operation') {
    _action = action;
    document.getElementById('fa-action-label').textContent = label;
    _errEl().textContent = '';
    _input().value = '';
    document.getElementById('fa-submit').textContent = 'Verify ↵';
    document.getElementById('fa-submit').disabled = false;
    _overlay().style.display = 'flex';
    setTimeout(() => _input().focus(), 50);
    return new Promise((res, rej) => { _resolve = res; _reject = rej; });
  }

  return { require, _submit, _cancel };
})();

// Patch file delete to use 2FA
const _origDeleteFile = deleteFile;
window.deleteFile = async function(path) {
  try {
    const token = await nova2fa.require('hud_delete_file', `Delete: ${path.split('/').pop()}`);
    const r = await _apiFetch('/api/file/delete', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({path, totp_token: token})
    });
    const d = await r.json();
    if(d.ok) { showToast('Deleted'); treeNav(currentPath); }
    else showToast('Error: ' + d.error, true);
  } catch(e) {
    if(e.message !== '2FA cancelled') showToast('Delete cancelled', true);
  }
};

// Check 2FA status on load and show indicator in header
(async () => {
  try {
    const r = await _apiFetch('/api/admin/2fa-status');
    const s = await r.json();
    const indicator = document.createElement('div');
    indicator.title = s.configured ? '2FA Active — Admin ops require Google Authenticator' : '2FA not configured — run nova_2fa_setup.py';
    indicator.style.cssText = 'position:fixed;bottom:18px;right:18px;background:#0a1628;border:1px solid ' +
      (s.configured ? '#00ff9d' : '#ff3c78') + ';border-radius:20px;padding:5px 12px;font-size:0.75rem;' +
      'color:' + (s.configured ? '#00ff9d' : '#ff3c78') + ';cursor:default;z-index:1000;';
    indicator.textContent = s.configured ? '🔐 2FA ON' : '⚠️ 2FA OFF';
    document.body.appendChild(indicator);
  } catch(e) {}
})();
</script>
</body>
</html>"""

if __name__ == "__main__":
    print("[NOVA HUD v4.0] http://0.0.0.0:3001", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=3001, log_level="warning",
                ws_ping_interval=20, ws_ping_timeout=30)
