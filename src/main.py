"""FastAPI Proxy Rotator Dashboard — Main Application (SB Admin 2)."""

import asyncio
import html
import json
import shutil
import subprocess
import time
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .proxy_fetcher import fetch_all
from .local_proxy import local_proxy
from .proxy_manager import (
    upsert_proxies, get_all, get_stats, get_recent_logs,
    bulk_validate, rotator, request_with_proxy, add_log, get_by_id, test_proxy,
)

DB_PATH = os.path.join(os.path.dirname(__file__), "proxies.db")


class ProxyTestRequest(BaseModel):
    url: str = "https://httpbin.org/ip"
    method: str = "GET"


class GmailCreateRequest(BaseModel):
    email: str
    password: str = ""
    proxy_url: Optional[str] = None


class AddProxyRequest(BaseModel):
    ip: str
    port: Optional[int] = None
    protocol: str = "http"
    country: str = ""
    source: str = "manual"


class SessionConnectRequest(BaseModel):
    proxy_id: Optional[int] = None


class SystemProxyRequest(BaseModel):
    enabled: bool


class AutoRotateRequest(BaseModel):
    enabled: bool


# ── Lifespan ──────────────────────────────────────────────────────────────────

AUTO_ROTATE_INTERVAL = float(os.getenv("AUTO_ROTATE_INTERVAL", "60"))
auto_rotate_task: Optional[asyncio.Task] = None


