from __future__ import annotations

import math

from PySide6.QtCore import QRect, QSize, QTimer
from PySide6.QtGui import QCloseEvent, QGuiApplication, QShowEvent
from PySide6.QtWidgets import QMainWindow, QStyle, QStyleOptionTab, QStylePainter, QTabBar, QTabWidget

from src.core.config_store import ConfigStore
from src.ui.styles import app_stylesheet
from src.ui.tabs.help_tab import HelpTab
from src.ui.tabs.placeholders import PlaceholderTab
from src.ui.tabs.power_cycle_tab import PowerCycleTab


class HorizontalWestTabBar(QTabBar):
    def tabSizeHint(self, index: int) -> QSize:  # noqa: N802
        size = super().tabSizeHint(index)
        size.transpose()
        text_height = self.fontMetrics().height()
        size.setHeight(max(26, text_height + 10))
        return size

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QStylePainter(self)
        for index in range(self.count()):
            option = QStyleOptionTab()
            self.initStyleOption(option, index)
            painter.drawControl(QStyle.ControlElement.CE_TabBarTabShape, option)

            horizontal_option = QStyleOptionTab(option)
            horizontal_option.shape = QTabBar.Shape.RoundedNorth
            horizontal_rect = QRect(option.rect)
            horizontal_rect.setSize(option.rect.size().transposed())
            horizontal_rect.moveCenter(option.rect.center())
            horizontal_option.rect = horizontal_rect
            painter.drawControl(QStyle.ControlElement.CE_TabBarTabLabel, horizontal_option)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("鼠标自动化测试工具")
        self._config_store = ConfigStore()
        self._power_cycle_tab = PowerCycleTab(config_store=self._config_store, parent=self)
        self._did_post_show_adjust = False
        self._init_ui()
        self._apply_responsive_window_size(default_width=1180, default_height=760)

    def _init_ui(self) -> None:
        tabs = QTabWidget()
        tabs.setTabBar(HorizontalWestTabBar())
        tabs.setTabPosition(QTabWidget.TabPosition.West)
        tabs.setDocumentMode(True)
        tabs.setMovable(False)

        tabs.addTab(self._power_cycle_tab, "上下电测试")
        tabs.addTab(PlaceholderTab("蓝牙连接测试"), "蓝牙连接测试")
        tabs.addTab(PlaceholderTab("蓝牙开关测试"), "蓝牙开关测试")
        tabs.addTab(PlaceholderTab("休眠唤醒测试"), "休眠唤醒测试")
        tabs.addTab(HelpTab(), "帮助")

        self.setCentralWidget(tabs)
        self.setStyleSheet(app_stylesheet())

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
