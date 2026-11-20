#!/usr/bin/env bash
# 레일보드 서버 설치 (Ubuntu 24.04, Lightsail)
#   사용법:  bash install.sh <GitHub 저장소 주소> <주소이름.duckdns.org>
#   예:      bash install.sh https://github.com/아이디/railboard.git railboard-dh.duckdns.org
set -euo pipefail

REPO="${1:?GitHub 저장소 주소를 적어 주세요}"
DOMAIN="${2:?접속 주소(예: railboard-dh.duckdns.org)를 적어 주세요}"
APP=/opt/railboard/app

echo "▶ 1/6 시스템 업데이트 (몇 분 걸려요)"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confold" upgrade
sudo apt-get install -y git python3 curl gnupg debian-keyring debian-archive-keyring apt-transport-https

echo "▶ 2/6 메모리 여유분(스왑 1GB) 만들기"
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  grep -q /swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "▶ 3/6 HTTPS 웹서버(Caddy) 설치"
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -y && sudo apt-get install -y caddy
fi

echo "▶ 4/6 프로그램 받기"
sudo mkdir -p /opt/railboard && sudo chown "$USER":"$USER" /opt/railboard
if [ -d "$APP/.git" ]; then git -C "$APP" pull --ff-only; else git clone "$REPO" "$APP"; fi

echo "▶ 5/6 인증키 설정"
if [ ! -f "$APP/config.json" ]; then
  read -rp "  공공데이터포털(TAGO·열차운행정보) 키: " K1
  read -rp "  레일포털(KRIC) 키: " K2
  read -rp "  서울 실시간 키: " K3
  read -rp "  친구들과 쓸 비밀번호: " PW
  python3 - "$APP/config.json" "$K1" "$K2" "$K3" "$PW" <<'PY'
import json, sys
path, k1, k2, k3, pw = sys.argv[1:]
json.dump({"data_go_kr_key": k1.strip(), "kric_key": k2.strip(), "seoul_key": k3.strip(),
           "password": pw.strip(), "port": 8000, "host": "127.0.0.1", "holidays": []},
          open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY
  chmod 600 "$APP/config.json"
else
  echo "  (이미 있는 config.json 을 그대로 씁니다)"
fi

echo "▶ 6/6 자동 실행 + HTTPS 설정"
sudo tee /etc/systemd/system/railboard.service >/dev/null <<EOF
[Unit]
Description=Railboard train board
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$APP
Environment=PORT=8000 HOST=127.0.0.1 PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 $APP/server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
sudo tee /etc/caddy/Caddyfile >/dev/null <<EOF
$DOMAIN {
	encode gzip
	reverse_proxy 127.0.0.1:8000
}
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now railboard
sudo systemctl restart railboard
sudo systemctl reload caddy || sudo systemctl restart caddy

sleep 3
if curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/ | grep -qE '200|401'; then
  echo
  echo "✅ 설치 완료!  https://$DOMAIN  으로 접속해 보세요."
  echo "   (HTTPS 인증서 발급에 1~2분 걸릴 수 있어요)"
else
  echo "⚠ 프로그램이 아직 안 떠요. 아래 명령 결과를 캡처해 주세요:"
  echo "   sudo journalctl -u railboard -n 50 --no-pager"
fi
