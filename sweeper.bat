@echo off
rem Windows 版统一入口：等价于 macOS/Linux 的 ./sweeper（自动使用项目虚拟环境）
setlocal
set "DIR=%~dp0"

rem 1) 项目自带 venv（Windows 布局）
if exist "%DIR%.venv\Scripts\python.exe" (
    "%DIR%.venv\Scripts\python.exe" -m thunder_sweeper %*
    exit /b %errorlevel%
)

rem 2) Unix 布局的 venv（从 mac 把 .venv 整个拷过来的情况）
if exist "%DIR%.venv\bin\python" (
    "%DIR%.venv\bin\python" -m thunder_sweeper %*
    exit /b %errorlevel%
)

rem 3) 系统 Python 启动器
for %%E in ("py -3" python) do (
    where %%~E >nul 2>nul
    if not errorlevel 1 (
        %%~E -m thunder_sweeper %*
        exit /b %errorlevel%
    )
)

echo 未找到 Python，请先安装 Python 3.10+，然后在项目目录执行：
echo   python -m venv .venv
echo   .venv\Scripts\python -m pip install -r requirements.txt imageio-ffmpeg
exit /b 1