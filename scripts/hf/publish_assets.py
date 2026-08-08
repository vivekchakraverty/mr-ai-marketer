"""Move the app's datasets and models out of the repo and into Hugging Face.

Run once per asset group by whoever owns them. After this, the files can leave the source
tree and the build stops shipping them — `app/services/hf_assets.py` fetches each one on
first use instead.

    python scripts/hf/publish_assets.py --owner your-hf-username --dry-run
    python scripts/hf/publish_assets.py --owner your-hf-username --only ctr

Visibility is a per-asset decision and the defaults here are the conservative ones:

* **private** for the CTR *training data*. This is the corpus, not the product. The app never
  reads it; only `backend/ml/ctr/train_ctr_model.py` does, and only when retraining. Keeping
  it private is the whole "protect the training corpus" step — a model can be distributed
  without the data it was fitted on.
* **gated** for the catalogues (influencers, guest-post sites). Gating is the useful middle:
  users accept terms and are approved against their own HF account, downloads are
  attributable, and access can be revoked per person. Public would work too and needs no
  token; it just gives up all three of those.
* **public** for the fitted CTR model, unless you say otherwise. It is 13 MB of tree weights
  that reveal little about the underlying campaigns, and making it public means the Email
  Writer works on a fresh install with no token at all.

Gating cannot be switched on from the API — it is a checkbox in the repo's settings on the
website. This script prints the URL to click when it creates a repo meant to be gated, rather
than pretending it did something it cannot.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"


@dataclass(frozen=True)
class Group:
    """One HF repo and the files that belong in it."""

    key: str
    repo_suffix: str
    repo_type: str          # "model" | "dataset"
    files: list[Path]
    visibility: str         # "public" | "gated" | "private"
    env_var: str
    why: str
    delete_after: list[Path] = field(default_factory=list)


GROUPS: list[Group] = [
    Group(
        key="ctr",
        repo_suffix="mr-ai-marketer-ctr",
        repo_type="model",
        files=[
            BACKEND / "app" / "ml" / "ctr_model.joblib",
            BACKEND / "app" / "ml" / "ctr_reference_stats.json",
        ],
        visibility="public",
        env_var="HF_ASSETS_CTR_MODEL_REPO",
        why="the fitted CTR model the Email Writer scores drafts with",
    ),
    Group(
        key="ctr-training",
        repo_suffix="mr-ai-marketer-ctr-training",
        repo_type="dataset",
        files=[
            BACKEND / "ml" / "ctr" / "data" / "train_data.csv",
            BACKEND / "ml" / "ctr" / "data" / "test_Data.csv",
        ],
        visibility="private",
        env_var="CTR_TRAINING_REPO",
        why="the campaign data the CTR model was fitted on — never distributed with the app",
        # The point of the exercise: once this is on the Hub, the copies in the source tree
        # should go, so a clone of the repo does not carry the corpus.
        delete_after=[
            BACKEND / "ml" / "ctr" / "data" / "train_data.csv",
            BACKEND / "ml" / "ctr" / "data" / "test_Data.csv",
        ],
    ),
    Group(
        key="influencers",
        repo_suffix="mr-ai-marketer-influencers",
        repo_type="dataset",
        files=[BACKEND / "app" / "data" / "influencer_database.xlsx"],
        visibility="gated",
        env_var="HF_ASSETS_INFLUENCER_REPO",
        why="the Influencer Database catalogue",
        delete_after=[BACKEND / "app" / "data" / "influencer_database.xlsx"],
    ),
    Group(
        key="guest-post",
        repo_suffix="mr-ai-marketer-guest-post",
        repo_type="dataset",
        files=[
            BACKEND / "vendor" / "guestpostsuggester" / "data" / "guest_post_database.xlsx",
            BACKEND / "vendor" / "guestpostsuggester" / "data" / "opr_scores.json",
        ],
        visibility="gated",
        env_var="HF_ASSETS_GUEST_POST_REPO",
        why="the Guest Post Suggester's site catalogue and PageRank cache",
        # Left in place by default: this one lives under vendor/, and deleting a vendored
        # project's own data files makes the subtree diverge from upstream. Pass --prune to
        # remove them anyway once you are sure the fetch path works.
    ),
]


def _api(token: str | None):
    from huggingface_hub import HfApi

    # No token argument means the Hub uses the machine's stored login, which is how the RAG
    # index was published and avoids a credential on the command line.
    return HfApi(token=token or None)


def publish(group: Group, owner: str, token: str | None, dry_run: bool, prune: bool) -> bool:
    repo_id = f"{owner}/{group.repo_suffix}"
    missing = [f for f in group.files if not f.exists()]
    if missing:
        print(f"  ! skipping {group.key}: not on disk — {', '.join(m.name for m in missing)}")
        return False

    total = sum(f.stat().st_size for f in group.files)
    print(f"\n{group.key}: {group.why}")
    print(f"  repo       {repo_id}  ({group.repo_type}, {group.visibility})")
    print(f"  files      {', '.join(f.name for f in group.files)}  [{total / 1e6:.1f} MB]")
    if group.env_var:
        print(f"  set        {group.env_var}={repo_id}")

    if dry_run:
        print("  (dry run — nothing uploaded)")
        return True

    api = _api(token)
    api.create_repo(
        repo_id=repo_id,
        repo_type=group.repo_type,
        # "gated" is not a creation flag — the repo starts private and the gate is turned on
        # in the website settings. Creating it private first means it is never briefly public.
        private=group.visibility in ("private", "gated"),
        exist_ok=True,
    )
    for f in group.files:
        api.upload_file(
            path_or_fileobj=str(f),
            path_in_repo=f.name,
            repo_id=repo_id,
            repo_type=group.repo_type,
        )
        print(f"  uploaded   {f.name}")

    if group.visibility == "gated":
        kind = "datasets/" if group.repo_type == "dataset" else ""
        print(f"  ACTION     turn gating on at https://huggingface.co/{kind}{repo_id}/settings")
        print("             (and flip visibility to public once gated, so approved users can pull it)")

    to_delete = group.delete_after if not prune else group.delete_after + group.files
    for f in dict.fromkeys(to_delete):
        if f.exists():
            f.unlink()
            print(f"  removed    {f.relative_to(REPO_ROOT)}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--owner", required=True, help="your Hugging Face username or org")
    parser.add_argument("--token", default="", help="HF token; omit to use your stored login")
    parser.add_argument("--only", default="", help=f"one of: {', '.join(g.key for g in GROUPS)}")
    parser.add_argument("--dry-run", action="store_true", help="print what would happen, upload nothing")
    parser.add_argument("--prune", action="store_true",
                        help="also delete vendored data files after upload (diverges from upstream)")
    args = parser.parse_args()

    groups = [g for g in GROUPS if not args.only or g.key == args.only]
    if not groups:
        print(f"no such group: {args.only}", file=sys.stderr)
        return 2

    done = sum(publish(g, args.owner, args.token or None, args.dry_run, args.prune) for g in groups)

    if not args.dry_run and done:
        print("\nNow set these for the backend (backend/.env or the environment):")
        for g in groups:
            if g.env_var and g.env_var.startswith("HF_ASSETS_"):
                print(f"  {g.env_var}={args.owner}/{g.repo_suffix}")
        print("\nFiles removed from the source tree are still in git history. If the corpus was "
              "never meant to be public, rewrite history before pushing, or start the public "
              "repo from a fresh commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
