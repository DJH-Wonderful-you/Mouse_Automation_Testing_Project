from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class PlaceholderTab(QWidget):
    def __init__(self, title: str, description: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        intro = QWidget()
        intro.setObjectName("PageIntroCard")
        intro_layout = QVBoxLayout(intro)
        intro_layout.setContentsMargins(14, 12, 14, 10)
        intro_layout.setSpacing(4)

        title_label = QLabel(title)
        title_label.setObjectName("PageTitle")
        desc_label = QLabel(description or "该页面功能正在开发中，后续版本将逐步开放详细配置与执行能力。")
        desc_label.setObjectName("PageSubtitle")
        desc_label.setWordWrap(True)
        intro_layout.addWidget(title_label)
        intro_layout.addWidget(desc_label)

        hint = QLabel("当前为占位页面。")
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(intro)
        layout.addStretch(1)
        layout.addWidget(hint)
        layout.addStretch(1)
