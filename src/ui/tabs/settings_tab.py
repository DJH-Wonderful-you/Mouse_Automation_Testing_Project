from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.core.config_store import ConfigStore
from src.core.types import (
    TEST_ITEM_ORDER,
    AppSettings,
    BluetoothConnectSettings,
    BluetoothSwitchSettings,
    TestItemId,
    TestPlanSettings,
)


class NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()


ITEM_LABELS: dict[TestItemId, str] = {
    "power_cycle": "上下电测试",
    "bluetooth_connect": "蓝牙连接测试",
    "bluetooth_switch": "蓝牙开关测试",
    "sleep_wake": "休眠唤醒测试",
}


class SettingsTab(QWidget):
    def __init__(self, config_store: ConfigStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config_store = config_store
        self._suspend_auto_save = True
        self._item_checks: dict[TestItemId, QCheckBox] = {}
        self._single_count_inputs: list[QSpinBox] = []

        self._build_ui()
        self._load_settings_into_ui()
        self._bind_auto_save_signals()
        self._update_mode_state()
        self._suspend_auto_save = False

    def _build_ui(self) -> None:
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
        content_layout.addWidget(self._create_test_plan_group())
        content_layout.addWidget(self._create_power_cycle_group())
        content_layout.addWidget(self._create_bluetooth_connect_group())
        content_layout.addWidget(self._create_bluetooth_switch_group())
        content_layout.addWidget(self._create_sleep_wake_group())
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
        subtitle = QLabel("集中管理测试计划和各测试项参数；设备连接与蓝牙目标在设备管理页设置。")
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)
        card_layout.addWidget(title)
        card_layout.addWidget(subtitle)
        return card

    def _create_test_plan_group(self) -> QGroupBox:
        group = QGroupBox("测试计划")
        layout = QGridLayout(group)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(10)

        item_row = QHBoxLayout()
        for item_id in TEST_ITEM_ORDER:
            checkbox = QCheckBox(ITEM_LABELS[item_id])
            self._item_checks[item_id] = checkbox
            item_row.addWidget(checkbox)
        item_row.addStretch(1)

        self.radio_sequential = QRadioButton("单项压测模式")
        self.radio_sequential.setToolTip("先完成当前测试项的全部次数，再进入下一个已勾选测试项。")
        self.radio_round_robin = QRadioButton("轮次压测模式")
        self.radio_round_robin.setToolTip("每轮按顺序执行所有已勾选测试项各一次，并重复指定轮数。")
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.radio_sequential)
        self.mode_group.addButton(self.radio_round_robin)

        self.input_round_count = NoWheelSpinBox()
        self.input_round_count.setRange(1, 1_000_000)
        self.input_round_count.setValue(10)

        self.combo_switch_method = NoWheelComboBox()
        self.combo_switch_method.addItem("系统适配器开关", "adapter")
        self.combo_switch_method.addItem("系统 UI 切换", "ui")

        self.label_round_hint = QLabel("轮次压测模式下，各测试项单独的测试次数无效。")
        self.label_round_hint.setStyleSheet("color: #8a5a00; font-weight: 600;")

        layout.addWidget(QLabel("测试项目："), 0, 0)
        layout.addLayout(item_row, 0, 1, 1, 5)
        layout.addWidget(QLabel("测试模式："), 1, 0)
        layout.addWidget(self.radio_sequential, 1, 1, 1, 5)
        layout.addWidget(QWidget(), 2, 0)
        layout.addWidget(self.radio_round_robin, 2, 1, 1, 3)
        layout.addWidget(QLabel("轮数："), 2, 4)
        layout.addWidget(self.input_round_count, 2, 5)
        layout.addWidget(QLabel("蓝牙开关方式："), 3, 0)
        layout.addWidget(self.combo_switch_method, 3, 1)
        layout.addWidget(self.label_round_hint, 3, 2, 1, 4)
        layout.setColumnStretch(3, 1)
        return group

    def _create_power_cycle_group(self) -> QGroupBox:
        group = QGroupBox("上下电测试")
        layout = QGridLayout(group)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(8)

        self.input_power_count = self._spin(1, 1_000_000, 100)
        self.input_voltage_threshold = self._double_spin(0.01, 1000.0, 3.0, " V", 3)
        self.input_power_interval = self._double_spin(0.0, 3600.0, 1.0, " s", 3)
        self.input_power_relay_channel = self._spin(1, 8, 1)
        self.input_power_timeout = self._double_spin(0.5, 120.0, 5.0, " s", 3)
        self.input_power_sample_interval = self._double_spin(0.05, 10.0, 0.2, " s", 3)
        self.input_power_consecutive = self._spin(1, 20, 2)
        self._single_count_inputs.append(self.input_power_count)

        layout.addWidget(QLabel("测试次数："), 0, 0)
        layout.addWidget(self.input_power_count, 0, 1)
        layout.addWidget(QLabel("运行判定电压阈值："), 0, 2)
        layout.addWidget(self.input_voltage_threshold, 0, 3)
        layout.addWidget(QLabel("上下电间隔："), 1, 0)
        layout.addWidget(self.input_power_interval, 1, 1)
        layout.addWidget(QLabel("继电器控制端口："), 1, 2)
        layout.addWidget(self.input_power_relay_channel, 1, 3)
        layout.addWidget(QLabel("状态判定超时："), 2, 0)
        layout.addWidget(self.input_power_timeout, 2, 1)
        layout.addWidget(QLabel("采样间隔："), 2, 2)
        layout.addWidget(self.input_power_sample_interval, 2, 3)
        layout.addWidget(QLabel("连续通过次数："), 3, 0)
        layout.addWidget(self.input_power_consecutive, 3, 1)
        layout.setColumnStretch(4, 1)
        return group

    def _create_bluetooth_connect_group(self) -> QGroupBox:
        group = QGroupBox("蓝牙连接测试")
        layout = QGridLayout(group)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(8)

        self.input_connect_count = self._spin(1, 1_000_000, 100)
        self.input_connect_timeout = self._double_spin(1.0, 180.0, 15.0, " s", 3)
        self.input_connect_sample_interval = self._double_spin(0.05, 10.0, 0.5, " s", 3)
        self.input_pairing_press = self._double_spin(0.1, 30.0, 2.0, " s", 3)
        self.input_mode_relay_channel = self._spin(1, 8, 1)
        self.input_pairing_relay_channel = self._spin(1, 8, 2)
        self._single_count_inputs.append(self.input_connect_count)

        layout.addWidget(QLabel("测试次数："), 0, 0)
        layout.addWidget(self.input_connect_count, 0, 1)
        layout.addWidget(QLabel("状态等待超时："), 0, 2)
        layout.addWidget(self.input_connect_timeout, 0, 3)
        layout.addWidget(QLabel("采样间隔："), 1, 0)
        layout.addWidget(self.input_connect_sample_interval, 1, 1)
        layout.addWidget(QLabel("配对按压时长："), 1, 2)
        layout.addWidget(self.input_pairing_press, 1, 3)
        layout.addWidget(QLabel("蓝牙模式通道："), 2, 0)
        layout.addWidget(self.input_mode_relay_channel, 2, 1)
        layout.addWidget(QLabel("配对按键通道："), 2, 2)
        layout.addWidget(self.input_pairing_relay_channel, 2, 3)
        layout.setColumnStretch(4, 1)
        return group

    def _create_bluetooth_switch_group(self) -> QGroupBox:
        group = QGroupBox("蓝牙开关测试")
        layout = QGridLayout(group)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(8)

        self.input_switch_count = self._spin(1, 1_000_000, 100)
        self.input_switch_timeout = self._double_spin(1.0, 180.0, 15.0, " s", 3)
        self.input_switch_sample_interval = self._double_spin(0.05, 10.0, 0.5, " s", 3)
        self._single_count_inputs.append(self.input_switch_count)

        layout.addWidget(QLabel("测试次数："), 0, 0)
        layout.addWidget(self.input_switch_count, 0, 1)
        layout.addWidget(QLabel("状态等待超时："), 0, 2)
        layout.addWidget(self.input_switch_timeout, 0, 3)
        layout.addWidget(QLabel("采样间隔："), 1, 0)
        layout.addWidget(self.input_switch_sample_interval, 1, 1)
        layout.setColumnStretch(4, 1)
        return group

    def _create_sleep_wake_group(self) -> QGroupBox:
        group = QGroupBox("休眠唤醒测试")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 12, 12, 12)
        label = QLabel("该测试项暂未开发，勾选后会在主界面显示为 SKIP。")
        label.setWordWrap(True)
        layout.addWidget(label)
        return group

    def _load_settings_into_ui(self) -> None:
        plan = self._config_store.load_test_plan()
        for item_id, checkbox in self._item_checks.items():
            checkbox.setChecked(item_id in plan.enabled_items)
        self.radio_round_robin.setChecked(plan.mode == "round_robin")
        self.radio_sequential.setChecked(plan.mode != "round_robin")
        self.input_round_count.setValue(plan.round_count)
        self._select_combo_value(self.combo_switch_method, plan.bluetooth_switch_method)

        power = self._config_store.load_power_cycle()
        self.input_power_count.setValue(power.test_count)
        self.input_voltage_threshold.setValue(power.voltage_threshold_v)
        self.input_power_interval.setValue(power.interval_ms / 1000.0)
        self.input_power_relay_channel.setValue(power.relay_channel)
        self.input_power_timeout.setValue(power.state_timeout_ms / 1000.0)
        self.input_power_sample_interval.setValue(power.sample_interval_ms / 1000.0)
        self.input_power_consecutive.setValue(power.consecutive_pass_needed)

        connect = self._config_store.load_bluetooth_connect()
        self.input_connect_count.setValue(connect.test_count)
        self.input_connect_timeout.setValue(connect.state_timeout_ms / 1000.0)
        self.input_connect_sample_interval.setValue(connect.sample_interval_ms / 1000.0)
        self.input_pairing_press.setValue(connect.pairing_press_ms / 1000.0)
        self.input_mode_relay_channel.setValue(connect.mode_relay_channel)
        self.input_pairing_relay_channel.setValue(connect.pairing_relay_channel)

        switch = self._config_store.load_bluetooth_switch()
        self.input_switch_count.setValue(switch.test_count)
        self.input_switch_timeout.setValue(switch.state_timeout_ms / 1000.0)
        self.input_switch_sample_interval.setValue(switch.sample_interval_ms / 1000.0)

    def _bind_auto_save_signals(self) -> None:
        for checkbox in self._item_checks.values():
            checkbox.toggled.connect(self._on_settings_changed)
        self.radio_sequential.toggled.connect(self._on_mode_changed)
        self.radio_round_robin.toggled.connect(self._on_mode_changed)
        self.input_round_count.valueChanged.connect(self._on_settings_changed)
        self.combo_switch_method.currentIndexChanged.connect(self._on_settings_changed)

        for widget in (
            self.input_power_count,
            self.input_voltage_threshold,
            self.input_power_interval,
            self.input_power_relay_channel,
            self.input_power_timeout,
            self.input_power_sample_interval,
            self.input_power_consecutive,
            self.input_connect_count,
            self.input_connect_timeout,
            self.input_connect_sample_interval,
            self.input_pairing_press,
            self.input_mode_relay_channel,
            self.input_pairing_relay_channel,
            self.input_switch_count,
            self.input_switch_timeout,
            self.input_switch_sample_interval,
        ):
            if isinstance(widget, QDoubleSpinBox):
                widget.valueChanged.connect(self._on_settings_changed)
            else:
                widget.valueChanged.connect(self._on_settings_changed)

    def _on_mode_changed(self, *_: object) -> None:
        self._update_mode_state()
        self._on_settings_changed()

    def _on_settings_changed(self, *_: object) -> None:
        if self._suspend_auto_save:
            return
        self.save_current_settings()

    def save_current_settings(self) -> bool:
        self._config_store.save_test_plan(self._collect_test_plan_settings())
        self._config_store.save_power_cycle(self._collect_power_cycle_settings())
        self._config_store.save_bluetooth_connect(self._collect_bluetooth_connect_settings())
        self._config_store.save_bluetooth_switch(self._collect_bluetooth_switch_settings())
        return True

    def _collect_test_plan_settings(self) -> TestPlanSettings:
        enabled_items = tuple(
            item_id
            for item_id in TEST_ITEM_ORDER
            if self._item_checks[item_id].isChecked()
        )
        return TestPlanSettings(
            enabled_items=enabled_items,
            mode="round_robin" if self.radio_round_robin.isChecked() else "sequential_items",
            round_count=self.input_round_count.value(),
            bluetooth_switch_method=self.combo_switch_method.currentData() or "adapter",
        )

    def _collect_power_cycle_settings(self) -> AppSettings:
        old = self._config_store.load_power_cycle()
        return replace(
            old,
            test_count=self.input_power_count.value(),
            voltage_threshold_v=self.input_voltage_threshold.value(),
            interval_ms=max(0, int(round(self.input_power_interval.value() * 1000))),
            relay_channel=self.input_power_relay_channel.value(),
            state_timeout_ms=max(100, int(round(self.input_power_timeout.value() * 1000))),
            sample_interval_ms=max(
                10, int(round(self.input_power_sample_interval.value() * 1000))
            ),
            consecutive_pass_needed=self.input_power_consecutive.value(),
        )

    def _collect_bluetooth_connect_settings(self) -> BluetoothConnectSettings:
        old = self._config_store.load_bluetooth_connect()
        mode_channel = self.input_mode_relay_channel.value()
        pairing_channel = self.input_pairing_relay_channel.value()
        return replace(
            old,
            test_count=self.input_connect_count.value(),
            mode_relay_channel=mode_channel,
            pairing_relay_channel=pairing_channel,
            pairing_press_ms=max(100, int(round(self.input_pairing_press.value() * 1000))),
            state_timeout_ms=max(1000, int(round(self.input_connect_timeout.value() * 1000))),
            sample_interval_ms=max(
                50, int(round(self.input_connect_sample_interval.value() * 1000))
            ),
        )

    def _collect_bluetooth_switch_settings(self) -> BluetoothSwitchSettings:
        old = self._config_store.load_bluetooth_switch()
        return replace(
            old,
            test_count=self.input_switch_count.value(),
            state_timeout_ms=max(1000, int(round(self.input_switch_timeout.value() * 1000))),
            sample_interval_ms=max(
                50, int(round(self.input_switch_sample_interval.value() * 1000))
            ),
        )

    def _update_mode_state(self) -> None:
        round_mode = self.radio_round_robin.isChecked()
        self.input_round_count.setEnabled(round_mode)
        self.label_round_hint.setVisible(round_mode)
        for widget in self._single_count_inputs:
            widget.setEnabled(not round_mode)

    def _spin(self, minimum: int, maximum: int, value: int) -> NoWheelSpinBox:
        spin = NoWheelSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    def _double_spin(
        self,
        minimum: float,
        maximum: float,
        value: float,
        suffix: str,
        decimals: int,
    ) -> NoWheelDoubleSpinBox:
        spin = NoWheelDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setSingleStep(0.1)
        spin.setSuffix(suffix)
        spin.setValue(value)
        return spin

    @staticmethod
    def _select_combo_value(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)
