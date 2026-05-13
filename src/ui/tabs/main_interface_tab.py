from __future__ import annotations

from datetime import datetime
from html import escape
import logging

from PySide6.QtCore import QThread, Qt, Slot
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.core.config_store import ConfigStore
from src.core.device_context import DeviceContext
from src.core.test_plan_runner import (
    ITEM_TITLES,
    SuiteProgress,
    TestItemStats,
    TestPlanRunner,
    TestPlanWorker,
)
from src.core.types import TEST_ITEM_ORDER, TestItemId

_LOGGER = logging.getLogger("ui.main_interface")


class MainInterfaceTab(QWidget):
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
        self._thread: QThread | None = None
        self._worker: TestPlanWorker | None = None
        self._runner: TestPlanRunner | None = None
        self._running = False
        self._row_by_item: dict[TestItemId, int] = {}
        self._latest_stats: dict[TestItemId, TestItemStats] = {}
        self._build_ui()
        self._reset_rows()

    def _build_ui(self) -> None:
        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(4, 4, 4, 4)
        root_layout.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        root_layout.addWidget(splitter)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        left_layout.addWidget(self._create_page_intro())
        left_layout.addWidget(self._create_control_group())
        left_layout.addWidget(self._create_status_group(), 1)
        left_layout.addWidget(self._create_progress_group())

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self._create_log_group())
        right.setMinimumWidth(360)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([820, 420])
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

    def _create_page_intro(self) -> QWidget:
        card = QWidget()
        card.setObjectName("PageIntroCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 10)
        layout.setSpacing(4)

        title = QLabel("主界面")
        title.setObjectName("PageTitle")
        subtitle = QLabel("按设置页的测试计划统一执行测试，并实时展示各测试项结果。")
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        return card

    def _create_control_group(self) -> QGroupBox:
        group = QGroupBox("主控区")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        self.btn_start = QPushButton("开始测试")
        self.btn_start.setObjectName("PrimaryButton")
        self.btn_start.clicked.connect(self._start_test)
        self.btn_stop = QPushButton("停止测试")
        self.btn_stop.setObjectName("DangerButton")
        self.btn_stop.clicked.connect(self._stop_test)
        self.btn_stop.setEnabled(False)
        self.label_plan_summary = QLabel("请在设置页选择测试计划，在设备管理页连接设备。")

        layout.addWidget(self.btn_start)
        layout.addWidget(self.btn_stop)
        layout.addWidget(self.label_plan_summary, 1)
        return group

    def _create_status_group(self) -> QGroupBox:
        group = QGroupBox("测试情况")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 10, 12, 12)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["测试项", "状态", "结果", "测试次数", "成功次数", "失败次数", "测试总时间"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.verticalHeader().setMinimumSectionSize(32)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(72)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 96), (2, 72), (3, 92), (4, 92), (5, 92), (6, 132)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table)
        return group

    def _create_progress_group(self) -> QGroupBox:
        group = QGroupBox("进度与统计")
        layout = QGridLayout(group)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(6)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
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
        self.log_view.setPlaceholderText("日志将在此显示统一测试计划的执行过程。")
        btn_clear = QPushButton("清空日志")
        btn_clear.clicked.connect(self.log_view.clear)
        layout.addWidget(self.log_view)
        layout.addWidget(btn_clear, alignment=Qt.AlignmentFlag.AlignRight)
        return group

    def _reset_rows(self) -> None:
        self.table.setRowCount(len(TEST_ITEM_ORDER))
        self._row_by_item.clear()
        self._latest_stats.clear()
        for row, item_id in enumerate(TEST_ITEM_ORDER):
            self._row_by_item[item_id] = row
            values = [ITEM_TITLES[item_id], "未开始", "-", "0", "0", "0", "00:00:00"]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, column, item)

    def _start_test(self) -> None:
        if self._running:
            return

        plan = self._config_store.load_test_plan()
        device_settings = self._config_store.load_device_settings()
        power_settings = self._config_store.load_power_cycle()
        connect_settings = self._config_store.load_bluetooth_connect()
        switch_settings = self._config_store.load_bluetooth_switch()

        self._reset_rows()
        self._update_stats(success=0, fail=0)
        self._update_plan_summary()

        runner = TestPlanRunner(
            plan_settings=plan,
            device_settings=device_settings,
            power_cycle_settings=power_settings,
            bluetooth_connect_settings=connect_settings,
            bluetooth_switch_settings=switch_settings,
            device_context=self._device_context,
            log_cb=lambda level, message: self._emit_worker_signal("log", level, message),
            progress_cb=lambda progress: self._emit_worker_signal("progress", progress),
            item_cb=lambda stats: self._emit_worker_signal("item", stats),
        )
        worker = TestPlanWorker(runner)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.sig_log.connect(self._append_log)
        worker.sig_progress.connect(self._on_progress)
        worker.sig_item.connect(self._on_item_update)
        worker.sig_finished.connect(self._on_finished)
        worker.sig_error.connect(self._on_error)
        worker.sig_finished.connect(self._cleanup_worker_thread)
        worker.sig_error.connect(self._cleanup_worker_thread)

        self._runner = runner
        self._worker = worker
        self._thread = thread
        self._running = True
        self._update_running_state()
        self._append_log("INFO", "统一测试计划线程启动。")
        thread.start()

    def _stop_test(self) -> None:
        if self._worker:
            self._worker.stop()
            self._append_log("WARNING", "已请求停止测试。")

    def _emit_worker_signal(self, kind: str, *args: object) -> None:
        if self._worker is None:
            return
        if kind == "log":
            self._worker.sig_log.emit(str(args[0]), str(args[1]))
        elif kind == "progress":
            self._worker.sig_progress.emit(args[0])
        elif kind == "item":
            self._worker.sig_item.emit(args[0])

    @Slot(object)
    def _on_progress(self, progress: object) -> None:
        if not isinstance(progress, SuiteProgress):
            return
        total = max(0, progress.total)
        done = max(0, min(progress.done, total)) if total else 0
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        self.label_done.setText(f"已完成：{done}/{total}")
        self.progress.setFormat(progress.label)

    @Slot(object)
    def _on_item_update(self, stats: object) -> None:
        if not isinstance(stats, TestItemStats):
            return
        row = self._row_by_item.get(stats.item_id)
        if row is None:
            return
        values = [
            stats.title,
            self._status_text(stats.status),
            stats.result,
            str(stats.test_count),
            str(stats.success_count),
            str(stats.fail_count),
            self._format_duration(stats.elapsed_seconds),
        ]
        for column, value in enumerate(values):
            item = self.table.item(row, column)
            if item is None:
                item = QTableWidgetItem()
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, column, item)
            item.setText(value)
        if stats.message:
            self.table.item(row, 1).setToolTip(stats.message)
            self.table.item(row, 2).setToolTip(stats.message)
        self._latest_stats[stats.item_id] = stats
        self._update_stats(
            success=sum(item.success_count for item in self._latest_stats.values()),
            fail=sum(item.fail_count for item in self._latest_stats.values()),
        )

    @Slot(int, int, float)
    def _on_finished(self, success_count: int, fail_count: int, success_rate: float) -> None:
        self._update_stats(success=success_count, fail=fail_count)
        self._append_log(
            "INFO",
            f"测试计划完成。成功 {success_count}，失败 {fail_count}，成功率 {success_rate:.2f}%",
        )

    @Slot(str)
    def _on_error(self, message: str) -> None:
        self._append_log("ERROR", f"测试计划异常: {message}")
        QMessageBox.critical(self, "测试计划异常", message)

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

    def _update_stats(self, *, success: int, fail: int) -> None:
        total = success + fail
        rate = (success / total * 100.0) if total else 0.0
        self.label_success.setText(f"成功：{success}")
        self.label_fail.setText(f"失败：{fail}")
        self.label_rate.setText(f"成功率：{rate:.2f}%")

    def _update_plan_summary(self) -> None:
        plan = self._config_store.load_test_plan()
        enabled = [ITEM_TITLES[item_id] for item_id in TEST_ITEM_ORDER if item_id in set(plan.enabled_items)]
        mode = "按轮数执行" if plan.mode == "round_robin" else "按测试项顺序执行"
        if plan.mode == "round_robin":
            mode = f"{mode}，{plan.round_count} 轮"
        switch_method = "系统 UI 切换" if plan.bluetooth_switch_method == "ui" else "系统适配器开关"
        self.label_plan_summary.setText(
            f"{mode} | 蓝牙开关：{switch_method} | 项目：{'、'.join(enabled) if enabled else '无'}"
        )

    def _update_running_state(self) -> None:
        self.btn_start.setEnabled(not self._running)
        self.btn_stop.setEnabled(self._running)

    @Slot(str, str)
    def _append_log(self, level: str, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        level_upper = level.upper()
        line = f"[{ts}] [{level_upper}] {message}"
        color = self._log_level_color(level_upper)
        self.log_view.append(f'<span style="color:{color}; white-space:pre;">{escape(line)}</span>')
        _LOGGER.log(getattr(logging, level_upper, logging.INFO), message)

    @staticmethod
    def _status_text(status: str) -> str:
        return {
            "idle": "未选择",
            "pending": "等待中",
            "running": "运行中",
            "passed": "已完成",
            "failed": "失败",
            "skipped": "已跳过",
            "stopped": "已停止",
        }.get(status, status)

    @staticmethod
    def _format_duration(seconds: float) -> str:
        total_seconds = max(0, int(round(seconds)))
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    @staticmethod
    def _log_level_color(level: str) -> str:
        if level in {"ERROR", "CRITICAL"}:
            return "#c62828"
        if level == "WARNING":
            return "#b26a00"
        if level in {"DEBUG", "TRACE"}:
            return "#546e7a"
        return "#1f5e94"

    def shutdown(self) -> None:
        if self._worker:
            self._worker.stop()
        if self._thread:
            self._thread.quit()
            self._thread.wait(1500)
