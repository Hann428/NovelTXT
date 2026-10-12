// Own child window hosted by the downloader, controlled only through inherited pipes.
using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

class WebViewHost : Form
{
    [StructLayout(LayoutKind.Sequential)] struct Rect { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")] static extern bool GetClientRect(IntPtr hWnd, out Rect rect);
    [DllImport("user32.dll")] static extern bool IsWindow(IntPtr hWnd);
    [DllImport("user32.dll")] static extern IntPtr GetParent(IntPtr hWnd);
    [DllImport("user32.dll", SetLastError = true)] static extern IntPtr SetParent(IntPtr hWnd, IntPtr newParent);
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    readonly IntPtr parent;
    readonly string profile;
    readonly int fixturePort;
    readonly object outputLock = new object();
    readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = 24000000 };
    readonly Dictionary<string, WebView2> pages = new Dictionary<string, WebView2>();
    readonly Dictionary<string, DateTime> checkedHosts = new Dictionary<string, DateTime>();
    CoreWebView2Environment environment;
    System.Windows.Forms.Timer sizeTimer;

    public WebViewHost(IntPtr hwnd, string folder, int testPort)
    {
        parent = hwnd; profile = folder; fixturePort = testPort;
        FormBorderStyle = FormBorderStyle.None; ShowInTaskbar = false;
        AutoScaleMode = AutoScaleMode.None; BackColor = Color.White;
        Shown += async (s, e) => {
            try {
                // WinForms parks unmanaged child forms before Show. Attach only
                // this helper's own HWND, never another application's browser.
                SetParent(Handle, parent);
                if (GetParent(Handle) != parent) throw new Exception("child-window-parent-failed: " + Marshal.GetLastWin32Error());
                environment = await CoreWebView2Environment.CreateAsync(null, profile);
                Emit(new { kind = "ready", runtime = environment.BrowserVersionString,
                           hwnd = Handle.ToInt64(), parent = GetParent(Handle).ToInt64() });
                new Thread(ReadCommands) { IsBackground = true }.Start();
            } catch (Exception ex) { Emit(new { kind = "fatal", error = ex.Message }); Close(); }
        };
        sizeTimer = new System.Windows.Forms.Timer { Interval = 100 };
        sizeTimer.Tick += (s, e) => {
            if (!IsWindow(parent)) { Close(); return; }
            Rect r; if (GetClientRect(parent, out r)) SetBounds(0, 0, Math.Max(1, r.Right), Math.Max(1, r.Bottom));
        };
        sizeTimer.Start();
    }
    protected override CreateParams CreateParams {
        get { var cp = base.CreateParams; cp.Parent = parent;
              cp.Style = (cp.Style & ~unchecked((int)0x80000000) & ~0x00CF0000) | 0x40000000 | 0x10000000;
              return cp; }
    }
    void Emit(object value) { lock (outputLock) { Console.WriteLine(json.Serialize(value)); Console.Out.Flush(); } }
    void ReadCommands()
    {
        string line;
        try {
            while ((line = Console.ReadLine()) != null) {
                if (line.Length > 200000) { Emit(new { kind = "protocol_error", error = "command-too-large" }); continue; }
                var captured = line;
                BeginInvoke(new Action(async () => await HandleCommand(captured)));
            }
        } catch (Exception) { }
        try { BeginInvoke(new Action(Close)); } catch (Exception) { }
    }
    static string Field(Dictionary<string, object> command, string key, string fallback = "") {
        object value; return command.TryGetValue(key, out value) ? Convert.ToString(value) : fallback;
    }
    bool PrivateAddress(IPAddress address)
    {
        if (address.IsIPv4MappedToIPv6) address = address.MapToIPv4();
        if (IPAddress.IsLoopback(address)) return true;
        byte[] b = address.GetAddressBytes();
        if (b.Length == 4) return b[0] == 0 || b[0] == 10 || b[0] == 127 || b[0] >= 224 ||
            (b[0] == 169 && b[1] == 254) || (b[0] == 172 && b[1] >= 16 && b[1] <= 31) ||
            (b[0] == 192 && b[1] == 168) || (b[0] == 100 && b[1] >= 64 && b[1] <= 127) ||
            (b[0] == 198 && (b[1] == 18 || b[1] == 19));
        return address.Equals(IPAddress.IPv6Any) || address.IsIPv6LinkLocal || address.IsIPv6SiteLocal ||
            address.IsIPv6Multicast || (b[0] & 0xFE) == 0xFC || (b[0] == 0x20 && b[1] == 1 && b[2] == 0x0D && b[3] == 0xB8);
    }
    async Task<bool> Allowed(string address)
    {
        Uri uri;
        if (!Uri.TryCreate(address, UriKind.Absolute, out uri)) return false;
        if (address == "about:blank") return true;
        if (uri.Scheme != "https" && uri.Scheme != "http") return false;
        if (uri.UserInfo.Length > 0) return false;
        if (fixturePort > 0 && uri.Host == "127.0.0.1" && uri.Port == fixturePort) return true;
        if (uri.Port != 80 && uri.Port != 443) return false;
        DateTime expiry;
        if (checkedHosts.TryGetValue(uri.Host, out expiry) && expiry > DateTime.UtcNow) return true;
        try {
            IPAddress[] ips = await Dns.GetHostAddressesAsync(uri.Host);
            if (ips.Length == 0) return false;
            foreach (var ip in ips) if (PrivateAddress(ip)) return false;
            checkedHosts[uri.Host] = DateTime.UtcNow.AddSeconds(60); return true;
        } catch (Exception) { return false; }
    }
    async Task<WebView2> NewPage(string key)
    {
        if (pages.ContainsKey(key)) return pages[key];
        if (pages.Count >= 12) throw new Exception("too-many-pages");
        var view = new WebView2 { Dock = DockStyle.Fill, Visible = pages.Count == 0 };
        pages.Add(key, view); Controls.Add(view);
        await view.EnsureCoreWebView2Async(environment);
        var core = view.CoreWebView2;
        core.Settings.AreHostObjectsAllowed = false;
        core.Settings.IsWebMessageEnabled = false;
        core.Settings.AreDevToolsEnabled = false;
        core.Settings.IsStatusBarEnabled = false;
        core.Settings.AreDefaultContextMenusEnabled = false;
        core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All);
        core.WebResourceRequested += async (s, e) => {
            var deferral = e.GetDeferral();
            try {
                if (!await Allowed(e.Request.Uri))
                    e.Response = environment.CreateWebResourceResponse(new MemoryStream(), 403, "Blocked by downloader", "Content-Type: text/plain");
            } finally { deferral.Complete(); }
        };
        core.DownloadStarting += (s, e) => e.Cancel = true;
        core.PermissionRequested += (s, e) => e.State = CoreWebView2PermissionState.Deny;
        core.NewWindowRequested += async (s, e) => {
            e.Handled = true; if (await Allowed(e.Uri)) core.Navigate(e.Uri);
        };
        core.NavigationStarting += (s, e) => {
            Emit(new { kind = "navigation_start", page = key, url = e.Uri });
            Uri uri;
            if (!Uri.TryCreate(e.Uri, UriKind.Absolute, out uri) ||
                (e.Uri != "about:blank" && uri.Scheme != "http" && uri.Scheme != "https")) e.Cancel = true;
        };
        core.NavigationCompleted += (s, e) => Emit(new { kind = "navigation", page = key,
            url = core.Source, title = core.DocumentTitle, ok = e.IsSuccess, code = e.HttpStatusCode, error = e.WebErrorStatus.ToString() });
        core.WebResourceResponseReceived += (s, e) => {
            var headers = e.Request.Headers;
            if (headers.Contains("Sec-Fetch-Dest") && headers.GetHeader("Sec-Fetch-Dest") == "document") {
                var reply = e.Response.Headers;
                Emit(new { kind = "response", page = key, url = e.Request.Uri, code = e.Response.StatusCode,
                    challenge = reply.Contains("cf-mitigated") ? reply.GetHeader("cf-mitigated") : "",
                    retry_after = reply.Contains("retry-after") ? reply.GetHeader("retry-after") : "" });
            }
        };
        core.ProcessFailed += (s, e) => Emit(new { kind = "process_failed", page = key, error = e.ProcessFailedKind.ToString() });
        return view;
    }
    async Task<object> Evaluate(WebView2 page, string expression)
    {
        var arguments = json.Serialize(new { expression = expression, awaitPromise = true, returnByValue = true });
        var response = json.Deserialize<Dictionary<string, object>>(await page.CoreWebView2.CallDevToolsProtocolMethodAsync("Runtime.evaluate", arguments));
        if (response.ContainsKey("exceptionDetails")) throw new Exception("javascript-evaluation-failed");
        var result = response["result"] as Dictionary<string, object>;
        object value; return result != null && result.TryGetValue("value", out value) ? value : null;
    }
    async Task Navigate(WebView2 view, string url, bool reload = false)
    {
        if (!await Allowed(url)) throw new Exception("URL denied: only public HTTP(S) sites are allowed");
        var completion = new TaskCompletionSource<CoreWebView2NavigationCompletedEventArgs>();
        EventHandler<CoreWebView2NavigationCompletedEventArgs> done = (s, e) => completion.TrySetResult(e);
        view.CoreWebView2.NavigationCompleted += done;
        try {
            // The toolbar refresh is an explicit fresh read. Ordinary Reload()
            // still respects HTTP cache entries, including long-lived denials.
            if (reload) await view.CoreWebView2.CallDevToolsProtocolMethodAsync("Page.reload", "{\"ignoreCache\":true}");
            else view.CoreWebView2.Navigate(url);
            if (await Task.WhenAny(completion.Task, Task.Delay(25000)) != completion.Task) throw new Exception("navigation-timeout");
            var completed = await completion.Task;
            // A rendered HTTP 403 challenge must remain available for the human.
            if (!completed.IsSuccess && completed.HttpStatusCode == 0) throw new Exception("navigation-failed");
        } finally { view.CoreWebView2.NavigationCompleted -= done; }
    }
    async Task HandleCommand(string line)
    {
        object id = null;
        try {
            var command = json.Deserialize<Dictionary<string, object>>(line); id = command["id"];
            string op = Field(command, "op"), key = Field(command, "page", "main");
            object result = null;
            if (op == "shutdown") { Emit(new { id = id, result = true }); Close(); return; }
            if (op == "status") {
                Rect r; GetClientRect(parent, out r);
                result = new { parent = GetParent(Handle).ToInt64(), hwnd = Handle.ToInt64(),
                    width = Width, height = Height, parent_width = r.Right, parent_height = r.Bottom, pages = pages.Count,
                    runtime = environment.BrowserVersionString };
            } else if (op == "close_page") {
                WebView2 old; if (pages.TryGetValue(key, out old)) { pages.Remove(key); Controls.Remove(old); old.Dispose(); }
                result = true;
            } else {
                var view = await NewPage(key);
                if (op == "new_page") result = true;
                else if (op == "navigate") { await Navigate(view, Field(command, "url")); result = new { url = view.CoreWebView2.Source }; }
                else if (op == "reload") { await Navigate(view, view.CoreWebView2.Source, true); result = true; }
                else if (op == "back") { if (view.CoreWebView2.CanGoBack) view.CoreWebView2.GoBack(); result = true; }
                else if (op == "show") {
                    foreach (var child in pages.Values) child.Visible = child == view;
                    view.BringToFront(); view.Focus(); result = true;
                    Emit(new { kind = "page_selected", page = key, url = view.CoreWebView2.Source });
                } else if (op == "evaluate") result = await Evaluate(view, Field(command, "expression"));
                else if (op == "content") result = await Evaluate(view, "document.documentElement.outerHTML");
                else if (op == "snapshot") {
                    using (var stream = new MemoryStream()) { await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, stream);
                        result = Convert.ToBase64String(stream.ToArray()); }
                } else throw new Exception("unknown-command");
            }
            Emit(new { id = id, result = result });
        } catch (Exception ex) { Emit(new { id = id, error = ex.Message }); }
    }
    protected override void Dispose(bool disposing)
    {
        if (disposing) { if (sizeTimer != null) sizeTimer.Dispose(); foreach (var view in pages.Values) view.Dispose(); }
        base.Dispose(disposing);
    }
    [STAThread] public static void Main(string[] args)
    {
        Console.InputEncoding = new UTF8Encoding(false); Console.OutputEncoding = new UTF8Encoding(false);
        if (args.Length < 2) return;
        SetProcessDPIAware(); Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        int fixture = args.Length > 2 ? int.Parse(args[2]) : 0;
        Application.Run(new WebViewHost(new IntPtr(long.Parse(args[0])), Path.GetFullPath(args[1]), fixture));
    }
}
