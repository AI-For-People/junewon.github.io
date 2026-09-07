#!/usr/bin/env bash
# macOS / Linux 실행 스크립트 — 토큰을 넣고 run-watch.sh 로 복사해 쓰세요.
#
#   cp run-watch.example.sh run-watch.sh && chmod +x run-watch.sh
#   $EDITOR run-watch.sh          # 아래 두 값 채우기
#   ./run-watch.sh
#
# run-watch.sh 는 .gitignore 에 있어 커밋되지 않습니다.
set -euo pipefail

export TELEGRAM_BOT_TOKEN="여기에-BotFather가-준-토큰"
export TELEGRAM_CHAT_ID="여기에-userinfobot이-알려준-Id"

cd "$(dirname "$0")"

python3 cgv_imax_watch.py --test-notify
python3 cgv_imax_watch.py -v
