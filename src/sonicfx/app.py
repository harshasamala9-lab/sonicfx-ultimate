"""Qt application bootstrap for SonicFX Ultimate (``sonicfx`` command).

The window is presentation only; :class:`~sonicfx.ui.controller.Controller`
owns the engine, persistence and default-output routing. On every exit path
(window close, SIGINT/SIGTERM, Qt quit) the controller restores the previous
default sink before the process leaves.
"""
from __future__ import annotations

import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from sonicfx.logging_setup import get_logger, setup_logging
from sonicfx.ui.controller import Controller
from sonicfx.ui.main_window import MainWindow
from sonicfx.ui.theme import apply_theme

log = get_logger("app")

APP_VERSION = "1.0.0"


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    log.info("starting SonicFX Ultimate %s", APP_VERSION)

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("SonicFX Ultimate")
    app.setApplicationDisplayName("SonicFX Ultimate")
    app.setOrganizationName("SonicFX")
    app.setOrganizationDomain("sonicfx.local")
    app.setApplicationVersion(APP_VERSION)
    app.setDesktopFileName("sonicfx-ultimate")
    app.setQuitOnLastWindowClosed(True)
    apply_theme(app)

    try:
        controller = Controller()
    except Exception as exc:  # noqa: BLE001 - startup boundary
        log.exception("controller startup failed")
        QMessageBox.critical(
            None,
            "SonicFX Ultimate",
            f"SonicFX could not start:\n{exc}\n\nSee ~/.local/state/sonicfx/logs/.",
        )
        return 1

    window = MainWindow(controller)
    app.aboutToQuit.connect(controller.shutdown)

    def _handle_signal(signum: int, _frame) -> None:
        log.info("received signal %s; shutting down", signum)
        app.quit()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _handle_signal)
    # Python signal handlers only run between bytecode instructions; a periodic
    # no-op timer keeps the interpreter responsive during Qt's event loop.
    signal_timer = QTimer()
    signal_timer.timeout.connect(lambda: None)
    signal_timer.start(250)

    def _excepthook(exc_type, exc, tb) -> None:
        log.error("uncaught exception", exc_info=(exc_type, exc, tb))
        QMessageBox.critical(
            window,
            "SonicFX Ultimate",
            f"Unexpected error:\n{exc_type.__name__}: {exc}",
        )

    sys.excepthook = _excepthook
    window.show()
    log.info("window shown")

    code = 0
    try:
        code = app.exec()
    finally:
        controller.shutdown()
        log.info("exiting with code %s", code)
    return code


if __name__ == "__main__":  # pragma: no cover - convenience
    raise SystemExit(main())
