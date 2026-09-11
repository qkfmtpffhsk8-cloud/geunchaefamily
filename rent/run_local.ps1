# 근채패밀리 매물 수집 - 집 PC(Windows)에서 하루 한 번 실행용.
# GitHub Actions 에서 네이버가 막힐 때 사용. 저장소를 임시 폴더에 받아 수집 → main 푸시 → 로컬 파일 삭제.
#
# 사전 준비 (한 번만):
#   1. Python 3.11+ 설치 (python.org, "Add to PATH" 체크) 와 Git 설치 (git-scm.com)
#   2. GitHub 로그인: git 이 푸시할 수 있게 Git Credential Manager 로 한 번 로그인 (첫 푸시 때 브라우저 창이 뜸)
#   3. (선택) 실거래 키: 시스템 환경변수 SEOUL_KEY, MOLIT_KEY 등록  (설정 → 시스템 → 정보 → 고급 시스템 설정 → 환경 변수)
#
# 실행:
#   PowerShell 에서   powershell -ExecutionPolicy Bypass -File run_local.ps1
#   또는 이 파일을 우클릭 → "PowerShell 에서 실행"
#
# 매일 자동 실행 (작업 스케줄러):
#   schtasks /Create /TN "근채패밀리 매물 수집" /SC DAILY /ST 06:00 /TR "powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\경로\run_local.ps1"
#
# 옵션:  -Sources "naver,zigbang"  (특정 소스만)   -Keep (임시 폴더 유지, 문제 확인용)

param(
    [string]$Repo = "https://github.com/qkfmtpffhsk8-cloud/geunchaefamily.git",
    [string]$Sources = "",
    [switch]$Keep
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:TZ = "Asia/Seoul"

$work = Join-Path $env:TEMP ("geunchae-rent-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
$log = Join-Path $env:TEMP "geunchae-rent-last.log"
Start-Transcript -Path $log -Force | Out-Null

try {
    Write-Host "== 저장소 받기 → $work"
    git clone --depth 1 --branch main $Repo $work
    if ($LASTEXITCODE -ne 0) { throw "git clone 실패" }
    Set-Location $work

    Write-Host "== 가상환경·의존성"
    python -m venv .venv
    & ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    & ".venv\Scripts\python.exe" -m pip install --quiet -r rent\requirements.txt
    & ".venv\Scripts\python.exe" -m playwright install chromium

    Write-Host "== 수집"
    if ($Sources -ne "") {
        & ".venv\Scripts\python.exe" rent\collect.py -v --trades 월세,전세 --only $Sources
    } else {
        & ".venv\Scripts\python.exe" rent\collect.py -v --trades 월세,전세
    }
    if ($LASTEXITCODE -ne 0) { throw "collect.py 실패 (종료 코드 $LASTEXITCODE)" }

    Write-Host "== 커밋·푸시"
    git config user.name "rent-local"
    git config user.email "rent-local@users.noreply.github.com"
    git add docs\rent\data docs\rent\list.md rent\data
    git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        Write-Host "변경 없음"
    } else {
        # 커밋 메시지 한글 깨짐 방지: UTF-8 파일로 전달
        $msgFile = Join-Path $env:TEMP "geunchae-commit-msg.txt"
        [System.IO.File]::WriteAllText($msgFile, ("chore: 매물 갱신 " + (Get-Date -Format "yyyy-MM-dd")), (New-Object System.Text.UTF8Encoding $false))
        git -c i18n.commitEncoding=utf-8 commit -F $msgFile
        git pull --rebase origin main
        git push origin HEAD:main
        if ($LASTEXITCODE -ne 0) { throw "git push 실패" }
        Write-Host "푸시 완료"
    }
}
finally {
    Stop-Transcript | Out-Null
    Set-Location $env:TEMP
    if (-not $Keep -and (Test-Path $work)) {
        Remove-Item -Recurse -Force $work
        Write-Host "임시 폴더 삭제: $work  (로그: $log)"
    }
}
