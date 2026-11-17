"""Settings page endpoints: connect Tripwire to Laminar and OpenAI (SIG-103).

    GET    /api/settings          what each key looks like (hint and source, never the key)
    PUT    /api/settings/{kind}   check a key with its provider, then save it encrypted
    DELETE /api/settings/{kind}   forget the saved key; the environment's key takes over

`kind` is "laminar" or "openai". Keys are stored by tripwire/project_keys.py, encrypted with
TRIPWIRE_SECRET_KEY (tripwire/crypto.py).

Checks before saving, so a typo never replaces a working key:
  * Laminar: GET /v1/project returns the project the key belongs to (Laminar's CLI uses the same
    call), then SELECT 1 proves the key can run the poller's queries. The project ID it returns is
    saved too, for "Open in Laminar" links: the user never types a project URL, so the links can't
    point at a different project than the key.
  * OpenAI: listing models proves the key is accepted.

Every Tripwire user can change these (they share Laminar's login; there are no roles yet).
"""

import logging
import os
from typing import Literal

import httpx
import openai
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from .. import crypto
from ..project_keys import ProjectKey, clear_key, project_key, save_key
from .db import get_conn

log = logging.getLogger("api.settings")
router = APIRouter(prefix="/api/settings")

CHECK_TIMEOUT_SECONDS = 10
MAX_KEY_LENGTH = 1000
Kind = Literal["laminar", "openai"]


# ------------------------------------------------------------------ shapes

class KeyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str


class KeyOut(BaseModel):
    source: Literal["saved", "environment", "missing"]
    hint: str | None
    unreadable: bool       # a saved key can't be decrypted: the page asks to set it again


class LaminarOut(KeyOut):
    project_id: str | None
    project_url: str | None  # the project in Laminar, for the page's link


class SettingsOut(BaseModel):
    connected: bool          # both keys usable; otherwise the UI shows "not connected yet"
    laminar: LaminarOut
    openai: KeyOut


# ------------------------------------------------------------------ checking keys with their provider

class KeyRejected(Exception):
    """The provider answered and said no: wrong key, or not allowed to do what Tripwire needs."""


class CheckUnavailable(Exception):
    """The provider couldn't be asked (down, unreachable, timed out)."""


class KeyChecks:
    """Real checks against Laminar and OpenAI. API tests replace this with a fake; `transport`
    lets the tests of the checks themselves answer the HTTP calls."""

    def __init__(self, transport=None):
        self.transport = transport

    def laminar(self, api_url, api_key):
        """The Laminar project ID behind `api_key`."""
        base = api_url.rstrip("/")
        try:
            with httpx.Client(headers={"Authorization": f"Bearer {api_key}"},
                              timeout=CHECK_TIMEOUT_SECONDS, transport=self.transport) as http:
                project = http.get(f"{base}/v1/project")
                _raise_for_laminar(project, "check the key")
                project_id = project.json().get("projectId")
                if not project_id:
                    raise CheckUnavailable("Laminar didn't say which project this key belongs to")
                query = http.post(f"{base}/v1/sql/query", json={"query": "SELECT 1", "parameters": {}})
                _raise_for_laminar(query, "run a query with the key")
        except httpx.TransportError as error:
            raise CheckUnavailable(f"can't reach Laminar at {base}: {error}") from error
        return str(project_id)

    def openai(self, api_key):
        try:
            http_client = httpx.Client(transport=self.transport) if self.transport else None
            with openai.OpenAI(api_key=api_key, max_retries=0, timeout=CHECK_TIMEOUT_SECONDS,
                               http_client=http_client) as client:
                client.models.list()
        except openai.AuthenticationError as error:
            raise KeyRejected("OpenAI rejected this key. Check that it was copied whole "
                              "and hasn't been revoked.") from error
        except openai.PermissionDeniedError as error:
            raise KeyRejected("OpenAI accepted this key but it isn't allowed to list models. "
                              "Use a key with access to Models, Responses and Embeddings.") from error
        except (openai.APIConnectionError, openai.APITimeoutError) as error:
            raise CheckUnavailable("can't reach OpenAI to check the key") from error
        except openai.APIStatusError as error:
            raise CheckUnavailable(f"OpenAI returned HTTP {error.status_code} while checking the key") from error


