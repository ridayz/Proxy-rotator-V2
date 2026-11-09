"""Local forward proxy — lets the user's browser go through the connected proxy.

    Browser -> 127.0.0.1:8888 -> rotator.current (upstream) -> internet

Supports CONNECT (HTTPS) and plain HTTP. The upstream is whatever proxy is
currently connected in the dashboard, so switching proxy there switches the
browser's exit location too.
"""

import asyncio
import logging
import os

log = logging.getLogger("proxy_rotator.local_proxy")

CHUNK = 65536
HEAD_TIMEOUT = 20
CONNECT_TIMEOUT = 10


async def _read_head(reader: asyncio.StreamReader) -> bytes | None:
    try:
        return await reader.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        return None


def _is_connect(head: bytes) -> bool:
    return head.split(b" ", 1)[0].strip().upper() == b"CONNECT"


def _rewrite_request(head: bytes) -> bytes:
    """Browsers send absolute-form to proxies; handle origin-form as a fallback."""
    try:
        text = head.decode("latin-1")
    except Exception:
        return head

    lines = text.split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) == 3 and parts[1].startswith("/"):
        host = ""
        for line in lines[1:]:
            if line.lower().startswith("host:"):
                host = line.split(":", 1)[1].strip()
                break
        if host:
            lines[0] = f"{parts[0]} http://{host}{parts[1]} {parts[2]}"

    out = [lines[0]]
    for line in lines[1:]:
        low = line.lower()
        if low.startswith("proxy-connection:"):
            out.append("Connection: keep-alive")
            continue
        if low.startswith("proxy-authorization:"):
            continue
        out.append(line)
    return "\r\n".join(out).encode("latin-1")


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(CHUNK)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        pass


def _bad_gateway(writer: asyncio.StreamWriter, reason: str) -> None:
    body = reason.encode("utf-8", "replace")
    writer.write(
        b"HTTP/1.1 502 Bad Gateway\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"Connection: close\r\n\r\n" + body
    )


class LocalForwardProxy:
    def __init__(self) -> None:
        self.host = os.getenv("LOCAL_PROXY_HOST", "127.0.0.1")
        self.port = int(os.getenv("LOCAL_PROXY_PORT", "8888"))
        self._server: asyncio.AbstractServer | None = None

    @property
    def running(self) -> bool:
        return self._server is not None

    def info(self) -> dict:
        from .proxy_manager import rotator

        upstream = rotator.current
        return {
            "running": self.running,
            "host": self.host,
            "port": self.port,
            "url": f"http://{self.host}:{self.port}",
            "connected": upstream is not None,
            "upstream": upstream.url if upstream else None,
        }

    async def start(self) -> None:
        if self._server is not None:
            return
        try:
            self._server = await asyncio.start_server(
                self._handle, self.host, self.port
            )
            log.info("Local forward proxy listening on %s:%s", self.host, self.port)
        except OSError as exc:
            log.warning("Local forward proxy disabled: %s", exc)
            self._server = None

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        from .proxy_manager import rotator

        upstream = rotator.current
        if upstream is None:
            _bad_gateway(writer, "No proxy connected in Proxy Rotator dashboard")
            try:
                await writer.drain()
            except Exception:
                pass
            writer.close()
            return

        up_reader = up_writer = None
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(upstream.ip, upstream.port),
                timeout=CONNECT_TIMEOUT,
            )
            head = await asyncio.wait_for(_read_head(reader), timeout=HEAD_TIMEOUT)
            if not head:
                return

            if _is_connect(head):
                up_writer.write(head)
                await up_writer.drain()
                resp = await asyncio.wait_for(
                    up_reader.readuntil(b"\r\n\r\n"), timeout=HEAD_TIMEOUT
                )
                status = b""
                if resp.startswith(b"HTTP/"):
                    parts = resp.split(b" ", 2)
                    status = parts[1] if len(parts) > 1 else b""
                if not status.startswith(b"2"):
                    _bad_gateway(writer, f"Upstream refused CONNECT ({status.decode('latin-1')})")
                    await writer.drain()
                    return
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
            else:
                up_writer.write(_rewrite_request(head))
                await up_writer.drain()

            tasks = [
                asyncio.create_task(_pipe(reader, up_writer)),
                asyncio.create_task(_pipe(up_reader, writer)),
            ]
            _, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - log, never crash the server
            log.debug("local proxy error: %s", exc)
            try:
                _bad_gateway(writer, f"Proxy error: {exc}")
                await writer.drain()
            except Exception:
                pass
        finally:
            for target in (up_writer, writer):
                if target is not None:
                    try:
                        target.close()
                    except Exception:
                        pass


local_proxy = LocalForwardProxy()
