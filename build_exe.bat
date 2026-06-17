@echo off
chcp 65001 >nul
echo Building jina_file.exe ...
C:\Users\Admin\AppData\Local\Python\pythoncore-3.14-64\python.exe -m PyInstaller --onefile --windowed --name jina_file --distpath . --workpath __pycache__\pyibuild jina_file.py
if %errorlevel% equ 0 (
    echo.
    echo Done! jina_file.exe has been updated.
    REM clean up build artifacts
    if exist __pycache__\pyibuild rmdir /s /q __pycache__\pyibuild >nul 2>&1
    if exist build rmdir /s /q build >nul 2>&1
    if exist jina_file.spec del jina_file.spec >nul 2>&1
) else (
    echo.
    echo Build failed. Check errors above.
)
pause
