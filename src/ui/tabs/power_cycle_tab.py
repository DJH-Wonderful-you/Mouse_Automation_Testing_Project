from __future__ import annotations

import logging
from datetime import datetime

from PySide6.QtCore import QThread, Qt, Slot
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

from src.core.bluetooth_probe import BluetoothProbe, normalize_mac
from src.core.config_store import ConfigStore
from src.core.multimeter_victor86e import Victor86EMultimeter
from src.core.relay_lcus88 import LCUSRelay
from src.core.serial_utils import list_serial_ports
from src.core.simulators import SimulatedBluetoothProbe, SimulatedMultimeter, SimulatedRelay
from src.core.test_engine import PowerCycleRunner, TestEngineWorker
from src.core.types import AppSettings, CycleResult, VerificationPolicy

_LOGGER = logging.getLogger("ui.power_cycle")


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

        self._build_ui()
        self._load_settings_into_ui()
        self._refresh_serial_ports()
        self._update_device_control_state()

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

    def _create_main_control_group(self) -> QGroupBox:
        group = QGroupBox("主控区")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 14, 12, 12)
        layout.setSpacing(8)

        self.input_test_count = QSpinBox()
        self.input_test_count.setRange(1, 1_000_000)
        self.input_test_count.setValue(100)

        self.input_state_timeout = QSpinBox()
        self.input_state_timeout.setRange(500, 120_000)
        self.input_state_timeout.setSuffix(" ms")
        self.input_state_timeout.setValue(5000)

        self.input_sample_interval = QSpinBox()
        self.input_sample_interval.setRange(50, 10_000)
        self.input_sample_interval.setSuffix(" ms")
        self.input_sample_interval.setValue(200)

        self.input_consecutive_pass = QSpinBox()
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

        self.btn_apply_save = QPushButton("应用并保存")
        self.btn_apply_save.clicked.connect(self._save_current_settings)

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
        button_row.addWidget(self.btn_apply_save)
        button_row.addStretch(1)
        button_row.addWidget(self.btn_start)
        button_row.addWidget(self.btn_stop)

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

        self.input_voltage_threshold = QDoubleSpinBox()
        self.input_voltage_threshold.setRange(0.01, 1000.0)
        self.input_voltage_threshold.setDecimals(3)
        self.input_voltage_threshold.setValue(3.0)

        self.combo_multimeter_port = QComboBox()
        self.btn_refresh_ports = QPushButton("刷新串口")
        self.btn_refresh_ports.clicked.connect(self._refresh_serial_ports)
        self.btn_meter_connect = QPushButton("连接设备")
        self.btn_meter_connect.clicked.connect(self._connect_multimeter)
        self.btn_meter_disconnect = QPushButton("断开设备")
        self.btn_meter_disconnect.setObjectName("DangerButton")
        self.btn_meter_disconnect.clicked.connect(self._disconnect_multimeter)
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

        self.input_interval = QSpinBox()
        self.input_interval.setRange(0, 3600_000)
        self.input_interval.setValue(1000)
        self.input_interval.setSuffix(" ms")

        self.input_relay_channel = QSpinBox()
        self.input_relay_channel.setRange(1, 8)
        self.input_relay_channel.setValue(1)
        self.input_relay_channel.valueChanged.connect(self._sync_sim_target_channel)

        self.combo_relay_port = QComboBox()
        self.btn_relay_connect = QPushButton("连接设备")
        self.btn_relay_connect.clicked.connect(self._connect_relay)
        self.btn_relay_disconnect = QPushButton("断开设备")
        self.btn_relay_disconnect.setObjectName("DangerButton")
        self.btn_relay_disconnect.clicked.connect(self._disconnect_relay)
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
        row_actions = QHBoxLayout()
        row_actions.addWidget(self.btn_relay_connect)
        row_actions.addWidget(self.btn_relay_disconnect)
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

        self.combo_bt_mode = QComboBox()
        self.combo_bt_mode.addItem("名称或MAC（推荐）", "name_or_mac")
        self.combo_bt_mode.addItem("名称且MAC", "name_and_mac")

        button_row = QWidget()
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        self.btn_bt_detect = QPushButton("自动检测蓝牙设备")
        self.btn_bt_detect.clicked.connect(self._detect_bluetooth_devices)
        button_layout.addWidget(self.btn_bt_detect)
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
        self.input_interval.setValue(cfg.interval_ms)
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
        self.input_state_timeout.setValue(cfg.state_timeout_ms)
        self.input_sample_interval.setValue(cfg.sample_interval_ms)
        self.input_consecutive_pass.setValue(cfg.consecutive_pass_needed)

        mode_index = self.combo_bt_mode.findData(cfg.bt_match_mode)
        if mode_index >= 0:
            self.combo_bt_mode.setCurrentIndex(mode_index)
        self._on_simulation_options_changed()

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
            interval_ms=self.input_interval.value(),
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
            state_timeout_ms=self.input_state_timeout.value(),
            sample_interval_ms=self.input_sample_interval.value(),
            consecutive_pass_needed=self.input_consecutive_pass.value(),
        )

    def _save_current_settings(self) -> bool:
        try:
            cfg = self._collect_settings_from_ui()
            self._config_store.save(cfg)
        except ValueError as exc:
            QMessageBox.warning(self, "参数错误", str(exc))
            return False
        self.input_bt_mac.setText(cfg.bt_mac)
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
        devices = probe.query_devices()
        if not devices:
            self._append_log("WARNING", "未检测到蓝牙设备信息。")
            return

        source = "仿真蓝牙" if self.check_sim_bluetooth.isChecked() else "真实已配对蓝牙"
        self._append_log("INFO", f"检测到 {len(devices)} 个蓝牙相关设备（来源: {source}）：")
        for device in devices:
            self._append_log("INFO", f"  - {device.summary}")

        if not self.input_bt_name.text().strip():
            self.input_bt_name.setText(devices[0].name)
        if not self.input_bt_mac.text().strip() and devices[0].mac:
            self.input_bt_mac.setText(devices[0].mac)

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
        line = f"[{ts}] [{level}] {message}"
        self.log_view.append(line)
        log_level = getattr(logging, level.upper(), logging.INFO)
        _LOGGER.log(log_level, message)

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

    def _sync_sim_target_channel(self, channel: int) -> None:
        self._sim_multimeter.set_target_channel(channel)
        self._sim_bt_probe.set_target_channel(channel)

    def _update_device_control_state(self) -> None:
        busy = self._running
        meter_real_mode = not self.check_sim_multimeter.isChecked()
        relay_real_mode = not self.check_sim_relay.isChecked()

        self.combo_multimeter_port.setEnabled((not busy) and meter_real_mode)
        self.btn_meter_connect.setEnabled((not busy) and meter_real_mode)
        self.btn_meter_disconnect.setEnabled((not busy) and meter_real_mode)

        self.combo_relay_port.setEnabled((not busy) and relay_real_mode)
        self.btn_relay_connect.setEnabled((not busy) and relay_real_mode)
        self.btn_relay_disconnect.setEnabled((not busy) and relay_real_mode)

        self.btn_refresh_ports.setEnabled((not busy) and (meter_real_mode or relay_real_mode))
        self.btn_auto_connect.setEnabled(not busy)

        self.btn_start.setEnabled(not busy)
        self.btn_stop.setEnabled(busy)
        self.check_sim_multimeter.setEnabled(not busy)
        self.check_sim_relay.setEnabled(not busy)
        self.check_sim_bluetooth.setEnabled(not busy)
        self.btn_apply_save.setEnabled(not busy)
        self.btn_bt_detect.setEnabled(not busy)

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
        self._multimeter_real.disconnect()
        self._relay_real.disconnect()
        self._sim_multimeter.disconnect()
        self._sim_relay.disconnect()
