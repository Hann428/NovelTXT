# 贡献指南

感谢你对 Novel-Get 项目的关注！我们欢迎各种形式的贡献。

## 如何贡献

### 报告问题

- 使用 [Issue 模板](https://github.com/your-username/novel-get/issues/new/choose) 提交 bug 报告或功能请求
- 搜索现有 Issue 避免重复提交
- 提供尽可能详细的信息：操作系统、Python 版本、错误信息、复现步骤

### 提交代码

1. Fork 本仓库
2. 创建你的特性分支：`git checkout -b feature/amazing-feature`
3. 提交你的修改：`git commit -m 'feat: add amazing feature'`
4. 推送到分支：`git push origin feature/amazing-feature`
5. 创建 Pull Request

### 开发环境设置

```powershell
# 克隆你的 fork
git clone https://github.com/your-username/novel-get.git
cd novel-get

# 安装依赖
uv sync --locked

# 运行测试
uv run pytest -q

# 运行代码检查（如果有配置）
uv run ruff check .
```

## 代码规范

### 提交信息格式

使用 [Conventional Commits](https://www.conventionalcommits.org/) 格式：

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

类型：
- `feat`: 新功能
- `fix`: 修复 bug
- `docs`: 文档更新
- `style`: 代码格式（不影响功能）
- `refactor`: 重构
- `test`: 测试相关
- `chore`: 构建/工具相关

### Python 代码风格

- 遵循 PEP 8
- 使用类型注解
- 保持函数简洁，单一职责
- 编写有意义的文档字符串

### 测试要求

- 新功能必须包含测试
- 修复 bug 必须包含复现测试
- 确保所有测试通过：`uv run pytest -q`

## 项目结构

```
novel-get/
├── src/
│   └── novel_get/
│       ├── __init__.py
│       ├── cli.py          # CLI 入口
│       ├── downloader.py   # 下载逻辑
│       ├── parser.py       # HTML 解析
│       └── ...
├── tests/
├── examples/
└── pyproject.toml
```

## 行为准则

请遵守我们的 [行为准则](CODE_OF_CONDUCT.md)。

## 许可证

通过贡献代码，你同意你的贡献将在 [MIT 许可证](LICENSE) 下发布。
