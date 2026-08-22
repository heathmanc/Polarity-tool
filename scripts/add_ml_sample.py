from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path

LABELS = ("plus", "minus", "blank")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add a manually verified terminal-top image to the ML dataset."
    )
    parser.add_argument("image", type=Path)
    parser.add_argument("label", choices=LABELS)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dataset") / "manual",
    )
    parser.add_argument(
        "--confirmed-isolated-terminal-top",
        action="store_true",
        help=(
            "Required safety acknowledgement: the image contains only the isolated "
            "metal terminal-top region and does not expose the red ring or molded "
            "battery-case polarity symbol."
        ),
    )
    args = parser.parse_args()
    if not args.image.is_file():
        parser.error(f"Image does not exist: {args.image}")
    if not args.confirmed_isolated_terminal_top:
        parser.error(
            "Refusing to add an arbitrary crop. Pass --confirmed-isolated-terminal-top "
            "only after verifying that the red ring and molded case polarity symbols "
            "are not visible in the training image."
        )
    digest = sha256_file(args.image)
    destination_dir = args.output / args.label
    destination_dir.mkdir(parents=True, exist_ok=True)
    suffix = args.image.suffix.lower() if args.image.suffix else ".png"
    destination = destination_dir / f"{args.label}_{digest[:20]}{suffix}"
    if not destination.exists():
        shutil.copy2(args.image, destination)
    print(destination.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
