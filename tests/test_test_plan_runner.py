from __future__ import annotations

import unittest
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.core.test_plan_runner import SuiteProgress, TestItemStats, TestPlanRunner
from src.core.types import (
    AppSettings,
    BluetoothConnectSettings,
    BluetoothSwitchSettings,
    DeviceSettings,
    RunSummary,
    TestItemId,
    TestPlanSettings,
)


@dataclass(slots=True)
class _FakeCycleResult:
    index: int
    success: bool
    reason: str


class _FakeRunner:
    def __init__(
        self,
        *,
        item_id: TestItemId,
        count: int,
        outcomes: list[bool],
        progress_cb: Callable[[int, int], None] | None,
        cycle_cb: Callable[[object], None] | None,
        stop_after_run: Callable[[], None] | None = None,
    ) -> None:
        self._item_id = item_id
        self._count = count
        self._outcomes = outcomes
        self._progress_cb = progress_cb
        self._cycle_cb = cycle_cb
        self._stop_after_run = stop_after_run
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True

    def run(self) -> RunSummary:
        success = 0
        fail = 0
        for index in range(1, self._count + 1):
            if self._stopped:
                break
            outcome = self._outcomes[(index - 1) % len(self._outcomes)]
            if outcome:
                success += 1
            else:
                fail += 1
            if self._cycle_cb:
                self._cycle_cb(
                    _FakeCycleResult(
                        index=index,
                        success=outcome,
                        reason=f"{self._item_id}:{index}",
                    )
                )
            if self._progress_cb:
                self._progress_cb(success + fail, self._count)
        if self._stop_after_run:
            self._stop_after_run()
        total = success + fail
        rate = (success / total * 100.0) if total else 0.0
        return RunSummary(success, fail, rate)


class TestTestPlanRunner(unittest.TestCase):
    def _build_runner(
        self,
        plan: TestPlanSettings,
        *,
        outcomes: dict[TestItemId, list[bool]] | None = None,
        stop_after_first: bool = False,
        log_root: str | Path = "logs",
    ):
        calls: list[tuple[TestItemId, int]] = []
        item_updates: list[TestItemStats] = []
        progress_updates: list[SuiteProgress] = []
        holder: dict[str, TestPlanRunner] = {}
        outcomes = outcomes or {}

        def factory(item_id, count, log_cb, progress_cb, cycle_cb):
            _ = log_cb
            calls.append((item_id, count))
            stop_cb = None
            if stop_after_first and len(calls) == 1:
                stop_cb = lambda: holder["runner"].stop()
            return _FakeRunner(
                item_id=item_id,
                count=count,
                outcomes=outcomes.get(item_id, [True]),
                progress_cb=progress_cb,
                cycle_cb=cycle_cb,
                stop_after_run=stop_cb,
            )

        runner = TestPlanRunner(
            plan_settings=plan,
            device_settings=DeviceSettings(),
            power_cycle_settings=AppSettings(test_count=2),
            bluetooth_connect_settings=BluetoothConnectSettings(test_count=4),
            bluetooth_switch_settings=BluetoothSwitchSettings(test_count=3),
            runner_factory=factory,
            log_root=log_root,
            item_cb=item_updates.append,
            progress_cb=progress_updates.append,
        )
        holder["runner"] = runner
        return runner, calls, item_updates, progress_updates

    def test_sequential_mode_uses_each_item_count_and_skips_sleep_wake(self) -> None:
        runner, calls, item_updates, _ = self._build_runner(
            TestPlanSettings(
                enabled_items=("power_cycle", "bluetooth_switch", "sleep_wake"),
                mode="sequential_items",
            )
        )

        summary = runner.run()

        self.assertEqual(calls, [("power_cycle", 2), ("bluetooth_switch", 3)])
        self.assertEqual(summary.success_count, 5)
        self.assertEqual(summary.fail_count, 0)
        sleep_updates = [item for item in item_updates if item.item_id == "sleep_wake"]
        self.assertEqual(sleep_updates[-1].result, "SKIP")
        self.assertEqual(sleep_updates[-1].test_count, 0)

    def test_round_robin_mode_runs_each_enabled_item_once_per_round(self) -> None:
        runner, calls, _, progress_updates = self._build_runner(
            TestPlanSettings(
                enabled_items=("power_cycle", "bluetooth_connect"),
                mode="round_robin",
                round_count=3,
            )
        )

        summary = runner.run()

        self.assertEqual(
            calls,
            [
                ("power_cycle", 1),
                ("bluetooth_connect", 1),
                ("power_cycle", 1),
                ("bluetooth_connect", 1),
                ("power_cycle", 1),
                ("bluetooth_connect", 1),
            ],
        )
        self.assertEqual(summary.success_count, 6)
        self.assertEqual(summary.fail_count, 0)
        self.assertEqual(progress_updates[-1].done, 3)
        self.assertEqual(progress_updates[-1].total, 3)

    def test_single_cycle_failure_continues_to_next_item(self) -> None:
        runner, calls, _, _ = self._build_runner(
            TestPlanSettings(
                enabled_items=("power_cycle", "bluetooth_connect"),
                mode="sequential_items",
            ),
            outcomes={"power_cycle": [False], "bluetooth_connect": [True]},
        )

        summary = runner.run()

        self.assertEqual(calls, [("power_cycle", 2), ("bluetooth_connect", 4)])
        self.assertEqual(summary.fail_count, 2)
        self.assertEqual(summary.success_count, 4)

    def test_stop_after_current_item_prevents_next_item(self) -> None:
        runner, calls, _, _ = self._build_runner(
            TestPlanSettings(
                enabled_items=("power_cycle", "bluetooth_connect"),
                mode="sequential_items",
            ),
            stop_after_first=True,
        )

        summary = runner.run()

        self.assertEqual(calls, [("power_cycle", 2)])
        self.assertEqual(summary.success_count, 2)
        self.assertEqual(summary.fail_count, 0)

    def test_sequential_mode_saves_logs_by_item_subfolder(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner, _, _, _ = self._build_runner(
                TestPlanSettings(
                    enabled_items=("power_cycle", "bluetooth_switch"),
                    mode="sequential_items",
                ),
                log_root=tmpdir,
            )

            runner.run()

            log_root = Path(tmpdir) / "sequential_items"
            power_logs = list((log_root / "power_cycle").glob("*.log"))
            switch_logs = list((log_root / "bluetooth_switch").glob("*.log"))
            self.assertEqual(len(power_logs), 1)
            self.assertEqual(len(switch_logs), 1)
            self.assertIn("上下电测试开始", power_logs[0].read_text(encoding="utf-8"))
            self.assertIn(
                "蓝牙开关测试开始", switch_logs[0].read_text(encoding="utf-8")
            )

    def test_round_robin_mode_saves_one_log_without_item_subfolders(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner, _, _, _ = self._build_runner(
                TestPlanSettings(
                    enabled_items=("power_cycle", "bluetooth_connect"),
                    mode="round_robin",
                    round_count=2,
                ),
                log_root=tmpdir,
            )

            runner.run()

            log_root = Path(tmpdir) / "round_robin"
            logs = list(log_root.glob("*.log"))
            self.assertEqual(len(logs), 1)
            self.assertFalse((log_root / "power_cycle").exists())
            text = logs[0].read_text(encoding="utf-8")
            self.assertIn("测试计划开始", text)
            self.assertIn("上下电测试开始", text)
            self.assertIn("蓝牙连接测试开始", text)


if __name__ == "__main__":
    unittest.main()
