param([string]$OutputDirectory = 'bin')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$sdk = Join-Path $taskRoot 'vendor\WebView2SDK'
$out = Join-Path $PSScriptRoot $OutputDirectory
New-Item -ItemType Directory -Path $out -Force | Out-Null
$csc = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $csc /nologo /target:exe /platform:x64 /optimize+ /reference:System.dll /reference:System.Core.dll /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll "/reference:$sdk\lib\net462\Microsoft.Web.WebView2.Core.dll" "/reference:$sdk\lib\net462\Microsoft.Web.WebView2.WinForms.dll" "/out:$out\WebViewHost.exe" "$PSScriptRoot\WebViewHost.cs"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Copy-Item -LiteralPath "$sdk\lib\net462\Microsoft.Web.WebView2.Core.dll" -Destination $out -Force
Copy-Item -LiteralPath "$sdk\lib\net462\Microsoft.Web.WebView2.WinForms.dll" -Destination $out -Force
Copy-Item -LiteralPath "$sdk\runtimes\win-x64\native\WebView2Loader.dll" -Destination $out -Force
Copy-Item -LiteralPath "$sdk\LICENSE.txt" -Destination $out -Force
Write-Output "WEBVIEW_HOST_BUILD_OK $out"
