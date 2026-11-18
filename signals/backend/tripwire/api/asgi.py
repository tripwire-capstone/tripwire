"""The API with the MCP server inside it, at /api/mcp: what uvicorn runs.

Run: uvicorn tripwire.api.asgi:app --host 0.0.0.0 --port 8080

Compose runs this directly; on AWS, serve.py wraps it under /tripwire with the built UI. Tests of
the API itself use app.py's app, which has no MCP.
"""

from ..mcp_server.server import with_mcp
from .app import app as api_app

app = with_mcp(api_app)
