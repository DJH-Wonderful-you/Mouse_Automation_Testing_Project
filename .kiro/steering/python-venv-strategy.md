---
inclusion: always
---

# Python虚拟环境使用策略

## 虚拟环境配置

本项目使用Python虚拟环境，位于 `.venv` 目录。

## 执行规则

在执行任何Python相关命令时，必须使用虚拟环境中的Python解释器。

### Windows环境下的命令格式

#### 1. 执行Python脚本
```bash
.\.venv\Scripts\python.exe <script_name>.py
```

#### 2. 安装Python包
```bash
.\.venv\Scripts\pip.exe install <package_name> -i https://pypi.tuna.tsinghua.edu.cn/simple
```

#### 3. 查看已安装的包
```bash
.\.venv\Scripts\pip.exe list
```

#### 4. 安装requirements.txt
```bash
.\.venv\Scripts\pip.exe install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

#### 5. 运行Flask应用
```bash
.\.venv\Scripts\python.exe app.py
```

#### 6. 运行测试脚本
```bash
.\.venv\Scripts\python.exe test_excel_parser.py
```

## 禁止的行为

- ❌ 不要使用 `python` 命令（这会使用系统Python）
- ❌ 不要使用 `pip` 命令（这会安装到系统Python）
- ❌ 不要使用 `python -m pip` 命令（这会使用系统Python）

## 正确的行为

- ✅ 始终使用 `.\.venv\Scripts\python.exe` 执行Python脚本
- ✅ 始终使用 `.\.venv\Scripts\pip.exe` 安装包
- ✅ 在执行命令前，确认虚拟环境路径存在

## 验证虚拟环境

可以通过以下命令验证是否使用了正确的虚拟环境：

```bash
.\.venv\Scripts\python.exe -c "import sys; print(sys.executable)"
```

输出应该包含 `.venv` 路径。

## 适用范围

此规则适用于：
- 所有Python脚本执行
- 所有包安装操作
- 所有Python相关的命令行操作
- 开发、测试、打包等所有场景

## 优先级

此规则优先级高于系统默认Python环境配置。