def _raise_for_laminar(response, doing):
    if response.status_code == 200:
        return
    if response.status_code in (401, 403):
        raise KeyRejected("Laminar rejected this key. Create a project API key in Laminar "
                          "(Settings → Project API Keys) and paste it whole.")
    raise CheckUnavailable(f"Laminar returned HTTP {response.status_code} when trying to {doing}")


def get_key_checks():
    return KeyChecks()


# ------------------------------------------------------------------ helpers

def _project(conn):
    rows = conn.execute("""SELECT id, laminar_api_url, laminar_ui_url, laminar_project_id::text
                           FROM projects ORDER BY id LIMIT 2""").fetchall()
    if not rows:
        raise HTTPException(503, "no Laminar project yet: run the seed first")
    if len(rows) > 1:
        log.error("more than one row in projects; the API supports one project")
        raise HTTPException(500, "server misconfigured")
    return rows[0]


def _key_out(key: ProjectKey):
    return {"source": key.source, "hint": key.hint, "unreadable": key.unreadable}


def _settings(conn):
    project_id, _, ui_url, laminar_project_id = _project(conn)
    laminar = project_key(conn, project_id, "laminar")
    openai_key = project_key(conn, project_id, "openai")
    project_url = (f"{ui_url.rstrip('/')}/project/{laminar_project_id}/traces"
                   if laminar_project_id else None)
    return SettingsOut(
        connected=laminar.value is not None and openai_key.value is not None,
        laminar=LaminarOut(**_key_out(laminar), project_id=laminar_project_id, project_url=project_url),
        openai=KeyOut(**_key_out(openai_key)),
    )


def _clean(api_key):
    key = api_key.strip()
    if not key:
        raise HTTPException(422, ["Paste a key first."])
    if len(key) > MAX_KEY_LENGTH or any(character.isspace() for character in key):
        raise HTTPException(422, ["That doesn't look like an API key: it has spaces or is too long."])
    return key


# ------------------------------------------------------------------ routes

@router.get("", response_model=SettingsOut)
def read_settings(conn=Depends(get_conn)):
    return _settings(conn)


@router.put("/{kind}", response_model=SettingsOut)
def set_key(kind: Kind, body: KeyIn, conn=Depends(get_conn), checks=Depends(get_key_checks)):
    key = _clean(body.api_key)
    project_id, api_url, _, _ = _project(conn)
    try:
        laminar_project_id = checks.laminar(api_url, key) if kind == "laminar" else None
        if kind == "openai":
            checks.openai(key)
    except KeyRejected as error:
        raise HTTPException(422, str(error))
    except CheckUnavailable as error:
        raise HTTPException(502, f"Couldn't check the key, so it wasn't saved: {error}")
    try:
        with conn.transaction():
            save_key(conn, project_id, kind, key)
            if laminar_project_id:
                conn.execute("UPDATE projects SET laminar_project_id = %s WHERE id = %s",
                             (laminar_project_id, project_id))
    except crypto.SecretKeyError as error:
        raise HTTPException(503, str(error))
    log.info("saved a new %s key (%s)", kind, f"project {laminar_project_id}" if laminar_project_id else "checked")
    return _settings(conn)


@router.delete("/{kind}", response_model=SettingsOut)
def remove_key(kind: Kind, conn=Depends(get_conn)):
    project_id = _project(conn)[0]
    with conn.transaction():
        clear_key(conn, project_id, kind)
        if kind == "laminar":
            # Back to the deploy's project, the one the environment's key belongs to.
            conn.execute("UPDATE projects SET laminar_project_id = %s WHERE id = %s",
                         (os.environ.get("LMNR_PROJECT_ID") or None, project_id))
    log.info("cleared the saved %s key", kind)
    return _settings(conn)
