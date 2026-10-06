"""Build a small preference index from PAMELA's training annotations.

Usage: python build_pamela_index.py --remote
       python build_pamela_index.py --archive path/to/PAMELA.zip

The remote mode reads only ZIP directory and JSON ranges from the public bucket.
It never downloads the 2.25 GB image collection. The resulting index contains
aggregate ratings only, with no participant IDs or demographics.
"""

from __future__ import annotations

import argparse
import io
import json
import zipfile
from collections import defaultdict
from pathlib import Path

ARCHIVE_URL = "https://huggingface.co/buckets/vivekchakraverty/images/resolve/PAMELA.zip"
ANNOTATIONS = "PAMELA/annotations/pamela_train.json"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "backend/app/services/pamela_affinity.json"


class RemoteZip(io.RawIOBase):
    """Seekable HTTP range reader for the public archive."""

    def __init__(self, url: str) -> None:
        import requests

        self.session = requests.Session()
        response = self.session.head(url, allow_redirects=True, timeout=30)
        response.raise_for_status()
        self.url = response.url
        self.size = int(response.headers["Content-Length"])
        self.pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.size + offset
        return self.pos

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = self.size - self.pos
        if size <= 0:
            return b""
        last = min(self.size - 1, self.pos + size - 1)
        response = self.session.get(self.url, headers={"Range": f"bytes={self.pos}-{last}"}, timeout=90)
        response.raise_for_status()
        if response.status_code != 206:
            raise RuntimeError("The archive server did not honor byte ranges")
        self.pos += len(response.content)
        return response.content


def build(rows: list[dict]) -> dict:
    by_person: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_person[row["participant_id"]].append(float(row["original_score"]))
    baseline = {person: sum(scores) / len(scores) for person, scores in by_person.items()}

    buckets: dict[str, dict[str, dict[str, list[float]]]] = {
        "group": defaultdict(lambda: defaultdict(list)),
        "style": defaultdict(lambda: defaultdict(list)),
        "type": defaultdict(lambda: defaultdict(list)),
    }
    image_ids: dict[str, dict[str, set[int]]] = {
        "group": defaultdict(set), "style": defaultdict(set), "type": defaultdict(set)
    }
    for row in rows:
        meta = row["image_metadata"]
        for dimension in buckets:
            key = meta.get(dimension)
            if key is None or key == "":
                continue
            key = str(key)
            buckets[dimension][key][row["participant_id"]].append(float(row["original_score"]))
            image_ids[dimension][key].add(int(row["image_id"]))

    index = {}
    for dimension, keys in buckets.items():
        dimension_result = {}
        for key, participants in sorted(keys.items()):
            scores = [score for values in participants.values() for score in values]
            eligible = [
                (person, sum(values) / len(values))
                for person, values in participants.items()
                if len(values) >= 3
            ]
            admirers = [
                person for person, mean in eligible
                if mean >= 4.0 and mean - baseline[person] >= 0.2
            ]
            dimension_result[key] = {
                "ratings": len(scores),
                "images": len(image_ids[dimension][key]),
                "mean_rating": round(sum(scores) / len(scores), 3),
                "eligible_participants": len(eligible),
                "admirer_participants": len(admirers),
                "admirer_share": round(len(admirers) / len(eligible), 3) if eligible else None,
            }
        index[dimension] = dimension_result
    return {
        "source": "https://huggingface.co/buckets/vivekchakraverty/images",
        "upstream_dataset": "https://huggingface.co/datasets/bethgelab/PAMELA",
        "license": "CC BY 4.0",
        "split": "pamela_train",
        "rating_count": len(rows),
        "participant_count": len(by_person),
        "method": "Admirer: at least 3 ratings in a category, category mean >= 4/5, and >= 0.2 above that participant's training-set mean.",
        "dimensions": index,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--archive", type=Path)
    source.add_argument("--remote", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    handle = RemoteZip(ARCHIVE_URL) if args.remote else args.archive.open("rb")
    with handle, zipfile.ZipFile(handle) as archive:
        rows = json.loads(archive.read(ANNOTATIONS))
    result = build(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {args.output}: {result['rating_count']} ratings, {result['participant_count']} participants")


if __name__ == "__main__":
    main()
