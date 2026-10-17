@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>&1
if errorlevel 1 (
  echo ไม่พบ Python กรุณาติดตั้ง Python 3.12 ขึ้นไปจาก https://www.python.org/downloads/
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo กำลังสร้าง Python environment ครั้งแรก...
  python -m venv .venv
  if errorlevel 1 (
    echo สร้างสภาพแวดล้อมไม่สำเร็จ
    pause
    exit /b 1
  )
)
call ".venv\Scripts\activate.bat"
if not exist "data\source_pdfs" mkdir "data\source_pdfs"
".venv\Scripts\python.exe" -c "import streamlit,pandas,bs4,pypdf,truststore,openpyxl" >nul 2>&1
if errorlevel 1 (
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo ติดตั้งไลบรารีไม่สำเร็จ
    pause
    exit /b 1
  )
)
echo กำลังเปิด Teacher Exam Radar ที่ http://localhost:8501
echo ระบบจะตรวจและนำเข้า PDF ใน data\source_pdfs อัตโนมัติเมื่อเปิดเว็บ
set STREAMLIT_SERVER_HEADLESS=true
set STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
".venv\Scripts\python.exe" -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false
pause
