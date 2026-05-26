from __future__ import annotations

from abc import ABC, abstractmethod

from playwright.async_api import BrowserContext, Page


class AuthProvider(ABC):
    @abstractmethod
    async def authenticate(self, context: BrowserContext, page: Page) -> bool:
        """Authenticate the user in the browser.

        Implementations should navigate to the sign-in page, fill credentials,
        and submit. The user should end up on an authenticated page.

        Args:
            context: The Playwright BrowserContext.
            page: The Playwright Page to interact with.

        Returns:
            True if authentication succeeded, False otherwise.
        """
