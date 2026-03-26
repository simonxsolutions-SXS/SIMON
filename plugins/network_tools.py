"""
S.I.M.O.N. Plugin — Network Tools (v2.0)
==========================================
Comprehensive network diagnostics for IT/MSP daily use.
No external API keys required — everything runs locally or via free endpoints.

Tools:
  get_public_ip       — Public IP + geolocation + ISP
  ip_info             — Geolocation + ownership for any IP
  dns_lookup          — Hostname → IP resolution
  reverse_dns         — IP → hostname (PTR record)
  check_port          — Single TCP port open/closed
  scan_common_ports   — Top-20 ports scanned concurrently
  ping_host           — ICMP ping with packet loss + latency stats
  traceroute          — Hop-by-hop path to destination
  speed_test          — Download/upload Mbps + latency (Cloudflare, no key)
  ssl_cert_check      — TLS cert validity, expiry date, issuer, SANs
  whois_lookup        — Domain registration, expiry, registrar, nameservers
  wifi_info           — SSID, signal strength, channel, band, security, gateway
  local_network_info  — All interfaces, IPs, MACs, default gateway
  arp_scan            — All live devices visible on local subnet

Voice commands:
  "Simon, run a speed test"
  "Simon, ping google.com"
  "Simon, traceroute to 1.1.1.1"
  "Simon, scan ports on 192.168.1.1"
  "Simon, check the SSL cert for apple.com"
  "Simon, whois your-company.com"
  "Simon, what WiFi am I on?"
  "Simon, show all network interfaces"
  "Simon, who's on my network?"
  "Simon, reverse DNS 8.8.8.8"
"""

import asyncio
import httpx
import ipaddress
import re
import socket
import ssl
import subprocess
import time
from datetime import datetime, timezone
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────

METADATA = {
    "name":        "Network Tools",
    "description": "Full IT/MSP network diagnostic suite — no API keys needed",
    "version":     "2.0",
    "author":      "Simon-X Solutions",
}

# Common ports for the scan tool  (port → service name)
COMMON_PORTS = {
    21: "FTP",    22: "SSH",     23: "Telnet",   25: "SMTP",
    53: "DNS",    80: "HTTP",    110: "POP3",    135: "RPC",
    139: "NetBIOS", 143: "IMAP", 443: "HTTPS",  445: "SMB",
    993: "IMAPS", 995: "POP3S", 1433: "MSSQL",  3306: "MySQL",
    3389: "RDP",  5900: "VNC",  8080: "HTTP-ALT", 8443: "HTTPS-ALT",
}

# ─────────────────────────────────────────────────────────────────────────────

