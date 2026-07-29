"""REST client for the per-user, locally managed Activepieces instance (the Distribution
engine). Activepieces stays fully invisible to the user: the very first call this module
ever makes bootstraps a private admin account and claims the default platform automatically,
so there is no sign-up screen for the user to see or click through. See the Distribution
Layer plan for the full architecture.
"""
import json
import secrets
import time
from pathlib import Path
from typing import Optional

import requests

from .. import config

_CREDENTIALS_PATH = config.DATA_DIR / "activepieces_credentials.json"
_FLOWS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "resources" / "activepieces" / "flows"

_cached_token: Optional[str] = None
_cached_project_id: Optional[str] = None
_cached_token_exp: float = 0
_cached_flow_ids: dict[str, str] = {}

# Piece (name, version) for the channels that connect via a plain credential form
# (no OAuth) — used both to create app-connections and to validate /connections/{channel}
# requests. linkedin/facebook/instagram are OAUTH2 and deliberately excluded here: per the
# Distribution Layer plan, those deep-link to Activepieces' own connection screen instead
# of us reimplementing 3 separate OAuth handshakes.
CUSTOM_AUTH_PIECES = {
    "bluesky": ("@activepieces/piece-bluesky", "0.1.5"),
    "mastodon": ("@activepieces/piece-mastodon", "0.5.6"),
    "twitter": ("@activepieces/piece-twitter", "0.3.5"),
    "email": ("@activepieces/piece-smtp", "0.4.2"),
}
SECRET_TEXT_PIECES = {
    "discord": ("@activepieces/piece-discord", "0.5.3"),
}

# OAuth2 channels — no credential form of our own; the user connects these through
# Activepieces' own connection screen (see get_console_url), which handles the real
# OAuth handshake with each platform. Listed here purely so the frontend can show the
# right piece per setup guide.
OAUTH_PIECES = {
    "linkedin": ("@activepieces/piece-linkedin", "LinkedIn"),
    "facebook": ("@activepieces/piece-facebook-pages", "Facebook Pages"),
    "instagram": ("@activepieces/piece-instagram-business", "Instagram Business"),
    "reddit": ("@activepieces/piece-reddit", "Reddit"),
}


class ActivepiecesError(RuntimeError):
    pass


def _request(method: str, path: str, token: Optional[str] = None, body: Optional[dict] = None) -> dict:
    url = f"{config.ACTIVEPIECES_URL}{path}"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = requests.request(method, url, json=body, headers=headers, timeout=20)
    except requests.RequestException as err:
        raise ActivepiecesError(f"{method} {path} failed to connect: {err}") from err
    if not resp.ok:
        raise ActivepiecesError(f"{method} {path} -> {resp.status_code}: {resp.text[:500]}")
    return resp.json() if resp.content else {}


def _load_credentials() -> Optional[dict]:
    if _CREDENTIALS_PATH.exists():
        return json.loads(_CREDENTIALS_PATH.read_text(encoding="utf-8"))
    return None


def _save_credentials(creds: dict) -> None:
    _CREDENTIALS_PATH.write_text(json.dumps(creds), encoding="utf-8")


def _bootstrap_account() -> dict:
    """First-ever call against a fresh Activepieces instance: creates the sole admin
    account and claims the default platform. The password is generated once and
    persisted locally — same trust boundary as the rest of DATA_DIR, since Activepieces
    is bound to localhost only, reachable solely through the WSL2 port-forward this app
    itself sets up."""
    email = f"admin+{secrets.token_hex(6)}@mr-ai-marketer.local"
    password = secrets.token_urlsafe(24)
    signup = _request(
        "POST",
        "/api/v1/authentication/sign-up",
        body={
            "email": email,
            "password": password,
            "firstName": "Mr",
            "lastName": "AI Marketer",
            "trackEvents": False,
            "newsLetter": False,
        },
    )
    claimed = _request("POST", "/api/v1/platforms", token=signup["token"], body={"name": "Mr AI Marketer"})
    creds = {
        "email": email,
        "password": password,
        "platformId": claimed["platformId"],
        "projectId": claimed["projectId"],
    }
    _save_credentials(creds)
    return creds


