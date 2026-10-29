@echo off
rem Abre o RotaVan. Basta dar dois cliques neste arquivo.
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

"%PY%" -c "import streamlit, streamlit_folium, ortools, osmnx, folium" 2>nul
if errorlevel 1 (
  echo Instalando o que falta ^(so na primeira vez, leva alguns minutos^)...
  "%PY%" -m pip install -r requirements.txt
)

if not exist "dados\ijui_drive.graphml" (
  echo Baixando o mapa de Ijui...
  "%PY%" baixar_mapa_ijui.py
)

echo.
echo Abrindo o RotaVan no navegador. Para fechar, feche esta janela.
"%PY%" -m streamlit run app.py
pause
