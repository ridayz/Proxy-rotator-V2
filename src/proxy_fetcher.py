"""Proxy fetcher — scrape free proxy lists from multiple sources."""

import re
import asyncio
import httpx
from dataclasses import dataclass
from typing import AsyncGenerator


@dataclass
class ProxyRaw:
    ip: str
    port: int
    protocol: str = "http"
    anonymity: str = "unknown"
    country: str = ""
    source: str = ""


# ── Sources ─────────────────────────────────────────────────────────────────

SOURCES = {
    "free-proxy-list.net": "https://free-proxy-list.net/",
    "sslproxies.org":      "https://www.sslproxies.org/",
    "proxylist.run":       "https://proxylist.run/",
    "spys.ru":             "https://spys.me/proxy-list.html",
    "openproxy.space":     "https://openproxy.space/list",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

IP_PORT_RE = re.compile(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):?(\d{2,5})")
IP_ONLY_RE = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")# ── Scrapers ────────────────────────────────────────────────────────────────

async def fetch_free_proxy_list(client: httpx.AsyncClient) -> list[ProxyRaw]:
    """Parse free-proxy-list.net table."""
    proxies = []
    try:
        r = await client.get(SOURCES["free-proxy-list.net"], headers=HEADERS, timeout=15)
        r.raise_for_status()
        rows = re.findall(r"<tr[^>]*>.*?</tr>", r.text, re.DOTALL)
        for row in rows[1:]:  # skip header row
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            if len(cells) >= 8:
                ip = cells[0].strip()
                port_str = cells[1].strip()
                if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", ip) and port_str.isdigit():
                    proxies.append(ProxyRaw(
                        ip=ip,
                        port=int(port_str),
                        anonymity=cells[4].strip(),
                        country=cells[2].strip(),
                        source="free-proxy-list.net",
                    ))
    except Exception as e:
        print(f"  [free-proxy-list] error: {e}")
    return proxies


async def fetch_sslproxies(client: httpx.AsyncClient) -> list[ProxyRaw]:
    """Parse sslproxies.org table."""
    proxies = []
    try:
        r = await client.get(SOURCES["sslproxies.org"], headers=HEADERS, timeout=15)
        r.raise_for_status()
        rows = re.findall(r"<tr[^>]*>.*?</tr>", r.text, re.DOTALL)
        for row in rows[1:]:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            if len(cells) >= 3:
                ip = cells[0].strip()
                port_str = cells[1].strip()
                if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", ip) and port_str.isdigit():
                    proxies.append(ProxyRaw(
                        ip=ip,
                        port=int(port_str),
                        anonymity=cells[4].strip() if len(cells) > 4 else "anonymous",
                        country=cells[2].strip() if len(cells) > 2 else "",
                        source="sslproxies.org",
                    ))
    except Exception as e:
        print(f"  [sslproxies] error: {e}")
    return proxies


async def fetch_proxylist_run(client: httpx.AsyncClient) -> list[ProxyRaw]:
    """Parse proxylist.run JSON."""
    proxies = []
    try:
        r = await client.get("https://proxylist.run/http", headers=HEADERS, timeout=15)
        r.raise_for_status()
        import json
        data = r.json()
        for item in data.get("proxies", [])[:200]:
            proxies.append(ProxyRaw(
                ip=item.get("ip", ""),
                port=int(item.get("port", 0)),
                protocol=item.get("protocol", "http"),
                anonymity=item.get("anonymity", "unknown"),
                country=item.get("country", ""),
                source="proxylist.run",
            ))
    except Exception as e:
        print(f"  [proxylist.run] error: {e}")
    return proxies


async def fetch_spys_ru(client: httpx.AsyncClient) -> list[ProxyRaw]:
    """Parse spys.me proxy table."""
    proxies = []
    try:
        r = await client.get(SOURCES["spys.ru"], headers=HEADERS, timeout=15)
        r.raise_for_status()
        rows = re.findall(r"<tr[^>]*>.*?</tr>", r.text, re.DOTALL)
        for row in rows[2:]:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            if len(cells) >= 4:
                ip = cells[0].strip()
                port_str = cells[1].strip()
                if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", ip) and port_str.isdigit():
                    proxies.append(ProxyRaw(
                        ip=ip,
                        port=int(port_str),
                        anonymity=cells[2].strip()[:20] if len(cells) > 2 else "anonymous",
                        country=cells[3].strip() if len(cells) > 3 else "",
                        source="spys.ru",
                    ))
    except Exception as e:
        print(f"  [spys.ru] error: {e}")
    return proxies


async def fetch_openproxy(client: httpx.AsyncClient) -> list[ProxyRaw]:
    """Parse openproxy.space JSON."""
    proxies = []
    try:
        r = await client.get(SOURCES["openproxy.space"], headers=HEADERS, timeout=15)
        r.raise_for_status()
        import json
        data = r.json()
        for item in (data if isinstance(data, list) else data.get("proxies", []))[:200]:
            ip = item.get("ip", "")
            port = item.get("port", 0)
            if ip and port:
                proxies.append(ProxyRaw(
                    ip=ip,
                    port=int(port),
                    protocol=item.get("protocol", "http"),
                    anonymity=item.get("anonymity", "unknown"),
                    country=item.get("country", ""),
                    source="openproxy.space",
                ))
    except Exception as e:
        print(f"  [openproxy.space] error: {e}")
    return proxies


FETCHERS = {
    "free-proxy-list.net": fetch_free_proxy_list,
    "sslproxies.org":      fetch_sslproxies,
    "proxylist.run":       fetch_proxylist_run,
    "spys.ru":             fetch_spys_ru,
    "openproxy.space":     fetch_openproxy,
}


# ── Main API ────────────────────────────────────────────────────────────────

async def fetch_all(limit: int = 500) -> dict:
    """Fetch from all sources concurrently. Returns stats + raw list."""
    all_proxies: list[ProxyRaw] = []
    stats = {}

    async with httpx.AsyncClient(
        headers=HEADERS, timeout=15, follow_redirects=True
    ) as client:
        tasks = [
            asyncio.create_task(fn(client))
            for fn in FETCHERS.values()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, err in enumerate(results):
            name = list(FETCHERS.keys())[i]
            if isinstance(err, Exception):
                stats[name] = {"fetched": 0, "error": str(err)}
            else:
                stats[name] = {"fetched": len(err)}
                all_proxies.extend(err)

    # Deduplicate by (ip, port, protocol)
    seen = set()
    unique = []
    for p in all_proxies:
        key = (p.ip, p.port, p.protocol)
        if key not in seen and p.ip and p.port:
            seen.add(key)
            unique.append(p)

    stats["total_unique"] = len(unique)
    return {"stats": stats, "proxies": unique[:limit]}
