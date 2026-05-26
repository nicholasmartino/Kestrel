from __future__ import annotations

import aiohttp
from playwright.async_api import BrowserContext

from kestrel.auth_providers.base import AuthProvider
from kestrel.logging import log_event

API_BASE = "https://api.clerk.com/v1"


class ClerkAuthProvider(AuthProvider):
    def __init__(self, secret_key: str, identifier: str, password: str):
        self.secret_key = secret_key
        self.identifier = identifier
        self.password = password

    async def authenticate(self, context: BrowserContext, domain: str) -> bool:
        if not self.secret_key:
            log_event("warn", "Clerk secret key not set, skipping auth", {})
            return False

        async with aiohttp.ClientSession(
            headers={
                "Authorization": f"Bearer {self.secret_key}",
                "Content-Type": "application/json",
            }
        ) as session:
            user_id = await self._lookup_user(session)
            if not user_id:
                return False

            sess_id = await self._create_session(session, user_id)
            if not sess_id:
                return False

            jwt = await self._fetch_token(session, sess_id)
            if not jwt:
                return False

            await self._inject_cookie(context, domain, jwt)
            return True

    async def _lookup_user(self, session: aiohttp.ClientSession) -> str | None:
        log_event("info", "Looking up Clerk user", {
            "identifier": self.identifier,
        })

        resp = await session.get(
            f"{API_BASE}/users",
            params={"email_address": self.identifier},
        )
        data = await resp.json()

        if resp.status != 200:
            log_event("warn", "Clerk user lookup failed", {
                "status": resp.status,
                "response": data,
            })
            return None

        users = data if isinstance(data, list) else data.get("data", [])
        if not users:
            log_event("warn", "No Clerk user found for email", {
                "identifier": self.identifier,
            })
            return None

        user_id = users[0].get("id")
        log_event("info", "Clerk user found", {
            "user_id": user_id,
        })
        return user_id

    async def _create_session(
        self, session: aiohttp.ClientSession, user_id: str
    ) -> str | None:
        log_event("info", "Creating Clerk session via Backend API", {
            "user_id": user_id,
        })

        resp = await session.post(
            f"{API_BASE}/sessions",
            json={"user_id": user_id},
        )

        if resp.status == 404:
            log_event("error", (
                "Clerk Backend API session creation returned 404. "
                "This endpoint may not be enabled for this instance. "
                "Disable Client Trust in Clerk Dashboard (Settings > Updates) "
                "to allow sign-ins from test browsers."
            ), {})
            return None

        if resp.status != 200:
            data = await resp.json()
            log_event("warn", "Clerk session creation failed", {
                "status": resp.status,
                "response": data,
            })
            return None

        data = await resp.json()
        sess_id = data.get("id")
        if not sess_id:
            log_event("warn", "No session ID in Clerk response", {})
            return None

        log_event("info", "Clerk session created", {
            "session_id": sess_id,
        })
        return sess_id

    async def _fetch_token(
        self, session: aiohttp.ClientSession, sess_id: str
    ) -> str | None:
        log_event("info", "Fetching Clerk session token", {
            "session_id": sess_id,
        })

        resp = await session.post(
            f"{API_BASE}/sessions/{sess_id}/tokens",
        )
        data = await resp.json()

        if resp.status != 200:
            log_event("warn", "Clerk token fetch failed", {
                "status": resp.status,
                "response": data,
            })
            return None

        jwt = data.get("jwt")
        if not jwt:
            log_event("warn", "No JWT in Clerk token response", {})
            return None

        return jwt

    async def _inject_cookie(
        self, context: BrowserContext, domain: str, jwt: str
    ) -> None:
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
