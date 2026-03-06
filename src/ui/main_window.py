from __future__ import annotations

import math

from PySide6.QtGui import QCloseEvent, QGuiApplication
from PySide6.QtWidgets import QMainWindow, QTabWidget

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
        self._init_ui()
        self._apply_responsive_window_size(default_width=1180, default_height=760)

    def _init_ui(self) -> None:
        tabs = QTabWidget()
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
        dpr = max(1.0, float(screen.devicePixelRatio()))

        # Read actual screen size (physical pixels) first, then convert back to
        # logical size after applying DPI scale so UI adapts to Windows scaling.
        available_physical_width = max(1, int(available_logical.width() * dpr))
        available_physical_height = max(1, int(available_logical.height() * dpr))

        target_physical_width = int(default_width)
        target_physical_height = int(default_height)
        fit_scale = min(
            1.0,
            available_physical_width / target_physical_width,
            available_physical_height / target_physical_height,
        )
        target_physical_width = int(target_physical_width * fit_scale)
        target_physical_height = int(target_physical_height * fit_scale)

        width = max(760, int(math.floor(target_physical_width / dpr)))
        height = max(560, int(math.floor(target_physical_height / dpr)))

        width = min(width, available_logical.width())
        height = min(height, available_logical.height())
        self.resize(width, height)

        left = available_logical.x() + (available_logical.width() - width) // 2
        top = available_logical.y() + (available_logical.height() - height) // 2
        self.move(max(available_logical.x(), left), max(available_logical.y(), top))

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._power_cycle_tab.shutdown()
        super().closeEvent(event)
