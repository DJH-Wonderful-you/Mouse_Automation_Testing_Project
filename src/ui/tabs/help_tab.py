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

1. 在“设置”页先配置上下电测试的测试次数、判定阈值和继电器执行参数。
2. 回到“上下电测试”页连接或自动连接万用表与继电器。
3. 填写蓝牙鼠标定位信息（名称关键字和/或 MAC）。
4. 参数修改会自动保存，确认设备状态后即可开始测试。
5. 日志区会显示每轮判定结果与最终成功率。

## 仿真模式

- 万用表、继电器、蓝牙支持独立仿真开关，可按需混合调试。
- 仿真蓝牙会显示默认设备 `SimMouse`，其连接状态可跟随被控继电器通道状态。
- 例如：仅关闭“蓝牙仿真”，即可用真实蓝牙设备与仿真/真实电源链路联合调试。

## 常见问题

- 连接失败：请确认串口未被其他程序占用。
- 蓝牙检测为空：请确认鼠标已在系统中完成配对。
- 若日志提示串口断连，请重新连接后再开始测试。
"""
        )
        layout.addWidget(browser)
