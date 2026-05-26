from __future__ import annotations

from urllib.parse import urlparse

import aiohttp
from playwright.async_api import BrowserContext

from kestrel.auth_providers.base import AuthProvider
from kestrel.logging import log_event


class ClerkAuthProvider(AuthProvider):
    def __init__(self, secret_key: str, identifier: str, password: str):
        self.secret_key = secret_key
        self.identifier = identifier
        self.password = password

    async def authenticate(self, context: BrowserContext, domain: str) -> None:
        if not self.secret_key:
            log_event("warn", "Clerk secret key not set, skipping auth", {})
            return

        async with aiohttp.ClientSession(
            headers={
                "Authorization": f"Bearer {self.secret_key}",
                "Content-Type": "application/json",
            }
        ) as session:
            log_event("info", "Clerk Backend sign-in started", {
                "identifier": self.identifier,
            })

            sign_in_resp = await session.post(
                "https://api.clerk.com/v1/sign_ins",
                json={
                    "identifier": self.identifier,
                    "password": self.password,
                    "strategy": "password",
                },
            )
            sign_in_data = await sign_in_resp.json()

            if sign_in_resp.status != 200:
                log_event("warn", "Clerk sign-in API error", {
                    "status": sign_in_resp.status,
                    "response": sign_in_data,
                })
                return

            status = sign_in_data.get("status")
            session_id = sign_in_data.get("session_id")

            if status != "complete" or not session_id:
                log_event("warn", "Clerk sign-in did not complete", {
                    "status": status,
                    "session_id": session_id,
                })
                return

            log_event("info", "Clerk session created, fetching JWT", {
                "session_id": session_id,
            })

            token_resp = await session.post(
                f"https://api.clerk.com/v1/sessions/{session_id}/tokens",
            )
            token_data = await token_resp.json()

            if token_resp.status != 200:
                log_event("warn", "Clerk token API error", {
                    "status": token_resp.status,
                    "response": token_data,
                })
                return

            jwt = token_data.get("jwt")
            if not jwt:
                log_event("warn", "No JWT in Clerk token response", {})
                return

            cookie_domain = domain if domain.startswith(".") else domain
            await context.add_cookies([
                {
                    "name": "__session",
                    "value": jwt,
                    "domain": cookie_domain,
                    "path": "/",
                    "httpOnly": True,
                    "secure": domain not in ("localhost", "127.0.0.1"),
                    "sameSite": "Lax",
                },
            ])

            log_event("info", "Clerk session cookie injected", {
                "domain": cookie_domain,
            })
