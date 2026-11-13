#!/usr/bin/env python3
"""
ATV Daemon - UDP server that controls an Apple TV via pyatv.

Protocol (UTF-8 text over UDP):
  key <name> [action] - press a remote control key (up, down, left, right, select,
                    menu, home, play_pause, next, previous, volume_up, volume_down, etc.)
                    action: single (default), double, hold
  app <bundle_id> - launch an app by bundle ID
  text_set <text> - replace keyboard field contents
  text_append <text> - append to keyboard field contents
  text_clear      - clear keyboard field contents
  ping            - responds "pong" when connected to ATV, "connecting" otherwise
  disconnect      - drop the ATV connection

key, app and text_* connect first if needed, retrying for up to CONNECT_WAIT (20 s)
(e.g. while the ATV wakes), so the reply can take that long; still not connected
gives "error: <last connect error>".

Responses:
  ok              - command succeeded
  pong            - ATV connected and ready
  connecting      - ATV not yet connected (retry ping)
  error: <msg>    - command failed
"""

import asyncio
import inspect
import logging
from config import load_config
from pyatv.const import InputAction
from pyatv.storage.file_storage import FileStorage
from pyatv.interface import DeviceListener
import pyatv

_INPUT_ACTIONS = {
    "single": InputAction.SingleTap,
    "double": InputAction.DoubleTap,
    "hold":   InputAction.Hold,
}

# ── Configuration ─────────────────────────────────────────────────────────────

# Device, address and credentials come from ATVD_* environment variables (config.py has the defaults).
_CFG     = load_config()
ATV_ID   = _CFG["id"]
ATV_HOST = _CFG["host"]
UDP_HOST = _CFG["bind"]
UDP_PORT = _CFG["port"]
STORAGE  = _CFG["storage"]
HEALTH_INTERVAL = 5   # seconds between connection health checks
CONNECT_WAIT    = 20  # seconds a command waits for the ATV to connect before "error: ..."
CONNECT_RETRY   = 2   # seconds between connect attempts while a command waits
CONNECT_POLL    = 0.2 # seconds between checks while ping's background connect runs

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("atvdaemon")

