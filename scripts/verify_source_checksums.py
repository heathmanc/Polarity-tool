"""Verify SHA256SUMS.txt against the tracked source tree.

SHA256SUMS.txt is the repository's source-integrity manifest and is referenced
by the handoff checklist, but nothing enforced it. An unenforced manifest drifts
silently, and a drifted manifest is worse than none at all: it still looks like
evidence during a handoff while no longer describing the code being shipped.

The manifest covers every tracked file except itself and the git-archive
substituted battery_inspector/_git_archival.txt, whose content is rewritten by
`git archive` and therefore cannot carry a fixed digest.

Three failure modes are reported separately, because they call for different
responses: a changed digest means a file was edited without regenerating the
manifest; a missing entry means a new file was added without recording it; a
stale entry means a file was deleted or renamed.

Run with --write to regenerate the manifest after an intentional change.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "SHA256SUMS.txt"
EXCLUDED = {"SHA256SUMS.txt", "battery_inspector/_git_archival.txt"}
HEADER = (
    "# Generated source-file checksums. SHA256SUMS.txt and the git-archive-substituted\n"
    "# battery_inspector/_git_archival.txt are intentionally excluded.\n"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tracked_files() -> list[str]:
    listing = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(
        name
        for name in listing.stdout.splitlines()
        if name and name not in EXCLUDED
    )


def read_manifest() -> dict[str, str]:
    recorded: dict[str, str] = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        digest, _, name = line.partition("  ")
        recorded[name.strip()] = digest.strip().lower()
    return recorded


def write_manifest(names: list[str]) -> None:
    lines = [HEADER]
    lines.extend(f"{sha256_file(ROOT / name)}  {name}\n" for name in names)
    MANIFEST.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify or regenerate the tracked-source checksum manifest."
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Regenerate SHA256SUMS.txt instead of verifying it.",
    )
    arguments = parser.parse_args()

    names = tracked_files()
    if arguments.write:
        write_manifest(names)
        print(f"Wrote {len(names)} checksums to {MANIFEST.name}")
        return 0

    recorded = read_manifest()
    changed = [
        name
        for name in names
        if name in recorded and sha256_file(ROOT / name) != recorded[name]
    ]
    missing = [name for name in names if name not in recorded]
    stale = sorted(set(recorded) - set(names))

    for name in changed:
        print(f"[CHANGED] {name} does not match its recorded checksum")
    for name in missing:
        print(f"[MISSING] {name} is tracked but has no recorded checksum")
    for name in stale:
        print(f"[STALE]   {name} is recorded but no longer tracked")

    if changed or missing or stale:
        print(
            f"\n{MANIFEST.name} does not describe the current source tree. "
            "Regenerate it with: python scripts/verify_source_checksums.py --write"
        )
        return 1

    print(f"{MANIFEST.name} matches all {len(names)} tracked source files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
