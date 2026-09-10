# 근채패밀리

가족 주거·자산 프로젝트 모음. 업무(에즈금융) 코드·계정·저장소와 절대 섞지 않는다.

## 폴더 구조 (주제별)
- `<주제>/`        : 주제별 스크립트·설정·요구사항 (예: `rent/` 이사 매물)
- `docs/index.html`: 주제 목록 메인 페이지 (GitHub Pages 루트)
- `docs/<주제>/`   : 주제별 웹페이지와 데이터 (`docs/rent/index.html`, `docs/rent/data/*.json`)
- 각 주제 폴더의 `CLAUDE.md` 와 `요구사항.md` 를 먼저 읽고 작업한다.

## 공통 규칙
- 날짜·시각 계산은 전부 Asia/Seoul 기준 (TZ=Asia/Seoul, python은 zoneinfo("Asia/Seoul")). UTC/로컬 naive 시각 금지.
- API 키·비밀값은 환경변수만. 코드·커밋에 키 금지.
- 웹페이지는 모바일 우선, 외부 빌드 도구 없이 순수 HTML+JS 단일 파일. GitHub Pages(main 의 `docs/`)로 배포.
- 커밋 메시지는 한국어, 간결하게.
- 세션 시작 훅(`.claude/hooks/session-start.sh`)이 의존성과 playwright chromium을 설치한다.
