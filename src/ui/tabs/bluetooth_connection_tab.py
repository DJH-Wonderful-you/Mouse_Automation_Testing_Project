from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape
import logging
import time
from typing import Callable

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.core.bluetooth_connect_engine import BluetoothConnectRunner
from src.core.bluetooth_pairing import BluetoothActionResult, SystemBluetoothManager
from src.core.bluetooth_probe import BluetoothDeviceInfo, BluetoothProbe, normalize_mac
from src.core.config_store import ConfigStore
from src.core.relay_lcus88 import LCUSRelay
from src.core.serial_utils import list_serial_ports
from src.core.test_engine import TestEngineWorker
from src.core.types import BluetoothConnectCycleResult, BluetoothConnectSettings

_LOGGER = logging.getLogger("ui.bluetooth_connection")


class NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()


@dataclass(slots=True)
class _BluetoothDetectResult:
    devices: list[BluetoothDeviceInfo]


@dataclass(slots=True)
class _BluetoothCheckResult:
    mode_text: str
    criteria: list[str]
    connected: bool
    matched: list[BluetoothDeviceInfo]


@dataclass(slots=True)
class _RelayAutoConnectResult:
    ports: list[str]
    found_relay: str = ""
    relay_connected: bool = False
    relay_attempts: list[str] | None = None


@dataclass(slots=True)
class _BluetoothRemoveResult:
    result: BluetoothActionResult


class _AsyncTaskWorker(QObject):
    sig_success = Signal(object)
    sig_error = Signal(str)
    sig_finished = Signal()

    def __init__(self, task: Callable[[], object]) -> None:
        super().__init__()
        self._task = task

    @Slot()
    def run(self) -> None:
        try:
            self.sig_success.emit(self._task())
        except Exception as exc:  # noqa: BLE001
            self.sig_error.emit(str(exc))
        finally:
            self.sig_finished.emit()


