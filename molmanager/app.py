import logging
import os
import sys

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

logger = logging.getLogger(__name__)

from .config import load_config
from .rdkit_env import configure_rdkit_for_desktop_app
from .ui.main_window import ChemicalTableApp


def _configure_logging() -> None:
    """Console logging; level from MOLMANAGER_LOG_LEVEL (default INFO)."""
    level_name = load_config().log_level.strip()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    root.setLevel(level)


def _preload_qt_webengine() -> None:
    """Import QtWebEngine *before* ``QApplication`` — required for Chromium/QtWebEngineProcess on Windows."""
    try:
        import PyQt5.QtWebEngineWidgets  # noqa: F401 — side effect: registers WebEngine with Qt
    except Exception as e:
        logger.warning(
            "QtWebEngine could not be loaded (%s: %s). The in-app 3D viewer will fall back to the system browser "
            "unless PyQtWebEngine is installed and matches your PyQt5 version.",
            type(e).__name__,
            e,
        )


def _argv_for_qt(argv: list[str]) -> tuple[list[str], str | None, str | None]:
    """Remove MolManager-only flags so ``QApplication`` does not see unknown options."""
    out: list[str] = [argv[0]] if argv else []
    load_session: str | None = None
    open_file: str | None = None
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--load-session":
            if i + 1 < len(argv):
                load_session = argv[i + 1]
                i += 2
                continue
            i += 1
            continue
        if a in ("--open", "-o"):
            if i + 1 < len(argv):
                open_file = argv[i + 1]
                i += 2
                continue
            i += 1
            continue
        out.append(a)
        i += 1
    return out, load_session, open_file


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv

    _configure_logging()
    configure_rdkit_for_desktop_app()

    if "--demo-table-model" in argv:
        from .table_model_demo import main as demo_main

        return demo_main()

    _preload_qt_webengine()
    argv_qt, load_session, open_file = _argv_for_qt(list(argv))
    app = QApplication(argv_qt)

    w = ChemicalTableApp()
    w.show()
    if load_session:
        try:
            p = load_session.lower()
            if p.endswith(".cms") or p.endswith(".json"):
                w.apply_saved_session_from_file(load_session)
            else:
                w.load_session_csv(load_session)
        except Exception as e:
            logger.warning("Startup session load failed (%s): %s", load_session, e, exc_info=True)
    if open_file:
        path = os.path.abspath(open_file)

        def _do_open() -> None:
            try:
                w.load_file(path)
            except Exception as e:
                logger.warning("Startup file load failed (%s): %s", path, e, exc_info=True)

        QTimer.singleShot(0, _do_open)
    return app.exec_()