def _get_session() -> dict:
    """Returns {"token", "projectId"}, re-authenticating if the cached token is stale."""
    global _cached_token, _cached_project_id, _cached_token_exp
    if _cached_token and time.time() < _cached_token_exp:
        return {"token": _cached_token, "projectId": _cached_project_id}

    creds = _load_credentials() or _bootstrap_account()
    result = _request(
        "POST",
        "/api/v1/authentication/sign-in",
        body={"email": creds["email"], "password": creds["password"]},
    )
    _cached_token = result["token"]
    _cached_project_id = result["projectId"]
    _cached_token_exp = time.time() + 6 * 24 * 3600  # sessions last 7 days; refresh a day early
    return {"token": _cached_token, "projectId": _cached_project_id}


def list_flows() -> list[dict]:
    session = _get_session()
    result = _request("GET", f"/api/v1/flows?projectId={session['projectId']}", token=session["token"])
    return result.get("data", [])


def list_connections() -> list[dict]:
    session = _get_session()
    result = _request("GET", f"/api/v1/app-connections?projectId={session['projectId']}", token=session["token"])
    return result.get("data", [])


def get_flow_run(run_id: str) -> dict:
    session = _get_session()
    return _request("GET", f"/api/v1/flow-runs/{run_id}", token=session["token"])


def create_connection(channel: str, connection_type: str, value: dict) -> dict:
    """Creates (or replaces) the app-connection a channel's flow references as
    {{connections.<channel>}}. `connection_type` is one of Activepieces' own connection
    kinds (CUSTOM_AUTH or SECRET_TEXT here — OAUTH2 channels connect via Activepieces' own
    screen instead). Confirmed live against a real instance's OpenAPI schema: SECRET_TEXT
    takes its field (`secret_text`) at the top level of `value`, but CUSTOM_AUTH requires
    the piece's own fields nested one level deeper under `value.props` — sending them
    spread at the top level (as SECRET_TEXT does) 400s with FST_ERR_VALIDATION."""
    piece_name, piece_version = {**CUSTOM_AUTH_PIECES, **SECRET_TEXT_PIECES}[channel]
    session = _get_session()
    connection_value = (
        {"type": connection_type, "props": value} if connection_type == "CUSTOM_AUTH" else {"type": connection_type, **value}
    )
    return _request(
        "POST",
        "/api/v1/app-connections",
        token=session["token"],
        body={
            "externalId": channel,
            "displayName": channel,
            "pieceName": piece_name,
            "pieceVersion": piece_version,
            "projectId": session["projectId"],
            "type": connection_type,
            "value": connection_value,
        },
    )


def delete_connection(external_id: str) -> None:
    """Removes the app-connection matching `external_id`, looked up by that id since our
    own code never stores Activepieces' internal connection id. A no-op if no connection
    with that externalId exists (e.g. disconnecting a channel that was never connected)."""
    session = _get_session()
    match = next((c for c in list_connections() if c["externalId"] == external_id), None)
    if not match:
        return
    _request("DELETE", f"/api/v1/app-connections/{match['id']}", token=session["token"])


def get_console_url() -> str:
    """URL of Activepieces' own connections screen, for OAuth2 channels (linkedin,
    facebook, instagram, reddit) that we deep-link to instead of reimplementing their
    OAuth handshakes ourselves."""
    session = _get_session()
    return f"{config.ACTIVEPIECES_URL}/projects/{session['projectId']}/connections"


def get_flow_id(channel: str) -> Optional[str]:
    return _cached_flow_ids.get(channel) or ensure_flows_imported().get(channel)


def trigger_webhook(channel: str, payload: dict) -> dict:
    """Fires a channel's published flow via its webhook trigger. Returns whatever
    Activepieces' webhook endpoint responds with (the flow runs async; poll
    get_flow_run/list_flow_runs_for with the run id to see the outcome)."""
    flow_id = get_flow_id(channel)
    if not flow_id:
        raise ActivepiecesError(f"No flow imported for channel '{channel}'")
    try:
        resp = requests.post(f"{config.ACTIVEPIECES_URL}/api/v1/webhooks/{flow_id}", json=payload, timeout=20)
    except requests.RequestException as err:
        raise ActivepiecesError(f"POST /api/v1/webhooks/{flow_id} failed to connect: {err}") from err
    if not resp.ok:
        raise ActivepiecesError(f"POST /api/v1/webhooks/{flow_id} -> {resp.status_code}: {resp.text[:500]}")
    return resp.json() if resp.content else {}


