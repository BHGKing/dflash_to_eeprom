@echo off
REM Build a single-file Windows GUI executable with Python 3.10 or newer.
REM The result is dist\dflash_to_eee.exe
python -m pip install --upgrade pyinstaller tkinterdnd2
if errorlevel 1 exit /b 1
pyinstaller --noconfirm --clean dflash_to_eee.spec
if errorlevel 1 exit /b 1
if not exist dist\dflash_to_eee.exe (
    echo Build did not produce dist\dflash_to_eee.exe
    exit /b 1
)
echo Built dist\dflash_to_eee.exe
