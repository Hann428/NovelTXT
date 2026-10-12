@echo off
cd /d "%~dp0"
if exist "dist\NovelTXT-v11-fix9\NovelTXT-v11-fix9.exe" (
  start "" "dist\NovelTXT-v11-fix9\NovelTXT-v11-fix9.exe" --home "%~dp0."
) else if exist "dist\NovelTXT-v11-fix8\NovelTXT-v11-fix8.exe" (
  start "" "dist\NovelTXT-v11-fix8\NovelTXT-v11-fix8.exe" --home "%~dp0."
) else if exist "dist\NovelTXT-v11-fix7\NovelTXT-v11-fix7.exe" (
  start "" "dist\NovelTXT-v11-fix7\NovelTXT-v11-fix7.exe" --home "%~dp0."
) else if exist "dist\NovelTXT-v11-fix6\NovelTXT-v11-fix6.exe" (
  start "" "dist\NovelTXT-v11-fix6\NovelTXT-v11-fix6.exe" --home "%~dp0."
) else if exist "dist\NovelTXT-v11-fix5\NovelTXT-v11-fix5.exe" (
  start "" "dist\NovelTXT-v11-fix5\NovelTXT-v11-fix5.exe" --home "%~dp0."
) else if exist "dist\NovelTXT-v11-fix4\NovelTXT-v11-fix4.exe" (
  start "" "dist\NovelTXT-v11-fix4\NovelTXT-v11-fix4.exe" --home "%~dp0."
) else if exist "dist\NovelTXT-v11-fix3\NovelTXT-v11-fix3.exe" (
  start "" "dist\NovelTXT-v11-fix3\NovelTXT-v11-fix3.exe" --home "%~dp0."
) else if exist "dist\NovelTXT-v11-fix2\NovelTXT-v11-fix2.exe" (
  start "" "dist\NovelTXT-v11-fix2\NovelTXT-v11-fix2.exe" --home "%~dp0."
) else if exist "dist\NovelTXT-v11-fix1\NovelTXT-v11-fix1.exe" (
  start "" "dist\NovelTXT-v11-fix1\NovelTXT-v11-fix1.exe" --home "%~dp0."
) else if exist "dist\NovelTXT-v11\NovelTXT-v11.exe" (
  start "" "dist\NovelTXT-v11\NovelTXT-v11.exe" --home "%~dp0."
) else if exist "dist\NovelTXT-v10\NovelTXT-v10.exe" (
  start "" "dist\NovelTXT-v10\NovelTXT-v10.exe" --home "%~dp0."
) else if exist "dist\NovelTXT-v9\NovelTXT-v9.exe" (
  start "" "dist\NovelTXT-v9\NovelTXT-v9.exe" --home "%~dp0."
) else if exist "dist\NovelTXT-v8\NovelTXT-v8.exe" (
  start "" "dist\NovelTXT-v8\NovelTXT-v8.exe" --home "%~dp0."
) else if exist "dist\NovelTXT-v7\NovelTXT-v7.exe" (
  start "" "dist\NovelTXT-v7\NovelTXT-v7.exe" --home "%~dp0."
) else if exist "dist\NovelTXT-v6\NovelTXT-v6.exe" (
  start "" "dist\NovelTXT-v6\NovelTXT-v6.exe" --home "%~dp0."
) else if exist "dist\NovelTXT-v5\NovelTXT-v5.exe" (
  start "" "dist\NovelTXT-v5\NovelTXT-v5.exe" --home "%~dp0."
) else if exist "dist\NovelTXT-v4\NovelTXT-v4.exe" (
  start "" "dist\NovelTXT-v4\NovelTXT-v4.exe" --home "%~dp0."
) else if exist "dist\NovelTXT\NovelTXT.exe" (
  start "" "dist\NovelTXT\NovelTXT.exe" --home "%~dp0."
) else (
  start "" ".venv\Scripts\pythonw.exe" "launcher.py"
)
