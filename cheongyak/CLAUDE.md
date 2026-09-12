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
- 고정값: 매도 잔금·무주택 시작 2026-11-30, 청약통장 가입 2026-07-23 (state.json `fixed`). 24회 납입 예정 2028-06, 가입 2년 2028-07-23 은 여기서 계산한다. 시뮬레이터 기본 납입 횟수·저축총액도 가입일부터 자동(월 25만 가정).
- `docs/cheongyak/state.json` = 확정 상태 (`updated`, `fixed`, `inputs`: movein·annMonth·saveMonthly·incomeYear, `checks`, `milestones_prev`). 페이지는 이를 기본값으로 읽고 localStorage(cy.v1)가 덮어쓴다. `updated` 가 바뀌면 각 기기에 한 번 강제 적용(cy.v1.base). 개인 숫자(저축총액·현금)는 넣지 않는다.
- 세션에서 "통장 25만 변경 완료로 기록해줘" 같은 요청을 받으면 state.json 의 `checks`(키: nohouse·save25·movein·car·nowin·wife·autopay·headhh·precheck·noright·income·lease·rebuild·other3·fund·resale·baby, 할 일: todo25·todoMove·todoVol·todoWife·todoCash)나 `inputs` 를 직접 수정하고 `updated` 를 오늘(Asia/Seoul)로 올린 뒤, CHANGELOG.md 에 `## YYYY-MM-DD` 아래 `- [상태 변경] …` 로 기록하고 main 에 커밋한다. 마일스톤 날짜(전입일·공고 시점)를 바꿀 때는 `milestones_prev` 에 이전 날짜를 남긴다(페이지가 취소선으로 표시).
- `docs/cheongyak/inbox/*.json` (페이지 '상태 내보내기' 결과) 은 주간 루틴이 state.json 에 병합하고 삭제한다(`exported` 늦은 것 우선).
- `docs/cheongyak/CHANGELOG.md`: `## YYYY-MM-DD` 섹션 + `- [상태 변경]|[소식 반영]|[가정값 수정] …` 불릿. 주간 루틴이 (a) state.json 변경, (b) 요약에서 '타임라인 변경 필요: 예' 항목, (c) 페이지·가정값 수정을 기록. 페이지 하단 '변경 이력' 은 최근 8주만, 대시보드는 최근 7일 항목 수를 '지난주 변경 N건' 으로 표시.
