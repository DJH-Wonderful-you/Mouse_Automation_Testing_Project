from __future__ import annotations

from dataclasses import dataclass
from html import escape
import logging
from datetime import datetime
from typing import Callable

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
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
    QProgressBar,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QDoubleSpinBox,
)

from src.core.bluetooth_probe import BluetoothDeviceInfo, BluetoothProbe, normalize_mac
from src.core.config_store import ConfigStore
from src.core.multimeter_victor86e import Victor86EMultimeter
from src.core.relay_lcus88 import LCUSRelay
from src.core.serial_utils import list_serial_ports
from src.core.simulators import SimulatedBluetoothProbe, SimulatedMultimeter, SimulatedRelay
from src.core.test_engine import PowerCycleRunner, TestEngineWorker
from src.core.types import AppSettings, CycleResult, VerificationPolicy

_LOGGER = logging.getLogger("ui.power_cycle")


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
    source: str
    devices: list[BluetoothDeviceInfo]


@dataclass(slots=True)
class _BluetoothCheckResult:
    source: str
    mode_text: str
    criteria: list[str]
    connected: bool
    matched: list[BluetoothDeviceInfo]


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


class PowerCycleTab(QWidget):
    def __init__(self, config_store: ConfigStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config_store = config_store

        self._multimeter_real = Victor86EMultimeter()
        self._relay_real = LCUSRelay()
        self._bt_probe_real = BluetoothProbe()

        self._sim_relay = SimulatedRelay()
        self._sim_multimeter = SimulatedMultimeter(self._sim_relay)
        self._sim_bt_probe = SimulatedBluetoothProbe(self._sim_relay)

        self._thread: QThread | None = None
        self._worker: TestEngineWorker | None = None
        self._runner: PowerCycleRunner | None = None
        self._running = False
        self._success_count = 0
        self._fail_count = 0
        self._preferred_meter_port = ""
        self._preferred_relay_port = ""
        self._bt_inputs_locked_by_sim = False
        self._bt_name_before_sim = ""
        self._bt_mac_before_sim = ""
        self._suspend_auto_save = True
        self._bt_task_running = False
        self._bt_task_name = ""
        self._bt_task_thread: QThread | None = None
        self._bt_task_worker: _AsyncTaskWorker | None = None
        self._bt_task_success_handler: Callable[[object], None] | None = None

        self._build_ui()
        self._bind_auto_save_signals()
        self._load_settings_into_ui()
        self._refresh_serial_ports()
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
        left_layout.addWidget(self._create_multimeter_group())
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

        title = QLabel("上下电测试")
        title.setObjectName("PageTitle")
        subtitle = QLabel("执行上下电循环验证，支持设备自动连接、蓝牙目标定位、过程日志与统计分析。")
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        return card

    def _create_main_control_group(self) -> QGroupBox:
        group = QGroupBox("主控区")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 14, 12, 12)
        layout.setSpacing(8)

        self.input_test_count = NoWheelSpinBox()
        self.input_test_count.setRange(1, 1_000_000)
        self.input_test_count.setValue(100)

        self.input_state_timeout = NoWheelDoubleSpinBox()
        self.input_state_timeout.setRange(0.5, 120.0)
        self.input_state_timeout.setDecimals(3)
        self.input_state_timeout.setSingleStep(0.1)
        self.input_state_timeout.setSuffix(" s")
        self.input_state_timeout.setValue(5.0)

        self.input_sample_interval = NoWheelDoubleSpinBox()
        self.input_sample_interval.setRange(0.05, 10.0)
        self.input_sample_interval.setDecimals(3)
        self.input_sample_interval.setSingleStep(0.05)
        self.input_sample_interval.setSuffix(" s")
        self.input_sample_interval.setValue(0.2)

        self.input_consecutive_pass = NoWheelSpinBox()
        self.input_consecutive_pass.setRange(1, 20)
        self.input_consecutive_pass.setValue(2)

        self.check_sim_multimeter = QCheckBox("万用表仿真")
        self.check_sim_relay = QCheckBox("继电器仿真")
        self.check_sim_bluetooth = QCheckBox("蓝牙仿真")
        self.check_sim_multimeter.toggled.connect(self._on_simulation_options_changed)
        self.check_sim_relay.toggled.connect(self._on_simulation_options_changed)
        self.check_sim_bluetooth.toggled.connect(self._on_simulation_options_changed)

        self.btn_start = QPushButton("开始测试")
        self.btn_start.setObjectName("PrimaryButton")
        self.btn_start.clicked.connect(self._start_test)

        self.btn_stop = QPushButton("停止测试")
        self.btn_stop.setObjectName("DangerButton")
        self.btn_stop.clicked.connect(self._stop_test)
        self.btn_stop.setEnabled(False)

        self.btn_auto_connect = QPushButton("自动连接设备")
        self.btn_auto_connect.clicked.connect(self._auto_connect_devices)

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("测试次数："))
        top_row.addWidget(self.input_test_count)
        top_row.addStretch(1)

        sim_row = QHBoxLayout()
        sim_row.addWidget(QLabel("仿真开关："))
        sim_row.addWidget(self.check_sim_multimeter)
        sim_row.addWidget(self.check_sim_relay)
        sim_row.addWidget(self.check_sim_bluetooth)
        sim_row.addStretch(1)

        policy_grid = QGridLayout()
        policy_grid.addWidget(QLabel("状态超时："), 0, 0)
        policy_grid.addWidget(self.input_state_timeout, 0, 1)
        policy_grid.addWidget(QLabel("采样间隔："), 0, 2)
        policy_grid.addWidget(self.input_sample_interval, 0, 3)
        policy_grid.addWidget(QLabel("连续通过次数："), 1, 0)
        policy_grid.addWidget(self.input_consecutive_pass, 1, 1)
        policy_grid.setColumnStretch(4, 1)

        button_row = QHBoxLayout()
        button_row.addWidget(self.btn_auto_connect)
        button_row.addWidget(self.btn_start)
        button_row.addWidget(self.btn_stop)
        button_row.addStretch(1)

        layout.addLayout(top_row)
        layout.addLayout(sim_row)
        layout.addLayout(policy_grid)
        layout.addLayout(button_row)
        return group

    def _create_multimeter_group(self) -> QGroupBox:
        group = QGroupBox("万用表控制区")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 14, 12, 12)
        layout.setSpacing(8)

        self.input_voltage_threshold = NoWheelDoubleSpinBox()
        self.input_voltage_threshold.setRange(0.01, 1000.0)
        self.input_voltage_threshold.setDecimals(3)
        self.input_voltage_threshold.setValue(3.0)

        self.combo_multimeter_port = NoWheelComboBox()
        self.btn_refresh_ports = QPushButton("刷新串口")
        self.btn_refresh_ports.clicked.connect(self._refresh_serial_ports)
        self.btn_meter_connect = QPushButton("连接设备")
        self.btn_meter_connect.clicked.connect(self._connect_multimeter)
        self.btn_meter_disconnect = QPushButton("断开设备")
        self.btn_meter_disconnect.setObjectName("DangerButton")
        self.btn_meter_disconnect.clicked.connect(self._disconnect_multimeter)
        self.btn_meter_fetch = QPushButton("获取万用表数据")
        self.btn_meter_fetch.clicked.connect(self._read_multimeter_data)
        self.label_meter_status = QLabel("未连接")
        self.combo_multimeter_port.setMinimumWidth(240)

        row_threshold = QHBoxLayout()
        row_threshold.addWidget(QLabel("运行状态检测阈值（电压）："))
        row_threshold.addWidget(self.input_voltage_threshold)
        row_threshold.addWidget(QLabel("V"))
        row_threshold.addStretch(1)

        row_port = QHBoxLayout()
        row_port.addWidget(QLabel("万用表串口："))
        row_port.addWidget(self.combo_multimeter_port, 1)
        row_port.addWidget(self.btn_refresh_ports)

        row_actions = QHBoxLayout()
        row_actions.addWidget(self.btn_meter_connect)
        row_actions.addWidget(self.btn_meter_disconnect)
        row_actions.addWidget(self.btn_meter_fetch)
        row_actions.addStretch(1)

        row_status = QHBoxLayout()
        row_status.addWidget(QLabel("状态："))
        row_status.addWidget(self.label_meter_status, 1)

        layout.addLayout(row_threshold)
        layout.addLayout(row_port)
        layout.addLayout(row_actions)
        layout.addLayout(row_status)
        return group

    def _create_relay_group(self) -> QGroupBox:
        group = QGroupBox("继电器控制区")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 14, 12, 12)
        layout.setSpacing(8)

        self.input_interval = NoWheelDoubleSpinBox()
        self.input_interval.setRange(0.0, 3600.0)
        self.input_interval.setDecimals(3)
        self.input_interval.setSingleStep(0.1)
        self.input_interval.setValue(1.0)
        self.input_interval.setSuffix(" s")

        self.input_relay_channel = NoWheelSpinBox()
        self.input_relay_channel.setRange(1, 8)
        self.input_relay_channel.setValue(1)
        self.input_relay_channel.valueChanged.connect(self._sync_sim_target_channel)

        self.combo_relay_port = NoWheelComboBox()
        self.btn_refresh_relay_ports = QPushButton("刷新串口")
        self.btn_refresh_relay_ports.clicked.connect(self._refresh_serial_ports)
        self.btn_relay_connect = QPushButton("连接设备")
        self.btn_relay_connect.clicked.connect(self._connect_relay)
        self.btn_relay_disconnect = QPushButton("断开设备")
        self.btn_relay_disconnect.setObjectName("DangerButton")
        self.btn_relay_disconnect.clicked.connect(self._disconnect_relay)
        self.btn_relay_open_switch = QPushButton("打开端口开关")
        self.btn_relay_open_switch.clicked.connect(self._open_relay_port_switch)
        self.btn_relay_close_switch = QPushButton("关闭端口开关")
        self.btn_relay_close_switch.clicked.connect(self._close_relay_port_switch)
        self.label_relay_status = QLabel("未连接")
        self.combo_relay_port.setMinimumWidth(240)

        row_basic = QHBoxLayout()
        row_basic.addWidget(QLabel("上下电间隔时间："))
        row_basic.addWidget(self.input_interval)
        row_basic.addSpacing(10)
        row_basic.addWidget(QLabel("被控继电器端口："))
        row_basic.addWidget(self.input_relay_channel)
        row_basic.addStretch(1)

        row_port = QHBoxLayout()
        row_port.addWidget(QLabel("继电器串口："))
        row_port.addWidget(self.combo_relay_port, 1)
        row_port.addWidget(self.btn_refresh_relay_ports)
        row_actions = QHBoxLayout()
        row_actions.addWidget(self.btn_relay_connect)
        row_actions.addWidget(self.btn_relay_disconnect)
        row_actions.addWidget(self.btn_relay_open_switch)
        row_actions.addWidget(self.btn_relay_close_switch)
        row_actions.addStretch(1)

        row_status = QHBoxLayout()
        row_status.addWidget(QLabel("状态："))
        row_status.addWidget(self.label_relay_status, 1)

        layout.addLayout(row_basic)
        layout.addLayout(row_port)
        layout.addLayout(row_actions)
        layout.addLayout(row_status)
        return group

    def _create_bluetooth_group(self) -> QGroupBox:
        group = QGroupBox("蓝牙设备定位区")
        form = QFormLayout(group)
        form.setContentsMargins(12, 14, 12, 12)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.input_bt_name = QLineEdit()
        self.input_bt_name.setPlaceholderText("例如：MX Anywhere")

        self.input_bt_mac = QLineEdit()
        self.input_bt_mac.setPlaceholderText("例如：AA:BB:CC:11:22:33")

        self.combo_bt_mode = NoWheelComboBox()
        self.combo_bt_mode.addItem("名称或MAC（推荐）", "name_or_mac")
        self.combo_bt_mode.addItem("名称且MAC", "name_and_mac")

        button_row = QWidget()
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        self.btn_bt_detect = QPushButton("检测已配对蓝牙名称")
        self.btn_bt_detect.clicked.connect(self._detect_bluetooth_devices)
        self.btn_bt_check = QPushButton("检查连接状态")
        self.btn_bt_check.clicked.connect(self._check_bluetooth_connection)
        button_layout.addWidget(self.btn_bt_detect)
        button_layout.addWidget(self.btn_bt_check)
        button_layout.addStretch(1)

        form.addRow("蓝牙鼠标名称关键字：", self.input_bt_name)
        form.addRow("蓝牙鼠标MAC：", self.input_bt_mac)
        form.addRow("匹配模式：", self.combo_bt_mode)
        form.addRow("", button_row)
        return group

    def _create_progress_group(self) -> QGroupBox:
        group = QGroupBox("进度与统计")
        layout = QGridLayout(group)
        layout.setContentsMargins(12, 14, 12, 12)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(6)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

        self.label_done = QLabel("已完成: 0/0")
        self.label_success = QLabel("成功: 0")
        self.label_fail = QLabel("失败: 0")
        self.label_rate = QLabel("成功率: 0.00%")

        layout.addWidget(self.progress, 0, 0, 1, 4)
        layout.addWidget(self.label_done, 1, 0)
        layout.addWidget(self.label_success, 1, 1)
        layout.addWidget(self.label_fail, 1, 2)
        layout.addWidget(self.label_rate, 1, 3)
        return group

    def _create_log_group(self) -> QGroupBox:
        group = QGroupBox("运行日志")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 14, 12, 12)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("日志将在此显示运行情况与错误提示。")

        btn_clear = QPushButton("清空日志")
        btn_clear.clicked.connect(self.log_view.clear)

        layout.addWidget(self.log_view)
        layout.addWidget(btn_clear, alignment=Qt.AlignmentFlag.AlignRight)
        return group

    def _load_settings_into_ui(self) -> None:
        cfg = self._config_store.load()
        self.input_test_count.setValue(cfg.test_count)
        self.input_voltage_threshold.setValue(cfg.voltage_threshold_v)
        self.input_interval.setValue(cfg.interval_ms / 1000.0)
        self.input_relay_channel.setValue(cfg.relay_channel)
        self._preferred_meter_port = cfg.multimeter_port
        self._preferred_relay_port = cfg.relay_port
        self.input_bt_name.setText(cfg.bt_name_keyword)
        self.input_bt_mac.setText(cfg.bt_mac)
        for checkbox, value in (
            (self.check_sim_multimeter, cfg.simulation_multimeter),
            (self.check_sim_relay, cfg.simulation_relay),
            (self.check_sim_bluetooth, cfg.simulation_bluetooth),
        ):
            old = checkbox.blockSignals(True)
            checkbox.setChecked(value)
            checkbox.blockSignals(old)
        self.input_state_timeout.setValue(cfg.state_timeout_ms / 1000.0)
        self.input_sample_interval.setValue(cfg.sample_interval_ms / 1000.0)
        self.input_consecutive_pass.setValue(cfg.consecutive_pass_needed)

        mode_index = self.combo_bt_mode.findData(cfg.bt_match_mode)
        if mode_index >= 0:
            self.combo_bt_mode.setCurrentIndex(mode_index)
        self._on_simulation_options_changed()

    def _bind_auto_save_signals(self) -> None:
        self.input_test_count.valueChanged.connect(self._on_settings_changed_auto_save)
        self.input_state_timeout.valueChanged.connect(self._on_settings_changed_auto_save)
        self.input_sample_interval.valueChanged.connect(self._on_settings_changed_auto_save)
        self.input_consecutive_pass.valueChanged.connect(self._on_settings_changed_auto_save)
        self.input_voltage_threshold.valueChanged.connect(self._on_settings_changed_auto_save)
        self.input_interval.valueChanged.connect(self._on_settings_changed_auto_save)
        self.input_relay_channel.valueChanged.connect(self._on_settings_changed_auto_save)

        self.check_sim_multimeter.toggled.connect(self._on_settings_changed_auto_save)
        self.check_sim_relay.toggled.connect(self._on_settings_changed_auto_save)
        self.check_sim_bluetooth.toggled.connect(self._on_settings_changed_auto_save)

        self.combo_multimeter_port.currentIndexChanged.connect(self._on_settings_changed_auto_save)
        self.combo_relay_port.currentIndexChanged.connect(self._on_settings_changed_auto_save)
        self.combo_bt_mode.currentIndexChanged.connect(self._on_settings_changed_auto_save)

        self.input_bt_name.textChanged.connect(self._on_settings_changed_auto_save)
        self.input_bt_mac.textChanged.connect(self._on_settings_changed_auto_save)

    def _on_settings_changed_auto_save(self, *_: object) -> None:
        if self._suspend_auto_save:
            return
        self._save_current_settings(emit_log=False, show_error=False)

    def _collect_settings_from_ui(self) -> AppSettings:
        mode_data = self.combo_bt_mode.currentData()
        bt_match_mode = mode_data if mode_data in {"name_or_mac", "name_and_mac"} else "name_or_mac"
        normalized_mac = normalize_mac(self.input_bt_mac.text().strip())
        raw_mac = self.input_bt_mac.text().strip()
        if raw_mac and not normalized_mac:
            raise ValueError("蓝牙MAC格式无效，请输入12位十六进制地址。")

        return AppSettings(
            test_count=self.input_test_count.value(),
            voltage_threshold_v=self.input_voltage_threshold.value(),
            interval_ms=max(0, int(round(self.input_interval.value() * 1000))),
            relay_channel=self.input_relay_channel.value(),
            multimeter_port=self.combo_multimeter_port.currentData() or "",
            relay_port=self.combo_relay_port.currentData() or "",
            bt_name_keyword=self.input_bt_name.text().strip(),
            bt_mac=normalized_mac,
            bt_match_mode=bt_match_mode,  # type: ignore[arg-type]
            simulation_multimeter=self.check_sim_multimeter.isChecked(),
            simulation_relay=self.check_sim_relay.isChecked(),
            simulation_bluetooth=self.check_sim_bluetooth.isChecked(),
            simulation_mode=(
                self.check_sim_multimeter.isChecked()
                and self.check_sim_relay.isChecked()
                and self.check_sim_bluetooth.isChecked()
            ),
            state_timeout_ms=max(100, int(round(self.input_state_timeout.value() * 1000))),
            sample_interval_ms=max(10, int(round(self.input_sample_interval.value() * 1000))),
            consecutive_pass_needed=self.input_consecutive_pass.value(),
        )

    def _save_current_settings(self, *, emit_log: bool = True, show_error: bool = True) -> bool:
        try:
            cfg = self._collect_settings_from_ui()
            self._config_store.save(cfg)
        except ValueError as exc:
            if show_error:
                QMessageBox.warning(self, "参数错误", str(exc))
            return False
        self.input_bt_mac.setText(cfg.bt_mac)
        self._preferred_meter_port = cfg.multimeter_port
        self._preferred_relay_port = cfg.relay_port
        if emit_log:
            self._append_log("INFO", "配置已保存。")
        return True

    def _refresh_serial_ports(self) -> None:
        ports = list_serial_ports()
        current_meter = self.combo_multimeter_port.currentData() or self._preferred_meter_port
        current_relay = self.combo_relay_port.currentData() or self._preferred_relay_port

        self.combo_multimeter_port.clear()
        self.combo_relay_port.clear()
        self.combo_multimeter_port.addItem("请选择串口", "")
        self.combo_relay_port.addItem("请选择串口", "")

        for port in ports:
            self.combo_multimeter_port.addItem(port.label, port.device)
            self.combo_relay_port.addItem(port.label, port.device)

        self._select_combo_value(self.combo_multimeter_port, current_meter)
        self._select_combo_value(self.combo_relay_port, current_relay)
        self._append_log("INFO", f"串口刷新完成，共发现 {len(ports)} 个端口。")

    def _connect_multimeter(self) -> None:
        if self.check_sim_multimeter.isChecked():
            self._sim_multimeter.connect()
            self.label_meter_status.setText("仿真设备已就绪")
            self._append_log("INFO", "万用表仿真模式已开启，无需连接真实串口。")
            return
        port = self.combo_multimeter_port.currentData() or ""
        if not port:
            QMessageBox.warning(self, "提示", "请先选择万用表串口。")
            return
        if self._multimeter_real.connect(port):
            self.label_meter_status.setText(f"已连接({port})")
            self._append_log("INFO", f"万用表连接成功: {port}")
        else:
            self.label_meter_status.setText("连接失败")
            self._append_log("ERROR", f"万用表连接失败: {port}")

    def _disconnect_multimeter(self) -> None:
        if self.check_sim_multimeter.isChecked():
            self._sim_multimeter.disconnect()
            self.label_meter_status.setText("仿真模式")
            self._append_log("INFO", "万用表仿真设备已断开。")
            return
        self._multimeter_real.disconnect()
        self.label_meter_status.setText("未连接")
        self._append_log("INFO", "万用表已断开。")

    def _read_multimeter_data(self) -> None:
        meter_sim = self.check_sim_multimeter.isChecked()
        meter = self._sim_multimeter if meter_sim else self._multimeter_real
        source = "simulated multimeter" if meter_sim else "real multimeter"

        if meter_sim:
            if not self._sim_multimeter.is_connected:
                self._sim_multimeter.connect()
                self.label_meter_status.setText("Simulated device ready")
        elif not self._multimeter_real.is_connected:
            QMessageBox.warning(self, "Device not connected", "Multimeter is not connected.")
            return

        try:
            voltage = meter.read_voltage(attempts=3)
        except Exception as exc:  # noqa: BLE001
            self._append_log("ERROR", f"Failed to read multimeter data ({source}): {exc}")
            return

        if voltage is None:
            self._append_log("WARNING", f"No valid voltage from multimeter ({source}).")
            return

        self._append_log("INFO", f"Multimeter data ({source}): {voltage:.3f} V")

    def _connect_relay(self) -> None:
        if self.check_sim_relay.isChecked():
            self._sim_relay.connect()
            self.label_relay_status.setText("仿真设备已就绪")
            self._append_log("INFO", "继电器仿真模式已开启，无需连接真实串口。")
            return
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

    def _disconnect_relay(self) -> None:
        if self.check_sim_relay.isChecked():
            self._sim_relay.disconnect()
            self.label_relay_status.setText("仿真模式")
            self._append_log("INFO", "继电器仿真设备已断开。")
            return
        self._relay_real.disconnect()
        self.label_relay_status.setText("未连接")
        self._append_log("INFO", "继电器已断开。")

    def _open_relay_port_switch(self) -> None:
        self._set_relay_port_switch(True)

    def _close_relay_port_switch(self) -> None:
        self._set_relay_port_switch(False)

    def _set_relay_port_switch(self, on: bool) -> None:
        relay_sim = self.check_sim_relay.isChecked()
        relay = self._sim_relay if relay_sim else self._relay_real
        source = "simulated relay" if relay_sim else "real relay"
        channel = self.input_relay_channel.value()

        if relay_sim:
            if not self._sim_relay.is_connected:
                self._sim_relay.connect()
                self.label_relay_status.setText("Simulated device ready")
        elif not self._relay_real.is_connected:
            QMessageBox.warning(self, "Device not connected", "Relay is not connected.")
            return

        action_text = "open" if on else "close"
        try:
            relay.set_channel_state(channel, on)
        except Exception as exc:  # noqa: BLE001
            self._append_log("ERROR", f"Failed to {action_text} relay channel {channel} ({source}): {exc}")
            return

        self._append_log("INFO", f"Manual relay command sent: channel={channel}, state={action_text} ({source})")

    def _auto_connect_devices(self) -> None:
        use_sim_meter = self.check_sim_multimeter.isChecked()
        use_sim_relay = self.check_sim_relay.isChecked()

        if use_sim_meter:
            self._sim_multimeter.connect()
            self.label_meter_status.setText("仿真设备已就绪")
            self._append_log("INFO", "万用表已切换为仿真设备。")
        if use_sim_relay:
            self._sim_relay.connect()
            self.label_relay_status.setText("仿真设备已就绪")
            self._append_log("INFO", "继电器已切换为仿真设备。")

        if use_sim_meter and use_sim_relay:
            self._append_log("INFO", "自动连接完成：万用表/继电器均为仿真模式。")
            return

        ports = [port.device for port in list_serial_ports()]
        if not ports and (not use_sim_meter or not use_sim_relay):
            self._append_log("WARNING", "未发现可用串口，无法自动连接。")
            return

        found_meter = ""
        found_relay = ""

        self._append_log("INFO", f"开始自动识别真实串口设备，候选端口: {', '.join(ports)}")
        if not use_sim_meter:
            for port in ports:
                if self._multimeter_real.probe_device(port):
                    found_meter = port
                    break
            if found_meter:
                self._select_combo_value(self.combo_multimeter_port, found_meter)
                if self._multimeter_real.connect(found_meter):
                    self.label_meter_status.setText(f"已连接({found_meter})")
                    self._append_log("INFO", f"自动识别万用表成功: {found_meter}")
            else:
                self._append_log("WARNING", "自动识别万用表失败。")

        if not use_sim_relay:
            for port in ports:
                if port == found_meter:
                    continue
                if self._relay_real.probe_device(port):
                    found_relay = port
                    break
            if found_relay:
                self._select_combo_value(self.combo_relay_port, found_relay)
                if self._relay_real.connect(found_relay):
                    self.label_relay_status.setText(f"已连接({found_relay})")
                    self._append_log("INFO", f"自动识别继电器成功: {found_relay}")
            else:
                self._append_log("WARNING", "自动识别继电器失败。")

    def _detect_bluetooth_devices(self) -> None:
        probe = self._sim_bt_probe if self.check_sim_bluetooth.isChecked() else self._bt_probe_real
        source = "simulated bluetooth" if self.check_sim_bluetooth.isChecked() else "paired bluetooth"
        self._append_log("INFO", f"Detecting bluetooth devices ({source})...")

        def task() -> object:
            return _BluetoothDetectResult(source=source, devices=probe.query_devices())

        self._start_bt_task("检测已配对蓝牙名称", task, self._on_detect_bluetooth_devices_done)

    def _on_detect_bluetooth_devices_done(self, payload: object) -> None:
        if not isinstance(payload, _BluetoothDetectResult):
            self._append_log("ERROR", "Bluetooth detect result payload is invalid.")
            return

        devices = payload.devices
        if not devices:
            self._append_log("WARNING", "No bluetooth device information detected.")
            return

        self._append_log("INFO", f"Detected {len(devices)} bluetooth-related devices (source: {payload.source}):")
        for device in devices:
            self._append_log("INFO", f"  - {device.summary}")

    def _check_bluetooth_connection(self) -> None:
        mode_data = self.combo_bt_mode.currentData()
        bt_match_mode = mode_data if mode_data in {"name_or_mac", "name_and_mac"} else "name_or_mac"
        bt_name = self.input_bt_name.text().strip()
        raw_mac = self.input_bt_mac.text().strip()
        bt_mac = normalize_mac(raw_mac)
        if raw_mac and not bt_mac:
            QMessageBox.warning(self, "Invalid input", "Bluetooth MAC format is invalid.")
            return

        if not bt_name and not bt_mac:
            QMessageBox.warning(self, "Invalid input", "Please provide bluetooth name keyword or MAC.")
            return

        probe = self._sim_bt_probe if self.check_sim_bluetooth.isChecked() else self._bt_probe_real
        source = "simulated bluetooth" if self.check_sim_bluetooth.isChecked() else "paired bluetooth"
        mode_text = "name_and_mac" if bt_match_mode == "name_and_mac" else "name_or_mac"
        criteria: list[str] = []
        if bt_name:
            criteria.append(f"name={bt_name}")
        if bt_mac:
            criteria.append(f"mac={bt_mac}")
        self._append_log(
            "INFO",
            f"Checking bluetooth connection ({source} | mode: {mode_text} | criteria: {', '.join(criteria)})...",
        )

        def task() -> object:
            connected, matched = probe.is_target_connected(bt_name, bt_mac, bt_match_mode)
            return _BluetoothCheckResult(
                source=source,
                mode_text=mode_text,
                criteria=criteria,
                connected=connected,
                matched=matched,
            )

        self._start_bt_task("检查蓝牙连接状态", task, self._on_check_bluetooth_connection_done)

    def _on_check_bluetooth_connection_done(self, payload: object) -> None:
        if not isinstance(payload, _BluetoothCheckResult):
            self._append_log("ERROR", "Bluetooth check result payload is invalid.")
            return

        if payload.matched:
            self._append_log("INFO", f"Matched {len(payload.matched)} device(s):")
            for device in payload.matched:
                connected_hint = getattr(device, "connected", None)
                connected_text = "unknown" if connected_hint is None else ("connected" if connected_hint else "disconnected")
                self._append_log("INFO", f"  - {device.summary} | status={connected_text}")
        else:
            self._append_log("WARNING", "No target bluetooth device matched; please check name keyword/MAC.")

        self._append_log("INFO" if payload.connected else "WARNING", f"Bluetooth check result: {'connected' if payload.connected else 'disconnected'}")

    def _start_bt_task(
        self,
        task_name: str,
        task: Callable[[], object],
        on_success: Callable[[object], None],
    ) -> None:
        if self._bt_task_running:
            self._append_log("WARNING", f"{self._bt_task_name} is already running. Please wait.")
            return

        thread = QThread(self)
        worker = _AsyncTaskWorker(task)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.sig_success.connect(self._on_bt_task_success)
        worker.sig_error.connect(self._on_bt_task_error)
        worker.sig_finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_bt_task_thread_finished)

        self._bt_task_running = True
        self._bt_task_name = task_name
        self._bt_task_success_handler = on_success
        self._bt_task_thread = thread
        self._bt_task_worker = worker
        self._update_device_control_state()
        thread.start()

    @Slot(object)
    def _on_bt_task_success(self, payload: object) -> None:
        if self._bt_task_success_handler is None:
            return
        try:
            self._bt_task_success_handler(payload)
        except Exception as exc:  # noqa: BLE001
            self._append_log("ERROR", f"{self._bt_task_name} result processing failed: {exc}")

    @Slot(str)
    def _on_bt_task_error(self, message: str) -> None:
        task_name = self._bt_task_name or "bluetooth operation"
        self._append_log("ERROR", f"{task_name} failed: {message}")

    @Slot()
    def _on_bt_task_thread_finished(self) -> None:
        self._bt_task_running = False
        self._bt_task_name = ""
        self._bt_task_success_handler = None
        self._bt_task_thread = None
        self._bt_task_worker = None
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

        if not cfg.simulation_multimeter and not self._multimeter_real.is_connected:
            QMessageBox.warning(self, "设备未连接", "万用表未连接，请先连接后再开始测试。")
            return
        if not cfg.simulation_relay and not self._relay_real.is_connected:
            QMessageBox.warning(self, "设备未连接", "继电器未连接，请先连接后再开始测试。")
            return
        if not cfg.simulation_bluetooth and not cfg.bt_name_keyword and not cfg.bt_mac:
            QMessageBox.warning(self, "参数错误", "蓝牙为真实模式时，请至少填写蓝牙名称关键字或MAC。")
            return

        self._success_count = 0
        self._fail_count = 0
        self._update_stats(done=0, total=cfg.test_count)

        policy = VerificationPolicy(
            state_timeout_ms=cfg.state_timeout_ms,
            sample_interval_ms=cfg.sample_interval_ms,
            consecutive_pass_needed=cfg.consecutive_pass_needed,
        )

        relay, multimeter, bt_probe = self._build_active_devices(cfg)
        self._runner = None
        self._worker = None
        self._thread = None

        worker_box: dict[str, TestEngineWorker | None] = {"worker": None}
        runner = PowerCycleRunner(
            relay=relay,
            multimeter=multimeter,
            bluetooth=bt_probe,
            settings=cfg,
            policy=policy,
            log_cb=lambda level, message: self._emit_worker_signal(
                worker_box["worker"], "log", level, message
            ),
            progress_cb=lambda done, total: self._emit_worker_signal(
                worker_box["worker"], "progress", done, total
            ),
            cycle_cb=lambda result: self._emit_worker_signal(
                worker_box["worker"], "cycle", result
            ),
        )
        worker = TestEngineWorker(runner)
        worker_box["worker"] = worker
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

    def _build_active_devices(self, cfg: AppSettings) -> tuple[object, object, object]:
        relay = self._sim_relay if cfg.simulation_relay else self._relay_real
        multimeter = self._sim_multimeter if cfg.simulation_multimeter else self._multimeter_real
        bt_probe = self._sim_bt_probe if cfg.simulation_bluetooth else self._bt_probe_real

        if cfg.simulation_relay:
            self._sim_relay.connect()
        if cfg.simulation_multimeter:
            self._sim_multimeter.connect()
        if cfg.simulation_bluetooth:
            # no explicit connect operation, keep source wired by relay.
            pass

        self._sim_multimeter.set_target_channel(cfg.relay_channel)
        self._sim_bt_probe.set_target_channel(cfg.relay_channel)
        self._sim_multimeter.set_relay_source(relay)
        self._sim_bt_probe.set_relay_source(relay)
        return relay, multimeter, bt_probe

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
        log_level = getattr(logging, level_upper, logging.INFO)
        _LOGGER.log(log_level, message)

    @staticmethod
    def _log_level_color(level: str) -> str:
        if level in {"ERROR", "CRITICAL"}:
            return "#c62828"
        if level == "WARNING":
            return "#b26a00"
        if level == "DEBUG":
            return "#546e7a"
        return "#1f5e94"

    @Slot(int, int)
    def _on_progress(self, done: int, total: int) -> None:
        self._update_stats(done=done, total=total)

    @Slot(object)
    def _on_cycle_result(self, result: object) -> None:
        if not isinstance(result, CycleResult):
            return
        if result.success:
            self._success_count += 1
            self._append_log(
                "INFO",
                f"[第{result.index}轮] 通过 | 断电电压={result.voltage_off}, 上电电压={result.voltage_on}",
            )
        else:
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
        self.label_done.setText(f"已完成: {done}/{total}")
        self.label_success.setText(f"成功: {self._success_count}")
        self.label_fail.setText(f"失败: {self._fail_count}")
        rate = (self._success_count / done * 100.0) if done else 0.0
        self.label_rate.setText(f"成功率: {rate:.2f}%")

    def _on_simulation_options_changed(self, *_: object) -> None:
        meter_sim = self.check_sim_multimeter.isChecked()
        relay_sim = self.check_sim_relay.isChecked()
        bt_sim = self.check_sim_bluetooth.isChecked()

        if meter_sim:
            self.label_meter_status.setText("仿真模式")
        else:
            meter_text = (
                f"已连接({self.combo_multimeter_port.currentData()})"
                if self._multimeter_real.is_connected
                else "未连接"
            )
            self.label_meter_status.setText(meter_text)

        if relay_sim:
            self.label_relay_status.setText("仿真模式")
        else:
            relay_text = (
                f"已连接({self.combo_relay_port.currentData()})"
                if self._relay_real.is_connected
                else "未连接"
            )
            self.label_relay_status.setText(relay_text)

        self._sync_bt_inputs_for_simulation(bt_sim)

        self._append_log(
            "INFO",
            "仿真设置更新：万用表=%s, 继电器=%s, 蓝牙=%s"
            % (
                "开" if meter_sim else "关",
                "开" if relay_sim else "关",
                "开" if bt_sim else "关",
            ),
        )
        self._update_device_control_state()

    def _sync_bt_inputs_for_simulation(self, bt_sim: bool) -> None:
        if bt_sim:
            if not self._bt_inputs_locked_by_sim:
                self._bt_name_before_sim = self.input_bt_name.text().strip()
                self._bt_mac_before_sim = self.input_bt_mac.text().strip()
            sim_name, sim_mac = self._get_simulated_bt_identity()
            self._set_bt_inputs(sim_name, sim_mac)
            self.input_bt_name.setReadOnly(True)
            self.input_bt_mac.setReadOnly(True)
            self._bt_inputs_locked_by_sim = True
            return

        self.input_bt_name.setReadOnly(False)
        self.input_bt_mac.setReadOnly(False)
        if not self._bt_inputs_locked_by_sim:
            return
        self._set_bt_inputs(self._bt_name_before_sim, self._bt_mac_before_sim)
        self._bt_inputs_locked_by_sim = False

    def _get_simulated_bt_identity(self) -> tuple[str, str]:
        devices = self._sim_bt_probe.query_devices()
        if not devices:
            return "SimMouse", "00:11:22:AA:BB:CC"
        device = devices[0]
        name = (device.name or "SimMouse").strip() or "SimMouse"
        mac = normalize_mac(device.mac) or "00:11:22:AA:BB:CC"
        return name, mac

    def _set_bt_inputs(self, name: str, mac: str) -> None:
        if self.input_bt_name.text() != name:
            self.input_bt_name.setText(name)
        if self.input_bt_mac.text() != mac:
            self.input_bt_mac.setText(mac)

    def _sync_sim_target_channel(self, channel: int) -> None:
        self._sim_multimeter.set_target_channel(channel)
        self._sim_bt_probe.set_target_channel(channel)

    def _update_device_control_state(self) -> None:
        busy = self._running
        control_busy = self._running or self._bt_task_running
        meter_real_mode = not self.check_sim_multimeter.isChecked()
        relay_real_mode = not self.check_sim_relay.isChecked()

        self.combo_multimeter_port.setEnabled((not control_busy) and meter_real_mode)
        self.btn_meter_connect.setEnabled((not control_busy) and meter_real_mode)
        self.btn_meter_disconnect.setEnabled((not control_busy) and meter_real_mode)
        self.btn_meter_fetch.setEnabled(not control_busy)

        self.combo_relay_port.setEnabled((not control_busy) and relay_real_mode)
        self.btn_relay_connect.setEnabled((not control_busy) and relay_real_mode)
        self.btn_relay_disconnect.setEnabled((not control_busy) and relay_real_mode)
        self.btn_relay_open_switch.setEnabled(not control_busy)
        self.btn_relay_close_switch.setEnabled(not control_busy)

        allow_refresh = (not control_busy) and (meter_real_mode or relay_real_mode)
        self.btn_refresh_ports.setEnabled(allow_refresh)
        self.btn_refresh_relay_ports.setEnabled(allow_refresh)
        self.btn_auto_connect.setEnabled(not control_busy)

        self.btn_start.setEnabled(not control_busy)
        self.btn_stop.setEnabled(busy)
        self.check_sim_multimeter.setEnabled(not control_busy)
        self.check_sim_relay.setEnabled(not control_busy)
        self.check_sim_bluetooth.setEnabled(not control_busy)
        self.btn_bt_detect.setEnabled(not control_busy)
        self.btn_bt_check.setEnabled(not control_busy)

    def _update_running_state(self) -> None:
        self._update_device_control_state()

    @staticmethod
    def _select_combo_value(combo: QComboBox, value: str) -> None:
        if not value:
            combo.setCurrentIndex(0)
            return
        idx = combo.findData(value)
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    def shutdown(self) -> None:
        self._save_current_settings()
        if self._worker:
            self._worker.stop()
        if self._thread:
            self._thread.quit()
            self._thread.wait(1500)
        if self._bt_task_thread:
            self._bt_task_thread.quit()
            self._bt_task_thread.wait(1500)
        self._multimeter_real.disconnect()
        self._relay_real.disconnect()
        self._sim_multimeter.disconnect()
        self._sim_relay.disconnect()
