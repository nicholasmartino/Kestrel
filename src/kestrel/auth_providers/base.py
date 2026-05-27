from __future__ import annotations

from abc import ABC, abstractmethod

from playwright.async_api import BrowserContext, Page


class AuthProvider(ABC):
    def __init__(self, **credentials: str) -> None:
        self.credentials = credentials

    @abstractmethod
    async def authenticate(self, context: BrowserContext, page: Page) -> bool:
        """Authenticate the user in the browser context.

        Implementations should create a session via backend API calls
        and inject it into the browser (e.g. via page.evaluate or
        route interception). The user should appear authenticated
        on subsequent page loads without form interaction.

        Args:
            context: The Playwright BrowserContext.
            page: The Playwright Page to interact with.

        Returns:
            True if authentication succeeded, False otherwise.
        """
