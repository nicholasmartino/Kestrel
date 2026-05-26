from __future__ import annotations

from abc import ABC, abstractmethod

from playwright.async_api import BrowserContext


class AuthProvider(ABC):
    @abstractmethod
    async def authenticate(self, context: BrowserContext, domain: str) -> None:
        """Authenticate and inject session into the browser context.

        Implementations should use backend API credentials to create a session,
        then inject cookies/localStorage into the Playwright context so the
        user appears authenticated before any page loads.

        Args:
            context: The Playwright BrowserContext to inject credentials into.
            domain: The domain to scope cookies to (e.g. "localhost" or ".example.com").
        """
