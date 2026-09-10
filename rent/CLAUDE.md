# rent — 이사 매물 조회·비교

## 목표
- 2026-11-30 이사. 과천 청약(2029 과천과천지구) 목표로 무주택 유지.
- 와이프와 공유하는 월세 매물 조회·비교 웹페이지 (과천 + 서울 전 지역).
- 상세 요구사항: `rent/요구사항.md` 를 반드시 먼저 읽는다.

## 구조
- `rent/collect.py`      : 서울시 열린데이터 + 국토부 실거래 + 네이버부동산 수집 → `docs/rent/data/*.json`
- `rent/config.json`     : 네이버 수집 대상 구 목록, 실거래 개월 수, 기본 전환율
- `rent/requirements.txt`: python 의존성
- `rent/data/`           : 원본 CSV·실거래 전체(6개월) 보관
- `docs/rent/index.html` : 단일 파일 웹페이지 (GitHub Pages)
- `docs/rent/data/`      : listings.json(현재 매물), deals.json(실거래 3개월), meta.json(수집 시각·건수·소스 상태)

## 실행
- `python rent/collect.py` (저장소 루트에서). 옵션: `--skip-naver`, `--skip-deals`, `-v`
- 환경변수: `SEOUL_KEY`(서울 열린데이터광장), `MOLIT_KEY`(공공데이터포털 Decoding 키). 없으면 해당 소스는 건너뛰고 기존 데이터 유지.
- 필요 허용 도메인: `new.land.naver.com`, `m.land.naver.com`, `*.naver.com`, `*.pstatic.net`, `openapi.seoul.go.kr`, `apis.data.go.kr`

## 규칙
- 네이버 API 차단(401/403) 시 m.land → playwright(chromium headless) 순으로 자동 전환.
- 수집은 필터 없이 전부. 필터는 웹페이지에서 사용자가 조절.
- 소스 하나가 실패해도 나머지는 진행하고, 실패한 소스의 기존 JSON은 지우지 않는다.
- 수집 갱신 커밋은 "chore: 매물 갱신 YYYY-MM-DD".
