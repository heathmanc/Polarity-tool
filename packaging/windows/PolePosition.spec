# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-directory build for the complete Pole Position station HMI.

Run only through build-installer.ps1 on Windows x64. PyInstaller is not a
cross-compiler. The generated directory contains Python and every runtime and
training package, but deliberately contains no production or base-model weight.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata


ROOT = Path(SPECPATH).resolve().parents[1]

datas = [
    (str(ROOT / "battery_inspector" / "assets"), "battery_inspector/assets"),
    (str(ROOT / "battery_inspector" / "ui" / "theme.qss"), "battery_inspector/ui"),
    (str(ROOT / "battery_inspector" / "_git_archival.txt"), "battery_inspector"),
]
binaries = []
hiddenimports = []

# These packages contain native libraries, dynamically imported backends, model
# definitions, or data files that static import analysis cannot safely infer.
for package in (
    "onnxruntime",
    "pypylon",
    "pycomm3",
    "torch",
    "torchvision",
    "ultralytics",
    "onnx",
):
    package_datas, package_binaries, package_hidden = collect_all(package)
    if package == "onnx":
        # collect_all("onnx") also discovers ONNX's backend conformance suite,
        # including hundreds of test-only .onnx fixtures. Pole Position needs
        # the ONNX exporter/runtime modules, not the package's test corpus.
        excluded_modules = ("onnx.backend.test", "onnx.test")
        package_datas = [
            item
            for item in package_datas
            if not any(
                marker in str(part).replace("\\", "/").lower()
                for marker in ("/onnx/backend/test/", "/onnx/test/")
                for part in item
            )
        ]
        package_hidden = [
            module
            for module in package_hidden
            if not module.startswith(excluded_modules)
        ]
    elif package == "onnxruntime":
        # onnxruntime.datasets contains three example .onnx files used by its
        # documentation/tests. They are not needed by Pole Position inference.
        package_datas = [
            item
            for item in package_datas
            if not any(
                "/onnxruntime/datasets/" in str(part).replace("\\", "/").lower()
                for part in item
            )
        ]
        package_hidden = [
            module
            for module in package_hidden
            if not module.startswith("onnxruntime.datasets")
        ]
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden
    datas += copy_metadata(package)

for distribution in ("PySide6", "numpy", "opencv-python-headless"):
    datas += copy_metadata(distribution)

analysis = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "ruff",
        "IPython",
        "jupyter",
        "notebook",
        "onnx.backend.test",
        "onnx.test",
        "onnxruntime.datasets",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="PolePosition",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="x86_64",
    icon=str(ROOT / "battery_inspector" / "assets" / "app_icon.ico"),
    version=str(ROOT / "packaging" / "windows" / "file_version_info.txt"),
)

collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PolePosition",
)