async def _auto_rotate_loop() -> None:
    """Continuously rotate the session proxy while auto_rotate is enabled."""
    while True:
        await asyncio.sleep(AUTO_ROTATE_INTERVAL)
        if not rotator.auto_rotate or rotator.current is None:
            continue
        proxy = await rotator.rotate_now()
        if proxy is None:
            await rotator.clear_current()
            continue
        await add_log("AUTO_ROTATE", proxy=proxy.url, result=f"switched exit-> {proxy.url}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .database import db
    await db.connect()
    app.state.db = db
    # A previous run may have left the desktop pointing at our local proxy.
    # Reset it so the browser is not stuck when the server is not running.
    try:
        if _system_proxy_status().get("enabled"):
            _gs_set("org.gnome.system.proxy", "mode", "'none'")
    except Exception:
        pass
    await local_proxy.start()
    app.state.local_proxy = local_proxy
    global auto_rotate_task
    auto_rotate_task = asyncio.create_task(_auto_rotate_loop())
    yield
    auto_rotate_task.cancel()
    try:
        await auto_rotate_task
    except asyncio.CancelledError:
        pass
    await local_proxy.stop()
    await db.close()


# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="Proxy Rotator Dashboard v2", version="2.0", lifespan=lifespan)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ═══════════════════════════════════════════════════════════════════════════
#  API Endpoints
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    with open(os.path.join(os.path.dirname(__file__), "templates", "index.html")) as f:
        html = f.read()
    return HTMLResponse(html)


def _parse_ipinfo(d: dict) -> dict | None:
    if not d.get("ip"):
        return None
    return {
        "ip": d.get("ip"), "country": d.get("country"), "region": d.get("region"),
        "city": d.get("city"), "timezone": d.get("timezone"),
        "org": d.get("org"), "loc": d.get("loc"),
    }


def _parse_ipwho(d: dict) -> dict | None:
    if d.get("success") is False or not d.get("ip"):
        return None
    tz = (d.get("timezone") or {}).get("id")
    conn = d.get("connection") or {}
    loc = None
    if d.get("latitude") is not None and d.get("longitude") is not None:
        loc = f"{d['latitude']},{d['longitude']}"
    return {
        "ip": d.get("ip"), "country": d.get("country"), "region": d.get("region"),
        "city": d.get("city"), "timezone": tz,
        "org": conn.get("org") or conn.get("isp"), "loc": loc,
    }


def _parse_ipapi(d: dict) -> dict | None:
    if d.get("status") != "success" or not d.get("query"):
        return None
    return {
        "ip": d.get("query"), "country": d.get("country"),
        "region": d.get("regionName"), "city": d.get("city"),
        "timezone": d.get("timezone"), "org": d.get("org") or d.get("isp"),
        "loc": None,
    }


async def fetch_public_ip_info() -> tuple[dict | None, str | None]:
    """Query geo-IP providers through the active proxy, in fallback order."""
    providers = (
        ("ipinfo.io", "https://ipinfo.io/json", _parse_ipinfo),
        ("ipwho.is", "https://ipwho.is/", _parse_ipwho),
        (
            "ip-api.com",
            "http://ip-api.com/json/?fields=status,country,countryCode,regionName,"
            "city,timezone,isp,org,query",
            _parse_ipapi,
        ),
    )
    last_error = None
    for name, url, parser in providers:
        result = await request_with_proxy("GET", url, timeout=15)
        if result.get("status") == 200:
            try:
                raw = json.loads(result.get("body") or "{}")
            except json.JSONDecodeError:
                last_error = f"{name}: respons bukan JSON"
                continue
            parsed = parser(raw)
            if parsed:
                parsed["_source"] = name
                return parsed, None
            last_error = f"{name}: data lokasi tidak tersedia"
        else:
            last_error = result.get("error") or f"{name}: HTTP {result.get('status')}"
    return None, last_error


@app.get("/proof", response_class=HTMLResponse)
async def location_proof():
    """Bukti lokasi: query ipinfo.io lewat proxy yang sedang terhubung."""
    proxy = rotator.current
    data = None
    error = None

    if proxy is None:
        error = "Belum ada proxy yang terhubung. Buka Dashboard → pilih proxy → Connect."
    else:
        data, error = await fetch_public_ip_info()

    rows = ""
    if data:
        for label, key in (
            ("Exit IP", "ip"),
            ("Country", "country"),
            ("Region", "region"),
            ("City", "city"),
            ("Timezone", "timezone"),
            ("ISP / Org", "org"),
            ("Coordinates", "loc"),
        ):
            value = data.get(key)
            if value:
                rows += (f"<tr><th style='width:180px'>{html.escape(label)}</th>"
                         f"<td class='mono' style='font-weight:bold'>{html.escape(str(value))}</td></tr>")

    if data:
        body = f"""
          <div class="alert alert-success">
            <i class="fa fa-check-circle"></i>
            <strong>Proxy aktif.</strong> Traffic aplikasi keluar dengan IP
            <span class="mono">{html.escape(str(data.get('ip', '?')))}</span>
            — lokasi keluar: <strong>{html.escape(str(data.get('city') or '-'))},
            {html.escape(str(data.get('country') or '-'))}</strong>.
          </div>
          <table class="table table-striped table-bordered">{rows}</table>
          <p class="text-muted">Proxy: <span class="mono">{html.escape(proxy.url)}</span>
             &middot; country pool: {html.escape(proxy.country or '-')}
             &middot; latency: {html.escape(str(round(proxy.latency_ms or 0, 1)))} ms
             &middot; sumber geo: {html.escape(str(data.get('_source') or '-'))}
             &middot; dicek: {time.strftime('%Y-%m-%d %H:%M:%S')}</p>
        """
    elif error:
        body = f"""
          <div class="alert alert-warning">
            <i class="fa fa-exclamation-triangle"></i> {html.escape(error)}
          </div>
        """
        if proxy is not None:
            body += (f"<p class='text-muted'>Proxy terhubung: "
                     f"<span class='mono'>{html.escape(proxy.url)}</span></p>")
    else:
        body = "<div class='alert alert-info'>Tidak ada data.</div>"

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="id">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Location Proof — Proxy Rotator v2</title>
  <link href="/static/vendor/bootstrap/css/bootstrap.min.css" rel="stylesheet">
  <link href="/static/vendor/sb-admin-2/dist/css/sb-admin-2.min.css" rel="stylesheet">
  <link href="/static/vendor/font-awesome/css/font-awesome.min.css" rel="stylesheet">
  <link href="/static/css/app.css" rel="stylesheet">
</head>
<body>
  <nav class="navbar navbar-default navbar-static-top" style="margin-bottom:0">
    <div class="container">
      <a class="navbar-brand" href="/"><i class="fa fa-shield"></i> Proxy Rotator <small>v2.0</small></a>
      <ul class="nav navbar-top-links navbar-right">
        <li><a href="/"><i class="fa fa-dashboard fa-fw"></i> Dashboard</a></li>
      </ul>
    </div>
  </nav>
  <div class="container" style="margin-top: 24px;">
    <div class="row">
      <div class="col-lg-8 col-lg-offset-2">
        <div class="panel panel-primary">
          <div class="panel-heading">
            <i class="fa fa-globe"></i> Location Proof — lokasi keluar lewat proxy
          </div>
          <div class="panel-body">
            {body}
            <hr>
            <p class="text-muted" style="margin:0;">
              <i class="fa fa-info-circle"></i>
              Halaman ini menampilkan IP/lokasi keluar dari <strong>aplikasi ini</strong> saat memakai proxy
              yang terhubung. Browser kamu sendiri baru ikut berpindah lokasi kalau browser/OS di-set
              memakai proxy tersebut (misalnya isi HTTP proxy = IP:port proxy di atas).
            </p>
          </div>
        </div>
        <p><a href="/" class="btn btn-default"><i class="fa fa-arrow-left"></i> Kembali ke Dashboard</a></p>
      </div>
    </div>
  </div>
</body>
</html>""")


@app.get("/api/stats")
async def api_stats():
    return await get_stats()


@app.get("/api/proxies")
async def api_proxies(
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    alive_only: bool = False,
    source: str = Query("", description="Filter by source"),
    country: str = Query("", description="Filter by country"),
    protocol: str = Query("", description="Filter by protocol"),
    search: str = Query("", description="Search IP / port"),
    sort: str = Query("latency", description="latency|fail_count|created|country"),
    order: str = Query("asc", description="asc|desc"),
):
    proxies = await get_all(limit=limit, offset=offset, alive_only=alive_only,
                             source=source, country=country, protocol=protocol, search=search)
    reverse = order == "desc"
    if sort == "latency":
        proxies.sort(key=lambda p: p.latency_ms, reverse=reverse)
    elif sort == "fail_count":
        proxies.sort(key=lambda p: p.fail_count, reverse=reverse)
    elif sort == "country":
        proxies.sort(key=lambda p: p.country, reverse=reverse)
    else:
        proxies.sort(key=lambda p: p.created_at, reverse=reverse)
    return {"proxies": [p.__dict__ for p in proxies], "total": len(proxies)}


@app.post("/api/proxies/fetch")
async def api_fetch_proxies(limit: int = Query(500, ge=50, le=5000)):
    """Fetch free proxies from multiple sources concurrently."""
    result = await fetch_all(limit=limit)
    count = await upsert_proxies(result["proxies"])
    await add_log("fetch", result=f"fetched={result['stats'].get('total_unique', 0)} inserted={count}")
    return {"fetched": result["stats"]["total_unique"], "inserted": count, "sources": result["stats"]}


@app.post("/api/proxies")
async def api_add_proxies(proxies: list[AddProxyRequest]):
    """Bulk add proxies manually: [{ip, port, protocol, country, source}]."""
    inserted = 0
    for p in proxies:
        if not p.ip or not p.port:
            continue
        from .proxy_fetcher import ProxyRaw
        await upsert_proxies([ProxyRaw(
            ip=p.ip, port=int(p.port), protocol=p.protocol or "http",
            anonymity="unknown", country=p.country or "", source=p.source or "manual",
        )])
        inserted += 1
    await add_log("add_manual", result=f"inserted={inserted}")
    return {"inserted": inserted}


@app.post("/api/proxies/validate")
async def api_validate():
    """Validate all stored proxies (alive check + latency)."""
    return await bulk_validate(concurrency=25)


@app.get("/api/proxies/{proxy_id}/test")
async def api_test_proxy(proxy_id: int):
    proxy = await get_by_id(proxy_id)
    if not proxy:
        raise HTTPException(404, "Proxy not found")
    return await test_proxy(proxy)


@app.post("/api/proxies/test-request")
async def api_test_request(req: ProxyTestRequest):
    """Send a test request using the proxy rotator."""
    return await request_with_proxy(req.method, req.url)


@app.delete("/api/proxies/clear-dead")
async def api_clear_dead():
    from .database import db
    conn = await db.get()
    dead = await conn.execute("DELETE FROM proxies WHERE is_alive = 0")
    fails = await conn.execute("DELETE FROM proxies WHERE fail_count > 5")
    await conn.commit()
    count = (dead.rowcount or 0) + (fails.rowcount or 0)
    await add_log("clear_dead", result=f"removed={count}")
    return {"removed": count}


@app.delete("/api/proxies/clear-all")
async def api_clear_all():
    from .database import db
    conn = await db.get()
    await conn.execute("DELETE FROM proxies")
    await conn.commit()
    await add_log("clear_all", result="all proxies removed")
    return {"message": "All proxies cleared"}


@app.delete("/api/proxies/{proxy_id}")
async def api_delete_proxy(proxy_id: int):
    from .database import db
    conn = await db.get()
    await conn.execute("DELETE FROM proxies WHERE id = ?", (proxy_id,))
    await conn.commit()
    return {"deleted": proxy_id}


# ── Session (Connect / Disconnect) ───────────────────────────────────────────

@app.get("/api/local-proxy")
async def api_local_proxy():
    """Info local forward proxy untuk browser (127.0.0.1:8888)."""
    return local_proxy.info()


# ── System / browser proxy (GNOME gsettings) ────────────────────────────────

def _gs_get(schema: str, key: str) -> Optional[str]:
    if not shutil.which("gsettings"):
        return None
    try:
        proc = subprocess.run(
            ["gsettings", "get", schema, key],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _gs_set(schema: str, key: str, value: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["gsettings", "set", schema, key, value],
            capture_output=True, text=True, timeout=5,
        )
    except Exception as exc:
        return False, str(exc)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip()
    return True, ""


def _unquote(raw: Optional[str]) -> str:
    return (raw or "").strip().strip("'\"")


def _system_proxy_status() -> dict:
    mode = _unquote(_gs_get("org.gnome.system.proxy", "mode"))
    host = _unquote(_gs_get("org.gnome.system.proxy.http", "host"))
    port_raw = _unquote(_gs_get("org.gnome.system.proxy.http", "port"))
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        port = 0

    local = local_proxy.info()
    enabled = mode == "manual" and host == local["host"] and port == local["port"]
    return {
        "available": _gs_get("org.gnome.system.proxy", "mode") is not None,
        "mode": mode or None,
        "host": host or None,
        "port": port,
        "enabled": enabled,
        "local_proxy": local,
    }


@app.get("/api/system-proxy")
async def api_system_proxy_status():
    """Status proxy browser (system-wide, GNOME)."""
    return _system_proxy_status()


@app.post("/api/system-proxy")
async def api_set_system_proxy(req: SystemProxyRequest):
    """Nyalakan/matikan proxy browser (system-wide) ke local forward proxy."""
    if req.enabled:
        if not local_proxy.running:
            return {"ok": False, "error": "Local forward proxy tidak berjalan",
                    **_system_proxy_status()}
        local = local_proxy.info()
        steps = [
            ("org.gnome.system.proxy", "mode", "'manual'"),
            ("org.gnome.system.proxy.http", "host", f"'{local['host']}'"),
            ("org.gnome.system.proxy.http", "port", str(local["port"])),
            ("org.gnome.system.proxy.https", "host", f"'{local['host']}'"),
            ("org.gnome.system.proxy.https", "port", str(local["port"])),
        ]
        for schema, key, value in steps:
            ok, err = _gs_set(schema, key, value)
            if not ok:
                return {"ok": False, "error": f"{schema} {key}: {err}",
                        **_system_proxy_status()}
        await add_log("SYSTEM_PROXY", result=f"browser proxy ON {local['url']}")
        return {"ok": True, **_system_proxy_status()}

    ok, err = _gs_set("org.gnome.system.proxy", "mode", "'none'")
    if not ok:
        return {"ok": False, "error": err, **_system_proxy_status()}
    await add_log("SYSTEM_PROXY", result="browser proxy OFF")
    return {"ok": True, **_system_proxy_status()}


@app.post("/api/session/auto-rotate")
async def api_session_auto_rotate(req: AutoRotateRequest):
    """Toggle continuous proxy auto-rotation for the session."""
    enabled = await rotator.set_auto_rotate(req.enabled)
    await add_log("AUTO_ROTATE", result=f"enabled={enabled}")
    return {"ok": True, "enabled": enabled}


@app.get("/api/session/rotate")
async def api_session_rotate():
    """Manual rotate: move session proxy to another alive proxy."""
    proxy = await rotator.rotate_now()
    if proxy is None:
        return {"ok": False, "error": "No alive proxies"}
    rotator.last_exit_ip = None
    rotator.last_exit_country = None
    return {
        "ok": True,
        "proxy": {
            "id": proxy.id, "ip": proxy.ip, "port": proxy.port,
            "protocol": proxy.protocol, "country": proxy.country,
            "source": proxy.source, "latency_ms": proxy.latency_ms,
            "is_alive": proxy.is_alive,
        },
    }


@app.get("/api/session")
async def api_session():
    proxy = rotator.current
    connected = proxy is not None
    return {
        "connected": connected,
        "proxy": {
            "id": proxy.id, "ip": proxy.ip, "port": proxy.port,
            "protocol": proxy.protocol, "country": proxy.country,
            "source": proxy.source, "latency_ms": proxy.latency_ms,
            "is_alive": proxy.is_alive,
        } if proxy else None,
        "exit_ip": rotator.last_exit_ip,
        "country": rotator.last_exit_country or (proxy.country if proxy else None),
        "source": proxy.source if proxy else None,
        "latency_ms": proxy.latency_ms if proxy else None,
        "auto_rotate": rotator.auto_rotate,
    }


@app.post("/api/session/connect")
async def api_session_connect(req: SessionConnectRequest):
    """Connect manual session through chosen proxy. On success the
    server (and all rotator traffic) exits via that proxy → IP/location
    of the machine = IP of the connected proxy."""
    proxy = await rotator.set_current(req.proxy_id)
    if not proxy:
        return {"connected": False, "error": "Proxy not found", "status": 404}

    result = await request_with_proxy("GET", "https://api.ipify.org?format=text")
    if result.get("status") == 200:
        exit_ip = (result.get("body") or "").strip()
        rotator.last_exit_ip = exit_ip
        rotator.last_exit_country = proxy.country
        rotator.last_test_at = time.time()
        await add_log("CONNECT", proxy=proxy.url, target_ip=exit_ip,
                      result=f"status=200 latency={result.get('latency_ms')} exit_ip={exit_ip}")
        return {
            "connected": True,
            "ok": True,
            "proxy": result.get("proxy", proxy.url),
            "proxy_id": proxy.id,
            "exit_ip": exit_ip,
            "country": proxy.country,
            "latency_ms": result.get("latency_ms"),
            "status": 200,
        }
    else:
        await rotator.clear_current()
        await add_log("CONNECT_FAIL", proxy=proxy.url, target_ip="",
                      result=f"status={result.get('status')} error={result.get('error', '')}")
        return {
            "connected": False,
            "ok": False,
            "proxy": proxy.url,
            "proxy_id": proxy.id,
            "error": result.get("error", "Connection failed"),
            "status": result.get("status", 0),
        }


@app.post("/api/session/disconnect")
async def api_session_disconnect():
    await rotator.clear_current()
    await add_log("DISCONNECT", result="manual session released")
    return {"ok": True, "connected": False}


# ── Rotate / Logs ────────────────────────────────────────────────────────────

@app.get("/api/logs")
async def api_logs(limit: int = Query(100, ge=1, le=500)):
    return {"logs": await get_recent_logs(limit)}


@app.get("/api/rotate/test")
async def api_rotate_test(url: str = "https://api.ipify.org?format=json", method: str = "GET"):
    result = await request_with_proxy(method, url)
    return result


# ═══════════════════════════════════════════════════════════════════════════
#  CORS / Middleware
# ═══════════════════════════════════════════════════════════════════════════

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=False)
