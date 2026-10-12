# 私人仓库与换电脑使用

项目位置：独立私人仓库 `Hann428/NovelTXT`，默认分支 `main`。这是个人项目；`Hann428/Clipto`只保存工作项目，本项目不得再同步到Clipto或其 `aws-project-from-md/` 子目录。

## 同步内容与本机数据

源码、测试、构建脚本、必要工具、原图标素材、固定版本依赖、已修改的Novel-Get源码及许可证进入GitHub。`vendor/Novel-Get`作为普通源码目录保存，不依赖额外Git子模块。

不上传 `.venv`、`dist`、`build`、WebView2 SDK二进制包、`data`、`evidence`、`logs`、小说TXT、`.novel-cache`、预备链接列表、账号Cookie、浏览器资料或密钥。`.gitignore`负责持续排除，提交前仍需核对实际暂存文件。保持独立仓库的私有设置，不改变其他项目。

这意味着在天津可以取回并修改代码，下载任务不会通过GitHub自动跨电脑续传。若要搬运已有小说和断点，请先在原电脑“停止并保存”并退出，再自行通过可信的本地方式复制“下载”或指定保存目录（包含隐藏的 `.novel-cache`）；浏览器资料不必复制，新电脑正常登录/手动验证即可。不要将这些目录提交GitHub。移动路径后重新选择实际保存目录，先核对缓存进度再开始。

## 新Windows电脑首次配置

已验证技术栈为64位Windows、Python3.12（包含Tkinter）、Microsoft Edge WebView2 Runtime及Windows自带的.NET Framework编译器。内置浏览器当前只构建x64组件；其他平台未验证。

1. 用有仓库权限的GitHub账号取回 `Hann428/NovelTXT`；可以克隆仓库或下载ZIP。建议使用Git克隆，后续方便拉取与提交。
2. 安装Python3.12 x64及Python Launcher。系统若没有WebView2 Runtime，先按微软官方说明安装。SDK是编译依赖，不能替代WebView2 Runtime。
3. 在PowerShell中进入实际的 `NovelTXT` 仓库文件夹，按顺序执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe tools\fetch_webview2_sdk.py
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\native\build_webview2.ps1 -OutputDirectory release
```

SDK脚本从NuGet下载固定版本 `Microsoft.Web.WebView2 1.0.3912.50`，验证NuGet SHA512，再提取编译需要的文件。网络请求有超时，哈希不符会报错；失败时不要跳过校验或当作组件已装好。

4. 双击 `启动下载器.cmd`。没有发布版EXE时，它会用本项目 `.venv\Scripts\pythonw.exe` 启动源码。成功条件是窗口能正常显示、内置浏览器可打开公共网页；首次浏览站点可能需要正常登录或手动验证。
5. 选择保存目录，先试下少量章节并检查排版，再进行批量下载。网站限流、缺页和需要核对的正文仍按软件原规则处理。

微软运行时说明：[WebView2 distribution](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)。

## 修改、检查和打包

修改前先确认无本项目未提交更改，再从集合仓库拉取：

```powershell
git status --short
git pull --ff-only
```

在 `NovelTXT` 仓库根目录执行离线回归：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

需要发布程序时，先停止并退出本项目工具与浏览器原型，避免原生组件被占用，再构建：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build.ps1 -Name NovelTXT-v11-fix9
```

结果位于 `dist\NovelTXT-v11-fix9`。使用或本地传输时保留整个文件夹，不要只复制EXE。构建产物继续留在本机，不提交集合仓库。若将来更换版本名，须同时更新启动脚本的首选版本与文档。

提交时在独立仓库根目录检查暂存内容，先确认 `git remote -v` 指向 `Hann428/NovelTXT`：

```powershell
git add -- .
git diff --cached --stat
git diff --cached --name-only
git commit -m "Update novel TXT downloader"
git push origin main
```

正常推送，不强制推送；如果远端被另一台电脑更新，先检查并合并/变基，再推送。最后检查远端提交和项目目录，不能只看本地commit就认为同步成功。

## 工具验证的范围

`tests/`提供隔离回归测试；`tools/`还包含开发期间的实站诊断、原型和历史速度/排版对比。这些历史工具部分依赖未上传的 `evidence` 样本、旧浏览器资料或本机捕获环境；不会在正常启动时自动执行，也不应作为新电脑已完成实站验收的证明。新电脑首次可运行的验收以离线回归、程序启动、内置浏览器和少量真实章节检查为准。
