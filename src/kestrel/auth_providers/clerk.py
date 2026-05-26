from __future__ import annotations

import aiohttp
from playwright.async_api import BrowserContext, Page

from kestrel.auth_providers.base import AuthProvider
from kestrel.logging import log_event

API_BASE = "https://api.clerk.com/v1"


class ClerkAuthProvider(AuthProvider):
    def __init__(self, secret_key: str, identifier: str, password: str):
        self.secret_key = secret_key
        self.identifier = identifier
        self.password = password

    async def authenticate(self, context: BrowserContext, page: Page) -> bool:
        if not self.secret_key:
            log_event("warn", "Clerk secret key not set, skipping auth", {})
            return False

        token = await self._generate_testing_token()
        if not token:
            return False

        await context.add_init_script(f"""
            window.__clerk_testing_token = '{token}';
            window.__clerk_clerkTesting = true;
        """)

        sign_in_url = "http://localhost:5173/sign-in"
        log_event("info", "Navigating to sign-in", {"url": sign_in_url})
        await page.goto(sign_in_url, wait_until="domcontentloaded")

        try:
            await page.wait_for_selector("input", timeout=15000)
        except Exception:
            log_event("warn", "Sign-in form inputs did not appear", {})
            return False

        await self._fill_field(page, "Email address", self.identifier)
        await self._fill_field(page, "Password", self.password)
        await self._click_continue(page)

        try:
            await page.wait_for_url(
                lambda url: "/sign-in" not in url, timeout=15000
            )
            log_event("info", "Redirected away from sign-in", {"url": page.url})
            return True
        except Exception:
            log_event("warn", "Did not redirect from sign-in", {"url": page.url})
            return False

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

    async def _fill_field(self, page: Page, label: str, value: str) -> None:
        try:
            await page.get_by_label(label, exact=False).first.fill(value, timeout=3000)
            return
        except Exception:
            pass
        try:
            await page.get_by_placeholder(label, exact=False).first.fill(value, timeout=3000)
            return
        except Exception:
            pass
        try:
            locator = page.locator(f"input[name='{label.lower()}']")
            await locator.first.fill(value, timeout=3000)
        except Exception:
            log_event("warn", f"Could not fill field: {label}", {})

    async def _click_continue(self, page: Page) -> None:
        for text in ("Continue", "Sign in", "Sign In", "Log in", "Submit"):
            try:
                await page.get_by_role("button", name=text, exact=False).first.click(timeout=3000)
                return
            except Exception:
                continue
        try:
            await page.get_by_text("Continue", exact=False).first.click(timeout=3000, force=True)
            return
        except Exception:
            pass
        log_event("warn", "Could not find submit button", {})
