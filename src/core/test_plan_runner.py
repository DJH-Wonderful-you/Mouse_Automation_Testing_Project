from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

from PySide6.QtCore import QObject, Signal, Slot

from src.core.bluetooth_connect_engine import BluetoothConnectRunner
from src.core.bluetooth_switch_engine import BluetoothSwitchRunner
from src.core.bluetooth_ui_switch_runner import BluetoothUiSwitchRunner
from src.core.device_context import DeviceContext
from src.core.test_engine import PowerCycleRunner
from src.core.types import (
    TEST_ITEM_ORDER,
    AppSettings,
    BluetoothConnectCycleResult,
    BluetoothConnectSettings,
    BluetoothSwitchSettings,
    BluetoothTargetSettings,
    DeviceSettings,
    RunSummary,
    TestItemId,
    TestPlanSettings,
    VerificationPolicy,
)

_LOGGER = logging.getLogger("test_plan_runner")

ITEM_TITLES: dict[TestItemId, str] = {
    "power_cycle": "上下电测试",
    "bluetooth_connect": "蓝牙连接测试",
    "bluetooth_switch": "蓝牙开关测试",
    "sleep_wake": "休眠唤醒测试",
}


class Runnable(Protocol):
    def run(self) -> RunSummary: ...

    def stop(self) -> None: ...


RunnerFactory = Callable[
    [
        TestItemId,
        int,
        Callable[[str, str], None] | None,
        Callable[[int, int], None] | None,
        Callable[[object], None] | None,
    ],
    Runnable,
]


@dataclass(slots=True)
class TestItemStats:
    item_id: TestItemId
    title: str
    status: str = "pending"
    result: str = "-"
    test_count: int = 0
    success_count: int = 0
    fail_count: int = 0
    elapsed_seconds: float = 0.0
    message: str = ""


@dataclass(slots=True)
class SuiteProgress:
    done: int
    total: int
    label: str
    item_id: TestItemId | None = None


class _SimulatedBluetoothSwitchRunner:
    def __init__(
        self,
        total: int,
        log_cb: Callable[[str, str], None] | None = None,
        progress_cb: Callable[[int, int], None] | None = None,
        cycle_cb: Callable[[BluetoothConnectCycleResult], None] | None = None,
    ) -> None:
        self._total = max(0, total)
        self._log_cb = log_cb
        self._progress_cb = progress_cb
        self._cycle_cb = cycle_cb
        self._stop_flag = threading.Event()

    def stop(self) -> None:
        self._stop_flag.set()

    def run(self) -> RunSummary:
        success_count = 0
        fail_count = 0
        self._log("INFO", f"仿真蓝牙开关测试开始，总轮次: {self._total}")
        for index in range(1, self._total + 1):
            if self._stop_flag.is_set():
                break
            result = BluetoothConnectCycleResult(
                index=index,
                success=True,
                reason="仿真蓝牙开关测试通过",
            )
            success_count += 1
            if self._cycle_cb:
                self._cycle_cb(result)
            if self._progress_cb:
                self._progress_cb(success_count + fail_count, self._total)
        done = success_count + fail_count
        rate = (success_count / done * 100.0) if done else 0.0
        return RunSummary(success_count, fail_count, rate)

    def _log(self, level: str, message: str) -> None:
        if self._log_cb:
            self._log_cb(level, message)


