from __future__ import annotations

from PySide6.QtWidgets import QTextBrowser, QVBoxLayout, QWidget


class HelpTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setMarkdown(
            """
# 使用说明

1. 在“设备管理”页设置蓝牙目标，并连接万用表、继电器；启用仿真后无需连接真实串口。
2. 在“设置”页勾选测试项目，选择执行模式，并配置各测试项参数。
3. 回到“主界面”点击“开始测试”，主界面会统一显示各测试项的 PASS、FAIL、SKIP、次数和耗时。
4. “顺序执行”模式会先完成一个测试项的全部次数，再进入下一个测试项。
5. “按轮数执行”模式会按勾选项顺序每项执行一次，重复 N 轮；此时各测试项自己的测试次数无效。

## 设备管理

- 蓝牙目标管理用于设置名称关键字、MAC 和匹配模式，所有测试项共用同一目标。
- 万用表控制区用于上下电测试的电压采样。
- 继电器控制区用于上下电、蓝牙连接测试和手动 8 路调试。
- 连接好的设备由主界面测试计划复用，不需要在每个测试项里重复连接。

## 蓝牙开关测试

- “系统适配器开关”会直接控制 Windows 蓝牙枚举器设备。
- “系统 UI 切换”会打开系统蓝牙设置界面并通过键盘模拟切换蓝牙开关。
- 两种方式在“设置”页的测试计划中单选一种。

## 休眠唤醒测试

- 当前版本只保留占位。
- 勾选后主界面会显示 SKIP，不计入 PASS/FAIL。

## 常见问题

- 连接失败：确认串口未被其他程序占用。
- 蓝牙检测为空：确认目标设备已配对，或处于可被系统发现的状态。
- 删除已配对设备失败：确认当前系统账号具备设备管理权限。
"""
        )
        layout.addWidget(browser)
