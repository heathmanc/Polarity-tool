from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# Keep OpenCV deterministic and prevent nested native thread pools from
# accumulating across the full image-heavy regression suite. Production code
# retains OpenCV's normal thread policy.
try:
    import cv2

    cv2.setNumThreads(1)
except ImportError:
    pass
