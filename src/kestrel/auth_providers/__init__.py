from typing import Callable

from kestrel.types import AuthConfig
from kestrel.auth_providers.base import AuthProvider

_provider_registry: dict[str, type[AuthProvider]] = {}


def register(name: str) -> Callable[[type[AuthProvider]], type[AuthProvider]]:
    def decorator(cls: type[AuthProvider]) -> type[AuthProvider]:
        _provider_registry[name] = cls
        return cls
    return decorator


def get_provider(auth: AuthConfig) -> AuthProvider | None:
    cls = _provider_registry.get(auth.provider)
    if cls is None:
        return None
    return cls(**auth.credentials)


from kestrel.auth_providers.clerk import ClerkAuthProvider

__all__ = [
    "AuthProvider",
    "ClerkAuthProvider",
    "get_provider",
    "register",
]
