from __future__ import annotations

from abc import ABC, abstractmethod

from playwright.async_api import BrowserContext, Page


class AuthProvider(ABC):
    @abstractmethod
    async def authenticate(self, context: BrowserContext, page: Page) -> bool:
        """Authenticate the user in the browser without form interaction.

        Implementations should use backend API calls to create a session,
        then inject the session into the browser context via Clerk's
        Frontend JS API (page.evaluate). No navigation to sign-in page
        or form fill is needed — the user appears authenticated on load.

        Args:
            context: The Playwright BrowserContext.
            page: The Playwright Page to interact with.

        Returns:
            True if authentication succeeded, False otherwise.
        """
