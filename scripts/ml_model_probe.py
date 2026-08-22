from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from battery_inspector.services.ml import OnnxPolarityModel  # noqa: E402


def _png_sha256(image) -> str:
    ok, encoded = cv2.imencode('.png', image)
    if not ok:
        return ''
    return hashlib.sha256(encoded.tobytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            'Verify the installed ONNX polarity model and optionally classify one '
            'EXACT saved ML crop. No Hough-circle normalization or recentering is performed.'
        )
    )
    parser.add_argument(
        '--model',
        type=Path,
        default=PROJECT_ROOT / 'models' / 'polarity_classifier.onnx',
    )
    parser.add_argument(
        '--manifest',
        type=Path,
        default=PROJECT_ROOT / 'models' / 'polarity_classifier.json',
    )
    parser.add_argument(
        '--image',
        type=Path,
        help=(
            'Optional exact ML-input image, such as *_marking.png for a legacy '
            'rectangle recipe or *_terminal_top.png / masked marking image for a circle recipe.'
        ),
    )
    parser.add_argument(
        '--tta',
        action='store_true',
        help='Explicitly enable 0/90/180/270 test-time averaging. Default is OFF.',
    )
    # Backward-compatible no-op so old troubleshooting commands do not fail.
    parser.add_argument('--no-tta', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()

    model = OnnxPolarityModel(args.model, args.manifest)
    info = model.info(require_runtime=True)
    print(json.dumps(info, indent=2, sort_keys=True))
    if not info.get('ready'):
        return 2
    if args.image:
        image = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
        if image is None:
            raise SystemExit(f'Could not read image: {args.image}')
        use_tta = bool(args.tta and not args.no_tta)
        result = model.infer(image, tta_quadrants=use_tta)
        print(
            json.dumps(
                {
                    'input_handling': 'AS_PROVIDED_EXACT_CROP',
                    'input_path': str(args.image.resolve()),
                    'input_width_px': int(image.shape[1]),
                    'input_height_px': int(image.shape[0]),
                    'input_png_sha256': _png_sha256(image),
                    'input_crop_contract': str(info.get('input_crop_contract', '')),
                    'prediction': result.top_label,
                    'confidence': result.confidence,
                    'margin': result.margin,
                    'scores': result.probabilities,
                    'tta_count': result.tta_count,
                },
                indent=2,
                sort_keys=True,
            )
        )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
