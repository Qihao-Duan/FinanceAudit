#!/usr/bin/env python3
"""Download the small, license-declared benchmark subset used by FinanceAudit.

Only author-linked GitHub/Hugging Face artifacts are included. Large filing
corpora and benchmarks without a public data release are intentionally omitted.
All URLs are pinned to immutable repository revisions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MAX_TOTAL_BYTES = 250 * 1024 * 1024
DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "data" / "benchmarks"


@dataclass(frozen=True)
class Artifact:
    benchmark: str
    relative_path: str
    url: str
    expected_bytes: int
    license: str
    sha256: str | None = None


ARTIFACTS = (
    # FinDVer, upstream revision e8bb237def4ce555a606a45edba22666e31df248.
    Artifact("findver", "README_UPSTREAM.md", "https://raw.githubusercontent.com/yilunzhao/FinDVer/e8bb237def4ce555a606a45edba22666e31df248/README.md", 5543, "MIT", "eaa1832c55a0f560ea9f3e7738cd38248cc3c200f56f607f8a19181db768e0e4"),
    Artifact("findver", "LICENSE", "https://raw.githubusercontent.com/yilunzhao/FinDVer/e8bb237def4ce555a606a45edba22666e31df248/LICENSE", 1067, "MIT", "4b775c6b1cf1d6dcfe73c6f1947720c6f8bf4aa7558f0ae6ba0c669b05f68260"),
    Artifact("findver", "test.json", "https://raw.githubusercontent.com/yilunzhao/FinDVer/e8bb237def4ce555a606a45edba22666e31df248/data/test.json", 1831233, "MIT", "7cfef6889d0355a0d07c06e66ab46e6fcd4ebf06080c0f908409c7be4c7aa3b7"),
    Artifact("findver", "testmini.json", "https://raw.githubusercontent.com/yilunzhao/FinDVer/e8bb237def4ce555a606a45edba22666e31df248/data/testmini.json", 971086, "MIT", "09ffb848fec6cc38c7df153500f63388fa5fb7d9bc78300690ddbb83fa4e403a"),

    # FinAuditing, CC BY 4.0 dataset cards and Parquet evaluation sets.
    Artifact("finauditing", "FinSM/README_UPSTREAM.md", "https://huggingface.co/datasets/TheFinAI/FinSM/resolve/08084d379017fcc387800300315e1b14b6ea0242/README.md?download=true", 1328, "CC-BY-4.0", "ce3d833a4a8a4a2f855d19f2b9510b0671bc002618dd34dbc137deb1f0eb6c60"),
    Artifact("finauditing", "FinSM/test.parquet", "https://huggingface.co/datasets/TheFinAI/FinSM/resolve/08084d379017fcc387800300315e1b14b6ea0242/data/test-00000-of-00001.parquet?download=true", 2881818, "CC-BY-4.0", "24ceb67ada9c4e3d984711f962bcf7da4dc9d6080c5b70fe71f5218f1324d942"),
    Artifact("finauditing", "FinRE/README_UPSTREAM.md", "https://huggingface.co/datasets/TheFinAI/FinRE/resolve/a524406e89d488203cfe0e822750955f954ffeaa/README.md?download=true", 1384, "CC-BY-4.0", "abe9653009abfa20a193269309207631f47bcf49fdcaf4a5092accc6ad1f54cd"),
    Artifact("finauditing", "FinRE/test.parquet", "https://huggingface.co/datasets/TheFinAI/FinRE/resolve/a524406e89d488203cfe0e822750955f954ffeaa/data/test-00000-of-00001.parquet?download=true", 4278211, "CC-BY-4.0", "82b067c6821ccc6bc371067b6bfae4e513d85de8a55e46cd5ecf9372ff63097f"),
    Artifact("finauditing", "FinMR/README_UPSTREAM.md", "https://huggingface.co/datasets/TheFinAI/FinMR/resolve/3b1babd0487c1d5a7229f7a26a1654d3fb693e92/README.md?download=true", 1312, "CC-BY-4.0", "01bc85c8cdf75fef05cdd056816d3db0f3634029c80adcb56904d7fa689f97c2"),
    Artifact("finauditing", "FinMR/test.parquet", "https://huggingface.co/datasets/TheFinAI/FinMR/resolve/3b1babd0487c1d5a7229f7a26a1654d3fb693e92/data/test-00000-of-00001.parquet?download=true", 4787195, "CC-BY-4.0", "a47cbef89ffd9e52b1b41340d9605314b36a46805cba7c1718e780399bdb50df"),

    # EDINET-Bench fraud-detection configuration only, under PDL 1.0.
    Artifact("edinet_bench", "README_UPSTREAM.md", "https://huggingface.co/datasets/SakanaAI/EDINET-Bench/resolve/fdd5db0f21567ebc5ff55a169312b6ca21735376/README.md?download=true", 10292, "PDL-1.0", "b8adef15dab88007ede07474398d81c2e1d7a1b0f619c044926400cde329514d"),
    Artifact("edinet_bench", "LICENSE", "https://huggingface.co/datasets/SakanaAI/EDINET-Bench/resolve/fdd5db0f21567ebc5ff55a169312b6ca21735376/LICENSE?download=true", 327, "PDL-1.0", "95605f7fc01bfefe15b35b08637bfa1835c50e9978e2f3a2eae2383957c06903"),
    Artifact("edinet_bench", "LICENSE.pdf", "https://huggingface.co/datasets/SakanaAI/EDINET-Bench/resolve/fdd5db0f21567ebc5ff55a169312b6ca21735376/LICENSE.pdf?download=true", 207095, "PDL-1.0", "48153b4bf7130d07ff58ec620d05f24d8402a0a5c18f1583287cfe8ea93f3a5a"),
    Artifact("edinet_bench", "fraud_detection/train.parquet", "https://huggingface.co/datasets/SakanaAI/EDINET-Bench/resolve/fdd5db0f21567ebc5ff55a169312b6ca21735376/fraud_detection/train-00000-of-00001.parquet?download=true", 37611066, "PDL-1.0", "a757b05555a36a4ce3e077d6ff4033ced3693a45a3a1a92ad412d3e0338a7eec"),
    Artifact("edinet_bench", "fraud_detection/test.parquet", "https://huggingface.co/datasets/SakanaAI/EDINET-Bench/resolve/fdd5db0f21567ebc5ff55a169312b6ca21735376/fraud_detection/test-00000-of-00001.parquet?download=true", 10013911, "PDL-1.0", "83551dfe6c9343b8fee5ffe185a6d6359d269e6606b0f97fbed7afdf3166d64d"),

    # FinRAGBench-V author-generated retrieval labels only. The 435 GB corpus,
    # PDFs, and citation-label image archive are omitted because they contain
    # third-party source material whose redistribution terms are not uniform.
    Artifact("finragbench_v", "README_UPSTREAM.md", "https://huggingface.co/datasets/zhaosuifeng/FinRAGBench-V/resolve/d0d65255c94e687caa81ac9da7758ed25ff046a5/README.md?download=true", 4326, "Apache-2.0", "232b7dee05689b8c1d70c0aedec0094b88e422e7d319a46c4923357c0c6b9a22"),
    Artifact("finragbench_v", "queries/queries_ch.json", "https://huggingface.co/datasets/zhaosuifeng/FinRAGBench-V/resolve/d0d65255c94e687caa81ac9da7758ed25ff046a5/queries/queries_ch.json?download=true", 403742, "Apache-2.0", "94e43cb0cb7cbed7eeb42944511cc7b47b74b74395b1514b2b722708d3040fa0"),
    Artifact("finragbench_v", "queries/queries_en.json", "https://huggingface.co/datasets/zhaosuifeng/FinRAGBench-V/resolve/d0d65255c94e687caa81ac9da7758ed25ff046a5/queries/queries_en.json?download=true", 269992, "Apache-2.0", "e748958ed09450066cd06cf8dc5ee324c8c66380e3169f235e3f66faaa5277c6"),
    Artifact("finragbench_v", "qrels/qrels_ch.tsv", "https://huggingface.co/datasets/zhaosuifeng/FinRAGBench-V/resolve/d0d65255c94e687caa81ac9da7758ed25ff046a5/qrels/qrels_ch.tsv?download=true", 166426, "Apache-2.0", "182fedb91c217a65d8560d14cfd32405e7cfd6ff2949f2dd08196492a3e77407"),
    Artifact("finragbench_v", "qrels/qrels_en.tsv", "https://huggingface.co/datasets/zhaosuifeng/FinRAGBench-V/resolve/d0d65255c94e687caa81ac9da7758ed25ff046a5/qrels/qrels_en.tsv?download=true", 60230, "Apache-2.0", "dd3e5085dcf74339ba511d7b8844a4e9c0d3e3655b6564a9f276f840263202c0"),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def matches(path: Path, artifact: Artifact) -> tuple[bool, str]:
    if not path.is_file():
        return False, "missing"
    size = path.stat().st_size
    if size != artifact.expected_bytes:
        return False, f"size {size} != {artifact.expected_bytes}"
    actual_hash = sha256_file(path)
    if artifact.sha256 and actual_hash != artifact.sha256:
        return False, f"sha256 {actual_hash} != {artifact.sha256}"
    return True, actual_hash


def download(artifact: Artifact, target: Path, force: bool) -> str:
    ok, detail = matches(target, artifact)
    if ok and not force:
        print(f"OK   {target} ({artifact.expected_bytes:,} bytes)")
        return detail

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    partial.unlink(missing_ok=True)
    request = Request(
        artifact.url,
        headers={"User-Agent": "FinanceAudit-benchmark-fetcher/1.0"},
    )
    print(f"GET  {artifact.url}")
    digest = hashlib.sha256()
    count = 0
    try:
        with urlopen(request, timeout=60) as response, partial.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
                count += len(chunk)
                if count > artifact.expected_bytes:
                    raise ValueError(
                        f"download exceeded expected size for {artifact.relative_path}"
                    )
    except (HTTPError, URLError, OSError, ValueError):
        partial.unlink(missing_ok=True)
        raise

    actual_hash = digest.hexdigest()
    if count != artifact.expected_bytes:
        partial.unlink(missing_ok=True)
        raise ValueError(
            f"size mismatch for {artifact.relative_path}: {count} != {artifact.expected_bytes}"
        )
    if artifact.sha256 and actual_hash != artifact.sha256:
        partial.unlink(missing_ok=True)
        raise ValueError(
            f"sha256 mismatch for {artifact.relative_path}: {actual_hash} != {artifact.sha256}"
        )
    os.replace(partial, target)
    print(f"SAVE {target} ({count:,} bytes; sha256 {actual_hash})")
    return actual_hash


def select_artifacts(includes: list[str]) -> list[Artifact]:
    unknown = sorted(set(includes) - {item.benchmark for item in ARTIFACTS})
    if unknown:
        raise SystemExit(f"unknown benchmark(s): {', '.join(unknown)}")
    return [item for item in ARTIFACTS if not includes or item.benchmark in includes]


def write_manifest(root: Path, records: list[dict[str, object]]) -> None:
    manifest = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "policy": "author-linked, revision-pinned artifacts; no large filing corpora",
        "total_bytes": sum(int(record["bytes"]) for record in records),
        "artifacts": records,
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "DOWNLOAD_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--include", action="append", default=[], metavar="NAME")
    parser.add_argument("--list", action="store_true", help="list the pinned download plan")
    parser.add_argument("--verify", action="store_true", help="verify existing files only")
    parser.add_argument("--force", action="store_true", help="redownload valid existing files")
    args = parser.parse_args()

    selected = select_artifacts(args.include)
    planned_bytes = sum(item.expected_bytes for item in selected)
    if planned_bytes > MAX_TOTAL_BYTES:
        raise SystemExit(
            f"planned download {planned_bytes:,} exceeds cap {MAX_TOTAL_BYTES:,} bytes"
        )

    if args.list:
        for item in selected:
            print(f"{item.benchmark:16} {item.expected_bytes:>10,}  {item.relative_path}")
        print(f"TOTAL {planned_bytes:,} bytes")
        return 0

    records: list[dict[str, object]] = []
    failures = 0
    for item in selected:
        target = args.root / item.benchmark / item.relative_path
        if args.verify:
            ok, detail = matches(target, item)
            print(f"{'OK  ' if ok else 'FAIL'} {target}: {detail}")
            if not ok:
                failures += 1
                continue
            actual_hash = detail
        else:
            try:
                actual_hash = download(item, target, args.force)
            except (HTTPError, URLError, OSError, ValueError) as error:
                print(f"FAIL {target}: {error}", file=sys.stderr)
                failures += 1
                continue

        record = asdict(item)
        record.update(
            {
                "path": str(target.relative_to(args.root)),
                "bytes": target.stat().st_size,
                "sha256": actual_hash,
            }
        )
        records.append(record)

    if not args.verify and records:
        write_manifest(args.root, records)
    print(
        f"Verified {len(records)}/{len(selected)} artifacts; "
        f"{sum(int(item['bytes']) for item in records):,} bytes."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
