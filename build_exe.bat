@echo off
chcp 65001 >nul
echo Generating icon ...
C:\Users\Admin\AppData\Local\Python\pythoncore-3.14-64\python.exe -c "from PIL import Image, ImageDraw, ImageFont; from pathlib import Path; img = Image.new('RGBA', (64,64), (26,26,46,255)); draw = ImageDraw.Draw(img); font = None; [exec('font=ImageFont.truetype(n,28)') or exit(0) for n in ['segoeuib.ttf','segoeui.ttf','arialbd.ttf','arial.ttf'] if __import__('contextlib').suppress(Exception) or True]; font = font or ImageFont.load_default(); b = draw.textbbox((0,0),'JF',font=font); draw.text(((64-(b[2]-b[0]))//2-b[0], (64-(b[3]-b[1]))//2-b[1]-1), 'JF', fill=(200,200,210,255), font=font); img.save(Path(r'%~dp0')/'jf_icon.ico', format='ICO', sizes=[(64,64),(32,32),(16,16)])"
echo Building jina_file.exe ...
C:\Users\Admin\AppData\Local\Python\pythoncore-3.14-64\python.exe -m PyInstaller --onefile --windowed --name jina_file --icon=jf_icon.ico --distpath . --workpath __pycache__\pyibuild jina_file.py
if %errorlevel% equ 0 (
    echo.
    echo Done! jina_file.exe has been updated.
    if exist __pycache__\pyibuild rmdir /s /q __pycache__\pyibuild >nul 2>&1
    if exist build rmdir /s /q build >nul 2>&1
    if exist jina_file.spec del jina_file.spec >nul 2>&1
) else (
    echo.
    echo Build failed. Check errors above.
)
pause
