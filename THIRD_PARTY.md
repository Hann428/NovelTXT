# 开源来源

本软件基于 Novel-Get 的部分基础能力改造。

- 项目：https://github.com/Churk-Ben/Novel-Get
- 固定上游提交：`47bb3aa5eb71829180d14af1d24ea30af4085a05`
- 许可证：MIT；原始许可证保留在 `vendor/Novel-Get/LICENSE`，发布包中位于 `_internal/licenses/Novel-Get/LICENSE`。
- 复用范围：目录及分页识别、章号解析、页面编码识别、访问限制识别、文件名清理。
- 本地修改：`parse.py` 改用保留缩进与空行的文本提取；`settings.py` 增加打包资源路径；`fetch.py` 补充中文验证页识别。
- 本地新增：Windows 界面、可暂停队列、按 URL 保留同名章节、逐页/逐章缓存校验、目录快照、原子保存、独立浏览器会话、两个测试站点的适配和完整性报告。

依赖通过 `requirements.txt` 固定版本，包括 Beautiful Soup、HTTPX、charset-normalizer、Playwright。打包使用 PyInstaller；界面使用 Python 标准库 Tkinter。未使用模型、远程 AI API 或付费服务。

网站样本、下载文件、浏览器登录状态和运行日志不属于可分发源码。不要将 `data`、`evidence`、`下载` 或日志中的私人信息公开上传。

## 追加参考（2026-09-30）

按用户提供的截图查阅 hoothin/UserScripts 的 DownloadAllContent（怠惰小说下载器，MIT）：
https://github.com/hoothin/UserScripts/tree/master/DownloadAllContent

核对了 `GM_xmlhttpRequest`、iframe 加载、`checkNextPage` 和文本导出逻辑。它已支持正文内分页及自定义下一页选择器，但不能据此认定用户安装的版本能遍历 41nr 的全部目录。其文本导出会合并部分空白，不适合作为本项目保留排版的直接替代。未引入该脚本源码或新运行依赖；继续保留本项目的目录快照、断点缓存和完整性校验。v10 参照其后台请求队列设计实现本软件章节内核，不安装或替换用户的篡改猴脚本。

## 并行调度参考（2026-10-09）

- 404-novel-project/novel-downloader 的自定义并发参数说明：https://github.com/404-novel-project/novel-downloader/blob/master/README.md 。只参考功能设计，未引入脚本代码。
- Scrapy AutoThrottle 的分站限速和按响应情况调节设计：https://docs.scrapy.org/en/master/topics/autothrottle.html 。本地实现有界任务队列、统一网页请求间隔、连续成功升并发与慢响应/错误降并发；未引入 Scrapy 或复制源码。
- Playwright 官方异步 API、线程使用和多页面文档：https://playwright.dev/python/docs/library 、https://playwright.dev/python/docs/pages 。全部浏览器对象保留在一个独立事件循环中，任务线程只接收网页数据。
- HTTPX 官方 Client 及超时文档：https://www.python-httpx.org/api/ 、https://www.python-httpx.org/advanced/timeouts/ 。沿用已有 HTTP 下载与错误处理，不添加模型或远程服务。

## v10 下载内核参考（2026-10-09）

- 再次阅读怠惰 DownloadAllContent 源码的后台 HTTP 队列、并发参数、下一页检测及分段合并设计：https://github.com/hoothin/UserScripts/blob/master/DownloadAllContent/DownloadAllContent.user.js 。独立实现本地章节线程池，未复制脚本源码。
- saudadez21/novel-downloader（MIT）：https://github.com/saudadez21/novel-downloader 。参考异步下载、可恢复缓存、HTTP 后端与站点适配设计；未引入代码或运行依赖，未声称其已支持本项目两个目标站点。
- Aayush-Loha-ni/WebChapterDownloader：https://github.com/Aayush-Loha-ni/WebChapterDownloader 。参考 README 中受控并发、分页以及 Retry-After/429 退避设计，未复制源码。
- HTTPX 连接池：https://www.python-httpx.org/advanced/clients/ ，aiohttp 会话：https://docs.aiohttp.org/en/latest/client_advanced.html ，浏览器 Fetch：https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch 。本项目沿用 HTTPX，并用专用浏览器同源 fetch 读取正文；不新增 aiohttp/Scrapy/模型依赖。
- HTTP 429 与 Retry-After：https://www.rfc-editor.org/rfc/rfc6585#section-4 。普通限流与验证挑战分别处理，冷却期间可暂停或停止。

## v11 内置浏览器（2026-10-09）

- Microsoft.Web.WebView2 SDK 1.0.3912.50：从官方 NuGet 下载，按官方元数据 SHA512 核验。版本、包 SHA256/SHA512 见 `vendor/WebView2SDK/provenance.json`；分发的 .NET DLL 与 WebView2Loader.dll 使用微软随包许可证，发布组件中包含 LICENSE.txt。WebView2 Runtime 使用用户本机已有安装，不复制运行时。SDK 并非本项目自主编写代码。
- 官方 WinForms 示例/文档：https://learn.microsoft.com/en-us/microsoft-edge/webview2/get-started/winforms ，https://github.com/MicrosoftEdge/WebView2Samples 。本地实现原生子窗口、stdio JSON 协议和下载内核适配，没有引入示例工程或 pywebview。
- 持久资料与共享进程：https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/user-data-folder ，https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/process-model 。资料仅保存在本机项目目录，不复制日常浏览器凭证；测试原型与集成模块复用 WebView2 自身支持的相同 UDF。
- Cloudflare 的嵌入浏览器兼容性限制：https://developers.cloudflare.com/cloudflare-challenges/reference/supported-browsers/ 。两站用正常手动验证实测；不保证所有站点接受内置 WebView，不自动解验证码、不伪装指纹。独立浏览器模式保留。

## v11-fix5 响应分类（2026-10-10）

- HTTP 状态语义参考 MDN：https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status 。Cloudflare 挑战页标识参考：https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/ 。没有复制文档源码。
- 本地修改 Novel-Get 的 `parse.py`，使目录/正文解析共享本项目的响应分类，并在正文提取前检查占位提示，避免第三方原有标题关键词规则再次误判。保留其原许可证；不修改原网页文本或降低章节连续性要求。
- Qwen3-0.6B / llama.cpp 仅作可选本地分类器方案调研，本版没有下载、安装或分发模型和运行时。
