from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QLabel, QScrollArea, QVBoxLayout, QWidget

if TYPE_CHECKING:
    from src.ui.tabs.bluetooth_connection_tab import BluetoothConnectionTab
    from src.ui.tabs.power_cycle_tab import PowerCycleTab


class SettingsTab(QWidget):
    def __init__(
        self,
        power_cycle_tab: PowerCycleTab,
        bluetooth_connection_tab: BluetoothConnectionTab,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        layout.addWidget(self._create_page_intro())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)
        content_layout.addWidget(power_cycle_tab.create_settings_section())
        content_layout.addWidget(bluetooth_connection_tab.create_settings_section())
        content_layout.addWidget(
            self._create_placeholder_section(
                "蓝牙开关测试",
                "该测试项的独立设置区域预留在此，后续补充开关节奏、恢复等待和结果判定参数。",
            )
        )
        content_layout.addWidget(
            self._create_placeholder_section(
                "休眠唤醒测试",
                "该测试项的独立设置区域预留在此，后续补充休眠时长、唤醒条件和恢复检测参数。",
            )
        )
        content_layout.addStretch(1)

        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

    def _create_page_intro(self) -> QWidget:
        card = QWidget()
        card.setObjectName("PageIntroCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 10)
        card_layout.setSpacing(4)

        title = QLabel("设置")
        title.setObjectName("PageTitle")
        subtitle = QLabel("按测试项集中管理参数配置，修改后会自动保存，并在下次执行时生效。")
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)

        card_layout.addWidget(title)
        card_layout.addWidget(subtitle)
        return card

    def _create_placeholder_section(self, title: str, description: str) -> QGroupBox:
        group = QGroupBox(title)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 14, 12, 12)
        layout.setSpacing(8)

        label = QLabel(description)
        label.setWordWrap(True)
        layout.addWidget(label)
        return group