class TestPlanRunner:
    def __init__(
        self,
        *,
        plan_settings: TestPlanSettings,
        device_settings: DeviceSettings,
        power_cycle_settings: AppSettings,
        bluetooth_connect_settings: BluetoothConnectSettings,
        bluetooth_switch_settings: BluetoothSwitchSettings,
        device_context: DeviceContext | None = None,
        runner_factory: RunnerFactory | None = None,
        log_root: str | Path = "logs",
        log_cb: Callable[[str, str], None] | None = None,
        progress_cb: Callable[[SuiteProgress], None] | None = None,
        item_cb: Callable[[TestItemStats], None] | None = None,
    ) -> None:
        self._plan_settings = plan_settings
        self._device_settings = device_settings
        self._power_cycle_settings = power_cycle_settings
        self._bluetooth_connect_settings = bluetooth_connect_settings
        self._bluetooth_switch_settings = bluetooth_switch_settings
        self._device_context = device_context
        self._runner_factory = runner_factory
        self._run_log = _TestRunLogWriter(
            plan_settings=plan_settings,
            log_root=Path(log_root),
        )
        self._log_cb = log_cb
        self._progress_cb = progress_cb
        self._item_cb = item_cb
        self._stop_flag = threading.Event()
        self._current_runner: Runnable | None = None
        self._stats: dict[TestItemId, TestItemStats] = {
            item_id: TestItemStats(item_id=item_id, title=ITEM_TITLES[item_id])
            for item_id in TEST_ITEM_ORDER
        }

    def stop(self) -> None:
        self._stop_flag.set()
        if self._current_runner is not None:
            self._current_runner.stop()

    def run(self) -> RunSummary:
        self._reset_stats()
        enabled_items = self._enabled_items()
        self._log("INFO", f"测试计划开始，启用项目: {self._format_item_names(enabled_items)}")
        try:
            if not enabled_items:
                self._log("WARNING", "未勾选任何测试项，测试计划结束。")
                self._emit_progress(SuiteProgress(0, 0, "未勾选测试项"))
                return RunSummary(0, 0, 0.0)

            if self._plan_settings.mode == "round_robin":
                self._run_round_robin(enabled_items)
            else:
                self._run_sequential(enabled_items)

            success_count = sum(item.success_count for item in self._stats.values())
            fail_count = sum(item.fail_count for item in self._stats.values())
            done = success_count + fail_count
            rate = (success_count / done * 100.0) if done else 0.0
            self._log(
                "INFO",
                f"测试计划结束：成功 {success_count}，失败 {fail_count}，成功率 {rate:.2f}%",
            )
            return RunSummary(success_count, fail_count, rate)
        finally:
            self._run_log.close()

    def _reset_stats(self) -> None:
        enabled = set(self._plan_settings.enabled_items)
        for item_id in TEST_ITEM_ORDER:
            stat = self._stats[item_id]
            stat.status = "pending" if item_id in enabled else "idle"
            stat.result = "-" if item_id in enabled else "未选择"
            stat.test_count = 0
            stat.success_count = 0
            stat.fail_count = 0
            stat.elapsed_seconds = 0.0
            stat.message = ""
            self._emit_item(stat)

    def _run_sequential(self, enabled_items: list[TestItemId]) -> None:
        for item_id in enabled_items:
            if self._stop_flag.is_set():
                break
            if item_id == "sleep_wake":
                self._skip_item(item_id, "暂未开发，已跳过。")
                continue
            count = self._item_test_count(item_id)
            try:
                self._run_item(item_id, count, progress_total=count)
            except Exception as exc:  # noqa: BLE001
                self._log("ERROR", f"{ITEM_TITLES[item_id]}不可恢复错误，测试计划终止: {exc}")
                self._stop_flag.set()
                break

    def _run_round_robin(self, enabled_items: list[TestItemId]) -> None:
        executable_items = [item for item in enabled_items if item != "sleep_wake"]
        if "sleep_wake" in enabled_items:
            self._skip_item("sleep_wake", "暂未开发，已跳过。")

        total_rounds = max(1, self._plan_settings.round_count)
        self._emit_progress(SuiteProgress(0, total_rounds, f"轮次进度 0/{total_rounds}"))
        if not executable_items:
            self._emit_progress(
                SuiteProgress(total_rounds, total_rounds, f"轮次进度 {total_rounds}/{total_rounds}")
            )
            return

        for round_index in range(1, total_rounds + 1):
            if self._stop_flag.is_set():
                break
            self._log("INFO", f"开始第 {round_index}/{total_rounds} 轮测试。")
            for item_id in executable_items:
                if self._stop_flag.is_set():
                    break
                try:
                    self._run_item(
                        item_id,
                        1,
                        progress_total=total_rounds,
                        round_index=round_index,
                    )
                except Exception as exc:  # noqa: BLE001
                    self._log(
                        "ERROR",
                        f"{ITEM_TITLES[item_id]}不可恢复错误，测试计划终止: {exc}",
                    )
                    self._stop_flag.set()
                    break
            self._emit_progress(
                SuiteProgress(
                    min(round_index, total_rounds),
                    total_rounds,
                    f"轮次进度 {min(round_index, total_rounds)}/{total_rounds}",
                )
            )

    def _run_item(
        self,
        item_id: TestItemId,
        count: int,
        *,
        progress_total: int,
        round_index: int | None = None,
    ) -> None:
        count = max(1, count)
        stat = self._stats[item_id]
        stat.status = "running"
        stat.result = "运行中"
        stat.message = ""
        start = time.monotonic()
        before_test_count = stat.test_count
        self._run_log.begin_item(item_id)
        self._emit_item(stat)
        self._log("INFO", f"{ITEM_TITLES[item_id]}开始，执行次数: {count}")
        if self._plan_settings.mode == "sequential_items":
            self._emit_progress(
                SuiteProgress(0, count, f"{ITEM_TITLES[item_id]} 0/{count}", item_id)
            )

        def child_progress(done: int, total: int) -> None:
            if self._plan_settings.mode == "round_robin":
                done_rounds = max(0, (round_index or 1) - 1)
                self._emit_progress(
                    SuiteProgress(
                        done_rounds,
                        progress_total,
                        f"第 {round_index}/{progress_total} 轮：{ITEM_TITLES[item_id]}",
                        item_id,
                    )
                )
                return
            self._emit_progress(
                SuiteProgress(
                    done,
                    total,
                    f"{ITEM_TITLES[item_id]} {done}/{total}",
                    item_id,
                )
            )

        def child_cycle(result: object) -> None:
            success = bool(getattr(result, "success", False))
            stat.test_count += 1
            if success:
                stat.success_count += 1
            else:
                stat.fail_count += 1
            stat.elapsed_seconds = time.monotonic() - start
            stat.message = str(getattr(result, "reason", ""))
            self._emit_item(stat)

        try:
            runner = self._build_runner(
                item_id,
                count,
                self._log,
                child_progress,
                child_cycle,
            )
            self._current_runner = runner
            try:
                summary = runner.run()
            finally:
                self._current_runner = None
        except Exception as exc:
            stat.elapsed_seconds = time.monotonic() - start
            stat.status = "failed"
            stat.result = "FAIL"
            stat.test_count += 1
            stat.fail_count += 1
            stat.message = str(exc)
            self._emit_item(stat)
            raise

        self._sync_summary(stat, summary, count, before_test_count)
        stat.elapsed_seconds = time.monotonic() - start
        if self._stop_flag.is_set():
            stat.status = "stopped"
            stat.result = "已停止"
        elif stat.fail_count > 0:
            stat.status = "failed"
            stat.result = "FAIL"
        elif stat.success_count > 0:
            stat.status = "passed"
            stat.result = "PASS"
        else:
            stat.status = "failed"
            stat.result = "FAIL"
            stat.test_count += 1
            stat.fail_count += 1
            stat.message = "测试未产生结果。"
        self._emit_item(stat)
        self._run_log.end_item()

    def _sync_summary(
        self,
        stat: TestItemStats,
        summary: RunSummary,
        planned_count: int,
        before_test_count: int,
    ) -> None:
        summary_total = summary.success_count + summary.fail_count
        if summary_total <= 0:
            return
        if stat.test_count == before_test_count:
            stat.success_count += summary.success_count
            stat.fail_count += summary.fail_count
            stat.test_count += summary_total
        if summary_total < planned_count and self._stop_flag.is_set():
            stat.message = "用户停止测试。"

    def _skip_item(self, item_id: TestItemId, reason: str) -> None:
        self._run_log.begin_item(item_id)
        stat = self._stats[item_id]
        stat.status = "skipped"
        stat.result = "SKIP"
        stat.message = reason
        stat.elapsed_seconds = 0.0
        self._emit_item(stat)
        self._log("WARNING", f"{ITEM_TITLES[item_id]}: {reason}")
        self._run_log.end_item()

    def _build_runner(
        self,
        item_id: TestItemId,
        count: int,
        log_cb: Callable[[str, str], None] | None,
        progress_cb: Callable[[int, int], None] | None,
        cycle_cb: Callable[[object], None] | None,
    ) -> Runnable:
        if self._runner_factory is not None:
            return self._runner_factory(item_id, count, log_cb, progress_cb, cycle_cb)
        if self._device_context is None:
            raise RuntimeError("缺少共享设备上下文。")
        if item_id == "power_cycle":
            return self._build_power_cycle_runner(count, log_cb, progress_cb, cycle_cb)
        if item_id == "bluetooth_connect":
            return self._build_bluetooth_connect_runner(count, log_cb, progress_cb, cycle_cb)
        if item_id == "bluetooth_switch":
            return self._build_bluetooth_switch_runner(count, log_cb, progress_cb, cycle_cb)
        raise RuntimeError(f"测试项暂未实现: {item_id}")

    def _build_power_cycle_runner(
        self,
        count: int,
        log_cb: Callable[[str, str], None] | None,
        progress_cb: Callable[[int, int], None] | None,
        cycle_cb: Callable[[object], None] | None,
    ) -> PowerCycleRunner:
        assert self._device_context is not None
        self._require_relay()
        self._require_multimeter()
        self._require_target(require_name=False)
        target = self._effective_target()
        cfg = replace(
            self._power_cycle_settings,
            test_count=count,
            multimeter_port=self._device_settings.multimeter_port,
            relay_port=self._device_settings.relay_port,
            bt_name_keyword=target.bt_name_keyword,
            bt_mac=target.bt_mac,
            bt_match_mode=target.bt_match_mode,
            simulation_multimeter=self._device_settings.simulation_multimeter,
            simulation_relay=self._device_settings.simulation_relay,
            simulation_bluetooth=self._device_settings.simulation_bluetooth,
            simulation_mode=(
                self._device_settings.simulation_multimeter
                and self._device_settings.simulation_relay
                and self._device_settings.simulation_bluetooth
            ),
        )
        relay = self._device_context.active_relay(self._device_settings)
        self._device_context.prepare_power_cycle_simulation(cfg.relay_channel, relay)
        multimeter = self._device_context.active_multimeter(self._device_settings)
        bluetooth = self._device_context.active_bluetooth_probe(self._device_settings)
        policy = VerificationPolicy(
            state_timeout_ms=cfg.state_timeout_ms,
            sample_interval_ms=cfg.sample_interval_ms,
            consecutive_pass_needed=cfg.consecutive_pass_needed,
        )
        return PowerCycleRunner(
            relay=relay,
            multimeter=multimeter,
            bluetooth=bluetooth,
            settings=cfg,
            policy=policy,
            log_cb=log_cb,
            progress_cb=progress_cb,
            cycle_cb=cycle_cb,
        )

    def _build_bluetooth_connect_runner(
        self,
        count: int,
        log_cb: Callable[[str, str], None] | None,
        progress_cb: Callable[[int, int], None] | None,
        cycle_cb: Callable[[object], None] | None,
    ) -> BluetoothConnectRunner:
        assert self._device_context is not None
        self._require_relay()
        self._require_target(require_name=True)
        target = self._effective_target()
        cfg = replace(
            self._bluetooth_connect_settings,
            test_count=count,
            relay_port=self._device_settings.relay_port,
            simulation_relay=self._device_settings.simulation_relay,
            bt_name_keyword=target.bt_name_keyword,
            bt_mac=target.bt_mac,
            bt_match_mode=target.bt_match_mode,
        )
        relay = self._device_context.active_relay(self._device_settings)
        manager = self._device_context.active_bluetooth_manager(self._device_settings)
        return BluetoothConnectRunner(
            relay=relay,
            bluetooth=manager,
            settings=cfg,
            log_cb=log_cb,
            progress_cb=progress_cb,
            cycle_cb=cycle_cb,
        )

    def _build_bluetooth_switch_runner(
        self,
        count: int,
        log_cb: Callable[[str, str], None] | None,
        progress_cb: Callable[[int, int], None] | None,
        cycle_cb: Callable[[object], None] | None,
    ) -> Runnable:
        assert self._device_context is not None
        self._require_target(require_name=False)
        target = self._effective_target()
        cfg = replace(
            self._bluetooth_switch_settings,
            test_count=count,
            bt_name_keyword=target.bt_name_keyword,
            bt_mac=target.bt_mac,
            bt_match_mode=target.bt_match_mode,
        )
        if self._device_settings.simulation_bluetooth:
            return _SimulatedBluetoothSwitchRunner(
                count,
                log_cb=log_cb,
                progress_cb=progress_cb,
                cycle_cb=cycle_cb,
            )
        if self._plan_settings.bluetooth_switch_method == "ui":
            return BluetoothUiSwitchRunner(
                bluetooth=self._device_context.active_bluetooth_probe(self._device_settings),
                settings=cfg,
                log_cb=log_cb,
                progress_cb=progress_cb,
                cycle_cb=cycle_cb,
            )
        return BluetoothSwitchRunner(
            settings=cfg,
            log_cb=log_cb,
            progress_cb=progress_cb,
            cycle_cb=cycle_cb,
        )

    def _require_relay(self) -> None:
        if self._device_context and not self._device_context.relay_ready(self._device_settings):
            raise RuntimeError("继电器未连接，请先在设备管理页连接或启用继电器仿真。")

    def _require_multimeter(self) -> None:
        if self._device_context and not self._device_context.multimeter_ready(self._device_settings):
            raise RuntimeError("万用表未连接，请先在设备管理页连接或启用万用表仿真。")

    def _require_target(self, *, require_name: bool) -> None:
        if self._device_settings.simulation_bluetooth:
            return
        target = self._device_settings.bluetooth_target
        if require_name and not target.bt_name_keyword.strip():
            raise RuntimeError("蓝牙连接测试需要填写蓝牙名称关键字。")
        if not require_name and not target.bt_name_keyword.strip() and not target.bt_mac.strip():
            raise RuntimeError("请先在设备管理页填写蓝牙名称关键字或 MAC。")

    def _effective_target(self) -> BluetoothTargetSettings:
        target = self._device_settings.bluetooth_target
        if self._device_settings.simulation_bluetooth and not (
            target.bt_name_keyword.strip() or target.bt_mac.strip()
        ):
            return BluetoothTargetSettings(
                bt_name_keyword="SimMouse",
                bt_mac="00:11:22:AA:BB:CC",
                bt_match_mode=target.bt_match_mode,
            )
        return target

    def _item_test_count(self, item_id: TestItemId) -> int:
        if item_id == "power_cycle":
            return max(1, self._power_cycle_settings.test_count)
        if item_id == "bluetooth_connect":
            return max(1, self._bluetooth_connect_settings.test_count)
        if item_id == "bluetooth_switch":
            return max(1, self._bluetooth_switch_settings.test_count)
        return 1

    def _enabled_items(self) -> list[TestItemId]:
        enabled = set(self._plan_settings.enabled_items)
        return [item for item in TEST_ITEM_ORDER if item in enabled]

    def _emit_progress(self, progress: SuiteProgress) -> None:
        if self._progress_cb:
            self._progress_cb(progress)

    def _emit_item(self, stats: TestItemStats) -> None:
        if self._item_cb:
            self._item_cb(replace(stats))

    def _log(self, level: str, message: str) -> None:
        self._run_log.write(level, message)
        if self._log_cb:
            self._log_cb(level, message)
            return
        _LOGGER.log(getattr(logging, level.upper(), logging.INFO), message)

    def _format_item_names(self, item_ids: list[TestItemId]) -> str:
        return "、".join(ITEM_TITLES[item_id] for item_id in item_ids) or "无"