TOOLS = [
    # ── Existing tools (kept identical) ─────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "get_public_ip",
            "description": (
                "Get the current public IP address and geolocation. "
                "Use when asked 'what's my IP', 'what's my public IP', "
                "'where am I appearing from'."
            ),
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ip_info",
            "description": (
                "Get geolocation and ownership info for any IP address. "
                "Use when asked 'who owns IP', 'where is IP', 'look up IP [address]'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ip": {"type": "string", "description": "IP address to look up"}
                },
                "required": ["ip"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "dns_lookup",
            "description": (
                "Resolve a hostname to its IP addresses (DNS A/AAAA lookup). "
                "Use when asked 'what IP is [hostname]', 'resolve [domain]', 'look up [hostname]'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "hostname": {"type": "string", "description": "Hostname or domain, e.g. 'google.com'"}
                },
                "required": ["hostname"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_port",
            "description": (
                "Check if a single TCP port is open on a host. "
                "Use when asked 'is port [N] open on [host]', 'can I reach [host]:[port]'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string", "description": "Hostname or IP address"},
                    "port": {"type": "integer", "description": "TCP port number 1–65535"}
                },
                "required": ["host", "port"]
            }
        }
    },

    # ── New tools ────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "reverse_dns",
            "description": (
                "Reverse DNS lookup — convert an IP address to its hostname. "
                "Use when asked 'what hostname is [IP]', 'reverse lookup [IP]', "
                "'who does [IP] belong to'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ip": {"type": "string", "description": "IP address for reverse lookup"}
                },
                "required": ["ip"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "scan_common_ports",
            "description": (
                "Scan the top 20 common TCP ports on a host concurrently. "
                "Use when asked 'scan ports on [host]', 'what ports are open on [host]', "
                "'what services is [host] running'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string", "description": "Hostname or IP address to scan"}
                },
                "required": ["host"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ping_host",
            "description": (
                "Ping a host and return latency statistics: min, avg, max RTT and packet loss. "
                "Use when asked 'ping [host]', 'is [host] reachable', 'latency to [host]', "
                "'response time for [host]'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "host":  {"type": "string",  "description": "Hostname or IP to ping"},
                    "count": {"type": "integer", "description": "Number of pings to send (default 5, max 20)"}
                },
                "required": ["host"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "traceroute",
            "description": (
                "Trace the network path (hops) from this machine to a destination. "
                "Use when asked 'traceroute to [host]', 'trace route to [host]', "
                "'show the path to [host]', 'where is my traffic going'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "host":    {"type": "string",  "description": "Destination hostname or IP"},
                    "max_hops": {"type": "integer", "description": "Maximum hops to trace (default 15, max 30)"}
                },
                "required": ["host"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "speed_test",
            "description": (
                "Measure current internet download speed, upload speed, and latency "
                "using Cloudflare's speed test infrastructure. No API key needed. "
                "Use when asked 'run a speed test', 'what's my internet speed', "
                "'how fast is my connection', 'check my bandwidth'."
            ),
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ssl_cert_check",
            "description": (
                "Check the TLS/SSL certificate for a domain: validity, expiry date, "
                "issuer, and subject alternative names. "
                "Use when asked 'check SSL cert for [domain]', 'when does the cert expire for [domain]', "
                "'is the certificate valid for [domain]'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "Domain name, e.g. 'apple.com' or 'your-company.com'"}
                },
                "required": ["domain"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "whois_lookup",
            "description": (
                "Get WHOIS information for a domain: registrar, registration date, "
                "expiry date, and nameservers. "
                "Use when asked 'whois [domain]', 'who owns [domain]', "
                "'when does [domain] expire', 'nameservers for [domain]'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "Domain name, e.g. 'your-company.com'"}
                },
                "required": ["domain"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "wifi_info",
            "description": (
                "Get current WiFi network details: SSID, signal strength, channel, "
                "band, security type, BSSID, and default gateway. "
                "Use when asked 'what WiFi am I on', 'WiFi signal strength', "
                "'what channel is my WiFi on', 'show WiFi details'."
            ),
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "local_network_info",
            "description": (
                "Show all active network interfaces with IP addresses, MAC addresses, "
                "subnet masks, and default gateway. "
                "Use when asked 'show network interfaces', 'what's my local IP', "
                "'show all IPs', 'network configuration', 'what's my MAC address'."
            ),
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "arp_scan",
            "description": (
                "Scan the local subnet and return all live devices with their IP addresses, "
                "MAC addresses, and hostnames. "
                "Use when asked 'who's on my network', 'show all devices on the network', "
                "'network device scan', 'list local devices', 'find devices on subnet'."
            ),
            "parameters": {"type": "object", "properties": {}}
        }
    },
]


# ─────────────────────────────────────────────────────────────────────────────
#  DISPATCHER
# ─────────────────────────────────────────────────────────────────────────────

async def execute(name: str, args: dict) -> Optional[str]:

    if name == "get_public_ip":       return await _get_public_ip()
    if name == "ip_info":             return await _ip_info(args.get("ip", ""))
    if name == "dns_lookup":          return await _dns_lookup(args.get("hostname", ""))
    if name == "reverse_dns":         return await _reverse_dns(args.get("ip", ""))
    if name == "check_port":          return await _check_port(args.get("host",""), int(args.get("port",0)))
    if name == "scan_common_ports":   return await _scan_common_ports(args.get("host", ""))
    if name == "ping_host":           return await _ping_host(args.get("host",""), int(args.get("count",5)))
    if name == "traceroute":          return await _traceroute(args.get("host",""), int(args.get("max_hops",15)))
    if name == "speed_test":          return await _speed_test()
    if name == "ssl_cert_check":      return await _ssl_cert_check(args.get("domain",""))
    if name == "whois_lookup":        return await _whois_lookup(args.get("domain",""))
    if name == "wifi_info":           return await _wifi_info()
    if name == "local_network_info":  return await _local_network_info()
    if name == "arp_scan":            return await _arp_scan()
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  IMPLEMENTATIONS
# ─────────────────────────────────────────────────────────────────────────────

async def _get_public_ip() -> str:
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            d = (await c.get("https://ipinfo.io/json")).json()
        loc = ", ".join(filter(None, [d.get("city",""), d.get("region",""), d.get("country","")]))
        return f"Public IP: {d.get('ip','?')} | Location: {loc} | ISP: {d.get('org','?')}"
    except Exception as e:
        return f"Could not retrieve public IP: {e}"


async def _ip_info(ip: str) -> str:
    ip = ip.strip()
    if not ip:
        return "No IP address provided."
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            d = (await c.get(f"https://ipinfo.io/{ip}/json")).json()
        if "error" in d:
            return f"IP info error: {d['error'].get('message','unknown')}"
        parts = [f"IP: {ip}"]
        loc = ", ".join(filter(None, [d.get("city",""), d.get("region",""), d.get("country","")]))
        if loc:              parts.append(f"Location: {loc}")
        if d.get("org"):     parts.append(f"Org: {d['org']}")
        if d.get("hostname"):parts.append(f"Hostname: {d['hostname']}")
        if d.get("timezone"):parts.append(f"TZ: {d['timezone']}")
        return " | ".join(parts)
    except Exception as e:
        return f"IP info lookup failed: {e}"


async def _dns_lookup(hostname: str) -> str:
    hostname = hostname.strip()
    if not hostname:
        return "No hostname provided."
    try:
        loop    = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, lambda: socket.getaddrinfo(hostname, None))
        ips     = sorted({r[4][0] for r in results})
        if not ips:
            return f"No DNS records found for {hostname}."
        return f"{hostname} → {', '.join(ips)}"
    except socket.gaierror:
        return f"DNS lookup failed — {hostname} not found."
    except Exception as e:
        return f"DNS error for {hostname}: {e}"


async def _reverse_dns(ip: str) -> str:
    ip = ip.strip()
    if not ip:
        return "No IP address provided."
    try:
        loop     = asyncio.get_event_loop()
        hostname = await loop.run_in_executor(None, lambda: socket.gethostbyaddr(ip)[0])
        return f"{ip} → {hostname}"
    except socket.herror:
        return f"{ip} → no PTR record (no reverse DNS configured)"
    except Exception as e:
        return f"Reverse DNS error: {e}"


async def _check_port(host: str, port: int) -> str:
    host = host.strip()
    if not host or not port:
        return "Host and port are required."
    if not (1 <= port <= 65535):
        return f"Invalid port {port}."
    try:
        open_ = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, lambda: _tcp_check(host, port, 4)),
            timeout=6
        )
        svc    = COMMON_PORTS.get(port, "")
        label  = f" ({svc})" if svc else ""
        status = "OPEN" if open_ else "CLOSED/FILTERED"
        return f"Port {port}{label} on {host}: {status}"
    except asyncio.TimeoutError:
        return f"Port check timed out for {host}:{port}."
    except Exception as e:
        return f"Port check error: {e}"