async def _reachable(host: str, port: int = 7000, timeout: float = 2.0) -> bool:
    """Return True if host accepts a TCP connection on the given port."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        writer.close()
        return True
    except Exception:
        return False

# ── ATV Controller ────────────────────────────────────────────────────────────

class ATVController(DeviceListener):
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self._loop       = loop
        self._atv        = None
        self._lock       = asyncio.Lock()
        self._connecting = False
        self._storage    = FileStorage(STORAGE, loop)

    async def connect(self) -> None:
        if self._atv is not None:
            self._atv.close()
            self._atv = None

        log.info("Loading credentials from %s", STORAGE)
        await self._storage.load()

        log.info("Scanning for ATV %s at %s ...", ATV_ID, ATV_HOST)
        configs = await pyatv.scan(
            self._loop,
            hosts=[ATV_HOST],
            identifier=ATV_ID,
            storage=self._storage,
        )
        if not configs:
            raise RuntimeError(f"ATV not found at {ATV_HOST}")

        log.info("Connecting ...")
        self._atv = await pyatv.connect(configs[0], self._loop, storage=self._storage)
        self._atv.listener = self
        log.info("Connected to %s", configs[0].name)

    async def _ensure(self) -> None:
        """Connect if needed, retrying every CONNECT_RETRY s for up to CONNECT_WAIT s.

        A background connect already in flight (from ping) is waited on rather than
        doubled. Raises the last connect error (or a timeout) if still not connected.
        """
        if self._atv is not None:
            return
        deadline = self._loop.time() + CONNECT_WAIT
        last_exc = None
        while True:
            if self._connecting:
                await asyncio.sleep(CONNECT_POLL)
            else:
                self._connecting = True   # stops ping starting a second connect meanwhile
                try:
                    await self.connect()
                except Exception as exc:
                    last_exc = exc
                    log.warning("Connect failed: %s", exc)
                finally:
                    self._connecting = False
            if self._atv is not None:
                return
            remaining = deadline - self._loop.time()
            if remaining <= 0:
                raise last_exc or RuntimeError(f"ATV not connected after {CONNECT_WAIT} s")
            if not self._connecting:
                await asyncio.sleep(min(CONNECT_RETRY, remaining))

    async def press_key(self, key: str, action: InputAction = InputAction.SingleTap) -> None:
        async with self._lock:
            await self._ensure()
            rc = self._atv.remote_control
            fn = getattr(rc, key, None)
            if fn is None or not callable(fn):
                raise ValueError(f"Unknown key: {key!r}")
            if "action" in inspect.signature(fn).parameters:
                await fn(action=action)
            else:
                await fn()

    async def launch_app(self, bundle_id: str) -> None:
        async with self._lock:
            await self._ensure()
            await self._atv.apps.launch_app(bundle_id)

    async def text_set(self, text: str) -> None:
        async with self._lock:
            await self._ensure()
            await self._atv.keyboard.text_set(text)

    async def text_append(self, text: str) -> None:
        async with self._lock:
            await self._ensure()
            await self._atv.keyboard.text_append(text)

    async def text_clear(self) -> None:
        async with self._lock:
            await self._ensure()
            await self._atv.keyboard.text_clear()

    def connection_lost(self, exception: Exception) -> None:
        log.warning("ATV connection lost: %s", exception)
        self._atv = None

    def connection_closed(self) -> None:
        log.info("ATV connection closed")
        self._atv = None

    @property
    def connected(self) -> bool:
        return self._atv is not None

    def connect_bg(self) -> None:
        """Start a connection attempt in the background if not already connecting."""
        if self._atv is not None or self._connecting:
            return
        self._connecting = True
        asyncio.ensure_future(self._connect_bg())

    async def _connect_bg(self) -> None:
        try:
            await self.connect()
        except Exception as exc:
            log.warning("Background connect failed: %s", exc)
        finally:
            self._connecting = False

    def invalidate(self) -> None:
        """Mark connection as dead so the next command reconnects."""
        if self._atv is not None:
            try:
                self._atv.close()
            except Exception:
                pass
            self._atv = None

    async def health_loop(self) -> None:
        """Periodically probe the ATV to detect stale connections quickly."""
        while True:
            await asyncio.sleep(HEALTH_INTERVAL)
            if self._atv is None or self._connecting:
                continue
            if not await _reachable(ATV_HOST):
                log.warning("Health check: ATV not responding to ping, dropping connection")
                self.invalidate()

    def close(self) -> None:
        if self._atv is not None:
            self._atv.close()
            self._atv = None

# ── UDP Server ────────────────────────────────────────────────────────────────

class UDPServer(asyncio.DatagramProtocol):
    def __init__(self, controller: ATVController):
        self._ctrl      = controller
        self._transport = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self._transport = transport

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        message = data.decode("utf-8", errors="replace").strip()
        log.info("← %s  %r", addr, message)
        asyncio.ensure_future(self._handle(message, addr))

    async def _handle(self, message: str, addr: tuple) -> None:
        try:
            reply = await self._dispatch(message)
        except Exception as exc:
            log.error("Command failed: %s", exc)
            self._ctrl.invalidate()
            reply = f"error: {exc}"
        log.info("→ %s  %r", addr, reply)
        self._transport.sendto(reply.encode("utf-8"), addr)

    async def _dispatch(self, message: str) -> str:
        parts = message.split(None, 1)
        if not parts:
            return "error: empty command"

        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else None

        if cmd == "disconnect":
            self._ctrl.invalidate()
            return "ok"

        if cmd == "ping":
            if self._ctrl.connected:
                return "pong"
            self._ctrl.connect_bg()
            return "connecting"

        if cmd == "key":
            if not arg:
                return "error: key requires an argument"
            key_parts = arg.split(None, 1)
            key_name = key_parts[0]
            if len(key_parts) > 1:
                action_str = key_parts[1].strip().lower()
                action = _INPUT_ACTIONS.get(action_str)
                if action is None:
                    return f"error: unknown action {action_str!r} (use single, double, hold)"
            else:
                action = InputAction.SingleTap
            await self._ctrl.press_key(key_name, action)
            return "ok"

        if cmd == "app":
            if not arg:
                return "error: app requires an argument"
            await self._ctrl.launch_app(arg)
            return "ok"

        if cmd == "text_set":
            if arg is None:
                return "error: text_set requires an argument"
            await self._ctrl.text_set(arg)
            return "ok"

        if cmd == "text_append":
            if arg is None:
                return "error: text_append requires an argument"
            await self._ctrl.text_append(arg)
            return "ok"

        if cmd == "text_clear":
            await self._ctrl.text_clear()
            return "ok"

        return f"error: unknown command: {cmd!r}"

# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    loop = asyncio.get_event_loop()
    ctrl = ATVController(loop)
    log.info("Apple TV %s at %s, credentials %s", ATV_ID, ATV_HOST, STORAGE)

    try:
        await ctrl.connect()
    except Exception as exc:
        log.warning("Initial connection failed (%s) — will retry on first command", exc)

    transport, _ = await loop.create_datagram_endpoint(
        lambda: UDPServer(ctrl),
        local_addr=(UDP_HOST, UDP_PORT),
    )
    log.info("Listening on UDP %s:%d", UDP_HOST, UDP_PORT)
    health_task = asyncio.ensure_future(ctrl.health_loop())

    try:
        await asyncio.sleep(float("inf"))
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("Shutting down")
    finally:
        health_task.cancel()
        transport.close()
        ctrl.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
