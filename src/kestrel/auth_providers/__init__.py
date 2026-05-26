from kestrel.types import AuthConfig
from kestrel.auth_providers.base import AuthProvider
from kestrel.auth_providers.clerk import ClerkAuthProvider


def get_provider(auth: AuthConfig) -> AuthProvider | None:
    if auth.provider == "clerk":
        return ClerkAuthProvider(
            secret_key=auth.credentials.get("secret_key", ""),
            identifier=auth.credentials.get("identifier", ""),
            password=auth.credentials.get("password", ""),
        )
    return None


__all__ = [
    "AuthProvider",
    "ClerkAuthProvider",
    "get_provider",
]