async def _scan_common_ports(host: str) -> str:
    host = host.strip()
    if not host:
        return "No host provided."

    loop = asyncio.get_event_loop()

    async def _check_one(port: int) -> tuple[int, bool]:
        try:
            open_ = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: _tcp_check(host, port, 1.5)),
                timeout=2.5
            )
            return port, open_
        except Exception:
            return port, False

    # Run all checks concurrently
    tasks   = [_check_one(p) for p in COMMON_PORTS]
    results = await asyncio.gather(*tasks)

    open_ports   = [(p, COMMON_PORTS[p]) for p, o in sorted(results) if o]
    closed_count = sum(1 for _, o in results if not o)

    if not open_ports:
        return f"{host}: no common ports open (scanned {len(COMMON_PORTS)} ports)."

    lines = [f"{p}/{svc}" for p, svc in open_ports]
    return (f"{host} — {len(open_ports)} open port(s): {', '.join(lines)} "
            f"| {closed_count} closed/filtered")


async def _ping_host(host: str, count: int = 5) -> str:
    host  = host.strip()
    count = max(1, min(count, 20))
    if not host:
        return "No host provided."
    try:
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                ["ping", "-c", str(count), "-W", "2000", host],
                capture_output=True, text=True, timeout=count * 3 + 5
            )
        )
        output = result.stdout + result.stderr

        # Parse macOS ping output
        # "round-trip min/avg/max/stddev = 12.345/15.678/18.901/1.234 ms"
        rtt_match  = re.search(r"min/avg/max/stddev = ([\d.]+)/([\d.]+)/([\d.]+)", output)
        loss_match = re.search(r"(\d+\.?\d*)% packet loss", output)
        tx_match   = re.search(r"(\d+) packets transmitted", output)
        rx_match   = re.search(r"(\d+) packets received", output)

        if rtt_match:
            mn, avg, mx = rtt_match.group(1), rtt_match.group(2), rtt_match.group(3)
            loss = loss_match.group(1) if loss_match else "?"
            return (f"Ping {host} ({count} packets): "
                    f"min {mn}ms | avg {avg}ms | max {mx}ms | loss {loss}%")
        elif result.returncode != 0:
            return f"{host} is unreachable — 100% packet loss."
        else:
            # Couldn't parse cleanly — return raw summary
            for line in output.splitlines():
                if "packet loss" in line or "min/avg" in line:
                    return f"Ping {host}: {line.strip()}"
            return f"Ping {host}: completed (no parseable stats)"

    except subprocess.TimeoutExpired:
        return f"Ping timed out for {host}."
    except Exception as e:
        return f"Ping error: {e}"


