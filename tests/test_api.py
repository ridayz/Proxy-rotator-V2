"""Proxy Rotator v2 — tests (no network, temp SQLite, ASGI in-process).

Run from the project root:

    /tmp/proxy_venv/bin/python -m unittest discover -s tests -v
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from src import database
from src.local_proxy import _is_connect, _rewrite_request
from src.main import app
from src.proxy_fetcher import ProxyRaw
from src.proxy_manager import get_all, rotator, upsert_proxies


class BaseAPITest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        database.DB_PATH = Path(self._tmp.name) / "test.db"
        if database.db._conn is not None:
            await database.db.close()
        database.db._conn = None
        await database.db.connect()

        # Fresh rotator state per test (new event loop -> new lock)
        rotator._lock = asyncio.Lock()
        rotator.current = None
        rotator._pool = []
        rotator._pool_loaded = False
        rotator.last_exit_ip = None
        rotator.last_exit_country = None

        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await database.db.close()
        database.db._conn = None
        self._tmp.cleanup()

    async def seed(self):
        """3 proxies: VN alive 123ms, SG alive 456ms, ID dead."""
        await upsert_proxies([
            ProxyRaw("10.0.0.1", 8080, country="VN", source="test"),
            ProxyRaw("10.0.0.2", 8080, country="SG", source="test"),
            ProxyRaw("10.0.0.3", 8080, country="ID", source="other"),
        ])
        conn = await database.db.get()
        await conn.execute(
            "UPDATE proxies SET is_alive = 1, latency_ms = ? WHERE ip = ?",
            (123.0, "10.0.0.1"),
        )
        await conn.execute(
            "UPDATE proxies SET is_alive = 1, latency_ms = ? WHERE ip = ?",
            (456.0, "10.0.0.2"),
        )
        await conn.commit()
        return await get_all(limit=10)

    async def proxy_id(self, ip: str) -> int:
        for p in await get_all(limit=10):
            if p.ip == ip:
                return p.id
        raise AssertionError(f"proxy {ip} not seeded")


class DashboardTests(BaseAPITest):
    async def test_root_serves_sb_admin_dashboard(self):
        r = await self.client.get("/")
        self.assertEqual(r.status_code, 200)
        html = r.text
        self.assertIn('id="side-menu"', html)
        self.assertIn('id="proxyTable"', html)
        self.assertIn("/static/js/app.js", html)
        # No broken metisMenu includes (removed after JS TypeError)
        self.assertNotIn("metisMenu.min.js", html)

    async def test_static_assets_served(self):
        for path in (
            "/static/css/app.css",
            "/static/js/app.js",
            "/static/vendor/bootstrap/css/bootstrap.min.css",
            "/static/vendor/jquery/jquery.min.js",
        ):
            r = await self.client.get(path)
            self.assertEqual(r.status_code, 200, path)

    async def test_proof_page_without_session_explains(self):
        r = await self.client.get("/proof")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Belum ada proxy yang terhubung", r.text)
        self.assertIn("Location Proof", r.text)

    async def test_local_proxy_info_endpoint(self):
        r = await self.client.get("/api/local-proxy")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["host"], "127.0.0.1")
        self.assertEqual(data["port"], 8888)
        self.assertEqual(data["url"], "http://127.0.0.1:8888")
        # Lifespan is not run by ASGITransport, so the listener is not started here.
        self.assertFalse(data["running"])
        self.assertFalse(data["connected"])


class SystemProxyTests(BaseAPITest):
    async def test_status_endpoint_shape(self):
        r = await self.client.get("/api/system-proxy")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        for key in ("available", "mode", "host", "port", "enabled", "local_proxy"):
            self.assertIn(key, data)

    async def test_enable_fails_when_local_proxy_not_running(self):
        # ASGITransport does not run lifespan, so the listener is not started.
        r = await self.client.post("/api/system-proxy", json={"enabled": True})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertFalse(data["ok"])
        self.assertIn("Local forward proxy", data["error"])


class LocalProxyHelperTests(unittest.TestCase):
    def test_detects_connect(self):
        self.assertTrue(_is_connect(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n"))
        self.assertFalse(_is_connect(b"GET http://example.com/ HTTP/1.1\r\n\r\n"))

    def test_origin_form_becomes_absolute(self):
        head = (b"GET /path HTTP/1.1\r\nHost: example.com\r\n"
                b"Proxy-Connection: keep-alive\r\n\r\n")
        out = _rewrite_request(head)
        self.assertIn(b"GET http://example.com/path HTTP/1.1", out)
        self.assertIn(b"Connection: keep-alive", out)
        self.assertNotIn(b"Proxy-Connection", out)

    def test_absolute_form_is_kept(self):
        head = (b"GET http://example.com/x HTTP/1.1\r\nHost: example.com\r\n"
                b"Proxy-Authorization: Basic abc\r\n\r\n")
        out = _rewrite_request(head)
        self.assertIn(b"GET http://example.com/x HTTP/1.1", out)
        self.assertNotIn(b"Proxy-Authorization", out)


class ProxiesAPITests(BaseAPITest):
    async def test_stats_empty(self):
        r = await self.client.get("/api/stats")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["alive"], 0)

    async def test_stats_after_seed(self):
        await self.seed()
        data = (await self.client.get("/api/stats")).json()
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["alive"], 2)
        self.assertEqual(data["dead"], 1)

    async def test_alive_only_filter(self):
        await self.seed()
        data = (await self.client.get("/api/proxies?alive_only=true")).json()
        ips = {p["ip"] for p in data["proxies"]}
        self.assertEqual(ips, {"10.0.0.1", "10.0.0.2"})

    async def test_country_and_source_filters(self):
        await self.seed()
        vn = (await self.client.get("/api/proxies?country=VN")).json()["proxies"]
        self.assertEqual([p["ip"] for p in vn], ["10.0.0.1"])

        other = (await self.client.get("/api/proxies?source=other")).json()["proxies"]
        self.assertEqual([p["ip"] for p in other], ["10.0.0.3"])

    async def test_search_filter(self):
        await self.seed()
        data = (await self.client.get("/api/proxies?search=10.0.0.2")).json()
        self.assertEqual([p["ip"] for p in data["proxies"]], ["10.0.0.2"])

    async def test_sort_by_latency(self):
        await self.seed()
        data = (await self.client.get("/api/proxies?sort=latency&order=asc")).json()
        latencies = [p["latency_ms"] for p in data["proxies"]]
        self.assertEqual(latencies, sorted(latencies))

    async def test_delete_proxy(self):
        await self.seed()
        pid = await self.proxy_id("10.0.0.3")
        r = await self.client.delete(f"/api/proxies/{pid}")
        self.assertEqual(r.status_code, 200)
        remaining = (await self.client.get("/api/proxies")).json()["proxies"]
        self.assertNotIn("10.0.0.3", {p["ip"] for p in remaining})

    async def test_clear_dead_removes_only_dead(self):
        await self.seed()
        r = await self.client.delete("/api/proxies/clear-dead")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["removed"], 1)
        remaining = (await self.client.get("/api/proxies")).json()["proxies"]
        self.assertEqual({p["ip"] for p in remaining}, {"10.0.0.1", "10.0.0.2"})

    async def test_clear_all(self):
        await self.seed()
        r = await self.client.delete("/api/proxies/clear-all")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((await self.client.get("/api/stats")).json()["total"], 0)


class SessionAPITests(BaseAPITest):
    async def test_session_starts_disconnected(self):
        data = (await self.client.get("/api/session")).json()
        self.assertFalse(data["connected"])
        self.assertIsNone(data["proxy"])
        self.assertIsNone(data["exit_ip"])

    async def test_connect_sets_exit_ip_and_location(self):
        await self.seed()
        pid = await self.proxy_id("10.0.0.1")
        fake = AsyncMock(return_value={
            "status": 200, "body": "203.0.113.7",
            "proxy": "http://10.0.0.1:8080", "proxy_id": pid,
            "latency_ms": 123.4,
        })
        with patch("src.main.request_with_proxy", new=fake):
            r = await self.client.post("/api/session/connect", json={"proxy_id": pid})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["connected"])
        self.assertEqual(data["exit_ip"], "203.0.113.7")
        self.assertEqual(data["country"], "VN")

        session = (await self.client.get("/api/session")).json()
        self.assertTrue(session["connected"])
        self.assertEqual(session["proxy"]["ip"], "10.0.0.1")
        self.assertEqual(session["exit_ip"], "203.0.113.7")
        self.assertEqual(session["country"], "VN")

    async def test_connect_unknown_proxy_returns_not_connected(self):
        r = await self.client.post("/api/session/connect", json={"proxy_id": 999999})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertFalse(data["connected"])
        self.assertEqual(data["status"], 404)
        self.assertIsNone(rotator.current)

    async def test_connect_failure_clears_session(self):
        await self.seed()
        pid = await self.proxy_id("10.0.0.2")
        fake = AsyncMock(return_value={"status": 0, "error": "timeout"})
        with patch("src.main.request_with_proxy", new=fake):
            data = (await self.client.post(
                "/api/session/connect", json={"proxy_id": pid}
            )).json()
        self.assertFalse(data["connected"])
        self.assertEqual(data["status"], 0)
        self.assertIsNone(rotator.current)
        self.assertIsNone((await self.client.get("/api/session")).json()["proxy"])

    async def test_disconnect_clears_session(self):
        await self.seed()
        pid = await self.proxy_id("10.0.0.1")
        fake = AsyncMock(return_value={
            "status": 200, "body": "203.0.113.7", "proxy": "http://10.0.0.1:8080",
        })
        with patch("src.main.request_with_proxy", new=fake):
            await self.client.post("/api/session/connect", json={"proxy_id": pid})
        self.assertIsNotNone(rotator.current)

        r = await self.client.post("/api/session/disconnect")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["connected"])
        session = (await self.client.get("/api/session")).json()
        self.assertFalse(session["connected"])
        self.assertIsNone(session["exit_ip"])


    async def test_auto_rotate_toggle(self):
        await self.seed()
        r = await self.client.post("/api/session/auto-rotate", json={"enabled": True})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["enabled"])
        self.assertTrue(rotator.auto_rotate)

        r2 = await self.client.post("/api/session/auto-rotate", json={"enabled": False})
        self.assertEqual(r2.status_code, 200)
        self.assertFalse(r2.json()["enabled"])
        self.assertFalse(rotator.auto_rotate)

    async def test_session_rotate_moves_to_next_alive(self):
        await self.seed()
        pid_a = await self.proxy_id("10.0.0.1")
        await rotator.set_current(pid_a)
        result = await self.client.get("/api/session/rotate")
        self.assertEqual(result.status_code, 200)
        data = result.json()
        self.assertTrue(data["ok"])
        self.assertNotEqual(data["proxy"]["id"], pid_a)
        self.assertEqual(rotator.current.id, data["proxy"]["id"])


class RotatorTests(BaseAPITest):
    async def test_pool_only_contains_alive(self):
        await self.seed()
        await rotator.refresh_pool()
        self.assertEqual({p.ip for p in rotator._pool}, {"10.0.0.1", "10.0.0.2"})

    async def test_round_robin_cycles_through_pool(self):
        await self.seed()
        await rotator.refresh_pool()
        first = await rotator.get_next()
        second = await rotator.get_next()
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first.id, second.id)

    async def test_session_proxy_takes_priority(self):
        await self.seed()
        await rotator.refresh_pool()
        pid = await self.proxy_id("10.0.0.2")
        await rotator.set_current(pid)
        for _ in range(3):
            proxy = await rotator.get_next()
            self.assertEqual(proxy.id, pid)

    async def test_clear_current_releases_session(self):
        await self.seed()
        pid = await self.proxy_id("10.0.0.1")
        await rotator.set_current(pid)
        rotator.last_exit_ip = "1.2.3.4"
        await rotator.clear_current()
        self.assertIsNone(rotator.current)
        self.assertIsNone(rotator.last_exit_ip)


class LogsAPITests(BaseAPITest):
    async def test_logs_recorded_by_actions(self):
        await self.seed()
        pid = await self.proxy_id("10.0.0.1")
        fake = AsyncMock(return_value={
            "status": 200, "body": "203.0.113.7", "proxy": "http://10.0.0.1:8080",
        })
        with patch("src.main.request_with_proxy", new=fake):
            await self.client.post("/api/session/connect", json={"proxy_id": pid})
        await self.client.post("/api/session/disconnect")

        logs = (await self.client.get("/api/logs?limit=10")).json()["logs"]
        actions = [l["action"] for l in logs]
        self.assertIn("CONNECT", actions)
        self.assertIn("DISCONNECT", actions)

    async def test_logs_limit_validation(self):
        r = await self.client.get("/api/logs?limit=0")
        self.assertEqual(r.status_code, 422)


class ValidationTests(BaseAPITest):
    async def test_validate_updates_db_without_network(self):
        """bulk_validate with a patched validate_proxy touches DB bookkeeping."""
        await self.seed()

        async def fake_validate(proxy, timeout=8.0):
            alive = proxy.country == "VN"
            # Mirrors validate_proxy(): failed checks report latency 0.0
            return alive, 99.0 if alive else 0.0

        with patch("src.proxy_manager.validate_proxy", new=fake_validate):
            from src.proxy_manager import bulk_validate
            result = await bulk_validate(concurrency=2)

        self.assertEqual(result["validated"], 3)
        conn = await database.db.get()
        async with conn.execute(
            "SELECT ip, is_alive, latency_ms FROM proxies ORDER BY ip"
        ) as cur:
            rows = await cur.fetchall()
        state = {row[0]: (row[1], row[2]) for row in rows}
        self.assertEqual(state["10.0.0.1"], (1, 99.0))
        self.assertEqual(state["10.0.0.2"], (0, 0.0))
        self.assertEqual(state["10.0.0.3"], (0, 0.0))


if __name__ == "__main__":
    unittest.main()
