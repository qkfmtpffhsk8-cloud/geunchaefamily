#!/usr/bin/env python3
"""근채패밀리 월세 매물 수집기.

소스
  1. 서울시 열린데이터광장 tbLnOpendataRentV (실거래, 서울 25개 구)  → SEOUL_KEY
  2. 국토부 실거래 API 아파트/오피스텔/연립다세대 전월세 (실거래, 과천시) → MOLIT_KEY
  3. 네이버부동산 (현재 매물, 과천 전체 + config.json naver_gu)
     - new.land.naver.com API → 실패 시 m.land.naver.com → 실패 시 playwright(chromium headless)

출력
  docs/rent/data/listings.json  현재 매물 (필터 없이 전부)
  docs/rent/data/deals.json     실거래 (최근 months_in_json 개월)
  docs/rent/data/meta.json      수집 시각, 건수, 소스별 상태
  rent/data/*.csv               원본 보관 (실거래 months_deals 개월 전체)

키가 없거나 소스가 실패해도 나머지 소스는 계속 진행하며,
실패한 소스의 기존 데이터는 지우지 않고 유지한다.

사용:  python rent/collect.py [--skip-naver] [--skip-deals] [--verbose]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

KST = ZoneInfo("Asia/Seoul")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIG_PATH = HERE / "config.json"
RAW_DIR = HERE / "data"
OUT_DIR = ROOT / "docs" / "rent" / "data"

SEOUL_GU = [
    "종로구", "중구", "용산구", "성동구", "광진구", "동대문구", "중랑구", "성북구", "강북구",
    "도봉구", "노원구", "은평구", "서대문구", "마포구", "양천구", "강서구", "구로구", "금천구",
    "영등포구", "동작구", "관악구", "서초구", "강남구", "송파구", "강동구",
]
GWACHEON_LAWD = "41290"
TYPES = ("아파트", "오피스텔", "빌라")

UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
UA_DESKTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

VERBOSE = False


def log(*a):
    print(*a, flush=True)


def vlog(*a):
    if VERBOSE:
        print("   ", *a, flush=True)


def now_kst() -> datetime:
    return datetime.now(KST)


def load_config() -> dict:
    cfg = {"naver_gu": [], "months_deals": 6, "months_in_json": 3, "conversion_rate_default": 5.5}
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return cfg


def month_list(months: int, base: datetime | None = None) -> list[str]:
    """base 포함 최근 months 개월의 YYYYMM 목록 (오래된 순)."""
    base = base or now_kst()
    y, m = base.year, base.month
    out = []
    for _ in range(months):
        out.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def cutoff_ym(months: int) -> str:
    return month_list(months)[0]


# ---------------------------------------------------------------- 공통 유틸
def to_int(v, default=None):
    if v is None:
        return default
    s = str(v).replace(",", "").strip()
    if s == "" or s == "-":
        return default
    try:
        return int(float(s))
    except ValueError:
        return default


def to_float(v, default=None):
    if v is None:
        return default
    s = str(v).replace(",", "").strip()
    if s == "":
        return default
    try:
        return round(float(s), 2)
    except ValueError:
        return default


def parse_korean_price(v) -> int | None:
    """'1억 5,000' / '5,000' / 15000 → 만원 정수."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).replace(",", "").replace(" ", "").strip()
    if not s:
        return None
    total = 0
    m = re.match(r"^(?:(\d+)억)?(\d+)?$", s)
    if not m:
        return to_int(s)
    if m.group(1):
        total += int(m.group(1)) * 10000
    if m.group(2):
        total += int(m.group(2))
    return total


def floor_from_info(info) -> int | None:
    """'5/15' → 5, '고/15' → None, '저' → None."""
    if not info:
        return None
    first = str(info).split("/")[0].strip()
    return to_int(first)


def norm_type(name: str | None) -> str | None:
    if not name:
        return None
    n = str(name)
    if "아파트" in n:
        return "아파트"
    if "오피스텔" in n:
        return "오피스텔"
    if any(k in n for k in ("빌라", "연립", "다세대")):
        return "빌라"
    return None