async def _traceroute(host: str, max_hops: int = 15) -> str:
    host     = host.strip()
    max_hops = max(1, min(max_hops, 30))
    if not host:
        return "No host provided."
    try:
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                ["traceroute", "-m", str(max_hops), "-w", "2", "-q", "1", host],
                capture_output=True, text=True, timeout=max_hops * 4 + 10
            )
        )
        lines = result.stdout.strip().splitlines()
        if not lines:
            return f"Traceroute to {host} returned no output."

        # Format: keep header + up to 15 hop lines, truncate long lines
        out_lines = []
        for line in lines[:max_hops + 2]:
            line = line.strip()
            if line:
                # Trim to 80 chars for SIMON to speak naturally
                out_lines.append(line[:80])

        if len(out_lines) <= 1:
            return f"Traceroute to {host}: no hops resolved (host may be unreachable)."

        hops = len([l for l in out_lines if l and l[0].isdigit()])
        return f"Traceroute to {host} — {hops} hop(s):\n" + "\n".join(out_lines)

    except subprocess.TimeoutExpired:
        return f"Traceroute to {host} timed out."
    except FileNotFoundError:
        return "traceroute command not found — install with: brew install traceroute"
    except Exception as e:
        return f"Traceroute error: {e}"


async def _speed_test() -> str:
    """
    Measure download speed, upload speed, and latency using Cloudflare's
    speed test infrastructure. No API key. No external library.

    Method:
      Latency  — 10 sequential small GETs to speed.cloudflare.com, measure RTT
      Download — Download a 10MB payload, measure throughput
      Upload   — POST a 5MB payload, measure throughput
    """
    BASE = "https://speed.cloudflare.com"

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:

            # ── Latency: 10 sequential round trips ────────────────────────
            latencies = []
            for _ in range(10):
                t0 = time.perf_counter()
                await client.get(f"{BASE}/__down?bytes=1000")
                latencies.append((time.perf_counter() - t0) * 1000)

            lat_avg = sum(latencies) / len(latencies)
            lat_min = min(latencies)

            # ── Download: 10MB ────────────────────────────────────────────
            t0   = time.perf_counter()
            resp = await client.get(f"{BASE}/__down?bytes=10000000")
            dl_elapsed = time.perf_counter() - t0
            dl_bytes   = len(resp.content)
            dl_mbps    = (dl_bytes * 8) / dl_elapsed / 1_000_000

            # ── Upload: 5MB ───────────────────────────────────────────────
            payload   = b"0" * 5_000_000
            t0        = time.perf_counter()
            await client.post(f"{BASE}/__up", content=payload,
                              headers={"Content-Type": "application/octet-stream"})
            ul_elapsed = time.perf_counter() - t0
            ul_mbps    = (len(payload) * 8) / ul_elapsed / 1_000_000

        return (
            f"Speed Test (Cloudflare) — "
            f"Download: {dl_mbps:.1f} Mbps | "
            f"Upload: {ul_mbps:.1f} Mbps | "
            f"Latency: {lat_avg:.1f}ms avg / {lat_min:.1f}ms min"
        )

    except httpx.TimeoutException:
        return "Speed test timed out — check internet connection."
    except Exception as e:
        return f"Speed test error: {e}"