def list_flow_runs_since(flow_id: str, created_after_iso: str, limit: int = 10) -> list[dict]:
    """Returns runs for a flow created at/after `created_after_iso`, newest first.
    GET /v1/flow-runs has no sort parameter — reproduced live: with a flow that had
    multiple prior runs, plain `limit=1` returned an old SUCCEEDED run instead of the
    one just triggered. Filtering by creation time and sorting client-side is the only
    reliable way to find the run that was just fired."""
    session = _get_session()
    result = _request(
        "GET",
        f"/api/v1/flow-runs?projectId={session['projectId']}&flowId={flow_id}"
        f"&createdAfter={created_after_iso}&limit={limit}",
        token=session["token"],
    )
    return sorted(result.get("data", []), key=lambda r: r["created"], reverse=True)


def resume_waitpoint(link: str) -> None:
    """Resumes a run paused at a `wait_for_approval` step — `link` is one of the
    approvalLink/disapprovalLink URLs that step's paired `create_approval_links` action
    generates (captured off the run's step output while PAUSED, not documented in
    Activepieces' public API — reproduced live against a real paused run)."""
    try:
        resp = requests.get(link, timeout=20)
    except requests.RequestException as err:
        raise ActivepiecesError(f"GET {link} failed to connect: {err}") from err
    if not resp.ok:
        raise ActivepiecesError(f"GET {link} -> {resp.status_code}: {resp.text[:500]}")


def _import_flow(spec: dict, flow_id: Optional[str] = None) -> dict:
    """Applies a flow's trigger (with the platform action nested as `nextAction`) via a
    single IMPORT_FLOW operation, creating the flow first if `flow_id` isn't given."""
    session = _get_session()
    if flow_id is None:
        created = _request(
            "POST",
            "/api/v1/flows",
            token=session["token"],
            body={"projectId": session["projectId"], "displayName": spec["displayName"]},
        )
        flow_id = created["id"]
    return _request(
        "POST",
        f"/api/v1/flows/{flow_id}",
        token=session["token"],
        body={"type": "IMPORT_FLOW", "request": {"displayName": spec["displayName"], "trigger": spec["trigger"]}},
    )


def _publish_flow(flow_id: str) -> None:
    session = _get_session()
    _request(
        "POST",
        f"/api/v1/flows/{flow_id}",
        token=session["token"],
        body={"type": "LOCK_AND_PUBLISH", "request": {"status": "ENABLED"}},
    )


def ensure_flows_imported() -> dict[str, str]:
    """Idempotently imports and publishes every *.json flow template in
    resources/activepieces/flows/ (matched by displayName). A flow's webhook only
    responds once it's published (ENABLED) — importing alone leaves it a disabled draft.

    Retries a flow whenever it exists but isn't ENABLED yet, rather than treating "the
    flow object exists" as "done": on a very freshly booted container the piece registry
    can still be mid-sync, so IMPORT_FLOW can 404 on ENTITY_NOT_FOUND/piece_trigger after
    the flow shell was already created — reproduced live. Without this, that half-created
    flow would be silently skipped as "already imported" on every future call, forever.

    Returns {channel: flow_id}; a no-op REST round-trip once all flows are ENABLED."""
    global _cached_flow_ids
    existing = {f["version"]["displayName"]: f for f in list_flows()}
    result: dict[str, str] = {}
    for path in sorted(_FLOWS_DIR.glob("*.json")):
        channel = path.stem
        spec = json.loads(path.read_text(encoding="utf-8"))
        found = existing.get(spec["displayName"])
        if found and found["status"] == "ENABLED":
            result[channel] = found["id"]
            continue
        updated = _import_flow(spec, flow_id=found["id"] if found else None)
        _publish_flow(updated["id"])
        result[channel] = updated["id"]
    _cached_flow_ids = result
    return result
