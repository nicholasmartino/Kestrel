from __future__ import annotations

import asyncio
import subprocess
import time
from typing import Any

import aiohttp

from kestrel.logging import log_event


class OllamaManager:
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.2:3b",
        retry_attempts: int = 30,
        retry_interval: float = 1.0,
        health_check_timeout: float = 2.0,
        startup_delay: float = 0.5,
    ):
        self.base_url = base_url
        self.model = model
        self._retry_attempts = retry_attempts
        self._retry_interval = retry_interval
        self._health_check_timeout = health_check_timeout
        self._startup_delay = startup_delay

    async def ensure_running(self) -> bool:
        """Check if Ollama is running; attempt to start if not."""
        if await self._is_healthy():
            log_event("info", "Ollama is running")
            return True

        log_event("warning", "Ollama not responding, attempting to start...")
        started = await self._start_ollama()
        if not started:
            log_event("error", "Failed to start Ollama")
            return False

        # Wait for it to come up
        for attempt in range(self._retry_attempts):
            if await self._is_healthy():
                log_event("info", "Ollama started successfully")
                return True
            await asyncio.sleep(self._retry_interval)

        log_event("error", "Ollama did not become healthy in time")
        return False

    async def ensure_model(self) -> bool:
        """Ensure the required model is pulled."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/api/tags") as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    models = data.get("models", [])
                    names = [m.get("name", "") for m in models]
                    if any(self.model in n for n in names):
                        log_event("info", f"Model {self.model} is available")
                        return True
        except Exception as exc:
            log_event("error", f"Failed to list models: {exc}")
            return False

        log_event("info", f"Pulling model {self.model}...")
        try:
            proc = await asyncio.create_subprocess_exec(
                "ollama",
                "pull",
                self.model,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                log_event("info", f"Model {self.model} pulled successfully")
                return True
            else:
                log_event("error", f"Failed to pull model: {stderr.decode()}")
                return False
        except Exception as exc:
            log_event("error", f"Exception pulling model: {exc}")
            return False

    async def _is_healthy(self) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/api/tags", timeout=aiohttp.ClientTimeout(total=self._health_check_timeout)
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def _start_ollama(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ollama",
                "serve",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Give it a moment to fail fast if the binary is missing
            await asyncio.sleep(self._startup_delay)
            if proc.returncode is not None and proc.returncode != 0:
                return False
            return True
        except FileNotFoundError:
            log_event("error", "ollama binary not found in PATH")
            return False
        except Exception as exc:
            log_event("error", f"Failed to start ollama: {exc}")
            return False