async def _ssl_cert_check(domain: str) -> str:
    domain = domain.strip().lower()
    # Strip scheme if provided
    domain = re.sub(r"^https?://", "", domain).split("/")[0]
    if not domain:
        return "No domain provided."

    try:
        loop = asyncio.get_event_loop()

        def _get_cert():
            ctx  = ssl.create_default_context()
            conn = ctx.wrap_socket(
                socket.create_connection((domain, 443), timeout=8),
                server_hostname=domain
            )
            cert = conn.getpeercert()
            conn.close()
            return cert

        cert = await asyncio.wait_for(
            loop.run_in_executor(None, _get_cert),
            timeout=10
        )

        # Parse expiry
        not_after_str = cert.get("notAfter", "")
        not_after     = datetime.strptime(not_after_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        now           = datetime.now(timezone.utc)
        days_left     = (not_after - now).days
        expired       = days_left < 0

        # Parse subject
        subject = dict(x[0] for x in cert.get("subject", []))
        cn      = subject.get("commonName", domain)

        # Parse issuer
        issuer  = dict(x[0] for x in cert.get("issuer", []))
        issuer_o = issuer.get("organizationName", "Unknown")

        # Parse SANs
        sans = [v for t, v in cert.get("subjectAltName", []) if t == "DNS"]
        san_str = ", ".join(sans[:5]) + ("..." if len(sans) > 5 else "")

        status = "❌ EXPIRED" if expired else ("⚠️ EXPIRING SOON" if days_left < 30 else "✅ VALID")

        return (
            f"SSL cert for {domain}: {status} | "
            f"CN: {cn} | "
            f"Issuer: {issuer_o} | "
            f"Expires: {not_after.strftime('%Y-%m-%d')} ({days_left}d) | "
            f"SANs: {san_str}"
        )

    except ssl.SSLCertVerificationError as e:
        return f"SSL cert for {domain}: INVALID — {e}"
    except ssl.SSLError as e:
        return f"SSL error for {domain}: {e}"
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        return f"Could not connect to {domain}:443 — {e}"
    except Exception as e:
        return f"SSL cert check error: {e}"


async def _whois_lookup(domain: str) -> str:
    domain = domain.strip().lower()
    domain = re.sub(r"^https?://", "", domain).split("/")[0]
    if not domain:
        return "No domain provided."

    try:
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                ["whois", domain],
                capture_output=True, text=True, timeout=15
            )
        )
        raw = result.stdout

        def _extract(patterns: list[str]) -> str:
            for pat in patterns:
                m = re.search(pat, raw, re.IGNORECASE | re.MULTILINE)
                if m:
                    return m.group(1).strip()
            return ""

        registrar   = _extract([r"Registrar:\s*(.+)", r"registrar:\s*(.+)"])
        created     = _extract([r"Creation Date:\s*(.+)", r"created:\s*(.+)", r"Registered:\s*(.+)"])
        expiry      = _extract([r"Registry Expiry Date:\s*(.+)", r"Expiry Date:\s*(.+)", r"expires:\s*(.+)"])
        updated     = _extract([r"Updated Date:\s*(.+)", r"last-update:\s*(.+)"])
        nameservers = re.findall(r"Name Server:\s*(.+)", raw, re.IGNORECASE)
        ns_str      = ", ".join(sorted({n.strip().lower() for n in nameservers[:4]}))

        # Trim dates to just the date portion
        def _trim_date(s: str) -> str:
            if not s: return ""
            m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
            return m.group(1) if m else s[:20]

        parts = [f"Domain: {domain}"]
        if registrar:              parts.append(f"Registrar: {registrar[:40]}")
        if _trim_date(created):    parts.append(f"Created: {_trim_date(created)}")
        if _trim_date(expiry):     parts.append(f"Expires: {_trim_date(expiry)}")
        if _trim_date(updated):    parts.append(f"Updated: {_trim_date(updated)}")
        if ns_str:                 parts.append(f"Nameservers: {ns_str}")

        if len(parts) == 1:
            return f"No parseable WHOIS data for {domain}."

        return " | ".join(parts)

    except subprocess.TimeoutExpired:
        return f"WHOIS timed out for {domain}."
    except FileNotFoundError:
        return "whois command not found — install with: brew install whois"
    except Exception as e:
        return f"WHOIS error: {e}"