class BluetoothConnectionTab(QWidget):
    def __init__(self, config_store: ConfigStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config_store = config_store
        self._relay_real = LCUSRelay()
        self._bt_probe_real = BluetoothProbe()
        self._bt_manager = SystemBluetoothManager()

        self._thread: QThread | None = None
        self._worker: TestEngineWorker | None = None
        self._runner: BluetoothConnectRunner | None = None
        self._running = False
        self._success_count = 0
        self._fail_count = 0
        self._preferred_relay_port = ""
        self._suspend_auto_save = True

        self._aux_task_running = False
        self._aux_task_name = ""
        self._aux_task_thread: QThread | None = None
        self._aux_task_worker: _AsyncTaskWorker | None = None
        self._aux_task_success_handler: Callable[[object], None] | None = None

        self._build_ui()
        self._bind_auto_save_signals()
        self._load_settings_into_ui()
        self._refresh_serial_ports()
        self._refresh_channel_hints()
        self._update_device_control_state()
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
        left_layout.addWidget(self._create_main_control_group())
        left_layout.addWidget(self._create_relay_group())
        left_layout.addWidget(self._create_bluetooth_group())
        left_layout.addWidget(self._create_progress_group())
        left_layout.addStretch(1)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setWidget(left_container)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self._create_log_group())
        right_widget.setMinimumWidth(330)

        splitter.addWidget(left_scroll)
        splitter.addWidget(right_widget)
        splitter.setSizes([760, 420])
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

    def _create_page_intro(self) -> QWidget:
        card = QWidget()
        card.setObjectName("PageIntroCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 10)
        layout.setSpacing(4)

        title = QLabel("蓝牙连接测试")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "执行蓝牙配对连接循环验证，支持自动连接继电器、已配对蓝牙检测、状态检查与配对后自动清理。"
        )
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        return card

    def _create_main_control_group(self) -> QGroupBox:
        group = QGroupBox("主控区")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        self.input_test_count = NoWheelSpinBox()
        self.input_test_count.setRange(1, 1_000_000)
        self.input_test_count.setValue(100)

        self.input_state_timeout = NoWheelDoubleSpinBox()
        self.input_state_timeout.setRange(1.0, 180.0)
        self.input_state_timeout.setDecimals(3)
        self.input_state_timeout.setSingleStep(0.5)
        self.input_state_timeout.setSuffix(" s")
        self.input_state_timeout.setValue(15.0)

        self.input_sample_interval = NoWheelDoubleSpinBox()
        self.input_sample_interval.setRange(0.05, 10.0)
        self.input_sample_interval.setDecimals(3)
        self.input_sample_interval.setSingleStep(0.05)
        self.input_sample_interval.setSuffix(" s")
        self.input_sample_interval.setValue(0.5)

        self.input_pairing_press = NoWheelDoubleSpinBox()
        self.input_pairing_press.setRange(0.1, 30.0)
        self.input_pairing_press.setDecimals(3)
        self.input_pairing_press.setSingleStep(0.1)
        self.input_pairing_press.setSuffix(" s")
        self.input_pairing_press.setValue(2.0)

        self.btn_auto_connect = QPushButton("自动连接设备")
        self.btn_auto_connect.clicked.connect(self._auto_connect_relay)

        self.btn_start = QPushButton("开始测试")
        self.btn_start.setObjectName("PrimaryButton")
        self.btn_start.clicked.connect(self._start_test)

        self.btn_stop = QPushButton("停止测试")
        self.btn_stop.setObjectName("DangerButton")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_test)

        button_row = QHBoxLayout()
        button_row.addWidget(self.btn_auto_connect)
        button_row.addWidget(self.btn_start)
        button_row.addWidget(self.btn_stop)
        button_row.addStretch(1)

        layout.addLayout(button_row)
        return group

    def _create_relay_group(self) -> QGroupBox:
        group = QGroupBox("继电器控制区")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        self.input_mode_relay_channel = NoWheelSpinBox()
        self.input_mode_relay_channel.setRange(1, 8)
        self.input_mode_relay_channel.setValue(1)
        self.input_mode_relay_channel.valueChanged.connect(self._refresh_channel_hints)

        self.input_pairing_relay_channel = NoWheelSpinBox()
        self.input_pairing_relay_channel.setRange(1, 8)
        self.input_pairing_relay_channel.setValue(2)
        self.input_pairing_relay_channel.valueChanged.connect(self._refresh_channel_hints)

        self.combo_relay_port = NoWheelComboBox()
        self.combo_relay_port.setMinimumWidth(240)
        self.btn_refresh_ports = QPushButton("刷新串口")
        self.btn_refresh_ports.clicked.connect(self._refresh_serial_ports)
        self.btn_relay_connect = QPushButton("连接设备")
        self.btn_relay_connect.clicked.connect(self._connect_relay)
        self.btn_relay_disconnect = QPushButton("断开设备")
        self.btn_relay_disconnect.setObjectName("DangerButton")
        self.btn_relay_disconnect.clicked.connect(self._disconnect_relay)
        self.label_relay_status = QLabel("未连接")

        self.label_mode_channel = QLabel()
        self.btn_mode_on = QPushButton("打开蓝牙模式通道")
        self.btn_mode_on.clicked.connect(self._open_mode_channel)
        self.btn_mode_off = QPushButton("关闭蓝牙模式通道")
        self.btn_mode_off.clicked.connect(self._close_mode_channel)

        self.label_pairing_channel = QLabel()
        self.btn_pairing_on = QPushButton("打开配对按键通道")
        self.btn_pairing_on.clicked.connect(self._open_pairing_channel)
        self.btn_pairing_off = QPushButton("关闭配对按键通道")
        self.btn_pairing_off.clicked.connect(self._close_pairing_channel)
        self.btn_pairing_pulse = QPushButton("触发一次配对模式")
        self.btn_pairing_pulse.clicked.connect(self._trigger_pairing_mode_manually)

        row_port = QHBoxLayout()
        row_port.addWidget(QLabel("继电器串口："))
        row_port.addWidget(self.combo_relay_port, 1)
        row_port.addWidget(self.btn_refresh_ports)

        row_actions = QHBoxLayout()
        row_actions.addWidget(self.btn_relay_connect)
        row_actions.addWidget(self.btn_relay_disconnect)
        row_actions.addStretch(1)
        row_actions.addWidget(QLabel("状态："))
        row_actions.addWidget(self.label_relay_status)

        row_mode = QHBoxLayout()
        row_mode.addWidget(self.label_mode_channel, 1)
        row_mode.addWidget(self.btn_mode_on)
        row_mode.addWidget(self.btn_mode_off)

        row_pairing = QHBoxLayout()
        row_pairing.addWidget(self.label_pairing_channel, 1)
        row_pairing.addWidget(self.btn_pairing_on)
        row_pairing.addWidget(self.btn_pairing_off)
        row_pairing.addWidget(self.btn_pairing_pulse)

        layout.addLayout(row_port)
        layout.addLayout(row_actions)
        layout.addLayout(row_mode)
        layout.addLayout(row_pairing)
        return group
    def _create_bluetooth_group(self) -> QGroupBox:
        group = QGroupBox("蓝牙设备定位区")
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

        button_row = QWidget()
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)

        self.btn_bt_detect = QPushButton("检测已配对蓝牙名称")
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
        form.addRow("", button_row)
        return group

    def _create_progress_group(self) -> QGroupBox:
        group = QGroupBox("进度与统计")
        layout = QGridLayout(group)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(6)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

        self.label_done = QLabel("已完成：0/0")
        self.label_success = QLabel("成功：0")
        self.label_fail = QLabel("失败：0")
        self.label_rate = QLabel("成功率：0.00%")

        layout.addWidget(self.progress, 0, 0, 1, 4)
        layout.addWidget(self.label_done, 1, 0)
        layout.addWidget(self.label_success, 1, 1)
        layout.addWidget(self.label_fail, 1, 2)
        layout.addWidget(self.label_rate, 1, 3)
        return group

    def _create_log_group(self) -> QGroupBox:
        group = QGroupBox("运行日志")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 10, 12, 12)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("日志将在此显示测试过程与错误提示。")

        btn_clear = QPushButton("清空日志")
        btn_clear.clicked.connect(self.log_view.clear)

        layout.addWidget(self.log_view)
        layout.addWidget(btn_clear, alignment=Qt.AlignmentFlag.AlignRight)
        return group

    def create_settings_section(self) -> QGroupBox:
        group = QGroupBox("蓝牙连接测试")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(10)

        summary = QLabel(
            "集中配置循环计划、配对等待与两路继电器执行参数，修改后自动保存，并在下次测试时生效。"
        )
        summary.setWordWrap(True)

        layout.addWidget(summary)
        layout.addWidget(self._create_cycle_plan_settings_group())
        layout.addWidget(self._create_pairing_settings_group())
        layout.addWidget(self._create_relay_settings_group())
        return group

    def _create_cycle_plan_settings_group(self) -> QGroupBox:
        group = QGroupBox("循环计划")
        layout = QGridLayout(group)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(8)

        layout.addWidget(QLabel("测试次数："), 0, 0)
        layout.addWidget(self.input_test_count, 0, 1)
        layout.setColumnStretch(2, 1)
        return group

    def _create_pairing_settings_group(self) -> QGroupBox:
        group = QGroupBox("配对等待")
        layout = QGridLayout(group)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(8)

        layout.addWidget(QLabel("状态等待超时："), 0, 0)
        layout.addWidget(self.input_state_timeout, 0, 1)
        layout.addWidget(QLabel("采样间隔："), 0, 2)
        layout.addWidget(self.input_sample_interval, 0, 3)
        layout.addWidget(QLabel("配对按压时长："), 1, 0)
        layout.addWidget(self.input_pairing_press, 1, 1)
        layout.setColumnStretch(4, 1)
        return group

    def _create_relay_settings_group(self) -> QGroupBox:
        group = QGroupBox("继电器执行")
        layout = QGridLayout(group)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(8)

        layout.addWidget(QLabel("蓝牙模式通道："), 0, 0)
        layout.addWidget(self.input_mode_relay_channel, 0, 1)
        layout.addWidget(QLabel("配对按键通道："), 0, 2)
        layout.addWidget(self.input_pairing_relay_channel, 0, 3)
        layout.setColumnStretch(4, 1)
        return group

    def _load_settings_into_ui(self) -> None:
        cfg = self._config_store.load_bluetooth_connect()
        self.input_test_count.setValue(cfg.test_count)
        self.input_state_timeout.setValue(cfg.state_timeout_ms / 1000.0)
        self.input_sample_interval.setValue(cfg.sample_interval_ms / 1000.0)
        self.input_pairing_press.setValue(cfg.pairing_press_ms / 1000.0)
        self.input_mode_relay_channel.setValue(cfg.mode_relay_channel)
        self.input_pairing_relay_channel.setValue(cfg.pairing_relay_channel)
        self._preferred_relay_port = cfg.relay_port
        self.input_bt_name.setText(cfg.bt_name_keyword)
        self.input_bt_mac.setText(cfg.bt_mac)

        mode_index = self.combo_bt_mode.findData(cfg.bt_match_mode)
        if mode_index >= 0:
            self.combo_bt_mode.setCurrentIndex(mode_index)

    def _bind_auto_save_signals(self) -> None:
        self.input_test_count.valueChanged.connect(self._on_settings_changed_auto_save)
        self.input_state_timeout.valueChanged.connect(self._on_settings_changed_auto_save)
        self.input_sample_interval.valueChanged.connect(self._on_settings_changed_auto_save)
        self.input_pairing_press.valueChanged.connect(self._on_settings_changed_auto_save)
        self.input_mode_relay_channel.valueChanged.connect(self._on_settings_changed_auto_save)
        self.input_pairing_relay_channel.valueChanged.connect(self._on_settings_changed_auto_save)
        self.combo_relay_port.currentIndexChanged.connect(self._on_settings_changed_auto_save)
        self.combo_bt_mode.currentIndexChanged.connect(self._on_settings_changed_auto_save)
        self.input_bt_name.textChanged.connect(self._on_settings_changed_auto_save)
        self.input_bt_mac.textChanged.connect(self._on_settings_changed_auto_save)

    def _on_settings_changed_auto_save(self, *_: object) -> None:
        if self._suspend_auto_save:
            return
        self._save_current_settings(emit_log=False, show_error=False)

    def _collect_settings_from_ui(self) -> BluetoothConnectSettings:
        mode_data = self.combo_bt_mode.currentData()
        bt_match_mode = mode_data if mode_data in {"name_or_mac", "name_and_mac"} else "name_or_mac"
        raw_mac = self.input_bt_mac.text().strip()
        normalized_mac = normalize_mac(raw_mac)
        if raw_mac and not normalized_mac:
            raise ValueError("蓝牙 MAC 格式无效，请输入 12 位十六进制地址。")

        mode_channel = self.input_mode_relay_channel.value()
        pairing_channel = self.input_pairing_relay_channel.value()
        if mode_channel == pairing_channel:
            raise ValueError("蓝牙模式通道和配对按键通道不能相同。")

        return BluetoothConnectSettings(
            test_count=self.input_test_count.value(),
            relay_port=self.combo_relay_port.currentData() or "",
            bt_name_keyword=self.input_bt_name.text().strip(),
            bt_mac=normalized_mac,
            bt_match_mode=bt_match_mode,  # type: ignore[arg-type]
            mode_relay_channel=mode_channel,
            pairing_relay_channel=pairing_channel,
            pairing_press_ms=max(100, int(round(self.input_pairing_press.value() * 1000))),
            state_timeout_ms=max(1000, int(round(self.input_state_timeout.value() * 1000))),
            sample_interval_ms=max(50, int(round(self.input_sample_interval.value() * 1000))),
        )

    def _save_current_settings(self, *, emit_log: bool = True, show_error: bool = True) -> bool:
        try:
            cfg = self._collect_settings_from_ui()
            self._config_store.save_bluetooth_connect(cfg)
        except ValueError as exc:
            if show_error:
                QMessageBox.warning(self, "参数错误", str(exc))
            return False
        self.input_bt_mac.setText(cfg.bt_mac)
        self._preferred_relay_port = cfg.relay_port
        if emit_log:
            self._append_log("INFO", "配置已保存。")
        return True
    def _refresh_serial_ports(self) -> None:
        ports = list_serial_ports()
        current_relay = self.combo_relay_port.currentData() or self._preferred_relay_port
        self.combo_relay_port.clear()
        self.combo_relay_port.addItem("请选择串口", "")
        for port in ports:
            self.combo_relay_port.addItem(port.label, port.device)
        self._select_combo_value(self.combo_relay_port, current_relay)
        self._append_log("INFO", f"串口刷新完成，共发现 {len(ports)} 个端口。")

    def _connect_relay(self) -> None:
        port = self.combo_relay_port.currentData() or ""
        if not port:
            QMessageBox.warning(self, "提示", "请先选择继电器串口。")
            return
        if self._relay_real.connect(port):
            self.label_relay_status.setText(f"已连接({port})")
            self._append_log("INFO", f"继电器连接成功: {port}")
        else:
            self.label_relay_status.setText("连接失败")
            self._append_log("ERROR", f"继电器连接失败: {port}")
        self._update_device_control_state()

    def _disconnect_relay(self) -> None:
        self._relay_real.disconnect()
        self.label_relay_status.setText("未连接")
        self._append_log("INFO", "继电器已断开。")
        self._update_device_control_state()

    def _auto_connect_relay(self) -> None:
        if self._aux_task_running:
            self._append_log("WARNING", f"{self._aux_task_name}正在执行，请稍候。")
            return

        self._append_log("INFO", "开始自动识别继电器设备，请稍候...")

        def task() -> object:
            result = _RelayAutoConnectResult(ports=[], relay_attempts=[])
            port_infos = list_serial_ports()
            result.ports = [port.device for port in port_infos]
            if not result.ports:
                return result

            filtered_infos = [
                port
                for port in port_infos
                if not any(
                    keyword in f"{port.description} {port.hwid}".lower()
                    for keyword in ("bluetooth", "bth")
                )
            ]
            candidate_infos = filtered_infos or port_infos
            relay_allow_open_only = len(candidate_infos) == 1
            for port_info in candidate_infos:
                port = port_info.device
                result.relay_attempts.append(port)
                if self._relay_real.auto_connect(port, allow_open_only=relay_allow_open_only):
                    result.found_relay = port
                    result.relay_connected = True
                    break
            return result

        self._start_aux_task("自动连接设备", task, self._on_auto_connect_relay_done)

    def _on_auto_connect_relay_done(self, payload: object) -> None:
        if not isinstance(payload, _RelayAutoConnectResult):
            self._append_log("ERROR", "自动连接结果无效。")
            return

        if not payload.ports:
            self._append_log("WARNING", "未发现可用串口，无法自动连接。")
            return

        self._append_log("INFO", f"候选继电器串口：{', '.join(payload.ports)}")
        if payload.relay_attempts:
            self._append_log("INFO", f"已尝试继电器端口：{', '.join(payload.relay_attempts)}")

        if payload.found_relay:
            self._ensure_combo_has_value(self.combo_relay_port, payload.found_relay)
            self._select_combo_value(self.combo_relay_port, payload.found_relay)
            if payload.relay_connected:
                self.label_relay_status.setText(f"已连接({payload.found_relay})")
                self._append_log("INFO", f"自动识别继电器成功: {payload.found_relay}")
            else:
                self.label_relay_status.setText("连接失败")
                self._append_log("ERROR", f"已识别到继电器端口，但连接失败: {payload.found_relay}")
        else:
            self._append_log("WARNING", "自动识别继电器失败。")

    def _open_mode_channel(self) -> None:
        self._set_manual_relay_channel(self.input_mode_relay_channel.value(), True, "蓝牙模式")

    def _close_mode_channel(self) -> None:
        self._set_manual_relay_channel(self.input_mode_relay_channel.value(), False, "蓝牙模式")

    def _open_pairing_channel(self) -> None:
        self._set_manual_relay_channel(self.input_pairing_relay_channel.value(), True, "配对按键")

    def _close_pairing_channel(self) -> None:
        self._set_manual_relay_channel(self.input_pairing_relay_channel.value(), False, "配对按键")

    def _set_manual_relay_channel(self, channel: int, on: bool, label: str) -> None:
        if not self._relay_real.is_connected:
            QMessageBox.warning(self, "设备未连接", "继电器尚未连接。")
            return
        action = "打开" if on else "关闭"
        try:
            self._relay_real.set_channel_state(channel, on)
        except Exception as exc:  # noqa: BLE001
            self._append_log("ERROR", f"{label}通道{channel}{action}失败: {exc}")
            return
        self._append_log("INFO", f"已发送手动继电器命令：{label}通道={channel}，状态={action}")

    def _trigger_pairing_mode_manually(self) -> None:
        if self._aux_task_running:
            self._append_log("WARNING", f"{self._aux_task_name}正在执行，请稍候。")
            return
        if not self._relay_real.is_connected:
            QMessageBox.warning(self, "设备未连接", "继电器尚未连接。")
            return

        channel = self.input_pairing_relay_channel.value()
        hold_seconds = max(0.1, self.input_pairing_press.value())
        self._append_log(
            "INFO",
            f"开始手动触发配对模式：通道{channel} 打开 {hold_seconds:.3f}s。",
        )

        def task() -> object:
            self._relay_real.set_channel_state(channel, True)
            try:
                time.sleep(hold_seconds)
            finally:
                self._relay_real.set_channel_state(channel, False)
            return {"channel": channel, "hold_seconds": hold_seconds}

        self._start_aux_task("触发一次配对模式", task, self._on_manual_pairing_pulse_done)

    def _on_manual_pairing_pulse_done(self, payload: object) -> None:
        if not isinstance(payload, dict):
            self._append_log("ERROR", "手动配对脉冲结果无效。")
            return
        channel = payload.get("channel")
        hold_seconds = payload.get("hold_seconds")
        self._append_log(
            "INFO",
            f"手动配对模式触发完成：通道{channel} 已自动关闭，持续 {hold_seconds:.3f}s。",
        )

    def _detect_bluetooth_devices(self) -> None:
        self._append_log("INFO", "开始检测已配对蓝牙设备...")

        def task() -> object:
            return _BluetoothDetectResult(devices=self._bt_probe_real.query_devices())

        self._start_aux_task("检测已配对蓝牙名称", task, self._on_detect_bluetooth_devices_done)

    def _on_detect_bluetooth_devices_done(self, payload: object) -> None:
        if not isinstance(payload, _BluetoothDetectResult):
            self._append_log("ERROR", "蓝牙检测结果无效。")
            return

        devices = payload.devices
        if not devices:
            self._append_log("WARNING", "未检测到蓝牙设备信息。")
            return

        self._append_log("INFO", f"检测到 {len(devices)} 个蓝牙相关设备：")
        for device in devices:
            self._append_log("INFO", f"  - {device.summary}")

    def _check_bluetooth_connection(self) -> None:
        validation = self._validate_bluetooth_target_inputs(require_name=False)
        if validation is None:
            return
        bt_name, bt_mac, bt_match_mode = validation
        mode_text = "名称+MAC" if bt_match_mode == "name_and_mac" else "名称或 MAC"
        criteria: list[str] = []
        if bt_name:
            criteria.append(f"名称关键字={bt_name}")
        if bt_mac:
            criteria.append(f"MAC={bt_mac}")
        self._append_log(
            "INFO",
            f"开始检查蓝牙连接状态（匹配模式: {mode_text} | 条件: {', '.join(criteria)}）...",
        )

        def task() -> object:
            connected, matched = self._bt_probe_real.is_target_connected(bt_name, bt_mac, bt_match_mode)
            return _BluetoothCheckResult(
                mode_text=mode_text,
                criteria=criteria,
                connected=connected,
                matched=matched,
            )

        self._start_aux_task("检查蓝牙连接状态", task, self._on_check_bluetooth_connection_done)

    def _on_check_bluetooth_connection_done(self, payload: object) -> None:
        if not isinstance(payload, _BluetoothCheckResult):
            self._append_log("ERROR", "蓝牙检查结果无效。")
            return

        if payload.matched:
            self._append_log("INFO", f"匹配到 {len(payload.matched)} 个设备：")
            for device in payload.matched:
                connected_hint = getattr(device, "connected", None)
                connected_text = "未知" if connected_hint is None else ("已连接" if connected_hint else "未连接")
                self._append_log("INFO", f"  - {device.summary} | 连接状态={connected_text}")
        else:
            self._append_log("WARNING", "未匹配到目标蓝牙设备，请检查名称关键字或 MAC。")

        self._append_log(
            "INFO" if payload.connected else "WARNING",
            f"蓝牙检查结果: {'已连接' if payload.connected else '未连接'}",
        )

    def _remove_paired_bluetooth_device(self) -> None:
        validation = self._validate_bluetooth_target_inputs(require_name=False)
        if validation is None:
            return
        bt_name, bt_mac, bt_match_mode = validation
        self._append_log("INFO", "开始删除已配对蓝牙设备...")

        def task() -> object:
            result = self._bt_manager.remove_target(
                bt_name,
                bt_mac,
                bt_match_mode,
                timeout_sec=max(1.0, self.input_state_timeout.value()),
                sample_interval_sec=max(0.05, self.input_sample_interval.value()),
            )
            return _BluetoothRemoveResult(result=result)

        self._start_aux_task("删除已配对设备", task, self._on_remove_paired_bluetooth_device_done)

    def _on_remove_paired_bluetooth_device_done(self, payload: object) -> None:
        if not isinstance(payload, _BluetoothRemoveResult):
            self._append_log("ERROR", "删除已配对设备结果无效。")
            return

        result = payload.result
        level = "INFO" if result.ok else "ERROR"
        message = result.reason or ("删除已配对设备成功。" if result.ok else "删除已配对设备失败。")
        self._append_log(level, message)

    def _validate_bluetooth_target_inputs(
        self, *, require_name: bool
    ) -> tuple[str, str, str] | None:
        mode_data = self.combo_bt_mode.currentData()
        bt_match_mode = mode_data if mode_data in {"name_or_mac", "name_and_mac"} else "name_or_mac"
        bt_name = self.input_bt_name.text().strip()
        raw_mac = self.input_bt_mac.text().strip()
        bt_mac = normalize_mac(raw_mac)
        if raw_mac and not bt_mac:
            QMessageBox.warning(self, "输入无效", "蓝牙 MAC 格式无效。")
            return None
        if not bt_name and not bt_mac:
            QMessageBox.warning(self, "输入无效", "请填写蓝牙名称关键字或 MAC。")
            return None
        if require_name and not bt_name:
            QMessageBox.warning(
                self,
                "参数错误",
                "自动配对需要填写蓝牙名称关键字，以便在系统配对列表中定位目标设备。",
            )
            return None
        return bt_name, bt_mac, bt_match_mode
    def _start_aux_task(
        self,
        task_name: str,
        task: Callable[[], object],
        on_success: Callable[[object], None],
    ) -> None:
        if self._aux_task_running:
            self._append_log("WARNING", f"{self._aux_task_name}正在执行，请稍候。")
            return

        thread = QThread(self)
        worker = _AsyncTaskWorker(task)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.sig_success.connect(self._on_aux_task_success)
        worker.sig_error.connect(self._on_aux_task_error)
        worker.sig_finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_aux_task_thread_finished)

        self._aux_task_running = True
        self._aux_task_name = task_name
        self._aux_task_success_handler = on_success
        self._aux_task_thread = thread
        self._aux_task_worker = worker
        self._update_device_control_state()
        thread.start()

    @Slot(object)
    def _on_aux_task_success(self, payload: object) -> None:
        if self._aux_task_success_handler is None:
            return
        try:
            self._aux_task_success_handler(payload)
        except Exception as exc:  # noqa: BLE001
            self._append_log("ERROR", f"{self._aux_task_name}结果处理失败: {exc}")

    @Slot(str)
    def _on_aux_task_error(self, message: str) -> None:
        task_name = self._aux_task_name or "辅助任务"
        self._append_log("ERROR", f"{task_name}失败: {message}")

    @Slot()
    def _on_aux_task_thread_finished(self) -> None:
        self._aux_task_running = False
        self._aux_task_name = ""
        self._aux_task_success_handler = None
        self._aux_task_thread = None
        self._aux_task_worker = None
        self._update_device_control_state()

    def _start_test(self) -> None:
        if self._running:
            return
        if not self._save_current_settings():
            return

        try:
            cfg = self._collect_settings_from_ui()
        except ValueError as exc:
            QMessageBox.warning(self, "参数错误", str(exc))
            return

        if not self._relay_real.is_connected:
            QMessageBox.warning(self, "设备未连接", "继电器未连接，请先连接后再开始测试。")
            return

        validation = self._validate_bluetooth_target_inputs(require_name=True)
        if validation is None:
            return

        self._success_count = 0
        self._fail_count = 0
        self._update_stats(done=0, total=cfg.test_count)

        runner = BluetoothConnectRunner(
            relay=self._relay_real,
            bluetooth=self._bt_manager,
            settings=cfg,
            log_cb=lambda level, message: self._emit_worker_signal(
                self._worker, "log", level, message
            ),
            progress_cb=lambda done, total: self._emit_worker_signal(
                self._worker, "progress", done, total
            ),
            cycle_cb=lambda result: self._emit_worker_signal(
                self._worker, "cycle", result
            ),
        )
        worker = TestEngineWorker(runner)
        self._runner = runner
        self._worker = worker

        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.sig_log.connect(self._append_log)
        worker.sig_progress.connect(self._on_progress)
        worker.sig_cycle_result.connect(self._on_cycle_result)
        worker.sig_finished.connect(self._on_finished)
        worker.sig_error.connect(self._on_error)
        worker.sig_finished.connect(self._cleanup_worker_thread)
        worker.sig_error.connect(self._cleanup_worker_thread)

        self._thread = thread
        self._running = True
        self._update_running_state()
        self._append_log("INFO", "测试线程启动。")
        thread.start()

    def _stop_test(self) -> None:
        if self._worker:
            self._worker.stop()
            self._append_log("WARNING", "已请求停止测试。")

    @Slot(str, str)
    def _append_log(self, level: str, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        level_upper = level.upper()
        line = f"[{ts}] [{level_upper}] {message}"
        color = self._log_level_color(level_upper)
        self.log_view.append(
            f'<span style="color:{color}; white-space:pre;">{escape(line)}</span>'
        )
        log_level = (
            logging.INFO
            if level_upper == "TRACE"
            else getattr(logging, level_upper, logging.INFO)
        )
        _LOGGER.log(log_level, message)

    @staticmethod
    def _log_level_color(level: str) -> str:
        if level in {"ERROR", "CRITICAL"}:
            return "#c62828"
        if level == "WARNING":
            return "#b26a00"
        if level in {"DEBUG", "TRACE"}:
            return "#546e7a"
        return "#1f5e94"

    @Slot(int, int)
    def _on_progress(self, done: int, total: int) -> None:
        self._update_stats(done=done, total=total)

    @Slot(object)
    def _on_cycle_result(self, result: object) -> None:
        if not isinstance(result, BluetoothConnectCycleResult):
            return
        if result.success:
            self._success_count += 1
            self._append_log("INFO", f"[第{result.index}轮] 通过 | {result.reason}")
            return

        self._fail_count += 1
        self._append_log("WARNING", f"[第{result.index}轮] 失败 | 原因: {result.reason}")

    @Slot(int, int, float)
    def _on_finished(self, success_count: int, fail_count: int, success_rate: float) -> None:
        self._success_count = success_count
        self._fail_count = fail_count
        done = success_count + fail_count
        self._update_stats(done=done, total=max(done, self.input_test_count.value()))
        self._append_log(
            "INFO",
            f"测试完成。成功 {success_count}，失败 {fail_count}，成功率 {success_rate:.2f}%",
        )

    @Slot(str)
    def _on_error(self, message: str) -> None:
        self._append_log("ERROR", f"测试线程异常: {message}")
        QMessageBox.critical(self, "测试异常", message)

    @Slot()
    def _cleanup_worker_thread(self, *_: object) -> None:
        if self._thread:
            self._thread.quit()
            self._thread.wait(1500)
            self._thread.deleteLater()
        if self._worker:
            self._worker.deleteLater()
        self._thread = None
        self._worker = None
        self._runner = None
        self._running = False
        self._update_running_state()

    def _emit_worker_signal(
        self, worker: TestEngineWorker | None, kind: str, *args: object
    ) -> None:
        if worker is None:
            return
        if kind == "log":
            worker.sig_log.emit(str(args[0]), str(args[1]))
        elif kind == "progress":
            worker.sig_progress.emit(int(args[0]), int(args[1]))
        elif kind == "cycle":
            worker.sig_cycle_result.emit(args[0])
    def _update_stats(self, done: int, total: int) -> None:
        total = max(0, total)
        done = min(max(0, done), total if total > 0 else done)
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        self.label_done.setText(f"已完成：{done}/{total}")
        self.label_success.setText(f"成功：{self._success_count}")
        self.label_fail.setText(f"失败：{self._fail_count}")
        rate = (self._success_count / done * 100.0) if done else 0.0
        self.label_rate.setText(f"成功率：{rate:.2f}%")

    def _refresh_channel_hints(self, *_: object) -> None:
        self.label_mode_channel.setText(
            f"蓝牙模式通道：当前使用通道 {self.input_mode_relay_channel.value()}（测试期间保持打开）"
        )
        self.label_pairing_channel.setText(
            f"配对按键通道：当前使用通道 {self.input_pairing_relay_channel.value()}（按设置时长脉冲触发）"
        )

    def _update_device_control_state(self) -> None:
        busy = self._running
        control_busy = self._running or self._aux_task_running

        self.combo_relay_port.setEnabled(not control_busy)
        self.btn_refresh_ports.setEnabled(not control_busy)
        self.btn_relay_connect.setEnabled((not control_busy) and (not self._relay_real.is_connected))
        self.btn_relay_disconnect.setEnabled((not control_busy) and self._relay_real.is_connected)
        self.btn_auto_connect.setEnabled(not control_busy)

        relay_ready = self._relay_real.is_connected and (not control_busy)
        self.btn_mode_on.setEnabled(relay_ready)
        self.btn_mode_off.setEnabled(relay_ready)
        self.btn_pairing_on.setEnabled(relay_ready)
        self.btn_pairing_off.setEnabled(relay_ready)
        self.btn_pairing_pulse.setEnabled(relay_ready)

        self.btn_start.setEnabled(not control_busy)
        self.btn_stop.setEnabled(busy)
        self.btn_bt_detect.setEnabled(not control_busy)
        self.btn_bt_check.setEnabled(not control_busy)
        self.btn_bt_remove.setEnabled(not control_busy)

    def _update_running_state(self) -> None:
        self._update_device_control_state()

    @staticmethod
    def _select_combo_value(combo: QComboBox, value: str) -> None:
        if not value:
            combo.setCurrentIndex(0)
            return
        idx = combo.findData(value)
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    @staticmethod
    def _ensure_combo_has_value(combo: QComboBox, value: str) -> None:
        if value and combo.findData(value) < 0:
            combo.addItem(value, value)

    def shutdown(self) -> None:
        self._save_current_settings()
        if self._worker:
            self._worker.stop()
        if self._thread:
            self._thread.quit()
            self._thread.wait(1500)
        if self._aux_task_thread:
            self._aux_task_thread.quit()
            self._aux_task_thread.wait(1500)
        self._relay_real.disconnect()
