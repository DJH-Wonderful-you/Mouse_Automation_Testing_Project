from __future__ import annotations

import math
from functools import partial

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QGuiApplication, QShowEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.core.config_store import ConfigStore
from src.ui.styles import app_stylesheet
from src.ui.tabs.help_tab import HelpTab
from src.ui.tabs.placeholders import PlaceholderTab
from src.ui.tabs.power_cycle_tab import PowerCycleTab


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("鼠标自动化测试工具")
        self._config_store = ConfigStore()
        self._power_cycle_tab = PowerCycleTab(config_store=self._config_store, parent=self)
        self._stack = QStackedWidget()
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_buttons: list[QPushButton] = []
        self._did_post_show_adjust = False
        self._init_ui()
        self._apply_responsive_window_size(default_width=1280, default_height=800)

    def _init_ui(self) -> None:
        pages: list[tuple[str, QWidget]] = [
            ("上下电测试", self._power_cycle_tab),
            (
                "蓝牙连接测试",
                PlaceholderTab(
                    "蓝牙连接测试",
                    "用于验证蓝牙配对设备的自动连接、断连重试与状态识别能力。",
                ),
            ),
            (
                "蓝牙开关测试",
                PlaceholderTab(
                    "蓝牙开关测试",
                    "用于验证蓝牙模块开关动作是否稳定，以及开关后连接状态恢复是否正常。",
                ),
            ),
            (
                "休眠唤醒测试",
                PlaceholderTab(
                    "休眠唤醒测试",
                    "用于验证设备休眠与唤醒流程，重点关注唤醒后响应和连接恢复时延。",
                ),
            ),
            ("帮助", HelpTab()),
        ]

        root = QWidget()
        root.setObjectName("MainRoot")
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(10)

        sidebar = self._build_sidebar([title for title, _ in pages])
        content = self._build_content_panel([page for _, page in pages])
        root_layout.addWidget(sidebar)
        root_layout.addWidget(content, 1)

        self.setCentralWidget(root)
        self.setStyleSheet(app_stylesheet())
        self._set_active_page(0)

    def _build_sidebar(self, titles: list[str]) -> QWidget:
        panel = QWidget()
        panel.setObjectName("SidebarPanel")
        panel.setFixedWidth(212)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 14, 12, 14)
        layout.setSpacing(12)

        title = QLabel("鼠标自动化测试工具")
        title.setObjectName("SidebarTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setWordWrap(True)
        layout.addWidget(title)

        divider = QWidget()
        divider.setObjectName("SidebarDivider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)

        for index, text in enumerate(titles):
            button = QPushButton(text)
            button.setObjectName("SidebarNavButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(partial(self._set_active_page, index))
            self._nav_group.addButton(button, index)
            self._nav_buttons.append(button)
            layout.addWidget(button)

        layout.addStretch(1)
        return panel

    def _build_content_panel(self, pages: list[QWidget]) -> QWidget:
        panel = QWidget()
        panel.setObjectName("ContentPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        for page in pages:
            self._stack.addWidget(page)
        layout.addWidget(self._stack)
        return panel

    def _set_active_page(self, index: int, *_: object) -> None:
        if index < 0 or index >= self._stack.count():
            return
        self._stack.setCurrentIndex(index)
        if index < len(self._nav_buttons):
            self._nav_buttons[index].setChecked(True)

    def _apply_responsive_window_size(self, default_width: int, default_height: int) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(default_width, default_height)
            return

        available_logical = screen.availableGeometry()
        width, height = self._fit_size_keep_ratio(
            default_width,
            default_height,
            available_logical.width(),
            available_logical.height(),
        )
        self.resize(width, height)
        self._center_window_by_frame_size(available_logical, width, height)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if self._did_post_show_adjust:
            return
        self._did_post_show_adjust = True
        QTimer.singleShot(0, self._post_show_frame_correction)

    def _post_show_frame_correction(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        frame = self.frameGeometry()
        client = self.size()

        extra_width = max(0, frame.width() - client.width())
        extra_height = max(0, frame.height() - client.height())
        max_client_width = max(1, available.width() - extra_width)
        max_client_height = max(1, available.height() - extra_height)

        new_width, new_height = self._fit_size_keep_ratio(
            client.width(),
            client.height(),
            max_client_width,
            max_client_height,
        )
        if new_width != client.width() or new_height != client.height():
            self.resize(new_width, new_height)
            frame = self.frameGeometry()
        self._center_window_by_frame_size(available, frame.width(), frame.height())

    @staticmethod
    def _fit_size_keep_ratio(width: int, height: int, max_width: int, max_height: int) -> tuple[int, int]:
        width = max(1, int(width))
        height = max(1, int(height))
        max_width = max(1, int(max_width))
        max_height = max(1, int(max_height))
        scale = min(1.0, max_width / width, max_height / height)
        return max(1, int(math.floor(width * scale))), max(1, int(math.floor(height * scale)))

    def _center_window_by_frame_size(self, available: QRect, frame_width: int, frame_height: int) -> None:
        left = available.x() + (available.width() - frame_width) // 2
        top = available.y() + (available.height() - frame_height) // 2
        self.move(max(available.x(), left), max(available.y(), top))

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._power_cycle_tab.shutdown()
        super().closeEvent(event)

