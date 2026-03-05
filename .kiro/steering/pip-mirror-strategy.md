# Python包安装镜像策略

## 安装优先级规则

在使用pip install安装Python包时，必须按照以下优先级顺序尝试：

### 1. 清华大学镜像（首选）
```bash
pip install <package_name> -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 2. 豆瓣镜像（备选）
如果清华镜像失败，使用豆瓣镜像：
```bash
pip install <package_name> -i http://pypi.douban.com/simple/
```

### 3. 官方源（最后选择）
如果前两个镜像都失败，才使用官方PyPI源：
```bash
pip install <package_name>
```

## 实施规范

- **自动化脚本**: 在编写安装脚本时，应包含镜像失败重试逻辑
- **错误处理**: 每次镜像失败时应记录错误信息，便于问题排查
- **网络环境**: 考虑到国内网络环境，优先使用国内镜像可显著提升安装速度
- **依赖安装**: 对于requirements.txt文件，也应遵循相同的镜像优先级策略

## 示例实现

```bash
# 尝试清华镜像
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple || \
# 失败则尝试豆瓣镜像
pip install -r requirements.txt -i http://pypi.douban.com/simple/ || \
# 最后使用官方源
pip install -r requirements.txt
```

## 适用范围

此策略适用于：
- 所有Python项目的依赖安装
- 开发环境搭建
- 生产环境部署
- CI/CD流水线中的包安装步骤