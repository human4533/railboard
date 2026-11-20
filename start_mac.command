#!/bin/bash
# 맥: 더블클릭으로 실행 (처음엔 우클릭 → 열기)
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null; then
  echo "[!] python3 이 없습니다. python.org 에서 설치하세요."; read -r; exit 1
fi
if ! command -v cloudflared >/dev/null; then
  if command -v brew >/dev/null; then
    echo "cloudflared 설치 중 (처음 한 번만)..."; brew install cloudflared
  else
    echo "[!] Homebrew(brew.sh)를 설치한 뒤 다시 실행하세요."; read -r; exit 1
  fi
fi

python3 server.py &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT
sleep 3
echo
echo "============================================================"
echo " 잠시 후 https://xxxx.trycloudflare.com 주소가 나옵니다."
echo " 그 주소와 config.json 의 비밀번호를 친구들에게 보내세요."
echo " 이 창을 닫으면 서버와 공유가 모두 꺼집니다."
echo "============================================================"
cloudflared tunnel --url http://localhost:8000
