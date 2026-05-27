from __future__ import annotations

import base64
import json
import os
import re

import aiohttp
from playwright.async_api import BrowserContext, Page, Route

from kestrel.auth_providers import register
from kestrel.auth_providers.base import AuthProvider
from kestrel.logging import log_event

API_BASE = "https://api.clerk.com/v1"


@register("clerk")
class ClerkAuthProvider(AuthProvider):
    def __init__(self, **credentials: str) -> None:
        super().__init__(**credentials)
        self.secret_key = credentials.get("secret_key", "")
        self.identifier = credentials.get("identifier", "")
        self.password = credentials.get("password", "")

    async def authenticate(self, context: BrowserContext, page: Page) -> bool:
        if not self.secret_key:
            log_event("warn", "Clerk secret key not set, skipping auth", {})
            return False

        testing_token = await self._generate_testing_token()
        if not testing_token:
            return False

        publishable_key = os.environ.get("CLERK_PUBLISHABLE_KEY", "")
        fapi_domain = self._parse_frontend_api_domain(publishable_key)
        if not fapi_domain:
            log_event("warn", "Could not determine Clerk Frontend API domain from publishable key", {})
            return False

        log_event("info", "Setting up Clerk Frontend API interception", {
            "fapi_domain": fapi_domain,
        })
        await self._setup_fapi_interception(context, testing_token, fapi_domain)

        await page.goto("http://localhost:5173/", wait_until="domcontentloaded")

        try:
            await page.wait_for_function(
                "window.Clerk && window.Clerk.client && window.Clerk.client.signIn",
                timeout=15000,
            )
        except Exception:
            log_event("warn", "Clerk JS did not load in time", {})
            return False

        escaped_identifier = json.dumps(self.identifier)
        escaped_password = json.dumps(self.password)
        try:
            result = await page.evaluate(f"""
                (async () => {{
                    try {{
                        let signIn = await window.Clerk.client.signIn.create({{
                            identifier: {escaped_identifier},
                            password: {escaped_password}
                        }});

                        if (signIn.status === 'needs_second_factor') {{
                            const factors = signIn.supportedSecondFactors
                                ? signIn.supportedSecondFactors.map(f => f.strategy)
                                : [];
                            if (factors.includes('email_code')) {{
                                await signIn.prepareSecondFactor({{ strategy: 'email_code' }});
                                signIn = await signIn.attemptSecondFactor({{
                                    strategy: 'email_code',
                                    code: '424242'
                                }});
                            }}
                        }}

                        if (signIn.status === 'complete') {{
                            await window.Clerk.setActive({{
                                session: signIn.createdSessionId
                            }});
                            return {{ ok: true }};
                        }}
                        const factors = signIn.supportedSecondFactors
                            ? signIn.supportedSecondFactors.map(f => f.strategy)
                            : [];
                        return {{ ok: false, status: signIn.status, sessionId: signIn.createdSessionId || null, secondFactors: factors }};
                    }} catch (e) {{
                        const msg = e?.errors?.[0]?.message || e?.message || String(e);
                        return {{ ok: false, error: msg }};
                    }}
                }})()
            """)
        except Exception as e:
            log_event("warn", "Clerk password sign-in evaluate failed", {"error": str(e)})
            return False

        if not result.get("ok"):
            log_event("warn", "Clerk password sign-in did not complete", {
                "status": result.get("status"),
                "error": result.get("error"),
                "sessionId": result.get("sessionId"),
                "secondFactors": result.get("secondFactors"),
            })
            return False

        try:
            await page.wait_for_function(
                "window.Clerk?.user !== null && window.Clerk?.user !== undefined",
                timeout=10000,
            )
        except Exception:
            log_event("warn", "Clerk did not report authenticated user", {})
            return False

        log_event("info", "Authenticated via Clerk sign-in token", {})
        return True

    def _parse_frontend_api_domain(self, publishable_key: str) -> str | None:
        if not publishable_key:
            return None
        for prefix in ("pk_test_", "pk_live_"):
            if publishable_key.startswith(prefix):
                key = publishable_key[len(prefix):]
                break
        else:
            return None
        try:
            padding = 4 - len(key) % 4
            if padding != 4:
                key += "=" * padding
            decoded = base64.b64decode(key).decode("utf-8")
            return decoded.rstrip("$")
        except Exception:
            return None

    async def _setup_fapi_interception(
        self, context: BrowserContext, testing_token: str, fapi_domain: str
    ) -> None:
        escaped = re.escape(fapi_domain)
        pattern = re.compile(f"^https://{escaped}/v1/")

        async def handle_route(route: Route):
            original_url = route.request.url
            log_event("debug", "Intercepting FAPI request", {"url": original_url})
            delimiter = "&" if "?" in original_url else "?"
            new_url = f"{original_url}{delimiter}__clerk_testing_token={testing_token}"

            try:
                response = await route.fetch(url=new_url)
                body = await response.json()
                if isinstance(body, dict):
                    if (
                        isinstance(body.get("response"), dict)
                        and body["response"].get("captcha_bypass") is False
                    ):
                        body["response"]["captcha_bypass"] = True
                    if (
                        isinstance(body.get("client"), dict)
                        and body["client"].get("captcha_bypass") is False
                    ):
                        body["client"]["captcha_bypass"] = True
                await route.fulfill(response=response, json=body)
            except Exception as e:
                log_event("warn", "FAPI interception fetch failed", {"error": str(e), "url": original_url})
                await route.continue_()

        await context.route(pattern, handle_route)

    async def _generate_testing_token(self) -> str | None:
        async with aiohttp.ClientSession(
            headers={
                "Authorization": f"Bearer {self.secret_key}",
                "Content-Type": "application/json",
            }
        ) as session:
            log_event("info", "Generating Clerk testing token", {})
            resp = await session.post(f"{API_BASE}/testing_tokens")
            data = await resp.json()

            if resp.status != 200:
                log_event("warn", "Testing token generation failed", {
                    "status": resp.status,
                    "response": data,
                })
                return None

            token = data.get("token") if isinstance(data, dict) else None
            if not token:
                log_event("warn", "No token in testing token response", {})
                return None

            log_event("info", "Clerk testing token generated", {})
            return token
