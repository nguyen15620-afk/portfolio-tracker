@echo off
title Portfolio App Launcher
cd /d "%~dp0"
echo ========================================================
echo   DANG KHOI DONG HE THONG THEO DOI DANH MUC CHUNG KHOAN
echo ========================================================
echo.

REM 1. Chay Streamlit Web Server o background
start /b "" "%USERPROFILE%\.venv\Scripts\python.exe" -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501 --server.headless true

timeout /t 3 /nobreak >nul

REM 2. Chay Cloudflare Tunnel tao link online
echo.
echo ========================================================
echo   DUONG LINK DANG DUOC KHOI TAO...
echo   Ban va Me co the mo tren dien thoai/may tinh qua link ben duoi:
echo ========================================================
echo.
cloudflared tunnel --url http://localhost:8501
pause
