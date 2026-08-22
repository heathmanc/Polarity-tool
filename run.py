from __future__ import annotations


def _run() -> int:
    # PyInstaller must intercept PyTorch/Ultralytics multiprocessing children
    # before the GUI and controller module graph is imported.
    import multiprocessing

    multiprocessing.freeze_support()
    try:
        from battery_inspector.main import main
    except ModuleNotFoundError as exc:
        if exc.name == "PySide6":
            print(
                "PySide6 is not installed. Create a virtual environment and run:\n"
                "    python -m pip install -r requirements.txt\n"
            )
            return 2
        raise
    return main()


if __name__ == "__main__":
    raise SystemExit(_run())
