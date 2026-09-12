# cheongyak — 과천 청약 준비 페이지

- `docs/cheongyak/index.html` : 단일 파일 웹페이지 (GitHub Pages, 모바일 우선, 빌드 도구 없음). 결론 → 타임라인 → 자격 체크리스트 → 통장 시뮬레이터 → 청약 경로 표 → 물량 시나리오 → 자금 계획 시뮬레이터 → 리스크 → 다음 할 일 → 링크 순.
- 개인 숫자(소득·자산·저축총액·현금·전입일)는 코드에 넣지 않는다. 전부 사용자 입력칸이며 localStorage(`cy.v1`)에만 저장. 코드에 두는 기본값은 계획 가정(월 납입 25만, 공고 2029-12, 보증금 대출 3.6억, 금리 3.8%, 월세 80, 관리비 8, 분양가 11.5억)뿐.
- 날짜는 Asia/Seoul 기준(Intl timeZone). 마일스톤 날짜·당첨선·배분 비율은 가정이며 공고문 기준으로 갱신한다.
- 판단 근거 문서는 `docs/decision.md`(md.html 로 표시), 매물 페이지는 `docs/rent/`.
- `cheongyak/news.py` : Google News RSS(검색어 10개)로 과천 청약 소식 수집 → `docs/cheongyak/data/news.json` (365일 보관, 제목 정규화 중복 제거, 키워드 규칙 분류 cats: 물량·일정·제도·과천시·LH·국토부·기타, 여러 분류 가능). RSS description 은 제목만 있어 미리보기(desc)는 대개 비어 있음.
- `.github/workflows/news.yml` : 매주 월요일 06:00 KST(일 21:00 UTC) 수집 → main 커밋 "chore: 청약 소식 갱신 YYYY-MM-DD". workflow_dispatch 가능.
- Claude Code 루틴 "근채패밀리 청약 소식 주간 요약" : 매주 월요일 07:00 KST(일 22:00 UTC) 새 세션에서 최근 7일 기사로 `docs/cheongyak/data/summary.md` 갱신(제목 1행 + 불릿 3~5개 + '타임라인 변경 필요: 예/아니오' 1행) → main 커밋 "chore: 청약 소식 요약 YYYY-MM-DD".
- 페이지 소식 보드: 이번 주 요약 카드(접기 상태 localStorage) · 제목 검색 · 탭(전체/물량/일정/제도/과천시/LH·국토부/⭐) · 최근 90일 카드 10개씩 더 보기 · 90일 초과는 '지난 소식' 접이식. 최근 7일 안 읽은 기사는 파란 띠, 탭하면 새 탭 원문 + 읽음. 읽음·북마크는 `cy.v1`(newsRead/newsStar).
- 메인 `docs/index.html` 은 대시보드: 매물(과천 후보 = 매물 페이지 기본 조건 재계산, 마지막 수집), 청약 준비(다음 마일스톤 D-day·체크 진행률, cy.v1 읽음), 이번 주 소식(새 기사 수·요약 첫 불릿·타임라인 변경 여부). `docs/manifest.json` + `docs/icons/` 로 홈화면 추가(이름 근채패밀리), 세 페이지 모두 연결.