class TestPlanWorker(QObject):
    sig_log = Signal(str, str)
    sig_progress = Signal(object)
    sig_item = Signal(object)
    sig_finished = Signal(int, int, float)
    sig_error = Signal(str)

    def __init__(self, runner: TestPlanRunner) -> None:
        super().__init__()
        self._runner = runner

    @Slot()
    def run(self) -> None:
        try:
            summary = self._runner.run()
            self.sig_finished.emit(
                summary.success_count, summary.fail_count, summary.success_rate
            )
        except Exception as exc:  # noqa: BLE001
            self.sig_error.emit(str(exc))

    def stop(self) -> None:
        self._runner.stop()


class _TestRunLogWriter:
    def __init__(self, *, plan_settings: TestPlanSettings, log_root: Path) -> None:
        self._plan_settings = plan_settings
        self._log_root = log_root
        self._run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._active_file = None
        self._active_path: Path | None = None
        if plan_settings.mode == "round_robin":
            self._open(log_root / "round_robin" / f"{self._run_stamp}.log")

    def begin_item(self, item_id: TestItemId) -> None:
        if self._plan_settings.mode != "sequential_items":
            return
        self._open(
            self._log_root
            / "sequential_items"
            / item_id
            / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        )

    def end_item(self) -> None:
        if self._plan_settings.mode == "sequential_items":
            self.close()

    def write(self, level: str, message: str) -> None:
        if self._active_file is None:
            return
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._active_file.write(f"{timestamp} [{level.upper()}] {message}\n")
        self._active_file.flush()

    def close(self) -> None:
        if self._active_file is None:
            return
        self._active_file.close()
        self._active_file = None
        self._active_path = None

    def _open(self, path: Path) -> None:
        if self._active_path == path and self._active_file is not None:
            return
        self.close()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._active_path = path
        self._active_file = path.open("a", encoding="utf-8")
