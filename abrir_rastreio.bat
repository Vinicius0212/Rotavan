@echo off
rem Abre o servidor de acompanhamento ao vivo do RotaVan (motorista e familias).
rem Deixe esta janela aberta enquanto estiver usando. Para parar, feche a janela.
cd /d "%~dp0"

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY if exist "%USERPROFILE%\anaconda3\python.exe" set "PY=%USERPROFILE%\anaconda3\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\anaconda3\python.exe" set "PY=%LOCALAPPDATA%\anaconda3\python.exe"
if not defined PY if exist "%USERPROFILE%\miniconda3\python.exe" set "PY=%USERPROFILE%\miniconda3\python.exe"
if not defined PY if exist "C:\ProgramData\anaconda3\python.exe" set "PY=C:\ProgramData\anaconda3\python.exe"
if not defined PY (
  echo Nao encontrei o Python. Abra o "Anaconda Prompt" e rode este arquivo por la.
  pause
  exit /b 1
)

"%PY%" -c "import fastapi, uvicorn, qrcode, ortools, osmnx, tzdata" 2>nul
if errorlevel 1 (
  echo Instalando o que falta ^(so na primeira vez^)...
  "%PY%" -m pip install -r requirements.txt
)

if not exist "dados\ijui_drive.graphml" (
  echo Baixando o mapa de Ijui...
  "%PY%" baixar_mapa_ijui.py
)

where cloudflared >nul 2>nul
if errorlevel 1 if not exist "cloudflared.exe" if not exist "%ProgramFiles%\cloudflared\cloudflared.exe" if not exist "%ProgramFiles(x86)%\cloudflared\cloudflared.exe" if not exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\cloudflared.exe" (
  echo Instalando o cloudflared ^(cria o endereco https para o celular; so na primeira vez^)...
  winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements
)

echo.
echo Iniciando o servidor. Aguarde o ENDERECO PARA O CELULAR aparecer abaixo.
"%PY%" servidor.py --tunel
pause
