#!/usr/bin/env bash
# 레일보드 업데이트: GitHub 의 최신 코드를 받아서 다시 시작
#   사용법:  bash /opt/railboard/app/deploy/update.sh
set -euo pipefail
git -C /opt/railboard/app pull --ff-only
sudo systemctl restart railboard
sleep 2
systemctl is-active --quiet railboard && echo "✅ 업데이트 완료" || sudo journalctl -u railboard -n 30 --no-pager
