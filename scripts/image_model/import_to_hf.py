"""Import a licensed Diffusers model into the app's Hugging Face Bucket.

Examples:

    HF_TOKEN=hf_... python scripts/image_model/import_to_hf.py \
      --source-dir /models/my-diffusers-export --confirm-license

    HF_TOKEN=hf_... python scripts/image_model/import_to_hf.py \
      --source-repo org/source-diffusers-model --confirm-license

The source model must be a Diffusers repository containing ``model_index.json``.
The Modal image worker syncs this exact target bucket during Settings deployment.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

# Your own Bucket. Pass --target or set BRANDFORGE_IMAGE_BUCKET; there is no default,
# because uploading into someone else's namespace is never what you meant to do.
DEFAULT_TARGET = os.environ.get("BRANDFORGE_IMAGE_BUCKET", "").strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source-dir", type=Path, help="Local standard Diffusers model folder.")
    source.add_argument("--source-repo", help="Hub model repository to mirror after license approval.")
    parser.add_argument("--target", default=DEFAULT_TARGET, help=f"Destination Bucket ID (default: {DEFAULT_TARGET}).")
    parser.add_argument("--token", default=os.environ.get("HF_TOKEN", ""), help="HF token with write access; defaults to HF_TOKEN.")
    parser.add_argument("--private", action="store_true", help="Create a private target Bucket when it does not yet exist.")
    parser.add_argument(
        "--confirm-license",
        action="store_true",
        help="Confirm that the source license permits this copy and you have accepted any gated-model terms.",
    )
    return parser.parse_args()


def validate_diffusers_folder(path: Path) -> None:
    if not path.is_dir():
        raise SystemExit(f"Source directory does not exist: {path}")
    if not (path / "model_index.json").is_file():
        raise SystemExit(
            f"{path} is not a standard Diffusers export: model_index.json is required. "
            "Export the source model with Diffusers before importing it."
        )


def upload(sync_bucket: Any, source: Path, target: str, token: str) -> None:
    # Bucket sync is resumable and chunk-deduplicated, which matters for
    # multi-gigabyte image checkpoints and later model updates.
    sync_bucket(
        str(source),
        f"hf://buckets/{target}",
        exclude=[".git/**", "**/__pycache__/**", "*.pyc"],
        token=token,
    )


def main() -> None:
    args = parse_args()
    if not args.confirm_license:
        raise SystemExit("Pass --confirm-license after verifying the source model's redistribution terms.")

    try:
        from huggingface_hub import create_bucket, get_token, snapshot_download, sync_bucket
    except ImportError as err:
        raise SystemExit(
            "Install the project's backend dependencies before importing a model: "
            "python -m pip install -r backend/requirements.txt"
        ) from err

    token = args.token.strip() or (get_token() or "").strip()
    if not token:
        raise SystemExit(
            "Set HF_TOKEN, pass --token, or run huggingface-cli login with write access to the target repository."
        )

    print(f"Checking destination Bucket {args.target}...")
    create_bucket(args.target, private=args.private, exist_ok=True, token=token)

    if args.source_dir:
        source = args.source_dir.expanduser().resolve()
        validate_diffusers_folder(source)
    else:
        # Keep the source in Hugging Face's normal cache, not a TemporaryDirectory:
        # snapshot_download resumes this path after an interrupted multi-gigabyte
        # download, and sync_bucket resumes target-side transfers.
        print(f"Downloading source model {args.source_repo} into the Hugging Face cache...")
        source = Path(
            snapshot_download(
                repo_id=args.source_repo,
                repo_type="model",
                token=token,
            )
        )
        validate_diffusers_folder(source)

    print(f"Syncing {source} to Bucket {args.target}...")
    upload(sync_bucket, source, args.target, token)

    print(f"Imported image model into https://huggingface.co/buckets/{args.target}")
    print("Open the app's Settings and run 'Set up my GPU' to deploy the Modal image worker.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("Import interrupted. Re-run the same command; Hub uploads resume safely.")
