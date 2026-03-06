from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

# Allow running via "python src/main.py" from IDEs that execute file directly.
if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from src.core.logger import setup_logging
from src.ui.main_window import MainWindow

_LOGGER = logging.getLogger("app")


def _handle_uncaught_exception(
    exc_type: type[BaseException], exc: BaseException, tb: object
) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return

    details = "".join(traceback.format_exception(exc_type, exc, tb))
    _LOGGER.error("Unhandled exception:\n%s", details)
    QMessageBox.critical(
        None,
        "程序异常",
        f"程序发生未处理异常：\n{exc}\n\n详细信息已写入日志。",
    )


def main() -> int:
    setup_logging()
    QCoreApplication.setOrganizationName("RJHZ")
    QCoreApplication.setApplicationName("MouseAutomationTool")
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    sys.excepthook = _handle_uncaught_exception

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
