"""Datasets and model files, fetched from Hugging Face instead of shipped in the build.

The rule this implements: **no model or dataset lives in the repo or the installer.** They
live in Hugging Face repos and are pulled on first use into the user's data directory, where
the Hub's cache keeps them across restarts.

Three reasons, in order of how much they matter:

1. **Protection.** A file inside a distributed app is a file every user has. A file in a
   private or gated HF repo is one you can see who has, and revoke. Nothing here makes a
   public dataset secret — it makes a restricted one restrictable.
2. **Size.** The installer stops carrying ~20 MB of spreadsheets and a 13 MB model that most
   users of any one tool never touch.
3. **Updates.** A corrected catalogue is a push, not a release.

**Training data is not distributed at all.** The CTR model ships as a fitted model; the
campaign data it was fitted on stays in a private repo that only the training script reads.
Users need the model, not the corpus — so the corpus never leaves.

Every asset degrades the same way: if the repo isn't configured and a local copy exists (a
dev checkout, or an old install), the local copy is used and a line is printed saying so.
Tools whose data is genuinely missing say which asset and where it should come from, rather
than failing somewhere deep in a parser.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .. import config

# Where fetched assets land. Inside DATA_DIR rather than the install directory, so an app
# installed under Program Files does not try to write next to its own executable.
CACHE_DIR = config.DATA_DIR / "hf-assets"

_lock = threading.Lock()
# Where each asset actually ended up, once resolved. Without this every call would re-enter
# hf_hub_download — cached on disk, but still a network round trip to revalidate, on a path
# that runs per request.
_resolved: dict[str, Path] = {}


class AssetError(RuntimeError):
    """The asset could not be obtained and the caller has nothing to fall back on."""


@dataclass(frozen=True)
class Asset:
    """One file in one HF repo.

    `local` is where the file used to live in the source tree. It is a fallback, not the
    source of truth — a checkout that still has it keeps working, and a build that dropped it
    fetches instead.
    """

    key: str
    filename: str
    repo_env: str
    repo_type: str
    local: Path
    what: str
    # A repo to fall back on when repo_env is unset, so the tool works on a fresh
    # install with no configuration. Only ever a PUBLIC repo holding data that is
    # fine to hand to everyone who installs the app — a default pointing at a
    # private repo would just 401 for every user but its owner. Empty means the
    # asset genuinely has no sensible default and the operator must name one.
    default_repo: str = ""


_APP = Path(__file__).resolve().parent.parent           # backend/app
_VENDOR = _APP.parent / "vendor"                        # backend/vendor

ASSETS: dict[str, Asset] = {
    "ctr-model": Asset(
        key="ctr-model",
        filename="ctr_model.joblib",
        repo_env="HF_ASSETS_CTR_MODEL_REPO",
        repo_type="model",
        local=_APP / "ml" / "ctr_model.joblib",
        what="the click-through-rate model the Email Writer scores drafts with",
    ),
    "ctr-stats": Asset(
        key="ctr-stats",
        filename="ctr_reference_stats.json",
        repo_env="HF_ASSETS_CTR_MODEL_REPO",
        repo_type="model",
        local=_APP / "ml" / "ctr_reference_stats.json",
        what="the reference statistics the CTR score is compared against",
    ),
    "influencers": Asset(
        key="influencers",
        filename="influencer_database.xlsx",
        repo_env="HF_ASSETS_INFLUENCER_REPO",
        repo_type="dataset",
        # Public edition: the same 14,632 profiles with the Email and Mobile columns
        # removed, so the Influencer Database works on a fresh install without anyone
        # having to publish a catalogue first. Set HF_ASSETS_INFLUENCER_REPO (Settings
        # -> Data repositories) to override with your own, contacts included.
        default_repo="vivekchakraverty/mr-ai-marketer-influencers-public",
        local=_APP / "data" / "influencer_database.xlsx",
        what="the Influencer Database catalogue",
    ),
    "guest-post-db": Asset(
        key="guest-post-db",
        filename="guest_post_database.xlsx",
        repo_env="HF_ASSETS_GUEST_POST_REPO",
        repo_type="dataset",
        local=_VENDOR / "guestpostsuggester" / "data" / "guest_post_database.xlsx",
        what="the Guest Post Suggester's site database",
    ),
    "opr-scores": Asset(
        key="opr-scores",
        filename="opr_scores.json",
        repo_env="HF_ASSETS_GUEST_POST_REPO",
        repo_type="dataset",
        local=_VENDOR / "guestpostsuggester" / "data" / "opr_scores.json",
        what="Open PageRank scores for the Guest Post Suggester",
    ),
}


def repo_for(asset: Asset) -> str:
    """The HF repo id for an asset, or "" if none is configured.

    Read at call time from the environment, falling back to the asset's default_repo.

    Only assets whose data is genuinely publishable carry a default; the rest stay unset,
    because the repos are the operator's to name and a default is a decision made on their
    behalf. The influencer catalogue has one so its tool works on a fresh install, and the
    edition it points at had its contact columns removed for exactly that reason. The
    environment always wins, so pointing Settings at your own repo overrides it.
    """
    return os.environ.get(asset.repo_env, "").strip() or asset.default_repo


def _fetch(asset: Asset, token: Optional[str]) -> Path:
    from huggingface_hub import hf_hub_download

    repo = repo_for(asset)
    return Path(
        hf_hub_download(
            repo_id=repo,
            repo_type=asset.repo_type,
            filename=asset.filename,
            token=token or None,
            cache_dir=str(CACHE_DIR),
        )
    )


def path_for(key: str, token: Optional[str] = None, required: bool = True) -> Optional[Path]:
    """Local path to an asset, downloading it once if needed.

    `token` is the user's own Hugging Face token. A gated or private repo needs it; a public
    one does not, which is why it is optional rather than demanded up front. When a caller
    passes nothing it falls back to HF_TOKEN, which Electron already puts in the backend's
    environment from Settings — otherwise every call site would have to thread the token
    through its own request model, and a private repo would 401 for the ones that don't.
    """
    asset = ASSETS[key]
    token = token or os.environ.get("HF_TOKEN", "").strip() or None

    cached = _resolved.get(key)
    if cached is not None and cached.exists():
        return cached

    # Still in a dev checkout, or an install from before this moved: use it. Checked before
    # the repo so a machine that already has the file never makes a network call.
    if asset.local.exists():
        _resolved[key] = asset.local
        return asset.local

    repo = repo_for(asset)
    if not repo:
        if required:
            raise AssetError(
                f"{asset.what} isn't available: no local copy, and {asset.repo_env} is not set. "
                f"Point it at a Hugging Face repo containing {asset.filename}."
            )
        return None

    with _lock:  # two requests racing on first use would otherwise both download
        again = _resolved.get(key)
        if again is not None and again.exists():
            return again
        try:
            path = _fetch(asset, token)
        except Exception as err:  # noqa: BLE001 — auth, network and 404 all mean the same here
            if required:
                raise AssetError(
                    f"couldn't fetch {asset.filename} from {repo}: {err}. "
                    "If the repo is private or gated, connect a Hugging Face token in Settings "
                    "that has access to it."
                ) from None
            return None
        _resolved[key] = path
        print(f"[hf-assets] {asset.key} fetched from {repo}")
        return path


def status() -> list[dict]:
    """What each asset is, where it would come from, and whether it is here yet.

    Used by the health check and worth having: "the Influencer Database is empty" is a much
    worse bug report than "influencers: no local copy, no repo configured".
    """
    out = []
    for asset in ASSETS.values():
        here = _resolved.get(asset.key)
        out.append({
            "key": asset.key,
            "what": asset.what,
            "filename": asset.filename,
            "repo": repo_for(asset),
            "present": bool((here and here.exists()) or asset.local.exists()),
        })
    return out
