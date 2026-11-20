@echo off
chcp 65001 > nul
cd /d "%~dp0"
title 열차 안내판

rem ── 파이썬 확인 ──
set PY=python
where python >nul 2>nul || set PY=py
where %PY% >nul 2>nul || (
  echo [!] 파이썬이 없습니다. python.org 에서 설치하고, 설치할 때 "Add to PATH" 에 체크하세요.
  pause
  exit /b
)

rem ── cloudflared 확인 (처음 한 번만 설치) ──
where cloudflared >nul 2>nul || (
  echo cloudflared 를 설치합니다. 처음 한 번만 하면 됩니다...
  winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements
  echo.
  echo 설치가 끝났습니다. 이 창을 닫고 start_windows.bat 을 다시 실행하세요.
  pause
  exit /b
)

rem ── 서버 켜기 (별도 창) ──
start "열차 안내판 서버 - 이 창을 닫으면 꺼집니다" %PY% server.py
timeout /t 3 > nul

echo.
echo ============================================================
echo  잠시 후 아래에 https://xxxx.trycloudflare.com 주소가 나옵니다.
echo  그 주소와 config.json 의 비밀번호를 친구들에게 보내세요.
echo  이 창을 닫으면 친구들 접속이 끊깁니다.
echo ============================================================
echo.
cloudflared tunnel --url http://localhost:8000
pause
