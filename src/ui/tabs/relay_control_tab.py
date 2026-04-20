from __future__ import annotations

from datetime import datetime
from functools import partial
from html import escape
import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.core.config_store import ConfigStore
from src.core.relay_lcus88 import LCUSRelay, build_switch_command
from src.core.serial_utils import list_serial_ports

_LOGGER = logging.getLogger("ui.relay_control")


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()


class RelayControlTab(QWidget):
    def __init__(self, config_store: ConfigStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config_store = config_store
        self._relay = LCUSRelay()
        self._preferred_port = self._config_store.load_preferred_relay_port()
        self._channel_status_labels: dict[int, QLabel] = {}
        self._channel_action_buttons: list[QPushButton] = []
        self._channel_states: dict[int, bool | None] = {
            channel: None for channel in range(1, 9)
        }
        self._suspend_port_persist = False

        self._build_ui()
        self._refresh_serial_ports(emit_log=False)
        self._update_controls()

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
        left_layout.addWidget(self._create_device_group())
        left_layout.addWidget(self._create_channel_group())
        left_layout.addStretch(1)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setWidget(left_container)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self._create_log_group())
        right_widget.setMinimumWidth(340)

        splitter.addWidget(left_scroll)
        splitter.addWidget(right_widget)
        splitter.setSizes([820, 400])
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

    def _create_page_intro(self) -> QWidget:
        card = QWidget()
        card.setObjectName("PageIntroCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 10)
        layout.setSpacing(4)

        title = QLabel("继电器控制")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "用于手动控制 USB Relay (LCUS-8,8) 的 8 路开关，适合验证通道映射、检查串口连通性和独立调试上下电链路。"
        )
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        return card

    def _create_device_group(self) -> QGroupBox:
        group = QGroupBox("设备连接")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        self.combo_relay_port = NoWheelComboBox()
        self.combo_relay_port.setMinimumWidth(260)
        self.combo_relay_port.currentIndexChanged.connect(self._on_port_changed)

        self.btn_refresh_ports = QPushButton("刷新串口")
        self.btn_refresh_ports.clicked.connect(self._refresh_serial_ports)

        self.btn_connect = QPushButton("连接设备")
        self.btn_connect.clicked.connect(self._connect_relay)

        self.btn_disconnect = QPushButton("断开设备")
        self.btn_disconnect.setObjectName("DangerButton")
        self.btn_disconnect.clicked.connect(self._disconnect_relay)

        self.btn_refresh_states = QPushButton("刷新状态")
        self.btn_refresh_states.clicked.connect(self._refresh_channel_states)

        self.label_device_status = QLabel("未连接")

        row_port = QHBoxLayout()
        row_port.addWidget(QLabel("继电器串口："))
        row_port.addWidget(self.combo_relay_port, 1)
        row_port.addWidget(self.btn_refresh_ports)

        row_actions = QHBoxLayout()
        row_actions.addWidget(self.btn_connect)
        row_actions.addWidget(self.btn_disconnect)
        row_actions.addWidget(self.btn_refresh_states)
        row_actions.addStretch(1)
        row_actions.addWidget(QLabel("状态："))
        row_actions.addWidget(self.label_device_status)

        hint = QLabel(
            "连接成功后可直接控制 1-8 路开关。若设备不支持状态查询，页面会显示最近一次已下发的通道状态。"
        )
        hint.setWordWrap(True)

        layout.addLayout(row_port)
        layout.addLayout(row_actions)
        layout.addWidget(hint)
        return group

    def _create_channel_group(self) -> QGroupBox:
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
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

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
        self.log_view.setPlaceholderText("日志将在此显示继电器连接、状态刷新和手动控制结果。")

        btn_clear = QPushButton("清空日志")
        btn_clear.clicked.connect(self.log_view.clear)

        layout.addWidget(self.log_view)
        layout.addWidget(btn_clear, alignment=Qt.AlignmentFlag.AlignRight)
        return group

    def _refresh_serial_ports(self, *_: object, emit_log: bool = True) -> None:
        ports = list_serial_ports()
        current_port = self.combo_relay_port.currentData() or self._preferred_port

        self._suspend_port_persist = True
        self.combo_relay_port.clear()
        self.combo_relay_port.addItem("请选择串口", "")
        for port in ports:
            self.combo_relay_port.addItem(port.label, port.device)
        self._select_combo_value(self.combo_relay_port, str(current_port or ""))
        self._suspend_port_persist = False

        self._update_controls()
        if emit_log:
            self._append_log("INFO", f"串口刷新完成，共发现 {len(ports)} 个端口。")

    def _on_port_changed(self, *_: object) -> None:
        if self._suspend_port_persist:
            return
        selected_port = str(self.combo_relay_port.currentData() or "").strip()
        if selected_port:
            self._preferred_port = selected_port
            self._config_store.save_preferred_relay_port(selected_port)
        self._update_controls()

    def _connect_relay(self) -> None:
        port = str(self.combo_relay_port.currentData() or "").strip()
        if not port:
            QMessageBox.warning(self, "提示", "请先选择继电器串口。")
            return

        if self._relay.connect(port):
            self._preferred_port = port
            self._config_store.save_preferred_relay_port(port)
            self.label_device_status.setText(f"已连接({port})")
            self._append_log("INFO", f"继电器连接成功: {port}")
            self._refresh_channel_states(emit_log=False, emit_warning=True)
        else:
            self.label_device_status.setText("连接失败")
            self._append_log("ERROR", f"继电器连接失败: {port}")

        self._update_controls()

    def _disconnect_relay(self) -> None:
        self._relay.disconnect()
        self.label_device_status.setText("未连接")
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
        if not self._relay.is_connected:
            if emit_warning:
                QMessageBox.warning(self, "设备未连接", "继电器尚未连接。")
            return False

        try:
            states = self._relay.query_status()
        except Exception as exc:  # noqa: BLE001
            if emit_warning:
                self._append_log(
                    "WARNING",
                    f"继电器状态刷新失败: {exc}。若设备不支持状态查询，页面将继续显示最近一次已下发的状态。",
                )
            return False

        for channel, state in states.items():
            if 1 <= channel <= 8:
                self._set_channel_status(channel, state)

        if emit_log:
            refreshed_count = sum(1 for channel in range(1, 9) if channel in states)
            scope_text = "全部通道" if refreshed_count >= 8 else f"{refreshed_count} 个通道"
            self._append_log("INFO", f"已刷新 {scope_text} 的继电器状态。")
        return True

    def _set_channel_state(self, channel: int, on: bool) -> None:
        if not self._ensure_connected():
            return

        action_text = "打开" if on else "关闭"
        command_hex = self._command_hex(channel, on)
        try:
            self._relay.set_channel_state(channel, on)
        except Exception as exc:  # noqa: BLE001
            self._append_log("ERROR", f"第 {channel} 路{action_text}失败: {exc}")
            return

        self._set_channel_status(channel, on)
        self._append_log("INFO", f"第 {channel} 路已{action_text}，HEX: {command_hex}")

    def _set_all_channel_states(self, on: bool) -> None:
        if not self._ensure_connected():
            return

        action_text = "打开" if on else "关闭"
        success_count = 0
        for channel in range(1, 9):
            try:
                self._relay.set_channel_state(channel, on)
            except Exception as exc:  # noqa: BLE001
                self._append_log(
                    "ERROR",
                    f"批量{action_text}在第 {channel} 路中断: {exc}",
                )
                break
            self._set_channel_status(channel, on)
            success_count += 1

        if success_count == 8:
            self._append_log("INFO", f"已批量{action_text}全部 8 路继电器。")
            return

        if success_count > 0:
            self._append_log(
                "WARNING",
                f"批量{action_text}仅完成 {success_count}/8 路，请检查设备连接状态。",
            )

    def _ensure_connected(self) -> bool:
        if self._relay.is_connected:
            return True
        QMessageBox.warning(self, "设备未连接", "继电器尚未连接。")
        return False

    def _set_channel_status(self, channel: int, state: bool | None) -> None:
        self._channel_states[channel] = state
        label = self._channel_status_labels[channel]
        if state is None:
            label.setText("当前状态：未知")
            label.setStyleSheet("color: #6b8094; font-weight: 600;")
            return
        if state:
            label.setText("当前状态：已打开")
            label.setStyleSheet("color: #1e7a46; font-weight: 600;")
            return
        label.setText("当前状态：已关闭")
        label.setStyleSheet("color: #c24a32; font-weight: 600;")

    def _update_controls(self) -> None:
        connected = self._relay.is_connected
        has_port = bool(self.combo_relay_port.currentData())

        self.combo_relay_port.setEnabled(not connected)
        self.btn_refresh_ports.setEnabled(not connected)
        self.btn_connect.setEnabled((not connected) and has_port)
        self.btn_disconnect.setEnabled(connected)
        self.btn_refresh_states.setEnabled(connected)
        self.btn_open_all.setEnabled(connected)
        self.btn_close_all.setEnabled(connected)
        for button in self._channel_action_buttons:
            button.setEnabled(connected)

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

    def shutdown(self) -> None:
        self._relay.disconnect()
