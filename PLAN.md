# 鼠标自动化测试工具（PySide）首版开发计划

## 概要
- 目标：按需求文档实现可运行的 Windows 桌面工具，核心完成“上下电测试”全流程，其他 4 个标签页先做占位与说明。
- 已锁定偏好：`源码可运行`、`蓝牙设备按名称+MAC组合定位`、`首版包含仿真模式`。
- 技术基线：Python 3.11 + PySide6 + pyserial，GUI 风格为简洁浅色、卡片式分区，视觉略优化但不花哨。

## 范围定义
- In Scope
- 上下电测试页完整功能（设备连接、自动连接、参数配置、测试循环、进度、日志、统计）。
- 万用表解析逻辑从 `victor_86e_parser.py` 重构到新模块（不直接修改原文件）。
- 继电器 LCUS-8,8 串口控制（按文档 HEX 指令）。
- 蓝牙设备自动检测与连接状态判断（Windows PowerShell + PnP 信息）。
- 配置持久化（下次启动自动恢复）。
- 仿真模式（无硬件可跑通流程）。
- Out Scope
- 蓝牙连接测试/蓝牙开关测试/休眠唤醒测试的业务实现（仅占位）。
- 安装包/EXE 打包（本次不做）。

## 项目结构与模块拆分
- `src/main.py`：应用入口、全局异常捕获、主窗口启动。
- `src/ui/main_window.py`：主界面、左侧标签页、窗口自适应尺寸逻辑。
- `src/ui/tabs/power_cycle_tab.py`：上下电测试页 UI 与交互绑定。
- `src/ui/tabs/placeholders.py`：其余 3 个测试页占位。
- `src/ui/tabs/help_tab.py`：帮助页（操作流程、常见问题）。
- `src/ui/styles.py`：QSS 主题与控件样式。
- `src/core/config_store.py`：QSettings 读写。
- `src/core/logger.py`：文件日志 + GUI 日志桥接。
- `src/core/serial_utils.py`：COM 枚举、端口刷新。
- `src/core/multimeter_victor86e.py`：万用表连接、14 字节解析、电压读取。
- `src/core/relay_lcus88.py`：继电器连接、通道开关、状态查询。
- `src/core/bluetooth_probe.py`：蓝牙鼠标检测与连接状态判断。
- `src/core/test_engine.py`：测试状态机（QThread Worker）。
- `src/core/simulators.py`：仿真万用表/继电器/蓝牙设备实现。
- `tests/`：单元测试与无硬件集成测试。
- `requirements.txt`：依赖声明。

## 公共接口/类型（对模块调用方稳定）
- `AppSettings`
- `test_count: int`
- `voltage_threshold_v: float`
- `interval_ms: int`
- `relay_channel: int`
- `multimeter_port: str`
- `relay_port: str`
- `bt_name_keyword: str`
- `bt_mac: str`
- `bt_match_mode: Literal["name_or_mac","name_and_mac"]`
- `simulation_mode: bool`
- `VerificationPolicy`
- `state_timeout_ms: int`
- `sample_interval_ms: int`
- `consecutive_pass_needed: int`
- `CycleResult`
- `index: int`
- `success: bool`
- `reason: str`
- `voltage_off: float | None`
- `voltage_on: float | None`
- `bt_off_connected: bool | None`
- `bt_on_connected: bool | None`
- `TestEngine` 信号
- `sig_log(level: str, message: str)`
- `sig_progress(done: int, total: int)`
- `sig_cycle_result(result: CycleResult)`
- `sig_finished(success_count: int, fail_count: int, success_rate: float)`

## UI 与美观规范
- 布局
- 主窗口默认 `1180x760`，左侧 `QTabWidget(West)` 五个标签。
- 上下电测试页采用三块卡片区：主控区、万用表区、继电器区；底部进度和统计；右侧日志。
- 风格
- 浅灰背景 + 白色卡片 + 蓝绿色强调色，圆角 8px，统一内边距与行间距。
- 按钮分级：主操作（开始）高亮，危险操作（停止/断开）警示色。
- 字体保持系统中文友好字体，字号层次明确。
- 自适应尺寸
- 启动时读取屏幕可用分辨率。
- 若屏幕大于默认尺寸：保持默认窗口大小。
- 若屏幕小于默认尺寸：按等比例缩放窗口，确保不超屏并居中。