async def _wifi_info() -> str:
    """
    WiFi status for macOS 26+ (Tahoe).

    macOS 26 privacy policy redacts the SSID from all background processes.
    Both `networksetup -getairportnetwork en0` and `system_profiler
    SPAirPortDataType` return empty/redacted SSID even when fully connected.
    This is intentional Apple behaviour, not a bug or connectivity problem.

    We use a compiled CoreWLAN Swift probe (wifi_probe binary) that reads
    the real hardware state: power, signal, channel, band, and TX rate.
    Falls back to system_profiler for older macOS if the probe is missing.
    """
    import pathlib
    loop      = asyncio.get_event_loop()
    # The probe lives in the jarvis/ root, one level up from plugins/
    PROBE_BIN = pathlib.Path(__file__).parent.parent / "wifi_probe"

    def _parse_probe(stdout: str) -> dict:
        """Parse KEY:VALUE lines from the Swift probe into a dict."""
        data = {}
        for line in stdout.strip().splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                data[k.strip()] = v.strip()
        return data

    def _quality_label(rssi_str: str) -> str:
        try:
            ri = int(rssi_str)
            if   ri >= -50: return "Excellent"
            elif ri >= -60: return "Good"
            elif ri >= -70: return "Fair"
            elif ri >= -80: return "Poor"
            else:           return "Very Poor — move closer to router"
        except ValueError:
            return ""

    try:
        # ── Primary path: compiled CoreWLAN Swift probe ──────────────────
        if PROBE_BIN.exists():
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: subprocess.run(
                        [str(PROBE_BIN)],
                        capture_output=True, text=True, timeout=6
                    )
                ),
                timeout=8
            )
            d = _parse_probe(result.stdout)

            if d.get("STATUS") == "off":
                return "WiFi: Powered off. Enable in System Settings → Wi-Fi."

            rssi   = d.get("RSSI",    "")
            noise  = d.get("NOISE",   "")
            txrate = d.get("TXRATE",  "")
            ch     = d.get("CHANNEL", "")
            band   = d.get("BAND",    "")
            width  = d.get("WIDTH",   "")

            parts = ["WiFi: Connected"]
            if rssi:   parts.append(f"Signal: {rssi} dBm ({_quality_label(rssi)})")
            if noise:  parts.append(f"Noise: {noise} dBm")
            if ch:     parts.append(f"Channel: {ch}")
            if band:   parts.append(band)
            if width:  parts.append(width)
            if txrate: parts.append(f"Link rate: {txrate} Mbps")
            gw = await _get_default_gateway()
            if gw:     parts.append(f"Gateway: {gw}")
            parts.append("(SSID hidden — macOS 26 privacy policy)")
            return " | ".join(parts)

        # ── Fallback: system_profiler (macOS < 26 or probe missing) ──────
        sp = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["system_profiler", "SPAirPortDataType"],
                    capture_output=True, text=True, timeout=15
                )
            ),
            timeout=18
        )
        raw = sp.stdout

        # macOS 26: "Status: Connected" but SSID line is "<redacted>"
        connected = ("Status: Connected" in raw or "Status: Associated" in raw)

        if not connected:
            # Last resort: ask CoreWLAN via swift one-liner
            try:
                sw = subprocess.run(
                    ["swift", "-e",
                     'import CoreWLAN; '
                     'if let i=CWWiFiClient.shared().interface() '
                     '{ print(i.powerOn() ? "on" : "off", i.rssiValue()) } '
                     'else { print("none") }'],
                    capture_output=True, text=True, timeout=8
                )
                tok = sw.stdout.strip().split()
                if tok and tok[0] == "on":
                    rssi = tok[1] if len(tok) > 1 else ""
                    return (f"WiFi: Connected | Signal: {rssi} dBm "
                            f"({_quality_label(rssi)}) | "
                            f"(SSID hidden — macOS 26 privacy policy)")
            except Exception:
                pass
            return "WiFi: Not connected or powered off."

        def _sp(key: str) -> str:
            m = re.search(rf"{re.escape(key)}:\s*(.+)", raw, re.IGNORECASE)
            return m.group(1).strip() if m else ""

        sig_raw  = _sp("Signal / Noise")
        channel  = _sp("Channel")
        tx_rate  = _sp("Transmit Rate")
        phy_mode = _sp("PHY Mode")
        security = _sp("Security")

        rssi_str, noise_str = "", ""
        sn = re.match(r"(-?\d+)\s*dBm\s*/\s*(-?\d+)\s*dBm", sig_raw)
        if sn:
            rssi_str, noise_str = sn.group(1), sn.group(2)

        parts = ["WiFi: Connected"]
        if rssi_str:  parts.append(f"Signal: {rssi_str} dBm ({_quality_label(rssi_str)})")
        if noise_str: parts.append(f"Noise: {noise_str} dBm")
        if channel:   parts.append(f"Channel: {channel}")
        if phy_mode:  parts.append(f"Standard: {phy_mode}")
        if security:  parts.append(f"Security: {security}")
        if tx_rate:   parts.append(f"Link rate: {tx_rate} Mbps")
        gw = await _get_default_gateway()
        if gw: parts.append(f"Gateway: {gw}")
        parts.append("(SSID hidden — macOS 26 privacy policy)")
        return " | ".join(parts)

    except asyncio.TimeoutError:
        return "WiFi info: probe timed out — try again in a moment."
    except Exception as e:
        return f"WiFi info error: {e}"


