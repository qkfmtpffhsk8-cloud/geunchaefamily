#!/bin/bash
# Claude Code 웹 세션 시작 시 의존성 설치 (playwright chromium 포함)
set -uo pipefail

# 원격(Claude Code on the web) 환경에서만 실행
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}"

pip install -r rent/requirements.txt || pip install -r requirements.txt || true

# 브라우저 디렉토리에 chromium이 이미 있으면 다운로드 건너뜀 (약 300MB 절약)
BROWSERS_DIR="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"
if ls -d "$BROWSERS_DIR"/chromium-* >/dev/null 2>&1; then
  echo "chromium 이미 설치됨 ($BROWSERS_DIR) - 다운로드 건너뜀"
else
  python -m playwright install --with-deps chromium || true
fi
