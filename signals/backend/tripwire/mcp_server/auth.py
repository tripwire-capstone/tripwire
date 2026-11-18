"""Checks Cognito access tokens sent to the MCP server.

Cognito access tokens carry no audience, so the token's `client_id` (the MCP app client) and its
`tripwire-mcp/read` scope are what tie it to this server. A token from the Laminar UI's login has
neither, so it's refused here.
"""

import logging

import anyio
import jwt
from mcp.server.auth.provider import AccessToken

from .settings import READ_SCOPE, Cognito

log = logging.getLogger("mcp_server.auth")


class CognitoVerifier:
    def __init__(self, cognito: Cognito, jwks_client: jwt.PyJWKClient | None = None):
        self.cognito = cognito
        # Fetches the pool's public keys once and caches them; tests pass a client with their own keys.
        self.jwks = jwks_client or jwt.PyJWKClient(cognito.jwks_url, cache_keys=True)

    async def verify_token(self, token: str) -> AccessToken | None:
        """The token's details when it's a valid access token for this server, else None (a 401)."""
        try:
            return await anyio.to_thread.run_sync(self.check, token)
        except (jwt.PyJWTError, ValueError) as e:
            log.info("refused a token: %s", e)
            return None

    def check(self, token: str) -> AccessToken:
        key = self.jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], issuer=self.cognito.issuer,
                            options={"require": ["exp", "iss", "token_use", "client_id"],
                                     "verify_aud": False})
        if claims["token_use"] != "access":
            raise ValueError(f"token_use is {claims['token_use']!r}, not 'access'")
        if claims["client_id"] != self.cognito.client_id:
            raise ValueError("issued to another app client")
        scopes = claims.get("scope", "").split()
        if READ_SCOPE not in scopes:
            raise ValueError(f"no {READ_SCOPE} scope")
        return AccessToken(token=token, client_id=claims["client_id"], scopes=scopes,
                           expires_at=claims["exp"], subject=claims.get("sub"), claims=claims)
