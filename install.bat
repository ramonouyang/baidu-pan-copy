@echo off
REM 百度网盘批量转存工具 - Windows 安装脚本
REM Baidu Pan Batch Transfer Tool - Windows Installation Script

setlocal enabledelayedexpansion

echo.
echo ╔═══════════════════════════════════════════════════════════╗
echo ║       百度网盘批量转存工具 - 安装程序                     ║
echo ║       Baidu Pan Batch Transfer Tool Installer             ║
echo ╚═══════════════════════════════════════════════════════════╝
echo.

REM 获取安装目录
set "INSTALL_DIR=%USERPROFILE%\baidu-pan-copy"
if not "%~1"=="" set "INSTALL_DIR=%~1"
echo [INFO] 安装目录: %INSTALL_DIR%

REM ==================== 检查 Python ====================
echo [INFO] 检查 Python 环境...
set "PYTHON_CMD="

REM 检查 python3
python3 --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=python3"
    goto :python_found
)

REM 检查 python
python --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=python"
    goto :python_found
)

REM Python 未找到，提示安装
echo.
echo [ERROR] 未找到 Python！
echo.
echo ╔═══════════════════════════════════════════════════════════╗
echo ║  请选择安装方式:                                          ║
echo ║                                                           ║
echo ║  1. 自动下载安装（推荐）                                  ║
echo ║  2. 打开下载页面手动安装                                  ║
echo ║  3. 通过 winget 安装                                      ║
echo ║  4. 通过 chocolatey 安装                                  ║
echo ╚═══════════════════════════════════════════════════════════╝
echo.
set /p "CHOICE=请选择 (1-4): "

if "!CHOICE!"=="1" goto :install_python_auto
if "!CHOICE!"=="2" goto :install_python_manual
if "!CHOICE!"=="3" goto :install_python_winget
if "!CHOICE!"=="4" goto :install_python_choco

echo [ERROR] 无效选择
pause
exit /b 1

:install_python_auto
echo.
echo [INFO] 正在下载 Python 3.11...
set "PYTHON_URL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
set "PYTHON_INSTALLER=%TEMP%\python-installer.exe"

REM 使用 PowerShell 下载
powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%PYTHON_INSTALLER%'}"
if errorlevel 1 (
    echo [ERROR] 下载失败，请检查网络连接
    echo [INFO] 您可以手动下载: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [INFO] 正在安装 Python（静默模式）...
echo [INFO] 这可能需要几分钟，请耐心等待...
"%PYTHON_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
if errorlevel 1 (
    echo [ERROR] 安装失败
    pause
    exit /b 1
)

REM 清理安装包
del "%PYTHON_INSTALLER%" 2>nul

REM 刷新环境变量
set "PATH=%LOCALAPPDATA%\Programs\Python\Python311;%LOCALAPPDATA%\Programs\Python\Python311\Scripts;%PATH%"

REM 验证安装
python --version >nul 2>&1
if errorlevel 1 (
    echo [WARNING] Python 已安装，但需要重启命令行窗口
    echo [INFO] 请关闭此窗口，重新打开命令行，再次运行 install.bat
    pause
    exit /b 0
)

set "PYTHON_CMD=python"
echo [SUCCESS] Python 安装成功！
goto :python_found

:install_python_winget
echo.
echo [INFO] 正在通过 winget 安装 Python...
winget install Python.Python.3.11
if errorlevel 1 (
    echo [ERROR] winget 安装失败
    pause
    exit /b 1
)
echo [SUCCESS] Python 安装成功！
echo [INFO] 请关闭此窗口，重新打开命令行，再次运行 install.bat
pause
exit /b 0

:install_python_choco
echo.
echo [INFO] 正在通过 chocolatey 安装 Python...
choco install python311 -y
if errorlevel 1 (
    echo [ERROR] chocolatey 安装失败
    pause
    exit /b 1
)
echo [SUCCESS] Python 安装成功！
echo [INFO] 请关闭此窗口，重新打开命令行，再次运行 install.bat
pause
exit /b 0

:install_python_manual
echo.
echo [INFO] 正在打开 Python 下载页面...
start https://www.python.org/downloads/
echo.
echo [INFO] 下载安装时请务必勾选:
echo        ☑ "Add Python to PATH"
echo.
echo [INFO] 安装完成后，请重新运行 install.bat
pause
exit /b 0

:python_found
REM 获取 Python 版本
for /f "tokens=2" %%i in ('%PYTHON_CMD% --version 2^>^&1') do set "PYTHON_VERSION=%%i"
echo [SUCCESS] Python %PYTHON_VERSION% 已安装

REM ==================== 检查 pip ====================
echo [INFO] 检查 pip...
%PYTHON_CMD% -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [WARNING] pip 未找到，正在安装...
    %PYTHON_CMD% -m ensurepip --upgrade
)

REM ==================== 创建安装目录 ====================
if exist "%INSTALL_DIR%" (
    echo [WARNING] 安装目录已存在: %INSTALL_DIR%
    set /p "OVERWRITE=是否覆盖? (y/n): "
    if /i not "!OVERWRITE!"=="y" (
        echo [INFO] 安装已取消
        pause
        exit /b 0
    )
) else (
    mkdir "%INSTALL_DIR%"
)

REM ==================== 复制文件 ====================
echo [INFO] 正在复制文件...
set "SCRIPT_DIR=%~dp0"

copy /y "%SCRIPT_DIR%main.py" "%INSTALL_DIR%\" >nul
copy /y "%SCRIPT_DIR%baidu_api.py" "%INSTALL_DIR%\" >nul
copy /y "%SCRIPT_DIR%start.py" "%INSTALL_DIR%\" >nul
copy /y "%SCRIPT_DIR%requirements.txt" "%INSTALL_DIR%\" >nul
copy /y "%SCRIPT_DIR%bookmarklet_template.js" "%INSTALL_DIR%\" >nul

