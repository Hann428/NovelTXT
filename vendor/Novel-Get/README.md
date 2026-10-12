# Novel-Get

站点无关的 Python CLI 小说下载器。自动识别目录、合并分页、补全阅读链，输出 TXT/EPUB。

## 安装

需要 Python 3.11+，使用 [uv](https://docs.astral.sh/uv/) 管理依赖：

```powershell
uv sync --locked
uv run novel-get --help
```

浏览器模式（需额外安装）：

```powershell
uv sync --locked --extra browser
```

## 快速开始

```powershell
# 查看目录（不下载）
uv run novel-get inspect "https://example.com/book/123/"

# 下载单本
uv run novel-get download "https://example.com/book/123/"

# 试下载前 6 章
uv run novel-get download "https://example.com/book/123/" --limit 6

# 批量下载 + 生成合集
uv run novel-get download --urls-file urls.txt --merge "我的合集"
```

## 命令

### `inspect` - 检查目录

输出 JSON 格式的目录信息，不下载正文。

```powershell
uv run novel-get inspect <URL> [选项]
```

### `download` - 下载小说

下载正文并导出为 TXT/EPUB。

```powershell
uv run novel-get download <URL...> [选项]
```

## 完整参数

### 输入

| 参数                  | 说明                                     | 默认值 |
| --------------------- | ---------------------------------------- | ------ |
| `urls`                | 目录页 URL（可传多个）                   | -      |
| `--urls-file PATH`    | 每行一个 URL 的文件（支持 `#` 注释）     | -      |
| `--config PATH`       | 站点 TOML 配置，可重复，后者优先          | -      |
| `--headers-file PATH` | 请求头 JSON（可存 Cookie，勿提交版本库） | -      |

### HTTP 请求

| 参数                  | 说明                 | 默认值    |
| --------------------- | -------------------- | --------- |
| `--user-agent UA`     | 自定义 User-Agent    | Chrome UA |
| `--header NAME:VALUE` | 额外请求头（可重复） | -         |

### 限速

| 参数              | 说明                         | 默认值 |
| ----------------- | ---------------------------- | ------ |
| `--delay SEC`     | 响应后最少等待秒数           | `15`   |
| `--jitter SEC`    | 额外随机等待上限             | `15`   |
| `--pause-every N` | 每 N 次请求长休息            | `20`   |
| `--pause SEC`     | 长休息时长（实际为 N~2N 秒） | `300`  |
| `--timeout SEC`   | 单次请求超时                 | `30`   |
| `--retries N`     | 临时错误重试次数（0-10）     | `0`    |

### 浏览器模式

| 参数                     | 说明                             | 默认值                        |
| ------------------------ | -------------------------------- | ----------------------------- |
| `--browser`              | 用 Chromium 渲染 JS（无头）      | `false`                       |
| `--browser-channel`      | 使用本机浏览器 `chrome`/`msedge` | -                             |
| `--interactive`          | 可见窗口 + 持久会话 + 手动登录   | `false`                       |
| `--browser-profile PATH` | 自定义会话目录                   | `.novel-get/browser/<浏览器>` |
| `--wait SEC`             | 页面加载后等待秒数               | `1`                           |
| `--wait-selector CSS`    | 等待指定元素出现                 | -                             |

### 下载控制

| 参数                    | 说明                          | 默认值      |
| ----------------------- | ----------------------------- | ----------- |
| `-o, --output DIR`      | 输出目录                      | `downloads` |
| `--format`              | 导出格式 `txt`/`epub`/`both`  | `both`      |
| `--merge TITLE`         | 生成跨源合集                  | -           |
| `--limit N`             | 每本仅下载 N 章（试下载模式） | -           |
| `--max-chapters N`      | 单本最大章节数                | `10000`     |
| `--max-chapter-pages N` | 单章最大分页数                | `100`       |
| `--min-chars N`         | 单页最少正文字数              | `80`        |
| `--max-toc-pages N`     | 目录最大翻页数                | `200`       |
| `--refresh`             | 忽略缓存重新下载              | `false`     |
| `--quiet`               | 隐藏进度输出                  | `false`     |

## 配置陌生站点

内置配置位于 `config/default.toml`（通用自动识别选择器）和 `config/sites/*.toml`（内置站点规则，如我读书网站的 `.title` 标题规则）。该文件会随安装包一起发布，无需在当前目录复制一份。

用户配置放在**运行命令的当前目录**下的 `config/sites/*.toml`，下载和 `inspect` 时自动加载；这些个人配置已加入 Git 忽略规则。可从 [examples/sites.toml](examples/sites.toml) 复制模板，例如创建 `config/sites/my-sites.toml`：

```toml
[sites.my_site]
domains = ["example.com", "*.example.com"]
toc_selector = "#list a[href]"
content_selector = "#chaptercontent"
title_selector = "h1"
remove_selector = ".advertisement"

[sites.mobile]
domains = ["m.example.com"]
content_selector = ".mobile-content"
```

每个 `[sites.<名称>]` 代表一条规则，名称用于区分配置，`domains` 是必填的非空数组；其他字段按需填写。不要直接复制整个 `default.toml` 作为用户配置，用户 TOML 仅支持 `sites` 表。

```powershell
# 自动加载 config/sites/*.toml，批量任务按每本书的域名分别匹配
uv run novel-get download "https://example.com/book/123/" "https://m.example.com/book/456/"

# 也可以指定任意位置的配置，多个 --config 按顺序叠加
uv run novel-get inspect "https://example.com/book/123/" --config D:/my-sites.toml
```

匹配与覆盖规则：

- 匹配 URL 的主机名，忽略大小写、端口和末尾的点，支持中文域名；不匹配路径或查询参数。
- `example.com` 只匹配该域名；`*.example.com` 匹配各级子域名，但不包含根域名，也不会匹配 `fakeexample.com` 或 `example.com.evil.org`。需要根域名和子域名时应同时写两项。
- 优先级从低到高为：内置站点配置 → `config/sites/*.toml` 按文件名字典序 → `--config` 按参数顺序。后面的文件按字段覆盖前面的配置。
- 同一个文件内，先应用通配规则（更长的域名后应用），再应用精确域名；同等匹配程度时，后声明的规则优先。多个规则匹配时按字段合并，站点名称不参与匹配或覆盖。
- 未填写字段沿用之前的配置；将选择器设为 `""` 可恢复该字段的通用自动识别，或取消额外的广告清理规则。
- 初始目录 URL 重定向后，按实际域名匹配，目录分页和章节下载使用该站点配置；后续页面仍执行现有的同站检查。
- `inspect` 输出中的 `site_config` 显示合并后的站点选择器。选择器或通用解析配置改变后，章节缓存会失效并重新下载。

配置仅控制页面解析。限速、浏览器模式、请求头等仍由命令行参数控制。无匹配规则时继续自动识别；未知字段、错误域名、非字符串选择器和无效 CSS 会在联网前报告配置错误。

支持的选择器字段：

| 字段                    | 用途                         |
| ----------------------- | ---------------------------- |
| `toc_selector`          | 章节链接的 a 元素            |
| `toc_next_selector`     | 目录分页的 a/option 元素     |
| `content_selector`      | 正文内容区域                 |
| `title_selector`        | 章节标题                     |
| `next_page_selector`    | 章内"下一页"链接             |
| `next_chapter_selector` | "下一章"链接                 |
| `remove_selector`       | 正文内需删除的节点（如广告） |

## 浏览器模式

```powershell
# 使用本机 Chrome（无头）
uv run novel-get download "https://..." --browser-channel chrome --wait 2

# 安装独立 Chromium
uv run playwright install chromium
uv run novel-get download "https://..." --browser

# 交互模式（可见窗口，可手动登录）
uv run novel-get download --interactive --browser-channel chrome --urls-file urls.txt
```

交互模式流程：

1. 首次打开站点后等待确认，可手动登录
2. 按回车开始下载
3. 遇到 401/403/429/验证码时停止，窗口保留供处理
4. 正常退出时保存会话，下次可复用

## 限速策略

默认保守串行请求：

- 每次请求间隔 15~30 秒
- 每 20 次请求休息 5~10 分钟
- 遇到 401/403/429/验证码立即停止整次任务
- 不自动换 UA 或重试

## 输出结构

```
downloads/
  run-report.json           # 运行报告
  合集标题.txt / .epub      # --merge 生成的合集
  书名-<哈希>/
    book.txt / book.epub
    manifest.json           # 章节元数据、状态、失败项
    chapters/<哈希>.json    # 章节缓存
```

## 断点续传

- 正文自动缓存，再次运行相同命令复用
- Ctrl+C 后保留已完成章节，未完成章下次重试
- `--refresh` 强制重新下载

## 退出码

| 码    | 含义                            |
| ----- | ------------------------------- |
| `0`   | 成功                            |
| `2`   | 访问失败/不完整/试下载/配置错误 |
| `130` | 用户中断                        |

## 测试

```powershell
uv run pytest -q
```

## 贡献

欢迎提交 Issue 和 Pull Request。请先阅读 [贡献指南](CONTRIBUTING.md) 和 [行为准则](CODE_OF_CONDUCT.md)。

## 许可证

[MIT](LICENSE)
