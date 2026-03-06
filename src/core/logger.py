from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

try:
    from PySide6.QtCore import QObject, Signal

    _QT_AVAILABLE = True
except Exception:  # pragma: no cover - only used when Qt is unavailable.
    QObject = object  # type: ignore[assignment]
    Signal = None  # type: ignore[assignment]
    _QT_AVAILABLE = False

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"


def setup_logging(log_dir: str | Path = "logs", level: int = logging.INFO) -> Path:
    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)
    log_file = path / "mouse_automation.log"

    root = logging.getLogger()
    if getattr(root, "_mouse_tool_configured", False):
        return log_file

    root.setLevel(level)
    formatter = logging.Formatter(LOG_FORMAT)

    file_handler = RotatingFileHandler(
        log_file, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(stream_handler)
    setattr(root, "_mouse_tool_configured", True)
    return log_file


if _QT_AVAILABLE:

    class GuiLogEmitter(QObject):
        sig_log = Signal(str, str)

        def emit_log(self, level: str, message: str) -> None:
            self.sig_log.emit(level, message)


else:

    class GuiLogEmitter(QObject):  # type: ignore[misc]
        def emit_log(self, level: str, message: str) -> None:  # noqa: ARG002
            return


class GuiLogHandler(logging.Handler):
    def __init__(self, emitter: GuiLogEmitter) -> None:
        super().__init__()
        self._emitter = emitter

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._emitter.emit_log(record.levelname, self.format(record))
        except Exception:
            self.handleError(record)