## 核心流程（上下电测试）
- 连接前校验
- 校验测试次数、阈值、间隔、通道范围（1-8）、蓝牙定位条件格式。
- 非仿真模式下要求万用表与继电器都已连接。
- 设备连接
- 手动连接：用户从下拉框选 COM 后连接/断开。
- 自动连接：遍历 COM，先识别万用表（可解析 14 字节包），再识别继电器（FF 查询有有效响应）。
- 单轮测试状态机
1. 读取继电器目标通道状态。
2. 若当前为上电态，先执行“断电”命令。
3. 在 `state_timeout_ms` 内轮询万用表和蓝牙，验证断电条件：`voltage < threshold` 且 `bluetooth == disconnected`。
4. 等待 `interval_ms`。
5. 执行“上电”命令。
6. 在 `state_timeout_ms` 内轮询，验证上电条件：`voltage > threshold` 且 `bluetooth == connected`。
7. 记录本轮成功/失败与原因，更新进度与统计。
8. 循环至目标次数或收到停止指令。
- 结束输出
- 日志输出总次数、成功数、失败数、成功率。
- UI 保持结果可见，允许用户再次运行。

## 蓝牙定位与自动检测（名称+MAC组合）
- 自动检测按钮触发 `bluetooth_probe`：
- 调用 PowerShell 获取鼠标/蓝牙相关 PnP 设备列表。
- 日志展示候选项：名称、实例 ID、状态、提取到的 MAC（若存在）。
- 定位规则
- 支持两种匹配模式：`name_or_mac`（默认）与 `name_and_mac`。
- 名称为不区分大小写包含匹配，MAC 标准化后匹配（去分隔符）。
- 连接状态判断
- 匹配设备中存在状态 `OK` 且设备当前可见则视为已连接；否则视为断开。
- 未取到 MAC 时仅按名称匹配并给出提示日志。

## 配置持久化与默认值
- 持久化方式：`QSettings("RJHZ","MouseAutomationTool")`。
- 保存时机：点击“应用并保存”立即落盘；程序退出前再同步一次。
- 默认值
- `test_count=100`
- `voltage_threshold_v=3.0`
- `interval_ms=1000`
- `relay_channel=1`
- `bt_match_mode=name_or_mac`
- `simulation_mode=false`
- 启动时自动回填全部输入框与下拉选择。

## 日志、错误处理与可观测性
- 日志双通道：GUI 实时日志 + `logs/` 文件滚动日志。
- 关键日志点：连接/断开、每轮判定、串口异常、解析失败、蓝牙检测结果、任务结束统计。
- 错误策略
- 可恢复错误（单轮读值失败）仅判本轮失败并继续。
- 不可恢复错误（串口断连）立即停止测试并提示。
- 所有异常统一捕获并展示可读错误信息。

## 测试计划与验收场景
- 单元测试
- 万用表 14 字节解析：正常包、长度错误、未知功能码、OL 场景。
- 继电器命令编码：1-8 路开关指令和校验字节正确。
- 蓝牙匹配：名称匹配、MAC 匹配、组合匹配、空结果。
- 配置读写：保存后重启可恢复。
- 集成测试（仿真模式）
- 自动连接流程可跑通。
- 10 轮测试进度、成功率统计正确。
- 中途点击停止可在 1 秒内结束线程。
- UI 验收
- 五个左侧标签完整，非核心页为占位说明。
- 界面在低分辨率屏幕不遮挡，输入与按钮排布清晰。
- 日志可看到运行信息和错误提示。
- 硬件联调验收
- 真机下可连接万用表与继电器。
- 至少完成 20 轮上下电，日志与成功率合理。

## 实施顺序（可直接执行）
1. 建立目录、依赖、入口与基础窗口框架。
2. 实现主题样式与五标签布局、自适应尺寸逻辑。
3. 实现配置中心与 GUI 日志桥接。
4. 重构万用表解析模块（基于现有脚本逻辑，不改原文件）。
5. 实现继电器驱动与查询解析。
6. 实现蓝牙探测与匹配模块。
7. 实现仿真设备层与统一设备接口。
8. 实现 `TestEngine` 状态机与线程通信。
9. 将上下电页控件与业务绑定（开始/停止/连接/自动检测/保存）。
10. 补充帮助页内容与占位页说明。
11. 完成单元+仿真集成测试并修正问题。

## 假设与默认决策
- 操作系统为 Windows 10/11（当前仓库环境为 Windows）。
- Python 版本为 3.11，GUI 使用 PySide6。
- 继电器协议以需求文档给定指令为准，状态查询响应按容错解析实现。
- “开始测试”按“断电验证 + 上电验证”为一轮执行。
- 首版只交付源码运行版本，不包含打包发布流程。