async def _local_network_info() -> str:
    """Show all active network interfaces with IPs and MACs."""
    try:
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5)
        )
        raw = result.stdout

        interfaces = []
        current    = None

        for line in raw.splitlines():
            # New interface block
            iface_match = re.match(r"^(\S+):", line)
            if iface_match:
                current = {
                    "name":  iface_match.group(1),
                    "ipv4":  "",
                    "ipv6":  "",
                    "mac":   "",
                    "mask":  "",
                    "up":    "RUNNING" in line,
                }
                interfaces.append(current)
                continue

            if current is None:
                continue

            # IPv4
            m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)\s+netmask (0x[\da-f]+|\S+)", line)
            if m:
                current["ipv4"] = m.group(1)
                # Convert hex netmask
                mask_raw = m.group(2)
                if mask_raw.startswith("0x"):
                    mask_int  = int(mask_raw, 16)
                    mask_bits = bin(mask_int).count("1")
                    current["mask"] = f"/{mask_bits}"
                else:
                    current["mask"] = f" {mask_raw}"

            # IPv6
            m6 = re.search(r"inet6 ([a-f0-9:]+)", line)
            if m6 and not m6.group(1).startswith("fe80"):
                current["ipv6"] = m6.group(1)

            # MAC
            mc = re.search(r"ether ([\da-f:]{17})", line)
            if mc:
                current["mac"] = mc.group(1)

        # Filter to active interfaces with an IP
        active = [i for i in interfaces if i["ipv4"] and not i["ipv4"].startswith("127.")]
        if not active:
            return "No active non-loopback network interfaces found."

        gw = await _get_default_gateway()
        lines = []
        for iface in active:
            parts = [f"{iface['name']}: {iface['ipv4']}{iface['mask']}"]
            if iface["mac"]:  parts.append(f"MAC {iface['mac']}")
            if iface["ipv6"]: parts.append(f"IPv6 {iface['ipv6']}")
            lines.append("  " + " | ".join(parts))

        header = f"Active interfaces ({len(active)}):"
        if gw:
            header += f" | Default Gateway: {gw}"
        return header + "\n" + "\n".join(lines)

    except Exception as e:
        return f"Network interface error: {e}"


