#!/bin/bash
# Claude Code 웹 세션 시작 시 의존성 설치 (playwright chromium 포함)
set -uo pipefail

# 원격(Claude Code on the web) 환경에서만 실행
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}"

pip install -r rent/requirements.txt || pip install -r requirements.txt || true
python -m playwright install --with-deps chromium || true
