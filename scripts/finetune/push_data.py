#!/usr/bin/env python
"""Upload train/val/test.jsonl to a PRIVATE Hugging Face dataset.

Run this on YOUR machine, once, before renting the GPU.

Why bother when scp works: it makes the GPU box disposable. If the instance dies
at 80% through training, a brand new box can pull the data from here and the
checkpoints from the model repo, and carry on — without your laptop being on, or
you remembering which files went where. scp makes your laptop a dependency of
the recovery path; this removes it.

The repo is created private. These are other people's public posts collected for
model training, and there is no reason to republish them.

    python push_data.py --repo you/bluesky-post-data

The token is READ from the environment or the pipeline's env file; this script
never writes it anywhere.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def find_token() -> str:
    token = (os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN") or "").strip()
    if token:
        return token
    # Same file the briefs stage reads, same rules: we read it, never write it.
    appdata = os.environ.get("APPDATA", "")
    for candidate in (
        Path(appdata) / "mr-ai-marketer" / "finetune" / "finetune.env",
        Path(appdata) / "mr-ai-marketer" / "social-post.env",
    ):
        if candidate.exists():
            for line in candidate.read_text(encoding="utf-8-sig").splitlines():
                name, sep, value = line.strip().partition("=")
                if sep and name.strip() in ("HF_TOKEN", "HUGGINGFACE_TOKEN"):
                    value = value.strip().strip("'\"")
                    if value:
                        return value
    sys.exit(
        "No HF_TOKEN found. Set it in your shell, or add HF_TOKEN=... to\n"
        f"  {Path(appdata) / 'mr-ai-marketer' / 'finetune' / 'finetune.env'}\n"
        "It needs WRITE scope. This script will not write the token for you."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="e.g. you/bluesky-post-data")
    parser.add_argument(
        "--dir",
        default=str(Path(os.environ.get("APPDATA", "")) / "mr-ai-marketer" / "finetune"),
    )
    args = parser.parse_args()

    from huggingface_hub import HfApi

    token = find_token()
    src = Path(args.dir)
    files = [src / f"{n}.jsonl" for n in ("train", "val", "test")]
    missing = [f.name for f in files if not f.exists()]
    if missing:
        sys.exit(f"Missing {', '.join(missing)} in {src} — run `finetune pairs` first.")

    api = HfApi(token=token)
    api.create_repo(args.repo, repo_type="dataset", private=True, exist_ok=True)
    print(f"dataset repo ready: {args.repo} (private)")

    for path in files:
        mb = path.stat().st_size / 1e6
        print(f"  uploading {path.name} ({mb:.1f} MB)…", flush=True)
        api.upload_file(
            path_or_fileobj=str(path),
            path_in_repo=path.name,
            repo_id=args.repo,
            repo_type="dataset",
        )

    print(f"\nDone. Put this in the box's /workspace/.env:\n  DATA_REPO={args.repo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
