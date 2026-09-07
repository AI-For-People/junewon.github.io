# Windows PowerShell 실행 스크립트 — 토큰을 넣고 run-watch.ps1 로 복사해 쓰세요.
#
#   copy run-watch.example.ps1 run-watch.ps1
#   notepad run-watch.ps1          # 아래 두 값 채우기
#   .\run-watch.ps1
#
# run-watch.ps1 은 .gitignore 에 있어 커밋되지 않습니다. 토큰이 든 파일을
# 저장소에 올리지 마세요 — 이 저장소는 공개입니다.

$env:TELEGRAM_BOT_TOKEN = "여기에-BotFather가-준-토큰"
$env:TELEGRAM_CHAT_ID   = "여기에-userinfobot이-알려준-Id"

Set-Location $PSScriptRoot

# 알림 경로부터 확인하고, 정상일 때만 감시를 시작한다
python cgv_imax_watch.py --test-notify
if ($LASTEXITCODE -ne 0) {
    Write-Host "`n알림 설정에 문제가 있습니다. 위 메시지를 확인하세요." -ForegroundColor Red
    exit 1
}

python cgv_imax_watch.py -v
