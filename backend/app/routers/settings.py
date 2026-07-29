from fastapi import APIRouter, HTTPException
from huggingface_hub import HfApi
from huggingface_hub.errors import HfHubHTTPError
from pydantic import BaseModel

router = APIRouter(prefix="/settings", tags=["settings"])


class VerifyTokenRequest(BaseModel):
    token: str


class VerifyTokenResponse(BaseModel):
    valid: bool
    username: str | None = None
    detail: str | None = None


@router.post("/verify-hf-token", response_model=VerifyTokenResponse)
def verify_hf_token(body: VerifyTokenRequest) -> VerifyTokenResponse:
    token = body.token.strip()
    if not token:
        return VerifyTokenResponse(valid=False, detail="Token is empty.")
    try:
        info = HfApi().whoami(token=token)
        return VerifyTokenResponse(valid=True, username=info.get("name"))
    except HfHubHTTPError as err:
        return VerifyTokenResponse(valid=False, detail=str(err))
    except Exception as err:  # noqa: BLE001 — surface any failure as an invalid-token result, not a 500
        return VerifyTokenResponse(valid=False, detail=str(err))


# ---------------------------------------------------------------------------
# Environment settings for vendor/socialpost
#
# The renderer keeps credentials in Electron's encrypted per-user store and pushes
# them here — on boot and whenever Settings is saved — because the vendored package
# reads its configuration from the process environment, not from request bodies.
#
# Mutating os.environ process-wide is acceptable here specifically because this is a
# single-user desktop app: one user, one backend process, spawned and owned by their
# Electron session. It would not be in a multi-tenant server.
# ---------------------------------------------------------------------------


class EnvSettingOut(BaseModel):
    name: str
    group: str
    help: str
    isSecret: bool
    choices: list[str]
    value: str  # masked for secrets — never the real credential
    isSet: bool


class ApplyEnvRequest(BaseModel):
    values: dict[str, str]


class ApplyEnvResponse(BaseModel):
    changed: list[str]
    detail: str | None = None


class VerifyResponse(BaseModel):
    valid: bool
    detail: str


def _spg_config():
    from vendor.socialpost.src import config as spg_config

    return spg_config


@router.get("/social-post/schema", response_model=list[EnvSettingOut])
def social_post_schema() -> list[EnvSettingOut]:
    """The settings the Social Post Generator understands.

    Derived from the vendored package's own .env.example, so adding a documented
    variable there makes it appear in the Settings screen with no UI change.
    """
    spg_config = _spg_config()
    return [
        EnvSettingOut(
            name=s.name,
            group=s.group,
            help=s.help,
            isSecret=s.is_secret,
            choices=s.choices,
            value=spg_config.masked_value(s.name),
            isSet=spg_config.is_set(s.name),
        )
        for s in spg_config.load_schema()
    ]


@router.post("/social-post/env", response_model=ApplyEnvResponse)
def apply_social_post_env(body: ApplyEnvRequest) -> ApplyEnvResponse:
    """Apply settings to the running backend and persist them for this user.

    A value equal to the masked placeholder means "the UI showed a masked secret
    and the user did not retype it" — the vendored config layer skips those, so a
    save never blanks a credential the user could not see.
    """
    spg_config = _spg_config()
    try:
        changed = spg_config.apply(body.values)
    except spg_config.ConfigError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None
    return ApplyEnvResponse(changed=changed)


@router.post("/social-post/verify/{target}", response_model=VerifyResponse)
def verify_social_post(target: str) -> VerifyResponse:
    """Test a configured credential for real (supabase | bluesky | llm | hf)."""
    spg_config = _spg_config()
    verifier = spg_config.VERIFIERS.get(target)
    if verifier is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown check {target!r}. Try one of: {', '.join(spg_config.VERIFIERS)}.",
        )
    ok, detail = verifier()
    return VerifyResponse(valid=ok, detail=detail)


# ---------------------------------------------------------------------------
# Environment settings for vendor/leadgen (the Lead Gen Agent)
#
# Identical contract to the social-post block above: the vendored package derives its
# settings schema from its own .env.example, and this router just exposes read/apply/verify.
# ---------------------------------------------------------------------------


def _leadgen_config():
    from vendor.leadgen import config as lg_config

    return lg_config


@router.get("/leadgen/schema", response_model=list[EnvSettingOut])
def leadgen_schema() -> list[EnvSettingOut]:
    lg_config = _leadgen_config()
    return [
        EnvSettingOut(
            name=s.name,
            group=s.group,
            help=s.help,
            isSecret=s.is_secret,
            choices=s.choices,
            value=lg_config.masked_value(s.name),
            isSet=lg_config.is_set(s.name),
        )
        for s in lg_config.load_schema()
    ]


@router.post("/leadgen/env", response_model=ApplyEnvResponse)
def apply_leadgen_env(body: ApplyEnvRequest) -> ApplyEnvResponse:
    lg_config = _leadgen_config()
    try:
        changed = lg_config.apply(body.values)
    except lg_config.ConfigError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None
    return ApplyEnvResponse(changed=changed)


@router.post("/leadgen/verify/{target}", response_model=VerifyResponse)
def verify_leadgen(target: str) -> VerifyResponse:
    """Test a configured credential/service (hf | llm | smtp | imap | reacher | searxng)."""
    lg_config = _leadgen_config()
    verifier = lg_config.VERIFIERS.get(target)
    if verifier is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown check {target!r}. Try one of: {', '.join(lg_config.VERIFIERS)}.",
        )
    ok, detail = verifier()
    return VerifyResponse(valid=ok, detail=detail)