REM 复制目录
if not exist "%INSTALL_DIR%\templates" mkdir "%INSTALL_DIR%\templates"
xcopy /s /e /y "%SCRIPT_DIR%templates" "%INSTALL_DIR%\templates\" >nul

REM ==================== 创建虚拟环境 ====================
echo [INFO] 正在创建虚拟环境...
%PYTHON_CMD% -m venv "%INSTALL_DIR%\venv"
if errorlevel 1 (
    echo [ERROR] 创建虚拟环境失败
    echo [INFO] 尝试安装 venv 模块...
    %PYTHON_CMD% -m pip install virtualenv
    %PYTHON_CMD% -m virtualenv "%INSTALL_DIR%\venv"
    if errorlevel 1 (
        echo [ERROR] 创建虚拟环境失败，请手动安装 Python venv 模块
        pause
        exit /b 1
    )
)

REM ==================== 安装依赖 ====================
echo [INFO] 正在安装依赖...
"%INSTALL_DIR%\venv\Scripts\python" -m pip install --upgrade pip >nul 2>&1

echo.
echo [INFO] 是否使用国内镜像源加速安装？（中国大陆用户建议选择 y）
set /p "USE_MIRROR=使用国内镜像源? (y/n): "
if /i "%USE_MIRROR%"=="y" (
    echo [INFO] 使用清华镜像源安装依赖...
    "%INSTALL_DIR%\venv\Scripts\pip" install -r "%INSTALL_DIR%\requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
) else (
    "%INSTALL_DIR%\venv\Scripts\pip" install -r "%INSTALL_DIR%\requirements.txt"
)
if errorlevel 1 (
    echo.
    echo [WARNING] 安装失败，可能是网络问题
    echo [INFO] 尝试使用国内镜像源重新安装...
    "%INSTALL_DIR%\venv\Scripts\pip" install -r "%INSTALL_DIR%\requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
    if errorlevel 1 (
        echo [ERROR] 安装依赖失败，请检查网络连接
        pause
        exit /b 1
    )
)

REM ==================== 创建启动脚本 ====================
echo [INFO] 正在创建启动脚本...

REM run.bat
(
echo @echo off
echo cd /d "%%~dp0"
echo if not exist "venv" ^(
echo     echo 错误: 虚拟环境不存在，请重新运行 install.bat
echo     pause
echo     exit /b 1
echo ^)
echo echo ╔═══════════════════════════════════════════════════════════╗
echo echo ║       百度网盘批量转存工具                                ║
echo echo ║       访问地址: http://localhost:8080                     ║
echo echo ║       按 Ctrl+C 停止服务                                 ║
echo echo ╚═══════════════════════════════════════════════════════════╝
echo echo.
echo venv\Scripts\python start.py
echo pause
) > "%INSTALL_DIR%\run.bat"

REM stop.bat
(
echo @echo off
echo echo 正在停止百度网盘批量转存工具...
echo taskkill /f /fi "IMAGENAME eq python.exe" /fi "WINDOWTITLE eq *start*" 2^>nul
echo echo 已停止
echo timeout /t 2 /nobreak ^>nul
) > "%INSTALL_DIR%\stop.bat"

REM uninstall.bat
(
echo @echo off
echo echo.
echo echo ╔═══════════════════════════════════════════════════════════╗
echo echo ║       百度网盘批量转存工具 - 卸载程序                     ║
echo echo ╚═══════════════════════════════════════════════════════════╝
echo echo.
echo echo [INFO] 正在停止服务...
echo taskkill /f /fi "IMAGENAME eq python.exe" /fi "WINDOWTITLE eq *start*" 2^>nul
echo timeout /t 2 /nobreak ^>nul
echo echo.
echo set /p "CONFIRM=[WARNING] 确认卸载? 将删除 %INSTALL_DIR% (y/n): "
echo if /i not "%%CONFIRM%%"=="y" ^(
echo     echo [INFO] 卸载已取消
echo     pause
echo     exit /b 0
echo ^)
echo echo.
echo echo [INFO] 正在删除文件...
echo rmdir /s /q "%INSTALL_DIR%" 2^>nul
echo echo [INFO] 正在删除桌面快捷方式...
echo del /f "%%USERPROFILE%%\Desktop\百度网盘转存工具.bat" 2^>nul
echo echo.
echo echo [SUCCESS] 卸载完成！
echo pause
) > "%INSTALL_DIR%\uninstall.bat"

REM ==================== 创建桌面快捷方式 ====================
echo [INFO] 正在创建桌面快捷方式...
set "SHORTCUT_PATH=%USERPROFILE%\Desktop\百度网盘转存工具.bat"
(
echo @echo off
echo cd /d "%INSTALL_DIR%"
echo call run.bat
) > "%SHORTCUT_PATH%"

REM ==================== 完成 ====================
echo.
echo ╔═══════════════════════════════════════════════════════════╗
echo ║                    安装完成！                             ║
echo ╠═══════════════════════════════════════════════════════════╣
echo ║  Python:  %PYTHON_VERSION%
echo ║  安装目录: %INSTALL_DIR%
echo ║                                                           ║
echo ║  启动方式:                                                ║
echo ║    1. 双击桌面快捷方式 "百度网盘转存工具"                 ║
echo ║    2. 或进入安装目录双击 run.bat                          ║
echo ║                                                           ║
echo ║  访问地址: http://localhost:8080                          ║
echo ║                                                           ║
echo ║  停止服务: 双击 stop.bat                                  ║
echo ║  卸载程序: 双击 uninstall.bat                             ║
echo ╚═══════════════════════════════════════════════════════════╝
echo.

pause
