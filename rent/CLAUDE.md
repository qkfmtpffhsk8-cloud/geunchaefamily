# rent — 이사 매물 조회·비교

## 목표
- 2026-11-30 이사. 과천 청약(2029 과천과천지구) 목표로 무주택 유지.
- 와이프와 공유하는 월세 매물 조회·비교 웹페이지 (과천 + 서울 관심 구).
- 상세 요구사항: `rent/요구사항.md` 를 반드시 먼저 읽는다.

## 구조
- `rent/collect.py`      : 네이버·직방·다방(현재 매물) + 서울시 열린데이터·국토부(실거래) 수집 → `docs/rent/data/*.json`, `docs/rent/list.md`
- `rent/config.json`     : 대상 구 목록(naver_gu), 인접 지역 옵션(extra_regions, 기본 비움: '안양 평촌·관양동', '서초 방배·양재'), 실거래 개월 수, 기본 전환율, 요청 간격
- `rent/regions.json`    : 과천시 + 서울 25개 구 + 인접 부분지역(optional)의 법정동 목록·bbox (직방·다방·네이버 수집 범위·지역 판정용)
- `rent/requirements.txt`: python 의존성
- `rent/run_local.ps1`   : 집 PC(Windows)에서 하루 한 번 수집·푸시하는 스크립트 (Actions 에서 네이버가 막힐 때)
- `rent/data/`           : 원본(listings_raw.json, deals_all.json, CSV) 보관
- `docs/rent/index.html` : 단일 파일 웹페이지 (GitHub Pages)
- `docs/rent/list.md`    : 지역별 아파트·오피스텔 매물 표 + 빌라·주택 건수 (collect.py 가 생성)
- `docs/rent/data/`      : listings/r*.json(현재 매물, 지역별 분할·최소 필드), deals.json(실거래 3개월), meta.json(수집 시각·건수·소스 상태·region_files)
- `.github/workflows/collect.yml`: 매일 06:00 KST 수집 → main 커밋·푸시 (workflow_dispatch 로 수동 실행 가능)

## 실행
- `python rent/collect.py` (저장소 루트에서). 옵션: `--only naver,zigbang,dabang,seoul,molit`, `--skip ...`, `-v`
- 환경변수: `SEOUL_KEY`(서울 열린데이터광장), `MOLIT_KEY`(공공데이터포털 Decoding 키). GitHub Actions 에서는 repository secrets 로 주입. 없으면 실거래 소스는 경고 없이 조용히 건너뛴다(당분간 미사용).
- Anthropic 클라우드 세션과 GitHub Actions 둘 다 네이버가 막힌다(API 429, 모바일 타임아웃, chromium 연결 리셋). 네이버는 집 PC(run_local.ps1)에서만 가능. 직방·다방은 Actions 에서 정상.

## 소스별 메모
- 네이버: new.land API → m.land → playwright(chromium headless, HTTPS_PROXY 있으면 사용) 순 자동 전환.
- 직방: `apis.zigbang.com/house/property/v1/items/{villas,onerooms,officetels}?geohash=&salesTypes=월세` (precision 4 셀, 상한 없음) → `items/list` POST 는 15개씩. 아파트 매물 API 는 enum 미확인으로 미수집.
- 다방: `dabangapp.com/api/v5/room-list/category/{apt,house-villa,officetel,one-two}/bbox` 헤더 `csrf: token`, `D-Api-Version: 5.0.0`, `D-App-Version: 1`, `D-Call-Type: web`. `/region` 경로는 403.
- 아실(asil.kr): 아파트 매매·실거래 분석 중심, 매물은 HTML 렌더링·서버 500 → 제외.

## 규칙
- 수집은 필터 없이 전부. 필터는 웹페이지에서 사용자가 조절.
- 유형은 아파트·오피스텔·빌라·주택(원룸/단독/다가구) 4가지로 정규화. 과천은 네이버 유형 제한 없이(분양권·재건축·한옥·원룸 포함) 수집하고 미분류는 주택으로 넣되, 공장·창고·건물·토지·상가·사무실 등 비주거 유형은 제외한다.
- 웹페이지 비용 지표: 환산 총주거비(월세+보증금×전환율/12)와 실질 월 부담(월세 + 대출보증금×대출금리/12 + 자기부담보증금×전환율/12, 자기부담=min(보증금, 자기자금 상한)). 매물 카드에는 실질 월 부담·6년 총액(×72개월)·방 개수(features 텍스트 파싱)·전입 확인 배지만 표시하고, 영등포 비교 기준·차액·청약 기대값은 계산하지 않는다. 청약 관련 문구는 '먼저 읽기'의 결론 한 문단에만 둔다. 가정값(대출금리 4%, 자기자금 상한 5,000만, 가용 현금 2.6억)은 '비용 가정' 패널에서 조정, URL s_loanRate/s_ownCap/s_cash.
- 웹페이지 첫 화면 기본값: 과천+영등포, 보증금 3억 이하, 월세 300 이하. URL 에 조건이 있으면 URL 우선.
- 같은 매물이 여러 소스에 있으면 지역+단지명(없으면 동)+전용㎡(반올림)+보증금+월세 로 합치고 `sites` 에 출처를 모두 남긴다.
- 소스 하나가 실패해도 나머지는 진행하고, 실패한 소스의 기존 데이터(rent/data/listings_raw.json)는 지우지 않는다.
- 같은 단지·면적대(5㎡) 환산 총주거비 중앙값의 40% 미만 매물은 suspect=true(확인 필요)로 표시하고 웹페이지 기본 화면에서 숨긴다.
- 웹페이지는 meta.region_files 를 보고 선택한 지역 파일만 내려받는다.
- 과천 매물 전입 판정(features 텍스트): '전입불가·전입신고불가·업무용·사업자전용·사업자만·법인만·단기·숙박' 포함 → 수집 단계에서 제외(원본에도 저장 안 함). 오피스텔인데 '주거용·전입가능' 언급 없음, 또는 '무허가·불법' 포함 → movein='check'(전입 확인 필요 배지). 과천 외 지역은 판정하지 않는다.
- 매물 링크 형식: 네이버 `https://m.land.naver.com/article/info/{articleNo}`, 직방 `https://m.zigbang.com/home/{villa|oneroom|officetel}/items/{id}`, 다방 `https://www.dabangapp.com/room/{id}`. new.land/www.zigbang 형식은 모바일에서 메인·앱스토어로 튕기므로 쓰지 않는다(수집 시 구형 링크는 자동 변환).
- 수집 갱신 커밋은 "chore: 매물 갱신 YYYY-MM-DD".