def make_id(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def record(**kw) -> dict:
    base = {
        "id": None, "source": None, "region": None, "dong": None, "complex": None,
        "type": None, "area_m2": None, "floor": None, "deposit": None, "rent": None,
        "built_year": None, "date": None, "features": None, "url": None,
        "lat": None, "lng": None,
    }
    base.update(kw)
    return base


class SourceStatus:
    def __init__(self):
        self.status: dict[str, dict] = {}

    def set(self, name: str, ok: bool, count: int = 0, note: str = ""):
        self.status[name] = {"ok": ok, "count": count, "note": note}
        log(f"[{name}] {'OK' if ok else 'FAIL'} {count}건 {note}")


# ---------------------------------------------------------------- 서울시 열린데이터
SEOUL_FIELD_MAP = {
    # 신규 필드명: 구 필드명
    "CGG_NM": "SGG_NM", "STDG_NM": "BJDONG_NM", "FLR": "FLR_NO", "CTRT_DAY": "CNTRCT_DE",
    "RENT_SE": "RENT_GBN", "RENT_AREA": "RENT_AREA", "GRFE": "RENT_GTN", "RTFE": "RENT_FEE",
    "BLDG_NM": "BLDG_NM", "ARCH_YR": "BUILD_YEAR", "BLDG_USG": "HOUSE_GBN_NM",
}


def seoul_get(row: dict, key: str):
    return row.get(key) if row.get(key) not in (None, "") else row.get(SEOUL_FIELD_MAP.get(key, key))


def collect_seoul(key: str, months: int, status: SourceStatus, max_rows: int = 400_000) -> list[dict]:
    cutoff = cutoff_ym(months)
    years = sorted({ym[:4] for ym in month_list(months)})
    out: list[dict] = []
    fetched = 0
    for year in years:
        start = 1
        step = 1000
        while fetched < max_rows:
            url = f"http://openapi.seoul.go.kr:8088/{key}/json/tbLnOpendataRentV/{start}/{start + step - 1}/{year}/"
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            data = r.json()
            body = data.get("tbLnOpendataRentV")
            if not body:
                msg = data.get("RESULT", {}).get("MESSAGE", "") if isinstance(data, dict) else ""
                if "해당하는 데이터가 없습니다" in msg or "INFO-200" in json.dumps(data, ensure_ascii=False):
                    break
                raise RuntimeError(f"서울 API 응답 이상: {json.dumps(data, ensure_ascii=False)[:200]}")
            rows = body.get("row", [])
            fetched += len(rows)
            for row in rows:
                if "월세" not in str(seoul_get(row, "RENT_SE") or ""):
                    continue
                typ = norm_type(seoul_get(row, "BLDG_USG"))
                if typ is None:
                    continue
                day = re.sub(r"\D", "", str(seoul_get(row, "CTRT_DAY") or ""))
                if len(day) < 6 or day[:6] < cutoff:
                    continue
                gu = seoul_get(row, "CGG_NM")
                dong = seoul_get(row, "STDG_NM")
                bldg = seoul_get(row, "BLDG_NM") or ""
                area = to_float(seoul_get(row, "RENT_AREA"))
                floor = to_int(seoul_get(row, "FLR"))
                dep = to_int(seoul_get(row, "GRFE"))
                rent = to_int(seoul_get(row, "RTFE"))
                out.append(record(
                    id=make_id("seoul", gu, dong, bldg, area, floor, dep, rent, day),
                    source="실거래", region=gu, dong=dong, complex=bldg or None, type=typ,
                    area_m2=area, floor=floor, deposit=dep, rent=rent,
                    built_year=to_int(seoul_get(row, "ARCH_YR")),
                    date=f"{day[:4]}-{day[4:6]}", features=None, url=None,
                ))
            vlog(f"seoul {year} {start}~ rows={len(rows)} kept={len(out)}")
            if len(rows) < step:
                break
            start += step
            time.sleep(0.2)
    status.set("서울 실거래", True, len(out), f"{years[0]}~{years[-1]} 원본 {fetched}행")
    return out


# ---------------------------------------------------------------- 국토부 실거래 (과천)
MOLIT_SERVICES = [
    ("RTMSDataSvcAptRent/getRTMSDataSvcAptRent", "아파트", "aptNm"),
    ("RTMSDataSvcOffiRent/getRTMSDataSvcOffiRent", "오피스텔", "offiNm"),
    ("RTMSDataSvcRHRent/getRTMSDataSvcRHRent", "빌라", "mhouseNm"),
]


def molit_text(item: ET.Element, *names):
    for n in names:
        el = item.find(n)
        if el is not None and el.text and el.text.strip():
            return el.text.strip()
    return None


def collect_molit(key: str, months: int, status: SourceStatus) -> list[dict]:
    out: list[dict] = []
    for path, typ, name_tag in MOLIT_SERVICES:
        for ym in month_list(months):
            page = 1
            while True:
                url = f"https://apis.data.go.kr/1613000/{path}"
                params = {"serviceKey": key, "LAWD_CD": GWACHEON_LAWD, "DEAL_YMD": ym,
                          "pageNo": page, "numOfRows": 1000}
                r = requests.get(url, params=params, timeout=60)
                r.raise_for_status()
                root = ET.fromstring(r.content)
                code = root.findtext(".//resultCode") or root.findtext(".//returnReasonCode")
                if code not in (None, "00", "000"):
                    msg = root.findtext(".//resultMsg") or root.findtext(".//returnAuthMsg")
                    raise RuntimeError(f"국토부 API 오류 {code}: {msg}")
                items = root.findall(".//item")
                for it in items:
                    rent = to_int(molit_text(it, "monthlyRent", "월세금액"), 0)
                    if not rent:
                        continue  # 전세 제외
                    dong = molit_text(it, "umdNm", "법정동")
                    name = molit_text(it, name_tag, "아파트", "단지", "연립다세대")
                    area = to_float(molit_text(it, "excluUseAr", "전용면적"))
                    floor = to_int(molit_text(it, "floor", "층"))
                    dep = to_int(molit_text(it, "deposit", "보증금액"))
                    y = molit_text(it, "dealYear", "년") or ym[:4]
                    m = molit_text(it, "dealMonth", "월") or ym[4:]
                    date = f"{y}-{int(m):02d}"
                    out.append(record(
                        id=make_id("molit", typ, dong, name, area, floor, dep, rent, date,
                                   molit_text(it, "dealDay", "일")),
                        source="실거래", region="과천시", dong=dong, complex=name, type=typ,
                        area_m2=area, floor=floor, deposit=dep, rent=rent,
                        built_year=to_int(molit_text(it, "buildYear", "건축년도")),
                        date=date, features=None, url=None,
                    ))
                total = to_int(root.findtext(".//totalCount"), 0)
                vlog(f"molit {typ} {ym} p{page} items={len(items)} total={total}")
                if page * 1000 >= total or not items:
                    break
                page += 1
                time.sleep(0.2)
    status.set("과천 실거래", True, len(out), f"최근 {months}개월")
    return out


# ---------------------------------------------------------------- 네이버부동산
NAVER_TYPES = "APT:OPST:VL"   # 아파트:오피스텔:빌라
NAVER_TRADE = "B2"            # 월세


class NaverBlocked(Exception):
    pass


class NaverClient:
    """new.land API → m.land 모바일 API → playwright 순으로 자동 전환."""

    def __init__(self):
        self.mode = "new"
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA_DESKTOP, "Referer": "https://new.land.naver.com/",
                               "Accept": "application/json, text/plain, */*",
                               "Accept-Language": "ko-KR,ko;q=0.9"})
        self._pw = None
        self._page = None

    # ---- HTTP
    def _get(self, url, params=None):
        r = self.s.get(url, params=params, timeout=30)
        if r.status_code in (401, 403, 429) or r.status_code >= 500:
            raise NaverBlocked(f"HTTP {r.status_code} {url}")
        r.raise_for_status()
        try:
            return r.json()
        except ValueError as e:
            raise NaverBlocked(f"JSON 아님 {url}: {r.text[:80]}") from e

    # ---- playwright
    def _ensure_playwright(self):
        if self._page is not None:
            return
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        ctx = self._browser.new_context(user_agent=UA_DESKTOP, locale="ko-KR",
                                        viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        captured = {}

        def on_request(req):
            if "new.land.naver.com/api/" in req.url:
                auth = req.headers.get("authorization")
                if auth:
                    captured["auth"] = auth

        page.on("request", on_request)
        page.goto("https://new.land.naver.com/houses?ms=37.4292,126.9876,15&a=APT:OPST:VL&b=B2",
                  wait_until="networkidle", timeout=90_000)
        page.wait_for_timeout(1500)
        self._page = page
        if captured.get("auth"):
            self.s.headers["Authorization"] = captured["auth"]
            for c in ctx.cookies():
                self.s.cookies.set(c["name"], c["value"], domain=c.get("domain"))
            vlog("playwright: Authorization 토큰 확보")

    def _page_fetch(self, url, params):
        """브라우저 컨텍스트 안에서 fetch (쿠키·토큰 자동)."""
        self._ensure_playwright()
        full = url + ("?" + requests.compat.urlencode(params) if params else "")
        auth = self.s.headers.get("Authorization", "")
        return self._page.evaluate(
            """async ([u, a]) => {
                const h = a ? {authorization: a} : {};
                const r = await fetch(u, {headers: h, credentials: 'include'});
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return await r.json();
            }""", [full, auth])

    def close(self):
        try:
            if self._pw:
                self._browser.close()
                self._pw.stop()
        except Exception:
            pass

    # ---- 자동 전환 래퍼
    def call(self, kind: str, **kw):
        """kind: 'regions'(cortarNo) | 'articles'(cortarNo, page)"""
        attempts = ["new", "mobile", "pw_token", "pw_page"]
        start = attempts.index(self.mode)
        last = None
        for mode in attempts[start:]:
            try:
                if mode == "new":
                    res = self._new(kind, **kw)
                elif mode == "mobile":
                    res = self._mobile(kind, **kw)
                elif mode == "pw_token":
                    self._ensure_playwright()
                    if "Authorization" not in self.s.headers:
                        raise NaverBlocked("토큰 미확보")
                    res = self._new(kind, **kw)
                else:
                    res = self._new(kind, fetch=self._page_fetch, **kw)
                if mode != self.mode:
                    log(f"[네이버] 전환: {self.mode} → {mode}")
                    self.mode = mode
                return res
            except (NaverBlocked, requests.RequestException, ImportError) as e:
                last = e
                vlog(f"naver {mode} 실패: {e}")
                continue
        raise NaverBlocked(f"모든 방식 실패: {last}")

    # ---- new.land.naver.com
    def _new(self, kind, fetch=None, **kw):
        fetch = fetch or self._get
        if kind == "regions":
            d = fetch("https://new.land.naver.com/api/regions/list", {"cortarNo": kw["cortarNo"]})
            return [{"cortarNo": r["cortarNo"], "name": r["cortarName"]} for r in d.get("regionList", [])]
        d = fetch("https://new.land.naver.com/api/articles", {
            "cortarNo": kw["cortarNo"], "order": "rank", "realEstateType": NAVER_TYPES,
            "tradeType": NAVER_TRADE, "tag": "::::::::", "rentPriceMin": 0, "rentPriceMax": 900000000,
            "priceMin": 0, "priceMax": 900000000, "areaMin": 0, "areaMax": 900000000,
            "priceType": "RETAIL", "page": kw["page"], "articleState": "",
        })
        arts = []
        for a in d.get("articleList", []):
            arts.append({
                "no": a.get("articleNo"), "name": a.get("articleName"), "type": a.get("realEstateTypeName"),
                "floor": a.get("floorInfo"), "deposit": a.get("dealOrWarrantPrc"), "rent": a.get("rentPrc"),
                "area": a.get("area2") or a.get("area1"), "features": a.get("articleFeatureDesc"),
                "building": a.get("buildingName"), "lat": a.get("latitude"), "lng": a.get("longitude"),
                "confirm": a.get("articleConfirmYmd"), "tags": a.get("tagList") or [],
                "direction": a.get("direction"),
            })
        return {"list": arts, "more": bool(d.get("isMoreData"))}

    # ---- m.land.naver.com
    def _mobile(self, kind, **kw):
        h = {"User-Agent": UA_MOBILE, "Referer": "https://m.land.naver.com/"}
        if kind == "regions":
            r = self.s.get("https://m.land.naver.com/map/getRegionList",
                           params={"cortarNo": kw["cortarNo"]}, headers=h, timeout=30)
            if r.status_code != 200:
                raise NaverBlocked(f"HTTP {r.status_code} m.land regions")
            lst = r.json().get("result", {}).get("list", [])
            return [{"cortarNo": x.get("CortarNo"), "name": x.get("CortarNm")} for x in lst]
        r = self.s.get("https://m.land.naver.com/cluster/ajax/articleList",
                       params={"rletTpCd": NAVER_TYPES, "tradTpCd": NAVER_TRADE, "cortarNo": kw["cortarNo"],
                               "page": kw["page"], "sort": "rank"}, headers=h, timeout=30)
        if r.status_code != 200:
            raise NaverBlocked(f"HTTP {r.status_code} m.land articles")
        d = r.json()
        arts = []
        for a in d.get("body", []):
            arts.append({
                "no": a.get("atclNo"), "name": a.get("atclNm"), "type": a.get("rletTpNm"),
                "floor": a.get("flrInfo"), "deposit": a.get("hanPrc") or a.get("prc"), "rent": a.get("rentPrc"),
                "area": a.get("spc2") or a.get("spc1"), "features": a.get("atclFetrDesc"),
                "building": a.get("bildNm"), "lat": a.get("lat"), "lng": a.get("lng"),
                "confirm": a.get("atclCfmYmd"), "tags": a.get("tagList") or [], "direction": a.get("direction"),
            })
        return {"list": arts, "more": bool(d.get("more"))}


def naver_article_url(no) -> str:
    return f"https://new.land.naver.com/articles/{no}"


def normalize_naver(a: dict, region: str, dong: str) -> dict | None:
    typ = norm_type(a.get("type"))
    if typ is None:
        return None
    confirm = re.sub(r"\D", "", str(a.get("confirm") or ""))
    if len(confirm) == 6:      # YYMMDD
        confirm = "20" + confirm
    date = f"{confirm[:4]}-{confirm[4:6]}-{confirm[6:8]}" if len(confirm) == 8 else now_kst().strftime("%Y-%m-%d")
    feats = [str(a.get("features") or "").strip()] + [str(t) for t in a.get("tags", [])]
    if a.get("direction"):
        feats.append(str(a["direction"]))
    features = " · ".join(f for f in feats if f) or None
    name = a.get("name") or a.get("building")
    return record(
        id=str(a.get("no")), source="현재매물", region=region, dong=dong, complex=name, type=typ,
        area_m2=to_float(a.get("area")), floor=floor_from_info(a.get("floor")),
        deposit=parse_korean_price(a.get("deposit")), rent=parse_korean_price(a.get("rent")),
        built_year=None, date=date, features=features, url=naver_article_url(a.get("no")),
        lat=to_float(a.get("lat")), lng=to_float(a.get("lng")),
    )


def collect_naver(cfg: dict, status: SourceStatus, max_pages: int = 200) -> list[dict]:
    client = NaverClient()
    out: list[dict] = []
    seen: set[str] = set()
    targets: list[tuple[str, str]] = []  # (region name, cortarNo)
    try:
        # 과천시: 경기(4100000000) 하위에서 찾기
        gg = client.call("regions", cortarNo="4100000000")
        for r in gg:
            if r["name"] == "과천시":
                targets.append(("과천시", r["cortarNo"]))
        seoul = client.call("regions", cortarNo="1100000000")
        want = set(cfg.get("naver_gu", []))
        for r in seoul:
            if r["name"] in want:
                targets.append((r["name"], r["cortarNo"]))
        missing = want - {t[0] for t in targets}
        if missing:
            log(f"[네이버] config.naver_gu 중 못 찾음: {sorted(missing)}")
        if not targets:
            raise NaverBlocked("수집 대상 지역을 찾지 못함")

        for region, gu_no in targets:
            dongs = client.call("regions", cortarNo=gu_no) or [{"cortarNo": gu_no, "name": ""}]
            n_region = 0
            for d in dongs:
                page = 1
                while page <= max_pages:
                    res = client.call("articles", cortarNo=d["cortarNo"], page=page)
                    for a in res["list"]:
                        rec = normalize_naver(a, region, d["name"] or None)
                        if rec and rec["id"] not in seen:
                            seen.add(rec["id"])
                            out.append(rec)
                            n_region += 1
                    if not res["more"] or not res["list"]:
                        break
                    page += 1
                    time.sleep(0.3)
                time.sleep(0.2)
            log(f"[네이버] {region}: {n_region}건 (동 {len(dongs)}개, 방식 {client.mode})")
        status.set("네이버 현재매물", True, len(out), f"방식 {client.mode}")
    finally:
        client.close()
    return out


# ---------------------------------------------------------------- 저장
def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def write_csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = list(record().keys())
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in cols})


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def run(args) -> dict:
    global VERBOSE
    VERBOSE = args.verbose
    cfg = load_config()
    status = SourceStatus()
    started = now_kst()
    log(f"== 수집 시작 {started:%Y-%m-%d %H:%M:%S %Z} | 대상 구: {cfg.get('naver_gu')}")

    prev_listings = read_json(OUT_DIR / "listings.json", [])
    prev_deals_raw = read_json(RAW_DIR / "deals_all.json", [])
    if not isinstance(prev_listings, list):
        prev_listings = []
    if not isinstance(prev_deals_raw, list):
        prev_deals_raw = []

    months_deals = int(cfg.get("months_deals", 6))
    months_json = int(cfg.get("months_in_json", 3))

    # --- 실거래
    deals_seoul = [d for d in prev_deals_raw if d.get("region") != "과천시"]
    deals_gc = [d for d in prev_deals_raw if d.get("region") == "과천시"]
    if not args.skip_deals:
        seoul_key = os.environ.get("SEOUL_KEY", "").strip()
        molit_key = os.environ.get("MOLIT_KEY", "").strip()
        if seoul_key:
            try:
                deals_seoul = collect_seoul(seoul_key, months_deals, status)
            except Exception as e:
                status.set("서울 실거래", False, len(deals_seoul), f"실패, 기존 데이터 유지: {e}")
                vlog(traceback.format_exc())
        else:
            status.set("서울 실거래", False, len(deals_seoul), "SEOUL_KEY 없음 (건너뜀, 기존 데이터 유지)")
        if molit_key:
            try:
                deals_gc = collect_molit(molit_key, months_deals, status)
            except Exception as e:
                status.set("과천 실거래", False, len(deals_gc), f"실패, 기존 데이터 유지: {e}")
                vlog(traceback.format_exc())
        else:
            status.set("과천 실거래", False, len(deals_gc), "MOLIT_KEY 없음 (건너뜀, 기존 데이터 유지)")

    # --- 네이버 현재 매물
    listings = prev_listings
    if not args.skip_naver:
        try:
            listings = collect_naver(cfg, status)
        except Exception as e:
            status.set("네이버 현재매물", False, len(prev_listings), f"실패, 기존 데이터 유지: {e}")
            vlog(traceback.format_exc())

    # --- 정리 · 저장
    cutoff_all = cutoff_ym(months_deals)
    cutoff_json = cutoff_ym(months_json)
    deals_all = [d for d in deals_seoul + deals_gc
                 if d.get("date") and d["date"].replace("-", "")[:6] >= cutoff_all]
    deals_all.sort(key=lambda d: (d.get("date") or "", d.get("region") or ""), reverse=True)
    deals_json = [d for d in deals_all if d["date"].replace("-", "")[:6] >= cutoff_json]
    listings.sort(key=lambda d: (d.get("date") or ""), reverse=True)

    write_json(OUT_DIR / "listings.json", listings)
    write_json(OUT_DIR / "deals.json", deals_json)
    write_json(RAW_DIR / "deals_all.json", deals_all)
    write_csv(RAW_DIR / "listings.csv", listings)
    write_csv(RAW_DIR / "deals.csv", deals_all)

    def by(items, key):
        c: dict[str, int] = {}
        for it in items:
            k = it.get(key) or "기타"
            c[k] = c.get(k, 0) + 1
        return dict(sorted(c.items(), key=lambda x: -x[1]))

    finished = now_kst()
    meta = {
        "collected_at": finished.strftime("%Y-%m-%d %H:%M:%S"),
        "collected_at_iso": finished.isoformat(),
        "timezone": "Asia/Seoul",
        "duration_sec": round((finished - started).total_seconds(), 1),
        "counts": {"listings": len(listings), "deals": len(deals_json), "deals_raw": len(deals_all)},
        "listings_by_region": by(listings, "region"),
        "deals_by_region": by(deals_json, "region"),
        "months_deals": months_deals, "months_in_json": months_json,
        "conversion_rate_default": cfg.get("conversion_rate_default", 5.5),
        "naver_gu": cfg.get("naver_gu", []),
        "sources": status.status,
    }
    write_json(OUT_DIR / "meta.json", meta)

    log("== 결과")
    log(f"   현재매물 {len(listings)}건  {meta['listings_by_region']}")
    log(f"   실거래(최근 {months_json}개월) {len(deals_json)}건 / 원본 {len(deals_all)}건  {meta['deals_by_region']}")
    log(f"   저장: {OUT_DIR}/listings.json, deals.json, meta.json | {RAW_DIR}/*.csv")
    log(f"   완료 {finished:%Y-%m-%d %H:%M:%S %Z} ({meta['duration_sec']}s)")
    return meta


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--skip-naver", action="store_true", help="네이버 현재매물 수집 생략")
    p.add_argument("--skip-deals", action="store_true", help="실거래(서울·국토부) 수집 생략")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)
    meta = run(args)
    failed = [k for k, v in meta["sources"].items() if not v["ok"]]
    if failed:
        log(f"!! 실패/생략 소스: {failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
