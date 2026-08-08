"""Store a Common Crawl WAT shard and its target-domain backlink partition on HF.

Run this outside the Space. It preserves the raw WAT object and uploads a small
Parquet sidecar that the CPU dashboard can query efficiently.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
from urllib.parse import urlparse

import pandas as pd
import requests
from huggingface_hub import HfFileSystem, batch_bucket_files, create_bucket

SPACE_DIR = Path(__file__).resolve().parents[2] / "resources" / "backlink-analyzer-space"
sys.path.insert(0, str(SPACE_DIR))
from backlink_data import canonical_target_domain, extract_backlinks_from_wat, manifest_template  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True, help="HF bucket ID, e.g. user/common-crawl-wat-backlinks")
    parser.add_argument("--target-domain", required=True, help="Destination domain to extract backlinks for")
    parser.add_argument("--crawl", required=True, help="Common Crawl name, e.g. CC-MAIN-2026-30")
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--wat-url", help="HTTPS URL of one .wat.gz file")
    input_group.add_argument("--wat-file", type=Path, help="Already-downloaded .wat.gz file")
    parser.add_argument("--max-records", type=int, help="Process at most this many WAT metadata records (smoke testing)")
    parser.add_argument("--no-store-raw", action="store_true", help="Upload only the derived Parquet and manifest")
    parser.add_argument("--dry-run", action="store_true", help="Parse locally but make no Hugging Face writes")
    return parser.parse_args()


def download_wat(url: str, destination: Path) -> None:
    with requests.get(url, stream=True, timeout=(20, 300)) as response:
        response.raise_for_status()
        with destination.open("wb") as handle:
            shutil.copyfileobj(response.raw, handle, length=1024 * 1024)


def read_manifest(fs: HfFileSystem, bucket: str, target_domain: str) -> dict:
    manifest_path = f"hf://buckets/{bucket}/manifest/{target_domain}.json"
    try:
        with fs.open(manifest_path, "rb") as handle:
            loaded = json.loads(handle.read().decode("utf-8"))
            return loaded if isinstance(loaded, dict) else manifest_template(target_domain)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return manifest_template(target_domain)


def object_name(source: str, local_file: Path) -> str:
    preferred = Path(urlparse(source).path).name if source.startswith(("http://", "https://")) else local_file.name
    return preferred or f"wat-{hashlib.sha256(source.encode()).hexdigest()[:12]}.wat.gz"


def main() -> int:
    args = parse_args()
    target_domain = canonical_target_domain(args.target_domain)
    if not target_domain:
        raise SystemExit("--target-domain must be a valid domain or HTTP(S) URL")
    if args.max_records is not None and args.max_records < 1:
        raise SystemExit("--max-records must be positive")

    with TemporaryDirectory(prefix="cc-wat-") as temporary_dir:
        temp_dir = Path(temporary_dir)
        if args.wat_url:
            local_wat = temp_dir / object_name(args.wat_url, temp_dir / "download.wat.gz")
            print(f"Downloading {args.wat_url}")
            download_wat(args.wat_url, local_wat)
            wat_source = args.wat_url
        else:
            local_wat = args.wat_file.resolve()
            if not local_wat.is_file():
                raise SystemExit(f"WAT file does not exist: {local_wat}")
            wat_source = str(local_wat)

        print(f"Parsing {local_wat.name} for backlinks to {target_domain}")
        with local_wat.open("rb") as stream:
            rows = extract_backlinks_from_wat(
                stream,
                target_domain=target_domain,
                crawl=args.crawl,
                wat_source=wat_source,
                max_records=args.max_records,
            )
        dataframe = pd.DataFrame(rows)
        if not dataframe.empty:
            dataframe = dataframe.drop_duplicates(
                subset=["source_url", "target_url", "anchor_text", "link_type", "crawl"]
            )

        fingerprint = hashlib.sha256(f"{wat_source}|{target_domain}".encode("utf-8")).hexdigest()[:16]
        wat_name = object_name(wat_source, local_wat)
        raw_path = f"raw/{args.crawl}/{wat_name}"
        parquet_path = f"derived/target_domain={target_domain}/backlinks-{fingerprint}.parquet"
        local_parquet = temp_dir / f"backlinks-{fingerprint}.parquet"
        dataframe.to_parquet(local_parquet, index=False)
        print(f"Found {len(dataframe):,} qualifying links; derived file is {local_parquet.stat().st_size:,} bytes")

        if args.dry_run:
            print("Dry run complete; no bucket was created or changed.")
            return 0

        create_bucket(args.bucket, exist_ok=True)
        filesystem = HfFileSystem()
        manifest = read_manifest(filesystem, args.bucket, target_domain)
        partition = {
            "path": parquet_path,
            "crawl": args.crawl,
            "wat_source": wat_source,
            "raw_wat_path": raw_path if not args.no_store_raw else None,
            "rows": int(len(dataframe)),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        old_partitions = [item for item in manifest.get("partitions", []) if item.get("path") != parquet_path]
        manifest["partitions"] = [*old_partitions, partition]
        manifest["target_domain"] = target_domain
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        local_manifest = temp_dir / f"{target_domain}.json"
        local_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

        uploads: list[tuple[str | Path, str]] = [
            (local_parquet, parquet_path),
            (local_manifest, f"manifest/{target_domain}.json"),
        ]
        if not args.no_store_raw:
            uploads.insert(0, (local_wat, raw_path))
        batch_bucket_files(args.bucket, add=uploads)
        print(f"Uploaded {len(uploads)} object(s) to hf://buckets/{args.bucket}")
        print(f"Dashboard key: {target_domain}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
