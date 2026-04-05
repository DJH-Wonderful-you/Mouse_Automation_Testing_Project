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

1. 在“设置”页先配置对应测试项的参数。
2. “上下电测试”需要连接万用表与继电器，并填写蓝牙目标信息。
3. “蓝牙连接测试”需要连接继电器，并填写蓝牙名称关键字；MAC 可作为辅助匹配条件。
4. 参数修改会自动保存，确认设备状态后即可开始测试。
5. 日志区会显示每轮判定结果与最终成功率。

## 蓝牙连接测试说明

- 蓝牙模式通道会在测试期间保持打开，用于将鼠标切到蓝牙模式。
- 配对按键通道会按设置页中的“配对按压时长”自动脉冲触发。
- 当前版本优先复用系统蓝牙状态探测能力；自动配对通过 Windows 蓝牙设置页进行界面自动化操作。
- 若只填写 MAC 而未填写名称关键字，可用于已配对检测和连接检查，但无法进行稳定的自动配对。

## 仿真模式

- 上下电测试保留万用表、继电器、蓝牙独立仿真开关，可按需混合调试。
- 仿真蓝牙会显示默认设备 `SimMouse`，其连接状态可跟随被控继电器通道状态。

## 常见问题

- 连接失败：请确认串口未被其他程序占用。
- 蓝牙检测为空：请确认鼠标已在系统中完成过配对，或已经进入配对广播状态。
- 如果删除已配对设备失败，请确认当前系统账号具备设备管理权限。
"""
        )
        layout.addWidget(browser)
