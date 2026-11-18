"""The MCP server's settings, read from the environment.

Auth is on when all three Cognito settings are given and off when none are. Giving only some of
them is refused, so a typo in one name can't silently leave the server open.
"""

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

COGNITO_SETTINGS = ("MCP_COGNITO_REGION", "MCP_COGNITO_USER_POOL_ID", "MCP_COGNITO_CLIENT_ID")
READ_SCOPE = "tripwire-mcp/read"
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


class SettingsError(Exception):
    pass


@dataclass(frozen=True)
class Cognito:
    region: str
    user_pool_id: str
    client_id: str

    @property
    def issuer(self) -> str:
        return f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}"

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"


@dataclass(frozen=True)
class Settings:
    public_url: str           # the address clients connect to, e.g. http://localhost:8080/api/mcp
    cognito: Cognito | None   # None = auth off

    @property
    def is_local(self) -> bool:
        """Only this machine uses the address, so running without auth is acceptable."""
        return urlsplit(self.public_url).hostname in LOCAL_HOSTS


def load(env=os.environ) -> Settings:
    given = {name: env.get(name, "").strip() for name in COGNITO_SETTINGS}
    present = [name for name, value in given.items() if value]
    if present and len(present) < len(COGNITO_SETTINGS):
        missing = [name for name in COGNITO_SETTINGS if name not in present]
        raise SettingsError(f"auth needs all of {', '.join(COGNITO_SETTINGS)}; missing {', '.join(missing)}")
    cognito = Cognito(*(given[name] for name in COGNITO_SETTINGS)) if present else None

    public_url = env.get("MCP_PUBLIC_URL", "").strip() or "http://localhost:8080/api/mcp"
    if urlsplit(public_url).scheme not in ("http", "https") or not urlsplit(public_url).netloc:
        raise SettingsError(f"MCP_PUBLIC_URL must be a full http(s) URL, got {public_url!r}")
    return Settings(public_url=public_url, cognito=cognito)