async def _arp_scan() -> str:
    """
    List devices on the local subnet using the ARP cache.
    Reads the kernel ARP table — fast, no network traffic generated.
    For a live sweep (to find devices not yet in cache), use:
      "Simon, ping sweep the network" (future enhancement)
    """
    try:
        loop = asyncio.get_event_loop()

        # Read the kernel ARP table via arp -a
        arp_result = await asyncio.wait_for(
            loop.run_in_executor(
                None, lambda: subprocess.run(
                    ["arp", "-a", "-n"],   # -n = no reverse DNS (much faster)
                    capture_output=True, text=True, timeout=4
                )
            ),
            timeout=6
        )
        raw = arp_result.stdout

        # Parse ARP output: "hostname (ip) at mac on interface"
        devices = []
        seen_macs = set()
        for line in raw.splitlines():
            m = re.match(r"^(\S+)\s+\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+([\da-f:]+)", line)
            if not m:
                continue
            hostname, ip, mac = m.group(1), m.group(2), m.group(3)
            # Skip incomplete entries and broadcast
            if mac in ("ff:ff:ff:ff:ff:ff", "(incomplete)") or mac in seen_macs:
                continue
            # Skip 169.254.x.x (link-local)
            if ip.startswith("169.254"):
                continue
            seen_macs.add(mac)
            # Clean up hostname — if it's just the IP with dots replaced, use "?"
            display_host = hostname if hostname != ip.replace(".", "-") else "?"
            devices.append((ip, mac, display_host))

        if not devices:
            return "No devices found on local network (ARP table empty — try again after a moment)."

        # Sort by last octet
        try:
            devices.sort(key=lambda x: int(x[0].split(".")[-1]))
        except Exception:
            pass

        lines = [f"  {ip:<18} {mac:<20} {host}" for ip, mac, host in devices]
        return (f"{len(devices)} device(s) on local network:\n"
                f"  {'IP':<18} {'MAC':<20} Hostname\n"
                + "\n".join(lines))

    except Exception as e:
        return f"ARP scan error: {e}"


# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _tcp_check(host: str, port: int, timeout: float = 4) -> bool:
    """Blocking TCP connect — run via executor."""
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


async def _get_default_gateway() -> str:
    """Return the default gateway IP address."""
    try:
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: subprocess.run(
                ["route", "get", "default"],
                capture_output=True, text=True, timeout=4
            )
        )
        m = re.search(r"gateway:\s*(\d+\.\d+\.\d+\.\d+)", result.stdout)
        return m.group(1) if m else ""
    except Exception:
        return ""
