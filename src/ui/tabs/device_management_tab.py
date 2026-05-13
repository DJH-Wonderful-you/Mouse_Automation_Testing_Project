from __future__ import annotations

from datetime import datetime
from functools import partial
from html import escape
import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.core.bluetooth_probe import normalize_mac
from src.core.config_store import ConfigStore
from src.core.device_context import DeviceContext
from src.core.relay_lcus88 import build_switch_command
from src.core.serial_utils import list_serial_ports
from src.core.types import BluetoothTargetSettings, DeviceSettings

_LOGGER = logging.getLogger("ui.device_management")


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()


class DeviceManagementTab(QWidget):
    def __init__(
        self,
        *,
        config_store: ConfigStore,
        device_context: DeviceContext,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config_store = config_store
        self._device_context = device_context
        self._suspend_auto_save = True
        self._channel_status_labels: dict[int, QLabel] = {}
        self._channel_action_buttons: list[QPushButton] = []
        self._channel_states: dict[int, bool | None] = {
            channel: None for channel in range(1, 9)
        }

        self._build_ui()
        self._load_settings_into_ui()
        self._refresh_serial_ports(emit_log=False)
        self._bind_auto_save_signals()
        self._update_controls()
        self._suspend_auto_save = False

    def _build_ui(self) -> None:
        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(4, 4, 4, 4)
        root_layout.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        root_layout.addWidget(splitter)

        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        left_layout.addWidget(self._create_page_intro())
        left_layout.addWidget(self._create_bluetooth_target_group())
        left_layout.addWidget(self._create_multimeter_group())
        left_layout.addWidget(self._create_relay_group())
        left_layout.addWidget(self._create_relay_channel_group())
        left_layout.addStretch(1)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setWidget(left_container)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self._create_log_group())
        right_widget.setMinimumWidth(360)

        splitter.addWidget(left_scroll)
        splitter.addWidget(right_widget)
        splitter.setSizes([820, 420])
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

    def _create_page_intro(self) -> QWidget:
        card = QWidget()
        card.setObjectName("PageIntroCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 10)
        layout.setSpacing(4)

        title = QLabel("设备管理")
        title.setObjectName("PageTitle")
        subtitle = QLabel("统一连接和管理测试设备；连接完成后，主界面的所有测试项都会按需复用。")
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        return card

    def _create_bluetooth_target_group(self) -> QGroupBox:
        group = QGroupBox("蓝牙目标管理")
        form = QFormLayout(group)
        form.setContentsMargins(12, 14, 12, 12)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.input_bt_name = QLineEdit()
        self.input_bt_name.setPlaceholderText("例如：LOWA Mouse")
        self.input_bt_mac = QLineEdit()
        self.input_bt_mac.setPlaceholderText("例如：AA:BB:CC:11:22:33")
        self.combo_bt_mode = NoWheelComboBox()
        self.combo_bt_mode.addItem("名称或 MAC（推荐）", "name_or_mac")
        self.combo_bt_mode.addItem("名称且 MAC", "name_and_mac")
        self.check_sim_bluetooth = QCheckBox("蓝牙仿真")

        button_row = QWidget()
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        self.btn_bt_detect = QPushButton("检测设备")
        self.btn_bt_detect.clicked.connect(self._detect_bluetooth_devices)
        self.btn_bt_check = QPushButton("检查连接状态")
        self.btn_bt_check.clicked.connect(self._check_bluetooth_connection)
        self.btn_bt_remove = QPushButton("删除已配对设备")
        self.btn_bt_remove.clicked.connect(self._remove_paired_bluetooth_device)
        button_layout.addWidget(self.btn_bt_detect)
        button_layout.addWidget(self.btn_bt_check)
        button_layout.addWidget(self.btn_bt_remove)
        button_layout.addStretch(1)

        form.addRow("蓝牙鼠标名称关键字：", self.input_bt_name)
        form.addRow("蓝牙鼠标 MAC：", self.input_bt_mac)
        form.addRow("匹配模式：", self.combo_bt_mode)
        form.addRow("仿真：", self.check_sim_bluetooth)
        form.addRow("", button_row)
        return group

    def _create_multimeter_group(self) -> QGroupBox:
        group = QGroupBox("万用表控制区")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        self.check_sim_multimeter = QCheckBox("万用表仿真")
        self.combo_multimeter_port = NoWheelComboBox()
        self.combo_multimeter_port.setMinimumWidth(260)
        self.btn_refresh_meter_ports = QPushButton("刷新串口")
        self.btn_refresh_meter_ports.clicked.connect(self._refresh_serial_ports)
        self.btn_meter_connect = QPushButton("连接设备")
        self.btn_meter_connect.clicked.connect(self._connect_multimeter)
        self.btn_meter_disconnect = QPushButton("断开设备")
        self.btn_meter_disconnect.setObjectName("DangerButton")
        self.btn_meter_disconnect.clicked.connect(self._disconnect_multimeter)
        self.btn_meter_fetch = QPushButton("获取万用表数据")
        self.btn_meter_fetch.clicked.connect(self._read_multimeter_data)
        self.label_meter_status = QLabel("未连接")

        row_port = QHBoxLayout()
        row_port.addWidget(QLabel("万用表串口："))
        row_port.addWidget(self.combo_multimeter_port, 1)
        row_port.addWidget(self.btn_refresh_meter_ports)
        row_actions = QHBoxLayout()
        row_actions.addWidget(self.check_sim_multimeter)
        row_actions.addWidget(self.btn_meter_connect)
        row_actions.addWidget(self.btn_meter_disconnect)
        row_actions.addWidget(self.btn_meter_fetch)
        row_actions.addStretch(1)
        row_actions.addWidget(QLabel("状态："))
        row_actions.addWidget(self.label_meter_status)

        layout.addLayout(row_port)
        layout.addLayout(row_actions)
        return group

    def _create_relay_group(self) -> QGroupBox:
        group = QGroupBox("继电器控制区")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        self.check_sim_relay = QCheckBox("继电器仿真")
        self.combo_relay_port = NoWheelComboBox()
        self.combo_relay_port.setMinimumWidth(260)
        self.btn_refresh_relay_ports = QPushButton("刷新串口")
        self.btn_refresh_relay_ports.clicked.connect(self._refresh_serial_ports)
        self.btn_relay_connect = QPushButton("连接设备")
        self.btn_relay_connect.clicked.connect(self._connect_relay)
        self.btn_relay_disconnect = QPushButton("断开设备")
        self.btn_relay_disconnect.setObjectName("DangerButton")
        self.btn_relay_disconnect.clicked.connect(self._disconnect_relay)
        self.btn_refresh_states = QPushButton("刷新状态")
        self.btn_refresh_states.clicked.connect(self._refresh_channel_states)
        self.label_relay_status = QLabel("未连接")

        row_port = QHBoxLayout()
        row_port.addWidget(QLabel("继电器串口："))
        row_port.addWidget(self.combo_relay_port, 1)
        row_port.addWidget(self.btn_refresh_relay_ports)
        row_actions = QHBoxLayout()
        row_actions.addWidget(self.check_sim_relay)
        row_actions.addWidget(self.btn_relay_connect)
        row_actions.addWidget(self.btn_relay_disconnect)
        row_actions.addWidget(self.btn_refresh_states)
        row_actions.addStretch(1)
        row_actions.addWidget(QLabel("状态："))
        row_actions.addWidget(self.label_relay_status)

        layout.addLayout(row_port)
        layout.addLayout(row_actions)
        return group

    def _create_relay_channel_group(self) -> QGroupBox:
        group = QGroupBox("8 路手动控制")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(10)

        toolbar = QHBoxLayout()
        self.btn_open_all = QPushButton("全部打开")
        self.btn_open_all.setObjectName("PrimaryButton")
        self.btn_open_all.clicked.connect(partial(self._set_all_channel_states, True))
        self.btn_close_all = QPushButton("全部关闭")
        self.btn_close_all.setObjectName("DangerButton")
        self.btn_close_all.clicked.connect(partial(self._set_all_channel_states, False))
        toolbar.addWidget(self.btn_open_all)
        toolbar.addWidget(self.btn_close_all)
        toolbar.addStretch(1)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        for channel in range(1, 9):
            row = (channel - 1) // 2
            column = (channel - 1) % 2
            grid.addWidget(self._create_channel_card(channel), row, column)

        layout.addLayout(toolbar)
        layout.addLayout(grid)
        return group

    def _create_channel_card(self, channel: int) -> QGroupBox:
        group = QGroupBox(f"第 {channel} 路")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        status_label = QLabel()
        self._channel_status_labels[channel] = status_label
        self._set_channel_status(channel, None)

        btn_open = QPushButton("打开")
        btn_open.setObjectName("PrimaryButton")
        btn_open.setToolTip(self._build_command_tooltip(channel, True))
        btn_open.clicked.connect(partial(self._set_channel_state, channel, True))
        btn_close = QPushButton("关闭")
        btn_close.setObjectName("DangerButton")
        btn_close.setToolTip(self._build_command_tooltip(channel, False))
        btn_close.clicked.connect(partial(self._set_channel_state, channel, False))
        self._channel_action_buttons.extend([btn_open, btn_close])

        button_row = QHBoxLayout()
        button_row.addWidget(btn_open)
        button_row.addWidget(btn_close)
        layout.addWidget(status_label)
        layout.addLayout(button_row)
        return group

    def _create_log_group(self) -> QGroupBox:
        group = QGroupBox("运行日志")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 10, 12, 12)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("日志将在此显示设备连接、蓝牙检测和手动继电器控制结果。")
        btn_clear = QPushButton("清空日志")
        btn_clear.clicked.connect(self.log_view.clear)
        layout.addWidget(self.log_view)
        layout.addWidget(btn_clear, alignment=Qt.AlignmentFlag.AlignRight)
        return group

    def _load_settings_into_ui(self) -> None:
        cfg = self._config_store.load_device_settings()
        self.input_bt_name.setText(cfg.bluetooth_target.bt_name_keyword)
        self.input_bt_mac.setText(cfg.bluetooth_target.bt_mac)
        self._select_combo_value(self.combo_bt_mode, cfg.bluetooth_target.bt_match_mode)
        self.check_sim_multimeter.setChecked(cfg.simulation_multimeter)
        self.check_sim_relay.setChecked(cfg.simulation_relay)
        self.check_sim_bluetooth.setChecked(cfg.simulation_bluetooth)
        self._preferred_meter_port = cfg.multimeter_port
        self._preferred_relay_port = cfg.relay_port
        if cfg.simulation_multimeter:
            self.label_meter_status.setText("仿真设备已就绪")
        if cfg.simulation_relay:
            self.label_relay_status.setText("仿真设备已就绪")

    def _bind_auto_save_signals(self) -> None:
        self.input_bt_name.textChanged.connect(self._on_settings_changed)
        self.input_bt_mac.textChanged.connect(self._on_settings_changed)
        self.combo_bt_mode.currentIndexChanged.connect(self._on_settings_changed)
        self.check_sim_multimeter.toggled.connect(self._on_simulation_changed)
        self.check_sim_relay.toggled.connect(self._on_simulation_changed)
        self.check_sim_bluetooth.toggled.connect(self._on_simulation_changed)
        self.combo_multimeter_port.currentIndexChanged.connect(self._on_settings_changed)
        self.combo_relay_port.currentIndexChanged.connect(self._on_settings_changed)

    def _refresh_serial_ports(self, *_: object, emit_log: bool = True) -> None:
        ports = list_serial_ports()
        current_meter = self.combo_multimeter_port.currentData() or getattr(
            self, "_preferred_meter_port", ""
        )
        current_relay = self.combo_relay_port.currentData() or getattr(
            self, "_preferred_relay_port", ""
        )

        self._suspend_auto_save = True
        self.combo_multimeter_port.clear()
        self.combo_relay_port.clear()
        self.combo_multimeter_port.addItem("请选择串口", "")
        self.combo_relay_port.addItem("请选择串口", "")
        for port in ports:
            self.combo_multimeter_port.addItem(port.label, port.device)
            self.combo_relay_port.addItem(port.label, port.device)
        self._select_combo_value(self.combo_multimeter_port, str(current_meter or ""))
        self._select_combo_value(self.combo_relay_port, str(current_relay or ""))
        self._suspend_auto_save = False

        self._update_controls()
        if emit_log:
            self._append_log("INFO", f"串口刷新完成，共发现 {len(ports)} 个端口。")

    def _connect_multimeter(self) -> None:
        if self.check_sim_multimeter.isChecked():
            self._device_context.multimeter_sim.connect()
            self.label_meter_status.setText("仿真设备已就绪")
            self._append_log("INFO", "万用表仿真模式已开启。")
            self._update_controls()
            return
        port = str(self.combo_multimeter_port.currentData() or "").strip()
        if not port:
            QMessageBox.warning(self, "提示", "请先选择万用表串口。")
            return
        if self._device_context.multimeter_real.connect(port):
            self.label_meter_status.setText(f"已连接({port})")
            self._append_log("INFO", f"万用表连接成功: {port}")
            self._save_current_settings()
        else:
            self.label_meter_status.setText("连接失败")
            self._append_log("ERROR", f"万用表连接失败: {port}")
        self._update_controls()

    def _disconnect_multimeter(self) -> None:
        if self.check_sim_multimeter.isChecked():
            self._device_context.multimeter_sim.disconnect()
            self.label_meter_status.setText("仿真设备已断开")
        else:
            self._device_context.multimeter_real.disconnect()
            self.label_meter_status.setText("未连接")
        self._append_log("INFO", "万用表已断开。")
        self._update_controls()

    def _read_multimeter_data(self) -> None:
        cfg = self._collect_settings()
        meter = self._device_context.active_multimeter(cfg)
        if not cfg.simulation_multimeter and not self._device_context.multimeter_real.is_connected:
            QMessageBox.warning(self, "设备未连接", "万用表尚未连接。")
            return
        voltage = meter.read_voltage(attempts=3)
        if voltage is None:
            self._append_log("WARNING", "未读取到有效万用表电压。")
            return
        self._append_log("INFO", f"万用表电压: {voltage:.4f} V")

    def _connect_relay(self) -> None:
        if self.check_sim_relay.isChecked():
            self._device_context.relay_sim.connect()
            self.label_relay_status.setText("仿真设备已就绪")
            self._append_log("INFO", "继电器仿真模式已开启。")
            self._refresh_channel_states(emit_log=False, emit_warning=False)
            self._update_controls()
            return
        port = str(self.combo_relay_port.currentData() or "").strip()
        if not port:
            QMessageBox.warning(self, "提示", "请先选择继电器串口。")
            return
        if self._device_context.relay_real.connect(port):
            self.label_relay_status.setText(f"已连接({port})")
            self._append_log("INFO", f"继电器连接成功: {port}")
            self._save_current_settings()
            self._refresh_channel_states(emit_log=False, emit_warning=True)
        else:
            self.label_relay_status.setText("连接失败")
            self._append_log("ERROR", f"继电器连接失败: {port}")
        self._update_controls()

    def _disconnect_relay(self) -> None:
        if self.check_sim_relay.isChecked():
            self._device_context.relay_sim.disconnect()
            self.label_relay_status.setText("仿真设备已断开")
        else:
            self._device_context.relay_real.disconnect()
            self.label_relay_status.setText("未连接")
        for channel in range(1, 9):
            self._set_channel_status(channel, None)
        self._append_log("INFO", "继电器已断开。")
        self._update_controls()

    def _refresh_channel_states(
        self,
        *_: object,
        emit_log: bool = True,
        emit_warning: bool = True,
    ) -> bool:
        cfg = self._collect_settings()
        relay = self._device_context.active_relay(cfg)
        if not cfg.simulation_relay and not self._device_context.relay_real.is_connected:
            if emit_warning:
                QMessageBox.warning(self, "设备未连接", "继电器尚未连接。")
            return False
        try:
            states = relay.query_status()
        except Exception as exc:  # noqa: BLE001
            self._append_log("WARNING", f"继电器状态刷新失败: {exc}")
            return False
        for channel, state in states.items():
            if 1 <= channel <= 8:
                self._set_channel_status(channel, state)
        if emit_log:
            self._append_log("INFO", "继电器状态已刷新。")
        return True

    def _set_channel_state(self, channel: int, on: bool) -> None:
        cfg = self._collect_settings()
        relay = self._device_context.active_relay(cfg)
        if not cfg.simulation_relay and not self._device_context.relay_real.is_connected:
            QMessageBox.warning(self, "设备未连接", "继电器尚未连接。")
            return
        action_text = "打开" if on else "关闭"
        try:
            relay.set_channel_state(channel, on)
        except Exception as exc:  # noqa: BLE001
            self._append_log("ERROR", f"第 {channel} 路{action_text}失败: {exc}")
            return
        self._set_channel_status(channel, on)
        self._append_log("INFO", f"第 {channel} 路已{action_text}，HEX: {self._command_hex(channel, on)}")

    def _set_all_channel_states(self, on: bool) -> None:
        for channel in range(1, 9):
            self._set_channel_state(channel, on)

    def _detect_bluetooth_devices(self) -> None:
        cfg = self._collect_settings()
        probe = self._device_context.active_bluetooth_probe(cfg)
        try:
            devices = probe.query_devices()
        except Exception as exc:  # noqa: BLE001
            self._append_log("ERROR", f"检测蓝牙设备失败: {exc}")
            return
        if not devices:
            self._append_log("WARNING", "未检测到蓝牙相关设备。")
            return
        self._append_log("INFO", f"检测到 {len(devices)} 个蓝牙相关设备：")
        for device in devices:
            mac = f" | MAC={device.mac}" if getattr(device, "mac", "") else ""
            self._append_log("INFO", f"  - {device.name} | {device.instance_id}{mac}")

    def _check_bluetooth_connection(self) -> None:
        cfg = self._collect_settings()
        target = cfg.bluetooth_target
        probe = self._device_context.active_bluetooth_probe(cfg)
        try:
            connected, matched = probe.is_target_connected(
                target.bt_name_keyword,
                target.bt_mac,
                target.bt_match_mode,
            )
        except Exception as exc:  # noqa: BLE001
            self._append_log("ERROR", f"检查蓝牙连接状态失败: {exc}")
            return
        status = "已连接" if connected else "未连接"
        self._append_log("INFO", f"蓝牙连接状态: {status}，匹配设备 {len(matched)} 个。")

    def _remove_paired_bluetooth_device(self) -> None:
        cfg = self._collect_settings()
        target = cfg.bluetooth_target
        manager = self._device_context.active_bluetooth_manager(cfg)
        try:
            result = manager.remove_target(
                target.bt_name_keyword,
                target.bt_mac,
                target.bt_match_mode,
                timeout_sec=10.0,
                sample_interval_sec=0.5,
                log_cb=self._append_log,
            )
        except Exception as exc:  # noqa: BLE001
            self._append_log("ERROR", f"删除已配对设备失败: {exc}")
            return
        level = "INFO" if result.ok else "WARNING"
        self._append_log(level, result.reason or ("删除成功" if result.ok else "删除失败"))

    def _on_simulation_changed(self, *_: object) -> None:
        if self.check_sim_multimeter.isChecked():
            self.label_meter_status.setText("仿真设备已就绪")
        elif not self._device_context.multimeter_real.is_connected:
            self.label_meter_status.setText("未连接")
        if self.check_sim_relay.isChecked():
            self.label_relay_status.setText("仿真设备已就绪")
        elif not self._device_context.relay_real.is_connected:
            self.label_relay_status.setText("未连接")
        self._on_settings_changed()
        self._update_controls()

    def _on_settings_changed(self, *_: object) -> None:
        if self._suspend_auto_save:
            return
        self._save_current_settings()
        self._update_controls()

    def _collect_settings(self) -> DeviceSettings:
        raw_mac = self.input_bt_mac.text().strip()
        normalized_mac = normalize_mac(raw_mac)
        if raw_mac and not normalized_mac:
            normalized_mac = raw_mac
        return DeviceSettings(
            multimeter_port=str(self.combo_multimeter_port.currentData() or ""),
            relay_port=str(self.combo_relay_port.currentData() or ""),
            simulation_multimeter=self.check_sim_multimeter.isChecked(),
            simulation_relay=self.check_sim_relay.isChecked(),
            simulation_bluetooth=self.check_sim_bluetooth.isChecked(),
            bluetooth_target=BluetoothTargetSettings(
                bt_name_keyword=self.input_bt_name.text().strip(),
                bt_mac=normalized_mac,
                bt_match_mode=self.combo_bt_mode.currentData() or "name_or_mac",
            ),
        )

    def _save_current_settings(self) -> None:
        cfg = self._collect_settings()
        self._config_store.save_device_settings(cfg)
        self._preferred_meter_port = cfg.multimeter_port
        self._preferred_relay_port = cfg.relay_port

    def _update_controls(self) -> None:
        meter_sim = self.check_sim_multimeter.isChecked()
        relay_sim = self.check_sim_relay.isChecked()
        meter_connected = meter_sim or self._device_context.multimeter_real.is_connected
        relay_connected = relay_sim or self._device_context.relay_real.is_connected

        self.combo_multimeter_port.setEnabled(not meter_sim and not self._device_context.multimeter_real.is_connected)
        self.btn_refresh_meter_ports.setEnabled(not meter_sim and not self._device_context.multimeter_real.is_connected)
        self.btn_meter_connect.setEnabled(meter_sim or (not meter_connected and bool(self.combo_multimeter_port.currentData())))
        self.btn_meter_disconnect.setEnabled(meter_connected)
        self.btn_meter_fetch.setEnabled(meter_connected)

        self.combo_relay_port.setEnabled(not relay_sim and not self._device_context.relay_real.is_connected)
        self.btn_refresh_relay_ports.setEnabled(not relay_sim and not self._device_context.relay_real.is_connected)
        self.btn_relay_connect.setEnabled(relay_sim or (not relay_connected and bool(self.combo_relay_port.currentData())))
        self.btn_relay_disconnect.setEnabled(relay_connected)
        self.btn_refresh_states.setEnabled(relay_connected)
        self.btn_open_all.setEnabled(relay_connected)
        self.btn_close_all.setEnabled(relay_connected)
        for button in self._channel_action_buttons:
            button.setEnabled(relay_connected)

    def _set_channel_status(self, channel: int, state: bool | None) -> None:
        self._channel_states[channel] = state
        label = self._channel_status_labels[channel]
        if state is None:
            label.setText("当前状态：未知")
            label.setStyleSheet("color: #6b8094; font-weight: 600;")
        elif state:
            label.setText("当前状态：已打开")
            label.setStyleSheet("color: #1e7a46; font-weight: 600;")
        else:
            label.setText("当前状态：已关闭")
            label.setStyleSheet("color: #c24a32; font-weight: 600;")

    def _append_log(self, level: str, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        level_upper = level.upper()
        line = f"[{ts}] [{level_upper}] {message}"
        color = self._log_level_color(level_upper)
        self.log_view.append(f'<span style="color:{color}; white-space:pre;">{escape(line)}</span>')
        _LOGGER.log(getattr(logging, level_upper, logging.INFO), message)

    @staticmethod
    def _log_level_color(level: str) -> str:
        if level in {"ERROR", "CRITICAL"}:
            return "#c62828"
        if level == "WARNING":
            return "#b26a00"
        if level in {"DEBUG", "TRACE"}:
            return "#546e7a"
        return "#1f5e94"

    @staticmethod
    def _select_combo_value(combo: QComboBox, value: str) -> None:
        if not value:
            combo.setCurrentIndex(0)
            return
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    @staticmethod
    def _command_hex(channel: int, on: bool) -> str:
        return build_switch_command(channel, on).hex(" ").upper()

    def _build_command_tooltip(self, channel: int, on: bool) -> str:
        action_text = "打开" if on else "关闭"
        return f"{action_text}第 {channel} 路，发送 HEX：{self._command_hex(channel, on)}"
