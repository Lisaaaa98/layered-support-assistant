"""Download the public DBS documents this project reads.

The documents are DBS's own published terms. They are fetched at build time
rather than committed, so the repository does not redistribute someone else's
copyrighted material, and so a stale copy can never drift out of sync with
what the bank currently publishes.

The flip side is that the sources can change under you. That is not a
hypothetical here: the two card agreements already disagree on three figures,
and `docs/findings.md` documents how the pipeline adjudicates them. If a
download changes a figure, re-run the evaluation before trusting it.
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import domain  # noqa: E402

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"


def main():
    manifest = json.loads(domain.sources_path().read_text(encoding="utf-8"))
    RAW = domain.raw_dir()
    RAW.mkdir(parents=True, exist_ok=True)
    failed = []

    for doc in manifest["docs"]:
        target = RAW / doc["file"]
        request = urllib.request.Request(doc["url"], headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
        except Exception as exc:  # noqa: BLE001 - report and continue
            failed.append((doc["file"], exc))
            print(f"  {doc['file']:<28} FAILED  {exc}")
            continue
        target.write_bytes(body)
        print(f"  {doc['file']:<28} {len(body) // 1024:>5} KB")

    print(f"\n{len(manifest['docs']) - len(failed)}/{len(manifest['docs'])} downloaded "
          f"to {RAW.relative_to(ROOT)}")
    if failed:
        print("Re-run for the failures, or check whether DBS moved the page.")
        return 1
    print("Next: uv run python src/ingest.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
