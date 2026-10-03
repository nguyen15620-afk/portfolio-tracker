@echo off
REM Mở app theo dõi danh mục. Truy cập từ điện thoại cùng wifi qua địa chỉ "Network URL" hiện ra.
cd /d "%~dp0"
"%USERPROFILE%\.venv\Scripts\python.exe" -m streamlit run app.py --server.address 0.0.0.0
