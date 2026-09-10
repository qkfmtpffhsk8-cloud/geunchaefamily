# rent — 이사 매물 조회·비교

## 목표
- 2026-11-30 이사. 과천 청약(2029 과천과천지구) 목표로 무주택 유지.
- 와이프와 공유하는 월세 매물 조회·비교 웹페이지 (과천 + 서울 관심 구).
- 상세 요구사항: `rent/요구사항.md` 를 반드시 먼저 읽는다.

## 구조
- `rent/collect.py`      : 네이버·직방·다방(현재 매물) + 서울시 열린데이터·국토부(실거래) 수집 → `docs/rent/data/*.json`, `docs/rent/list.md`
- `rent/config.json`     : 대상 구 목록(naver_gu), 실거래 개월 수, 기본 전환율, 요청 간격
- `rent/regions.json`    : 과천시 + 서울 25개 구의 법정동 목록·bbox (직방·다방 수집 범위·지역 판정용)
- `rent/requirements.txt`: python 의존성
- `rent/run_local.ps1`   : 집 PC(Windows)에서 하루 한 번 수집·푸시하는 스크립트 (Actions 에서 네이버가 막힐 때)
- `rent/data/`           : 원본(listings_raw.json, deals_all.json, CSV) 보관
- `docs/rent/index.html` : 단일 파일 웹페이지 (GitHub Pages)
- `docs/rent/list.md`    : 지역별 → 유형별 매물 목록 (collect.py 가 생성)
- `docs/rent/data/`      : listings.json(현재 매물), deals.json(실거래 3개월), meta.json(수집 시각·건수·소스 상태)
- `.github/workflows/collect.yml`: 매일 06:00 KST 수집 → main 커밋·푸시 (workflow_dispatch 로 수동 실행 가능)

## 실행
- `python rent/collect.py` (저장소 루트에서). 옵션: `--only naver,zigbang,dabang,seoul,molit`, `--skip ...`, `-v`
- 환경변수: `SEOUL_KEY`(서울 열린데이터광장), `MOLIT_KEY`(공공데이터포털 Decoding 키). GitHub Actions 에서는 repository secrets 로 주입. 없으면 해당 소스는 건너뛰고 기존 데이터 유지.
- Anthropic 클라우드 세션에서는 네이버·서울시 열린데이터가 연결 리셋으로 막힌다(IP 차단). 수집은 GitHub Actions 또는 집 PC 에서 실행.

## 소스별 메모
- 네이버: new.land API → m.land → playwright(chromium headless, HTTPS_PROXY 있으면 사용) 순 자동 전환.
- 직방: `apis.zigbang.com/house/property/v1/items/{villas,onerooms,officetels}?geohash=&salesTypes=월세` (precision 4 셀, 상한 없음) → `items/list` POST 는 15개씩. 아파트 매물 API 는 enum 미확인으로 미수집.
- 다방: `dabangapp.com/api/v5/room-list/category/{apt,house-villa,officetel,one-two}/bbox` 헤더 `csrf: token`, `D-Api-Version: 5.0.0`, `D-App-Version: 1`, `D-Call-Type: web`. `/region` 경로는 403.
- 아실(asil.kr): 아파트 매매·실거래 분석 중심, 매물은 HTML 렌더링·서버 500 → 제외.

## 규칙
- 수집은 필터 없이 전부. 필터는 웹페이지에서 사용자가 조절.
- 유형은 아파트·오피스텔·빌라·주택(원룸/단독/다가구) 4가지로 정규화.
- 같은 매물이 여러 소스에 있으면 지역+단지명(없으면 동)+전용㎡(반올림)+보증금+월세 로 합치고 `sites` 에 출처를 모두 남긴다.
- 소스 하나가 실패해도 나머지는 진행하고, 실패한 소스의 기존 데이터(rent/data/listings_raw.json)는 지우지 않는다.
- 수집 갱신 커밋은 "chore: 매물 갱신 YYYY-MM-DD".
