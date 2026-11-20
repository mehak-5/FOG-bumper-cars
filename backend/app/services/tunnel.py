"""Tunnel manager: subprocess management, health polling, auto-restart, and secret masking."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from typing import Any

import httpx

from backend.app.core.events import EventBus, Events

log = logging.getLogger("services.tunnel")

CLOUDFLARE_URL_REGEX = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
NGROK_URL_REGEX = re.compile(r"https://[a-zA-Z0-9-]+\.(?:ngrok-free\.app|ngrok\.io)")

TOKEN_MASK_REGEX = re.compile(r"(--token\s+)([^\s]+)")
AUTHTOKEN_MASK_REGEX = re.compile(r"(--authtoken\s+)([^\s]+)")
JWT_MASK_REGEX = re.compile(r"eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}")


def mask_secrets(text: str) -> str:
    """Mask tokens and secrets in log messages and command lines."""
    text = TOKEN_MASK_REGEX.sub(r"\1[REDACTED]", text)
    text = AUTHTOKEN_MASK_REGEX.sub(r"\1[REDACTED]", text)
    text = JWT_MASK_REGEX.sub("[REDACTED_JWT]", text)
    return text


class TunnelManager:
    def __init__(
        self,
        bus: EventBus,
        public_port: int = 8001,
        *,
        provider: str = "cloudflared",
        binary_path: str = "cloudflared",
        token: str | None = None,
        public_url: str | None = None,
        poll_interval: float = 30.0,
        auto_restart: bool = True,
        spawn_fn: Callable[..., Any] | None = None,
        health_check_fn: Callable[[str], Any] | None = None,
    ) -> None:
        self.bus = bus
        self.public_port = public_port
        self.provider = provider
        if binary_path == "cloudflared" and provider != "cloudflared":
            self.binary_path = provider
        else:
            self.binary_path = binary_path
        self.token = token
        self.public_url = public_url
        self.poll_interval = poll_interval
        self.auto_restart = auto_restart
        self._spawn_fn = spawn_fn
        self._health_check_fn = health_check_fn

        self.is_up: bool = False
        self._process: Any = None
        self._poll_task: asyncio.Task[None] | None = None
        self._read_task: asyncio.Task[None] | None = None
        self._stopped: bool = True
        self._lock = asyncio.Lock()

    def status(self) -> dict[str, Any]:
        return {
            "up": self.is_up,
            "url": self.public_url if self.is_up else None,
            "provider": self.provider,
        }

    def build_command(self) -> list[str]:
        target_url = f"http://127.0.0.1:{self.public_port}"
        if self.provider == "cloudflared":
            if self.token:
                return [self.binary_path, "tunnel", "run", "--token", self.token]
            return [self.binary_path, "tunnel", "--url", target_url]
        if self.provider == "ngrok":
            cmd = [self.binary_path, "http", str(self.public_port)]
            if self.token:
                cmd.extend(["--authtoken", self.token])
            return cmd
        return [self.binary_path, target_url]

    async def start(self) -> None:
        async with self._lock:
            if not self._stopped:
                return
            self._stopped = False
            await self._start_process()
            self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        async with self._lock:
            self._stopped = True
            if self._poll_task:
                self._poll_task.cancel()
                self._poll_task = None
            if self._read_task:
                self._read_task.cancel()
                self._read_task = None
            await self._terminate_process()
            if self.is_up:
                self.is_up = False
                await self.bus.publish(Events.TUNNEL_DOWN, {"error": "Tunnel stopped"})

    async def restart(self) -> None:
        async with self._lock:
            log.info("Restarting tunnel...")
            if self._read_task:
                self._read_task.cancel()
                self._read_task = None
            await self._terminate_process()
            self.is_up = False
            await self.bus.publish(Events.TUNNEL_DOWN, {"error": "Tunnel restarting"})
            if not self._stopped:
                await self._start_process()

    async def _start_process(self) -> None:
        cmd = self.build_command()
        masked_cmd = [mask_secrets(arg) for arg in cmd]
        log.info("Starting tunnel process: %s", " ".join(masked_cmd))

        try:
            if self._spawn_fn:
                self._process = await self._spawn_fn(cmd)
            else:
                self._process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            self._read_task = asyncio.create_task(self._monitor_output())
        except Exception as err:
            log.error("Failed to start tunnel: %s", err)
            self.is_up = False
            await self.bus.publish(Events.TUNNEL_DOWN, {"error": str(err)})

    async def _terminate_process(self) -> None:
        proc = self._process
        self._process = None
        if proc is None:
            return
        try:
            if hasattr(proc, "terminate"):
                proc.terminate()
            if hasattr(proc, "wait"):
                await asyncio.wait_for(proc.wait(), timeout=3.0)
        except Exception:
            if hasattr(proc, "kill"):
                proc.kill()

    async def _monitor_output(self) -> None:
        proc = self._process
        if not proc:
            return

        async def read_stream(stream: Any) -> None:
            while True:
                line_bytes = await stream.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                masked = mask_secrets(line)
                log.debug("Tunnel: %s", masked)

                # Look for public URL if not already fixed
                if not self.public_url:
                    match = CLOUDFLARE_URL_REGEX.search(line) or NGROK_URL_REGEX.search(line)
                    if match:
                        found_url = match.group(0)
                        log.info("Tunnel public URL detected: %s", found_url)
                        self.public_url = found_url
                        # Trigger immediate health check
                        asyncio.create_task(self._check_health_once())

        tasks = []
        if getattr(proc, "stdout", None):
            tasks.append(asyncio.create_task(read_stream(proc.stdout)))
        if getattr(proc, "stderr", None):
            tasks.append(asyncio.create_task(read_stream(proc.stderr)))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # Process exited
        if hasattr(proc, "wait"):
            exit_code = await proc.wait()
            log.warning("Tunnel process exited with code %s", exit_code)

        if not self._stopped and self.auto_restart:
            log.info("Tunnel auto-restart scheduled in 5s...")
            await asyncio.sleep(5.0)
            if not self._stopped:
                await self.restart()

    async def _check_health_once(self) -> bool:
        if not self.public_url:
            return False

        url = f"{self.public_url.rstrip('/')}/healthz"
        healthy = False

        if self._health_check_fn:
            try:
                res = self._health_check_fn(url)
                if asyncio.iscoroutine(res):
                    healthy = bool(await res)
                else:
                    healthy = bool(res)
            except Exception as err:
                log.debug("Tunnel custom health check failed: %s", err)
                healthy = False
        else:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(url)
                    healthy = resp.status_code == 200
            except Exception as err:
                log.debug("Tunnel HTTP health check failed: %s", err)
                healthy = False

        if healthy and not self.is_up:
            self.is_up = True
            log.info("Tunnel is UP at %s", self.public_url)
            await self.bus.publish(Events.TUNNEL_UP, {"url": self.public_url})
        elif not healthy and self.is_up:
            self.is_up = False
            log.warning("Tunnel is DOWN (health check failed)")
            await self.bus.publish(Events.TUNNEL_DOWN, {"error": "Health check failed"})

        return healthy

    async def _poll_loop(self) -> None:
        # Initial check
        await asyncio.sleep(1.0)
        while not self._stopped:
            try:
                healthy = await self._check_health_once()
                if not healthy and self.auto_restart and not self._stopped:
                    # If health fails while running with a known URL, check if restart needed
                    pass
            except asyncio.CancelledError:
                break
            except Exception as err:
                log.debug("Error in tunnel poll loop: %s", err)

            try:
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
