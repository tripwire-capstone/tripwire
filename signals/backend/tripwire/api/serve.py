"""The API plus the built UI, under an optional path prefix: what the API container runs on AWS.

Run: uvicorn tripwire.api.serve:app --host 0.0.0.0 --port 8080

On AWS, Tripwire lives under Laminar's address at /tripwire/ (the load balancer sends that prefix
here). This app strips the prefix, so the API in app.py still sees /api/...; everything else under
the prefix is the built UI, with index.html for any page address (the UI routes in the browser).

Locally, docker compose runs asgi.py directly (no prefix, UI from the Vite dev server), so nothing
here changes local development. app.py itself is not modified.

The API comes with the MCP server inside it (asgi.py). MCP clients look for its login document at
the host's root (/.well-known/oauth-protected-resource/tripwire/api/mcp), outside the prefix, so
that path is passed to the API too.

Settings (env):
  TRIPWIRE_BASE_PATH  the prefix, e.g. "/tripwire"; empty = serve at the root
  TRIPWIRE_UI_DIR     the built UI (Vite's dist/); default /app/ui, where the image puts it
"""

import os
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ..mcp_server.server import LOGIN_DOCUMENT_PREFIX

BASE_TAG = '<base href="/" />'


def normalize_base_path(raw):
    """'tripwire', '/tripwire/' and '/tripwire' all become '/tripwire'; '' and '/' become ''."""
    stripped = raw.strip().strip("/")
    return f"/{stripped}" if stripped else ""


def page_with_base(index_html, base_path):
    """index.html with its <base href> pointing at the prefix (the UI reads it: src/basePath.js)."""
    if BASE_TAG not in index_html:
        raise ValueError(f"the built index.html has no {BASE_TAG} to rewrite")
    return index_html.replace(BASE_TAG, f'<base href="{base_path}/" />', 1)


def route_path(scope):
    """The request path below the prefix (Starlette's Mount keeps the full path in scope["path"])."""
    path = scope["path"]
    root_path = scope.get("root_path", "")
    return path[len(root_path):] if root_path and path.startswith(root_path) else path


def build_site(ui_dir, base_path, api_app):
    """One ASGI app for everything under the prefix: /api/... to the API, the rest to the UI."""
    ui_dir = Path(ui_dir)
    index_file = ui_dir / "index.html"
    if index_file.is_file():
        page = HTMLResponse(page_with_base(index_file.read_text(), base_path),
                            headers={"Cache-Control": "no-cache"})
        static_files = StaticFiles(directory=ui_dir)
    else:
        page = PlainTextResponse(f"The UI isn't built into this image ({index_file} is missing).",
                                 status_code=404)
        static_files = None
    root = ui_dir.resolve()

    async def site(scope, receive, send):
        path = route_path(scope)
        if scope["type"] != "http" or path == "/api" or path.startswith("/api/"):
            await api_app(scope, receive, send)
            return
        candidate = (root / path.lstrip("/")).resolve()
        if static_files and path.strip("/") and candidate.is_file() and root in candidate.parents:
            await static_files(scope, receive, send)
            return
        await page(scope, receive, send)  # "/" and any page address: the UI routes in the browser

    return site


def build_app(ui_dir, base_path, api_app=None):
    """`api_app` defaults to the API with MCP (asgi.py); tests pass their own."""
    if api_app is None:
        from .asgi import app as api_app
    base_path = normalize_base_path(base_path)
    site = build_site(ui_dir, base_path, api_app)
    if not base_path:
        return site

    async def to_prefix(request):
        return RedirectResponse(f"{base_path}/")

    prefixed = Starlette(routes=[Route(base_path, to_prefix), Mount(base_path, app=site)])

    async def app(scope, receive, send):
        # Startup and shutdown reach the API (Mount doesn't pass them on), and so does the MCP
        # login document, which lives outside the prefix
        if scope["type"] == "lifespan" or (
                scope["type"] == "http" and scope["path"].startswith(LOGIN_DOCUMENT_PREFIX)):
            await api_app(scope, receive, send)
            return
        await prefixed(scope, receive, send)

    return app


app = build_app(os.environ.get("TRIPWIRE_UI_DIR", "/app/ui"), os.environ.get("TRIPWIRE_BASE_PATH", ""))
