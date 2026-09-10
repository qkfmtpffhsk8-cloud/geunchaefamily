#!/usr/bin/env python3
"""근채패밀리 월세 매물 수집기.

현재 매물 (호가)
  - 네이버부동산  new.land.naver.com API → 실패 시 m.land.naver.com → 실패 시 playwright(chromium headless)
  - 직방          apis.zigbang.com  /house/property/v1/items/{villas,onerooms,officetels} (geohash) + items/list
  - 다방          dabangapp.com     /api/v5/room-list/category/{apt,house-villa,officetel,one-two}/bbox
실거래
  - 서울시 열린데이터광장 tbLnOpendataRentV (서울 25개 구)          → SEOUL_KEY
  - 국토부 실거래 API 아파트/오피스텔/연립다세대/단독다가구 (과천시) → MOLIT_KEY

대상 지역: 과천시 + config.json naver_gu (서울 관심 구). 유형: 아파트·오피스텔·빌라·주택(원룸/단독/다가구).
지역별 법정동 목록·bbox 는 rent/regions.json.

출력
  docs/rent/data/listings.json  현재 매물 (필터 없이 전부, 소스 간 중복 제거·출처 병합)
  docs/rent/data/deals.json     실거래 (최근 months_in_json 개월)
  docs/rent/data/meta.json      수집 시각, 건수, 소스별 상태
  docs/rent/list.md             지역별 → 유형별 매물 목록
  rent/data/*.csv|json          원본 보관

소스 하나가 실패해도 나머지는 진행하며, 실패한 소스의 기존 데이터는 유지한다.

사용:  python rent/collect.py [--only naver,zigbang,dabang,seoul,molit] [--skip a,b] [-v]
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
REGIONS_PATH = HERE / "regions.json"
RAW_DIR = HERE / "data"
OUT_DIR = ROOT / "docs" / "rent" / "data"
LIST_MD = ROOT / "docs" / "rent" / "list.md"
LIST_MD_TYPES = ("아파트", "오피스텔")   # list.md 에 표로 싣는 유형 (나머지는 건수만)

GWACHEON = "과천시"
GWACHEON_LAWD = "41290"
TYPES = ("아파트", "오피스텔", "빌라", "주택")
SITES_ALL = ("naver", "zigbang", "dabang", "seoul", "molit")
SITE_LABEL = {"naver": "네이버", "zigbang": "직방", "dabang": "다방", "seoul": "서울실거래", "molit": "국토부실거래"}

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
    cfg = {"naver_gu": [], "months_deals": 6, "months_in_json": 3, "conversion_rate_default": 5.5,
           "dabang_max_pages": 200, "request_delay": 0.4}
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return cfg


def load_regions() -> dict:
    if not REGIONS_PATH.exists():
        return {}
    return json.loads(REGIONS_PATH.read_text(encoding="utf-8")).get("regions", {})


def month_list(months: int, base: datetime | None = None) -> list[str]:
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
    if s in ("", "-"):
        return default
    try:
        return int(float(s))
    except ValueError:
        return default


def to_float(v, default=None, nd=2):
    if v is None:
        return default
    s = str(v).replace(",", "").strip()
    if s == "":
        return default
    try:
        return round(float(s), nd)
    except ValueError:
        return default


def to_coord(v):
    return to_float(v, None, 6)


def parse_korean_price(v) -> int | None:
    """'1억 5,000' / '5,000' / '3억' / 15000 → 만원 정수."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).replace(",", "").replace(" ", "").strip()
    if not s:
        return None
    m = re.match(r"^(?:(\d+)억)?(\d+)?(?:만)?$", s)
    if not m or (m.group(1) is None and m.group(2) is None):
        return to_int(s)
    return (int(m.group(1)) * 10000 if m.group(1) else 0) + (int(m.group(2)) if m.group(2) else 0)


def floor_from_info(info) -> int | None:
    if info is None:
        return None
    first = str(info).split("/")[0].strip().replace("층", "")
    if first.startswith("B") or first.startswith("-"):
        v = to_int(first.lstrip("B"))
        return -v if v else None
    return to_int(first)


def norm_type(name: str | None) -> str | None:
    if not name:
        return None
    n = str(name)
    if "아파트" in n or n.upper() == "APT":
        return "아파트"
    if "오피스텔" in n:
        return "오피스텔"
    if "재건축" in n:
        return "아파트"
    if any(k in n for k in ("빌라", "연립", "다세대", "재개발")):
        return "빌라"
    if any(k in n for k in ("원룸", "투룸", "쓰리룸", "단독", "다가구", "주택", "한옥", "룸")):
        return "주택"
    return None


def norm_name(s) -> str:
    return re.sub(r"[\s()（）\-·.,]", "", str(s or "")).lower()


def make_id(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def record(**kw) -> dict:
    base = {
        "id": None, "source": None, "site": None, "sites": [], "region": None, "dong": None, "complex": None,
        "type": None, "area_m2": None, "floor": None, "deposit": None, "rent": None,
        "built_year": None, "date": None, "features": None, "url": None, "lat": None, "lng": None,
    }
    base.update(kw)
    if base["url"] and not base["sites"]:
        base["sites"] = [{"site": SITE_LABEL.get(base["site"], base["site"]), "url": base["url"]}]
    return base


class SourceStatus:
    def __init__(self):
        self.status: dict[str, dict] = {}

    def set(self, name: str, ok: bool, count: int = 0, note: str = ""):
        self.status[name] = {"ok": ok, "count": count, "note": note}
        log(f"[{name}] {'OK' if ok else 'FAIL'} {count}건 {note}")


def http_session(ua=UA_DESKTOP, **headers) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": ua, "Accept": "application/json, text/plain, */*",
                      "Accept-Language": "ko-KR,ko;q=0.9", **headers})
    return s


def get_json(s: requests.Session, url: str, params=None, retries=2, delay=0.4, **kw):
    """GET → JSON. 429/5xx 는 잠시 후 재시도."""
    last = None
    for i in range(retries + 1):
        try:
            r = s.get(url, params=params, timeout=40, **kw)
            if r.status_code in (429, 500, 502, 503, 504) and i < retries:
                time.sleep(3 * (i + 1))
                continue
            r.raise_for_status()
            time.sleep(delay)
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"{url}: {last}")


# ---------------------------------------------------------------- 지역
class Regions:
    """수집 대상 지역. 기본은 과천시 + naver_gu, config.extra_regions 로 인접 지역(동 단위 부분 지역)을 켤 수 있다."""

    def __init__(self, cfg: dict):
        self.data = load_regions()
        names = [GWACHEON] + [g for g in cfg.get("naver_gu", []) if g != GWACHEON]
        names += [g for g in cfg.get("extra_regions", []) if g not in names]
        missing = [t for t in names if t not in self.data]
        if missing:
            log(f"[지역] regions.json 에 없는 지역(수집 제외): {missing}")
        self.targets = [t for t in names if t in self.data]
        self.entries = {}
        for name in self.targets:
            e = self.data[name]
            self.entries[name] = {
                "parent": self.canon(e.get("parent") or name),          # 구/시 단위 비교용
                "parent_full": e.get("parent_full") or name,             # 네이버 지역 검색용 ('안양시 동안구')
                "dongs": set(e.get("dongs") or []),
                "partial": bool(e.get("parent")),                         # 동 단위 부분 지역 여부
            }

    def bbox(self, name, margin=0.0):
        b = self.data.get(name, {}).get("bbox")
        if not b:
            return None
        return (b["sw"]["lat"] - margin, b["sw"]["lng"] - margin, b["ne"]["lat"] + margin, b["ne"]["lng"] + margin)

    def in_any_bbox(self, lat, lng, margin=0.01) -> bool:
        if lat is None or lng is None:
            return False
        for name in self.targets:
            b = self.bbox(name, margin)
            if b and b[0] <= lat <= b[2] and b[1] <= lng <= b[3]:
                return True
        return False

    def dongs(self, name) -> set:
        return self.entries[name]["dongs"]

    def resolve(self, gu: str | None, dong: str | None) -> str | None:
        """(구/시 이름, 동 이름) → 대상 지역명. 전체 구 항목이 부분 지역보다 우선."""
        g = self.canon(gu)
        for name in self.targets:
            e = self.entries[name]
            if e["parent"] != g:
                continue
            if e["partial"] and (not dong or dong not in e["dongs"]):
                continue
            return name
        return None

    @staticmethod
    def canon(name: str | None) -> str | None:
        """'서울특별시 서초구' / '경기도 안양시 동안구' / '과천시' → '서초구' / '동안구' / '과천시'."""
        if not name:
            return None
        n = str(name).strip()
        toks = n.split()
        for tok in reversed(toks):
            if tok.endswith("구") or tok == GWACHEON:
                return tok
        return toks[-1]


# ---------------------------------------------------------------- 서울시 열린데이터
SEOUL_FIELD_MAP = {
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
        start, step = 1, 1000
        while fetched < max_rows:
            url = f"http://openapi.seoul.go.kr:8088/{key}/json/tbLnOpendataRentV/{start}/{start + step - 1}/{year}/"
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            data = r.json()
            body = data.get("tbLnOpendataRentV") if isinstance(data, dict) else None
            if not body:
                txt = json.dumps(data, ensure_ascii=False)
                if "INFO-200" in txt or "해당하는 데이터가 없습니다" in txt:
                    break
                raise RuntimeError(f"서울 API 응답 이상: {txt[:200]}")
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
                gu, dong = seoul_get(row, "CGG_NM"), seoul_get(row, "STDG_NM")
                bldg = seoul_get(row, "BLDG_NM") or ""
                area, floor = to_float(seoul_get(row, "RENT_AREA")), to_int(seoul_get(row, "FLR"))
                dep, rent = to_int(seoul_get(row, "GRFE")), to_int(seoul_get(row, "RTFE"))
                out.append(record(
                    id=make_id("seoul", gu, dong, bldg, area, floor, dep, rent, day),
                    source="실거래", site="seoul", region=gu, dong=dong, complex=bldg or None, type=typ,
                    area_m2=area, floor=floor, deposit=dep, rent=rent,
                    built_year=to_int(seoul_get(row, "ARCH_YR")), date=f"{day[:4]}-{day[4:6]}",
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
    ("RTMSDataSvcAptRent/getRTMSDataSvcAptRent", "아파트", ("aptNm",)),
    ("RTMSDataSvcOffiRent/getRTMSDataSvcOffiRent", "오피스텔", ("offiNm",)),
    ("RTMSDataSvcRHRent/getRTMSDataSvcRHRent", "빌라", ("mhouseNm",)),
    ("RTMSDataSvcSHRent/getRTMSDataSvcSHRent", "주택", ("houseType",)),
]


def molit_text(item: ET.Element, *names):
    for n in names:
        el = item.find(n)
        if el is not None and el.text and el.text.strip():
            return el.text.strip()
    return None


def collect_molit(key: str, months: int, status: SourceStatus) -> list[dict]:
    out: list[dict] = []
    for path, typ, name_tags in MOLIT_SERVICES:
        for ym in month_list(months):
            page = 1
            while True:
                r = requests.get(f"https://apis.data.go.kr/1613000/{path}", timeout=60, params={
                    "serviceKey": key, "LAWD_CD": GWACHEON_LAWD, "DEAL_YMD": ym, "pageNo": page, "numOfRows": 1000})
                r.raise_for_status()
                root = ET.fromstring(r.content)
                code = root.findtext(".//resultCode") or root.findtext(".//returnReasonCode")
                if code not in (None, "00", "000"):
                    raise RuntimeError(f"국토부 API 오류 {code}: {root.findtext('.//resultMsg') or root.findtext('.//returnAuthMsg')}")
                items = root.findall(".//item")
                for it in items:
                    rent = to_int(molit_text(it, "monthlyRent", "월세금액"), 0)
                    if not rent:
                        continue
                    dong = molit_text(it, "umdNm", "법정동")
                    name = molit_text(it, *name_tags, "아파트", "단지", "연립다세대")
                    area = to_float(molit_text(it, "excluUseAr", "totalFloorAr", "전용면적"))
                    floor, dep = to_int(molit_text(it, "floor", "층")), to_int(molit_text(it, "deposit", "보증금액"))
                    y = molit_text(it, "dealYear", "년") or ym[:4]
                    m = molit_text(it, "dealMonth", "월") or ym[4:]
                    date = f"{y}-{int(m):02d}"
                    out.append(record(
                        id=make_id("molit", typ, dong, name, area, floor, dep, rent, date, molit_text(it, "dealDay", "일")),
                        source="실거래", site="molit", region=GWACHEON, dong=dong, complex=name, type=typ,
                        area_m2=area, floor=floor, deposit=dep, rent=rent,
                        built_year=to_int(molit_text(it, "buildYear", "건축년도")), date=date,
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
NAVER_TYPES = "APT:OPST:VL:DDDGG:JWJT:SGJT"   # 아파트:오피스텔:빌라:단독/다가구:연립:상가주택
NAVER_TYPES_ALL = "APT:ABYG:JGC:OPST:OBYG:GJCG:DDDGG:VL:JWJT:SGJT:HOJT:GM"   # 과천: 분양권·재건축·재개발·한옥·원룸까지 전부
NAVER_TRADE = "B2"


class NaverBlocked(Exception):
    pass


class NaverClient:
    """new.land API → m.land 모바일 API → playwright 순으로 자동 전환."""

    def __init__(self):
        self.mode = "new"
        self.s = http_session(Referer="https://new.land.naver.com/")
        self._pw = None
        self._page = None

    def _get(self, url, params=None):
        r = self.s.get(url, params=params, timeout=30)
        if r.status_code in (401, 403, 429) or r.status_code >= 500:
            raise NaverBlocked(f"HTTP {r.status_code} {url}")
        r.raise_for_status()
        try:
            return r.json()
        except ValueError as e:
            raise NaverBlocked(f"JSON 아님 {url}: {r.text[:80]}") from e

    def _ensure_playwright(self):
        if self._page is not None:
            return
        if getattr(self, "_pw_error", None):
            raise NaverBlocked(f"playwright 이전 실패: {self._pw_error}")
        try:
            self._start_playwright()
        except Exception as e:
            self._pw_error = str(e).splitlines()[0][:160]
            self.close()
            self._pw = None
            raise NaverBlocked(f"playwright 실패: {self._pw_error}") from e

    def _start_playwright(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        self._browser = self._pw.chromium.launch(headless=True, proxy={"server": proxy} if proxy else None)
        ctx = self._browser.new_context(user_agent=UA_DESKTOP, locale="ko-KR", viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        captured = {}

        def on_request(req):
            if "new.land.naver.com/api/" in req.url and req.headers.get("authorization"):
                captured["auth"] = req.headers["authorization"]

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
        self._ensure_playwright()
        full = url + ("?" + requests.compat.urlencode(params) if params else "")
        auth = self.s.headers.get("Authorization", "")
        return self._page.evaluate(
            """async ([u, a]) => {
                const r = await fetch(u, {headers: a ? {authorization: a} : {}, credentials: 'include'});
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

    def call(self, kind: str, **kw):
        attempts = ["new", "mobile", "pw_token", "pw_page"]
        last = None
        for mode in attempts[attempts.index(self.mode):]:
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
            except Exception as e:  # playwright 오류 등
                last = e
                vlog(f"naver {mode} 오류: {e}")
        raise NaverBlocked(f"모든 방식 실패: {last}")

    def _new(self, kind, fetch=None, **kw):
        fetch = fetch or self._get
        types = kw.get("types") or NAVER_TYPES
        if kind == "regions":
            d = fetch("https://new.land.naver.com/api/regions/list", {"cortarNo": kw["cortarNo"]})
            return [{"cortarNo": r["cortarNo"], "name": r["cortarName"]} for r in d.get("regionList", [])]
        d = fetch("https://new.land.naver.com/api/articles", {
            "cortarNo": kw["cortarNo"], "order": "rank", "realEstateType": types, "tradeType": NAVER_TRADE,
            "tag": "::::::::", "rentPriceMin": 0, "rentPriceMax": 900000000, "priceMin": 0, "priceMax": 900000000,
            "areaMin": 0, "areaMax": 900000000, "priceType": "RETAIL", "page": kw["page"], "articleState": ""})
        arts = [{
            "no": a.get("articleNo"), "name": a.get("articleName"), "type": a.get("realEstateTypeName"),
            "floor": a.get("floorInfo"), "deposit": a.get("dealOrWarrantPrc"), "rent": a.get("rentPrc"),
            "area": a.get("area2") or a.get("area1"), "features": a.get("articleFeatureDesc"),
            "building": a.get("buildingName"), "lat": a.get("latitude"), "lng": a.get("longitude"),
            "confirm": a.get("articleConfirmYmd"), "tags": a.get("tagList") or [], "direction": a.get("direction"),
        } for a in d.get("articleList", [])]
        return {"list": arts, "more": bool(d.get("isMoreData"))}

    def _mobile(self, kind, **kw):
        h = {"User-Agent": UA_MOBILE, "Referer": "https://m.land.naver.com/"}
        types = kw.get("types") or NAVER_TYPES
        if kind == "regions":
            r = self.s.get("https://m.land.naver.com/map/getRegionList", params={"cortarNo": kw["cortarNo"]}, headers=h, timeout=30)
            if r.status_code != 200:
                raise NaverBlocked(f"HTTP {r.status_code} m.land regions")
            return [{"cortarNo": x.get("CortarNo"), "name": x.get("CortarNm")} for x in r.json().get("result", {}).get("list", [])]
        r = self.s.get("https://m.land.naver.com/cluster/ajax/articleList", headers=h, timeout=30, params={
            "rletTpCd": types, "tradTpCd": NAVER_TRADE, "cortarNo": kw["cortarNo"], "page": kw["page"], "sort": "rank"})
        if r.status_code != 200:
            raise NaverBlocked(f"HTTP {r.status_code} m.land articles")
        d = r.json()
        arts = [{
            "no": a.get("atclNo"), "name": a.get("atclNm"), "type": a.get("rletTpNm"), "floor": a.get("flrInfo"),
            "deposit": a.get("hanPrc") or a.get("prc"), "rent": a.get("rentPrc"), "area": a.get("spc2") or a.get("spc1"),
            "features": a.get("atclFetrDesc"), "building": a.get("bildNm"), "lat": a.get("lat"), "lng": a.get("lng"),
            "confirm": a.get("atclCfmYmd"), "tags": a.get("tagList") or [], "direction": a.get("direction"),
        } for a in d.get("body", [])]
        return {"list": arts, "more": bool(d.get("more"))}


def naver_url(no) -> str:
    """모바일·PC 모두 매물 상세로 바로 열리는 형식 (new.land.naver.com/articles/{no} 는 앱/메인으로 튕김)."""
    return f"https://m.land.naver.com/article/info/{no}"


def normalize_naver(a: dict, region: str, dong: str | None, loose: bool = False) -> dict | None:
    typ = norm_type(a.get("type")) or ("주택" if loose else None)
    if typ is None:
        return None
    confirm = re.sub(r"\D", "", str(a.get("confirm") or ""))
    if len(confirm) == 6:
        confirm = "20" + confirm
    date = f"{confirm[:4]}-{confirm[4:6]}-{confirm[6:8]}" if len(confirm) == 8 else now_kst().strftime("%Y-%m-%d")
    feats = [str(a.get("features") or "").strip()] + [str(t) for t in a.get("tags", [])]
    if loose and norm_type(a.get("type")) is None and a.get("type"):
        feats.insert(0, str(a["type"]))
    if a.get("direction"):
        feats.append(str(a["direction"]))
    return record(
        id=f"nv{a.get('no')}", source="현재매물", site="naver", region=region, dong=dong,
        complex=a.get("name") or a.get("building"), type=typ,
        area_m2=to_float(a.get("area")), floor=floor_from_info(a.get("floor")),
        deposit=parse_korean_price(a.get("deposit")), rent=parse_korean_price(a.get("rent")),
        date=date, features=" · ".join(f for f in feats if f) or None,
        url=naver_url(a.get("no")), lat=to_coord(a.get("lat")), lng=to_coord(a.get("lng")),
    )


def collect_naver(regions: Regions, status: SourceStatus, max_pages: int = 200) -> list[dict]:
    client = NaverClient()
    out: list[dict] = []
    seen: set[str] = set()
    try:
        lists = {"41": client.call("regions", cortarNo="4100000000"), "11": client.call("regions", cortarNo="1100000000")}

        def find_cortar(full_name: str):
            for lst in lists.values():
                for r in lst:
                    if r["name"] == full_name:
                        return r["cortarNo"]
            toks = full_name.split()
            if len(toks) == 2:   # '안양시 동안구' → 안양시 → 동안구
                for lst in lists.values():
                    for r in lst:
                        if r["name"] == toks[0]:
                            for c in client.call("regions", cortarNo=r["cortarNo"]):
                                if c["name"] == toks[1]:
                                    return c["cortarNo"]
            return None

        targets: list[tuple[str, str]] = []
        for name in regions.targets:
            no = find_cortar(regions.entries[name]["parent_full"])
            if no:
                targets.append((name, no))
            else:
                log(f"[네이버] 지역 못 찾음: {name}")
        if not targets:
            raise NaverBlocked("수집 대상 지역을 찾지 못함")
        for region, gu_no in targets:
            entry = regions.entries[region]
            dongs = client.call("regions", cortarNo=gu_no) or [{"cortarNo": gu_no, "name": ""}]
            if entry["partial"]:
                dongs = [d for d in dongs if d["name"] in entry["dongs"]]
            types = NAVER_TYPES_ALL if region == GWACHEON else NAVER_TYPES
            n = 0
            for d in dongs:
                page = 1
                while page <= max_pages:
                    res = client.call("articles", cortarNo=d["cortarNo"], page=page, types=types)
                    for a in res["list"]:
                        rec = normalize_naver(a, region, d["name"] or None, loose=(region == GWACHEON))
                        if rec and rec["id"] not in seen:
                            seen.add(rec["id"])
                            out.append(rec)
                            n += 1
                    if not res["more"] or not res["list"]:
                        break
                    page += 1
                    time.sleep(0.3)
            log(f"[네이버] {region}: {n}건 (동 {len(dongs)}개, 방식 {client.mode})")
        status.set("네이버", True, len(out), f"방식 {client.mode}")
    finally:
        client.close()
    return out


# ---------------------------------------------------------------- 직방
_GH32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def geohash_encode(lat: float, lng: float, precision: int) -> str:
    lat_i, lng_i = (-90.0, 90.0), (-180.0, 180.0)
    out, bit, ch, even = [], 0, 0, True
    while len(out) < precision:
        if even:
            mid = (lng_i[0] + lng_i[1]) / 2
            if lng > mid:
                ch |= 1 << (4 - bit)
                lng_i = (mid, lng_i[1])
            else:
                lng_i = (lng_i[0], mid)
        else:
            mid = (lat_i[0] + lat_i[1]) / 2
            if lat > mid:
                ch |= 1 << (4 - bit)
                lat_i = (mid, lat_i[1])
            else:
                lat_i = (lat_i[0], mid)
        even = not even
        if bit < 4:
            bit += 1
        else:
            out.append(_GH32[ch])
            bit, ch = 0, 0
    return "".join(out)


def geohash_cells(bbox, precision=4) -> set[str]:
    """bbox(s, w, n, e) 를 덮는 geohash 셀 집합 (precision 4 ≈ 39km×20km, 5 ≈ 4.9km×4.9km)."""
    step_lat = {4: 0.17578125, 5: 0.0439453125, 6: 0.0054931640625}[precision]
    step_lng = {4: 0.3515625, 5: 0.0439453125, 6: 0.010986328125}[precision]
    cells = set()
    lat = bbox[0]
    while lat <= bbox[2] + step_lat:
        lng = bbox[1]
        while lng <= bbox[3] + step_lng:
            cells.add(geohash_encode(min(lat, bbox[2]), min(lng, bbox[3]), precision))
            lng += step_lng
        lat += step_lat
    return cells


ZIGBANG_CATS = [("villas", "빌라"), ("onerooms", "주택"), ("officetels", "오피스텔")]


def zigbang_url(cat: str, item_id) -> str:
    """m.zigbang.com 은 모바일·PC 모두 매물 상세를 연다 (www 는 모바일에서 앱스토어로 리다이렉트)."""
    kind = {"villas": "villa", "onerooms": "oneroom", "officetels": "officetel"}.get(cat, cat)
    return f"https://m.zigbang.com/home/{kind}/items/{item_id}"


def collect_zigbang(regions: Regions, status: SourceStatus, delay: float) -> list[dict]:
    s = http_session(Referer="https://www.zigbang.com/", Origin="https://www.zigbang.com")
    B = "https://apis.zigbang.com"
    cells: set[str] = set()
    for name in regions.targets:
        b = regions.bbox(name, 0.01)
        if b:
            cells |= geohash_cells(b, 4)
    if not cells:
        raise RuntimeError("regions.json 없음")
    out: list[dict] = []
    per_cat: dict[str, int] = {}
    for cat, default_type in ZIGBANG_CATS:
        cand: dict[int, tuple] = {}
        for cell in sorted(cells):
            d = get_json(s, f"{B}/house/property/v1/items/{cat}", {"geohash": cell, "salesTypes": "월세"}, delay=delay)
            for it in d.get("items", []):
                if regions.in_any_bbox(it.get("lat"), it.get("lng")):
                    cand[it["id"]] = (it.get("lat"), it.get("lng"))
        ids = list(cand)
        vlog(f"zigbang {cat}: 후보 {len(ids)}건 (셀 {len(cells)}개)")
        n = 0
        for i in range(0, len(ids), 15):
            batch = ids[i:i + 15]
            r = None
            for attempt in range(3):
                r = s.post(f"{B}/house/property/v1/items/list", json={"itemIds": batch}, timeout=40)
                if r.status_code < 500:
                    break
                time.sleep(3)
            time.sleep(delay)
            if r is None or r.status_code != 200:
                vlog(f"zigbang list HTTP {r.status_code if r else '-'}")
                continue
            for it in r.json().get("items", []):
                if it.get("sales_type") != "월세":
                    continue
                ao = it.get("addressOrigin") or {}
                region = regions.resolve(ao.get("local2") or it.get("address1"), ao.get("local3"))
                if region is None:
                    continue
                typ = norm_type(it.get("service_type")) or default_type
                area = to_float((it.get("전용면적") or {}).get("m2")) or to_float(it.get("size_m2"))
                loc = it.get("location") or it.get("random_location") or {}
                floor_s = str(it.get("floor") or "")
                feats = [str(it.get("title") or "").strip()]
                if floor_s and not floor_s.isdigit():
                    feats.append(floor_s)
                if it.get("manage_cost"):
                    mc = to_int(it["manage_cost"])
                    if mc:
                        feats.append(f"관리비 {mc // 10000 if mc >= 10000 else mc}만" if mc >= 10000 else f"관리비 {mc}원")
                reg = str(it.get("reg_date") or "")[:10]
                out.append(record(
                    id=f"zb{it['item_id']}", source="현재매물", site="zigbang", region=region, dong=ao.get("local3") or None,
                    complex=it.get("building_name") or None, type=typ, area_m2=area,
                    floor=floor_from_info(floor_s) if floor_s.lstrip("-").isdigit() else None,
                    deposit=to_int(it.get("deposit")), rent=to_int(it.get("rent")),
                    date=reg or now_kst().strftime("%Y-%m-%d"), features=" · ".join(f for f in feats if f) or None,
                    url=zigbang_url(cat, it['item_id']),
                    lat=to_coord(loc.get("lat")), lng=to_coord(loc.get("lng")),
                ))
                n += 1
        per_cat[cat] = n
        log(f"[직방] {cat}: {n}건")
    status.set("직방", True, len(out), json.dumps(per_cat, ensure_ascii=False))
    return out


# ---------------------------------------------------------------- 다방
_RNG = {"min": 0, "max": 999999}
_DB_COMMON = {"sellingTypeList": ["MONTHLY_RENT"], "depositRange": _RNG, "priceRange": _RNG, "isIncludeMaintenance": False,
              "pyeongRange": {"min": 0, "max": 999999}, "useApprovalDateRange": _RNG, "isShortLease": False}
_DB_FLOORS = ["GROUND_FIRST", "GROUND_SECOND_OVER", "SEMI_BASEMENT", "ROOFTOP"]
_DB_ROOMS = ["ONE_ROOM", "TWO_ROOM", "THREE_ROOM", "FOUR_ROOM"]
DABANG_CATS = {
    "apt": {**_DB_COMMON, "tradeRange": _RNG, "householdNumRange": _RNG, "parkingNumRange": _RNG, "hasTakeTenant": False, "roomCountList": _DB_ROOMS},
    "officetel": {**_DB_COMMON, "tradeRange": _RNG, "dealTypeList": ["AGENT", "DIRECT"], "parkingNumRange": _RNG, "canParking": False,
                  "hasElevator": False, "hasPano": False, "roomCountList": _DB_ROOMS},
    "house-villa": {**_DB_COMMON, "tradeRange": _RNG, "roomFloorList": _DB_FLOORS, "dealTypeList": ["AGENT", "DIRECT"], "canParking": False,
                    "hasElevator": False, "hasPano": False, "roomCountList": _DB_ROOMS},
    "one-two": {**_DB_COMMON, "roomFloorList": _DB_FLOORS, "roomTypeList": ["ONE_ROOM", "TWO_ROOM"], "dealTypeList": ["AGENT", "DIRECT"],
                "canParking": False, "hasElevator": False, "hasPano": False, "isDivision": False, "isDuplex": False},
}
DABANG_DEFAULT_TYPE = {"apt": "아파트", "officetel": "오피스텔", "house-villa": "빌라", "one-two": "주택"}


def parse_dabang_desc(desc: str):
    """'29층, 115m², 관리비 30만' → (floor, area, features)"""
    floor = area = None
    feats = []
    for part in [p.strip() for p in str(desc or "").split(",")]:
        m = re.match(r"^(-?\d+)층$", part)
        if m:
            floor = int(m.group(1))
            continue
        m = re.match(r"^([\d.]+)\s*m", part)
        if m:
            area = to_float(m.group(1))
            continue
        if part:
            feats.append(part)
    return floor, area, feats


def collect_dabang(regions: Regions, status: SourceStatus, delay: float, max_pages: int) -> list[dict]:
    s = http_session(Referer="https://www.dabangapp.com/map/onetwo", csrf="token", **{
        "D-Api-Version": "5.0.0", "D-App-Version": "1", "D-Call-Type": "web"})
    B = "https://www.dabangapp.com"
    try:
        s.get(B + "/map/onetwo", timeout=30)
    except requests.RequestException:
        pass
    out: list[dict] = []
    seen: set[str] = set()
    per_region: dict[str, int] = {}
    errors = 0
    for name in regions.targets:
        b = regions.bbox(name, 0.005)
        if not b:
            continue
        dongs = regions.dongs(name)
        bbox = {"sw": {"lat": b[0], "lng": b[1]}, "ne": {"lat": b[2], "lng": b[3]}}
        n_region = 0
        for cat, filt in DABANG_CATS.items():
            page = 1
            while page <= max_pages:
                params = {"filters": json.dumps(filt, separators=(",", ":")), "bbox": json.dumps(bbox, separators=(",", ":")),
                          "zoom": 13, "useMap": "naver", "page": page}
                try:
                    res = get_json(s, f"{B}/api/v5/room-list/category/{cat}/bbox", params, delay=delay).get("result", {})
                except RuntimeError as e:
                    errors += 1
                    vlog(f"dabang {name} {cat} p{page} 실패: {e}")
                    break
                rooms = (res.get("roomList") or []) + (res.get("premiumList") or [])
                for r in rooms:
                    rid = r.get("id")
                    if not rid or rid in seen:
                        continue
                    if r.get("dongName") and r["dongName"] not in dongs:
                        continue
                    if "월세" not in str(r.get("priceTypeName") or "월세"):
                        continue
                    seen.add(rid)
                    price = str(r.get("priceTitle") or "")
                    dep, rent = (price.split("/") + [None])[:2] if "/" in price else (None, price)
                    floor, area, feats = parse_dabang_desc(r.get("roomDesc"))
                    if r.get("roomTitle"):
                        feats.insert(0, str(r["roomTitle"]).strip())
                    loc = r.get("randomLocation") or {}
                    out.append(record(
                        id=f"db{rid}", source="현재매물", site="dabang", region=name, dong=r.get("dongName"),
                        complex=r.get("complexName") or None, type=norm_type(r.get("roomTypeName")) or DABANG_DEFAULT_TYPE[cat],
                        area_m2=area, floor=floor, deposit=parse_korean_price(dep), rent=parse_korean_price(rent),
                        date=now_kst().strftime("%Y-%m-%d"), features=" · ".join(f for f in feats if f) or None,
                        url=f"https://www.dabangapp.com/room/{rid}", lat=to_coord(loc.get("lat")), lng=to_coord(loc.get("lng")),
                    ))
                    n_region += 1
                total, limit = to_int(res.get("total"), 0), to_int(res.get("limit"), 24) or 24
                if not rooms or not res.get("hasMore", page * limit < total):
                    break
                page += 1
        per_region[name] = n_region
        log(f"[다방] {name}: {n_region}건")
    if not out and errors:
        raise RuntimeError(f"요청 실패 {errors}회")
    status.set("다방", True, len(out), json.dumps(per_region, ensure_ascii=False) + (f" (요청 실패 {errors}회)" if errors else ""))
    return out


# ---------------------------------------------------------------- 중복 제거
def dedupe_listings(items: list[dict]) -> list[dict]:
    """단지명(없으면 동)+전용면적(반올림)+보증금+월세 로 같은 매물 판단 → 출처 병합."""
    order = {"naver": 0, "zigbang": 1, "dabang": 2}
    items = [dict(r, sites=[dict(x) for x in (r.get("sites") or [])]) for r in items]   # 원본(raw)은 건드리지 않음
    items = sorted(items, key=lambda r: (order.get(r.get("site"), 9), r.get("date") or ""))
    merged: dict[str, dict] = {}
    for r in items:
        if r.get("complex"):
            key = "|".join([str(r.get("region")), norm_name(r["complex"]),
                            str(round(r["area_m2"]) if r.get("area_m2") else "?"), str(r.get("deposit")), str(r.get("rent"))])
        else:  # 단지명 없는 원룸 등은 동+면적(소수점)+층까지 같아야 같은 매물로 본다
            key = "|".join(["@" + str(r.get("region")), norm_name(r.get("dong")), str(r.get("area_m2")),
                            str(r.get("floor")), str(r.get("deposit")), str(r.get("rent"))])
        m = merged.get(key)
        if m is None:
            merged[key] = r
            continue
        if any(x["site"] == sd["site"] for x in m["sites"] for sd in (r.get("sites") or [])):
            merged[key + "#" + str(r["id"])] = r   # 같은 사이트의 별개 게시물은 합치지 않음
            continue
        for sd in r.get("sites") or []:
            if not any(x["site"] == sd["site"] for x in m["sites"]):
                m["sites"].append(sd)
        for k in ("complex", "dong", "floor", "built_year", "features", "lat", "lng", "area_m2"):
            if m.get(k) in (None, "") and r.get(k) not in (None, ""):
                m[k] = r[k]
    return list(merged.values())


# ---------------------------------------------------------------- 전입 가능성 (과천만: 청약 거주기간 인정용)
MOVEIN_EXCLUDE = ("전입불가", "전입신고불가", "업무용", "사업자전용", "사업자만", "법인만", "단기", "숙박")
MOVEIN_OK_HINTS = ("주거용", "전입가능")
MOVEIN_DOUBT = ("무허가", "불법")


def movein_status(r: dict) -> tuple[str, str]:
    """features(제목·태그·설명) 텍스트로 전입신고 가능성 판정 → ('exclude'|'check'|'ok', 사유)."""
    txt = re.sub(r"\s+", "", str(r.get("features") or ""))
    for k in MOVEIN_EXCLUDE:
        if k in txt:
            return "exclude", k
    for k in MOVEIN_DOUBT:
        if k in txt:
            return "check", k
    if r.get("type") == "오피스텔" and not any(k in txt for k in MOVEIN_OK_HINTS):
        return "check", "오피스텔(주거용·전입가능 언급 없음)"
    return "ok", ""


def apply_movein(rows: list[dict], region: str = GWACHEON) -> tuple[list[dict], list[dict], int]:
    """region 매물에 판정 적용: 제외 건은 목록에서 빼고(excluded 반환), 확인 필요는 movein='check'."""
    kept, excluded, n_check = [], [], 0
    for r in rows:
        r.pop("movein", None)
        if r.get("region") != region or r.get("source") == "실거래":
            kept.append(r)
            continue
        st, why = movein_status(r)
        if st == "exclude":
            excluded.append({"id": r.get("id"), "complex": r.get("complex"), "dong": r.get("dong"), "type": r.get("type"),
                             "deposit": r.get("deposit"), "rent": r.get("rent"), "reason": why,
                             "features": str(r.get("features") or "")[:80]})
            continue
        if st == "check":
            r["movein"] = "check"
            r["movein_reason"] = why
            n_check += 1
        kept.append(r)
    return kept, excluded, n_check


# ---------------------------------------------------------------- 이상 매물 표시
def flag_suspects(listings: list[dict], rate: float = 5.5, ratio: float = 0.4, min_group: int = 3) -> int:
    """같은 단지·면적대(5㎡ 단위)의 환산 총주거비 중앙값 대비 ratio 미만이면 suspect=True ('확인 필요')."""
    groups: dict[tuple, list] = {}
    for r in listings:
        r.pop("suspect", None)
        if not r.get("complex") or r.get("area_m2") is None:
            continue
        key = (r.get("region"), norm_name(r["complex"]), int(r["area_m2"] // 5))
        groups.setdefault(key, []).append(r)
    n = 0
    for rows in groups.values():
        if len(rows) < min_group:
            continue
        costs = sorted((x.get("rent") or 0) + (x.get("deposit") or 0) * rate / 100 / 12 for x in rows)
        med = costs[len(costs) // 2] if len(costs) % 2 else (costs[len(costs) // 2 - 1] + costs[len(costs) // 2]) / 2
        if med <= 0:
            continue
        for x in rows:
            c = (x.get("rent") or 0) + (x.get("deposit") or 0) * rate / 100 / 12
            if c < med * ratio:
                x["suspect"] = True
                n += 1
    return n


# ---------------------------------------------------------------- list.md
def fmt_man(v) -> str:
    if v is None:
        return "-"
    v = int(v)
    eok, man = divmod(v, 10000)
    if eok and man:
        return f"{eok}억 {man:,}"
    return f"{eok}억" if eok else f"{man:,}"


def write_list_md(listings: list[dict], meta: dict, regions: Regions):
    lines = ["# 월세 매물 목록", "",
             f"수집 {meta['collected_at']} (KST) · 현재매물 {meta['counts']['listings']:,}건 · "
             f"보증금/월세 단위 만원 · 표는 아파트·오피스텔만, 빌라·주택은 건수만 (웹페이지에서 조회)", "",
             "필터·비교는 [웹페이지](./)에서. 소스별 상태:", ""]
    for k, v in meta["sources"].items():
        lines.append(f"- {'✅' if v['ok'] else '⚠️'} {k}: {v['count']:,}건 {v.get('note') or ''}".rstrip())
    lines.append("")
    by_region: dict[str, list] = {}
    for r in listings:
        by_region.setdefault(r.get("region") or "기타", []).append(r)
    ordered = [GWACHEON] + sorted((k for k in by_region if k != GWACHEON), key=lambda k: -len(by_region[k]))
    lines.append("## 목차")
    for name in ordered:
        if name in by_region:
            rr = by_region[name]
            lines.append(f"- [{name}](#{name}) {len(rr):,}건 (아파트 {sum(1 for r in rr if r.get('type') == '아파트'):,} · "
                         f"오피스텔 {sum(1 for r in rr if r.get('type') == '오피스텔'):,})")
    lines.append("")
    for name in ordered:
        rows = by_region.get(name)
        if not rows:
            continue
        lines.append(f"## {name}")
        lines.append("")
        cnt = {t: sum(1 for r in rows if r.get("type") == t) for t in TYPES}
        lines.append("아파트 {아파트:,} · 오피스텔 {오피스텔:,} · 빌라 {빌라:,} · 주택 {주택:,} (빌라·주택은 웹페이지에서 조회)".format(**cnt))
        lines.append("")
        for typ in LIST_MD_TYPES:
            trs = [r for r in rows if r.get("type") == typ]
            if not trs:
                continue
            trs.sort(key=lambda r: ((r.get("rent") or 0) + (r.get("deposit") or 0) * 0.055 / 12, r.get("deposit") or 0))
            lines.append(f"### {name} · {typ} ({len(trs):,}건)")
            lines.append("")
            lines.append("| 단지/동 | 전용㎡ | 층 | 보증금/월세 | 출처 |")
            lines.append("|---|---:|---:|---:|---|")
            for r in trs:
                nm = ((r.get("complex") or "").replace("|", "/") or "-") + (" ⚠️확인필요" if r.get("suspect") else "") + (" 🏠전입확인" if r.get("movein") == "check" else "")
                dong = r.get("dong") or ""
                links = " ".join(f"[{s['site']}]({s['url']})" for s in r.get("sites") or [])
                lines.append(f"| {nm}{' · ' + dong if dong else ''} | {r['area_m2'] if r.get('area_m2') is not None else '-'} | "
                             f"{r['floor'] if r.get('floor') is not None else '-'} | {fmt_man(r.get('deposit'))} / {r.get('rent') if r.get('rent') is not None else '-'} | {links or '-'} |")
            lines.append("")
    LIST_MD.parent.mkdir(parents=True, exist_ok=True)
    LIST_MD.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------- 저장
SLIM_KEYS = ("id", "region", "dong", "complex", "type", "area_m2", "floor", "deposit", "rent", "built_year", "date", "features", "sites", "lat", "lng", "suspect", "movein")


def slim(r: dict) -> dict:
    """웹페이지용 최소 필드. url/site/source 는 sites 로 대체(현재매물 파일이므로 source 생략), 좌표 5자리, 특징 100자."""
    o = {}
    for k in SLIM_KEYS:
        v = r.get(k)
        if v in (None, "", [], False):
            continue
        if k in ("lat", "lng"):
            v = round(v, 5)
        elif k == "features":
            v = str(v)[:100]
        elif k == "sites":
            v = [{"site": x["site"], "url": x["url"]} for x in v]
        o[k] = v
    return o


def write_region_files(listings: list[dict]) -> dict:
    """지역별 파일로 분할 저장 → meta.region_files {지역: {file, count}}. 이전 파일은 정리."""
    d = OUT_DIR / "listings"
    d.mkdir(parents=True, exist_ok=True)
    by: dict[str, list] = {}
    for r in listings:
        by.setdefault(r.get("region") or "기타", []).append(slim(r))
    files = {}
    for i, name in enumerate(sorted(by, key=lambda n: (n != GWACHEON, n))):
        fn = f"r{i:02d}.json"
        write_json(d / fn, by[name])
        files[name] = {"file": f"listings/{fn}", "count": len(by[name])}
    for old in d.glob("*.json"):
        if old.name not in {v["file"].split("/")[-1] for v in files.values()}:
            old.unlink()
    legacy = OUT_DIR / "listings.json"
    if legacy.exists():
        legacy.unlink()
    return files


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
            row = {k: r.get(k) for k in cols}
            row["sites"] = " ".join(s["url"] for s in r.get("sites") or [])
            w.writerow(row)


def read_json(path: Path, default):
    try:
        v = json.loads(path.read_text(encoding="utf-8"))
        return v if isinstance(v, type(default)) else default
    except Exception:
        return default


def run(args) -> dict:
    global VERBOSE
    VERBOSE = args.verbose
    cfg = load_config()
    regions = Regions(cfg)
    status = SourceStatus()
    started = now_kst()
    only = set(args.only.split(",")) if args.only else set(SITES_ALL)
    skip = set(args.skip.split(",")) if args.skip else set()
    active = [s for s in SITES_ALL if s in only and s not in skip]
    log(f"== 수집 시작 {started:%Y-%m-%d %H:%M:%S %Z} | 지역 {regions.targets} | 소스 {active}")

    delay = float(cfg.get("request_delay", 0.4))
    prev_raw = read_json(RAW_DIR / "listings_raw.json", [])
    prev_by_site: dict[str, list] = {}
    for r in prev_raw:
        prev_by_site.setdefault(r.get("site"), []).append(r)
    prev_deals = read_json(RAW_DIR / "deals_all.json", [])
    months_deals, months_json = int(cfg.get("months_deals", 6)), int(cfg.get("months_in_json", 3))

    # --- 실거래
    deals_seoul = [d for d in prev_deals if d.get("site") == "seoul" or (d.get("site") is None and d.get("region") != GWACHEON)]
    deals_gc = [d for d in prev_deals if d.get("site") == "molit" or (d.get("site") is None and d.get("region") == GWACHEON)]
    if "seoul" in active:
        key = os.environ.get("SEOUL_KEY", "").strip()
        if key:
            try:
                deals_seoul = collect_seoul(key, months_deals, status)
            except Exception as e:
                status.set("서울 실거래", False, len(deals_seoul), f"실패, 기존 데이터 유지: {str(e)[:160]}")
                vlog(traceback.format_exc())
        else:
            vlog("SEOUL_KEY 없음 → 서울 실거래 건너뜀 (기존 데이터 유지)")
    if "molit" in active:
        key = os.environ.get("MOLIT_KEY", "").strip()
        if key:
            try:
                deals_gc = collect_molit(key, months_deals, status)
            except Exception as e:
                status.set("과천 실거래", False, len(deals_gc), f"실패, 기존 데이터 유지: {str(e)[:160]}")
                vlog(traceback.format_exc())
        else:
            vlog("MOLIT_KEY 없음 → 과천 실거래 건너뜀 (기존 데이터 유지)")

    # --- 현재 매물 (소스별 격리)
    raw: list[dict] = []
    collectors = {
        "naver": ("네이버", lambda: collect_naver(regions, status)),
        "zigbang": ("직방", lambda: collect_zigbang(regions, status, delay)),
        "dabang": ("다방", lambda: collect_dabang(regions, status, delay, int(cfg.get("dabang_max_pages", 200)))),
    }
    prev_meta = read_json(OUT_DIR / "meta.json", {})
    for site, (label, fn) in collectors.items():
        if site not in active:
            kept = prev_by_site.get(site, [])
            raw.extend(kept)
            status.status[label] = (prev_meta.get("sources") or {}).get(label) or {
                "ok": True, "count": len(kept), "note": "이번 실행 제외, 기존 데이터 유지"}
            continue
        try:
            raw.extend(fn())
        except Exception as e:
            kept = prev_by_site.get(site, [])
            status.set(label, False, len(kept), f"실패, 기존 데이터 유지: {str(e).splitlines()[0][:160]}")
            vlog(traceback.format_exc())
            raw.extend(kept)

    def fix_url(u: str | None) -> str | None:   # 구버전 링크 형식 보정
        if not u:
            return u
        m = re.match(r"https://new\.land\.naver\.com/articles/(\d+)", u)
        if m:
            return naver_url(m.group(1))
        m = re.match(r"https://www\.zigbang\.com/home/(villa|oneroom|officetel)/items/(\d+)", u)
        if m:
            return f"https://m.zigbang.com/home/{m.group(1)}/items/{m.group(2)}"
        return u

    for r in raw:
        for x in r.get("sites") or []:
            x["url"] = fix_url(x.get("url"))
        r["url"] = fix_url(r.get("url"))
    for r in raw:  # 구버전 레코드 보정: 자기 사이트 출처 하나만 유지
        own = SITE_LABEL.get(r.get("site") or "naver", "네이버")
        r["sites"] = [x for x in (r.get("sites") or []) if x.get("site") == own][:1] or (
            [{"site": own, "url": r["url"]}] if r.get("url") else [])
    raw, excluded, _ = apply_movein(raw)        # 과천 전입불가 등은 원본에도 저장하지 않음
    listings = dedupe_listings(raw)
    listings, _, n_movein_check = apply_movein(listings)
    listings.sort(key=lambda d: (d.get("date") or ""), reverse=True)
    log(f"   과천 전입 판정: 제외 {len(excluded)}건, 전입 확인 필요 {n_movein_check}건")
    n_suspect = flag_suspects(listings, float(cfg.get("conversion_rate_default", 5.5)))
    log(f"   확인 필요(같은 단지·면적대 중앙값의 40% 미만): {n_suspect}건")

    # --- 실거래 정리
    cutoff_all, cutoff_json = cutoff_ym(months_deals), cutoff_ym(months_json)
    deals_all = [d for d in deals_seoul + deals_gc if d.get("date") and d["date"].replace("-", "")[:6] >= cutoff_all]
    deals_all.sort(key=lambda d: (d.get("date") or "", d.get("region") or ""), reverse=True)
    deals_json = [d for d in deals_all if d["date"].replace("-", "")[:6] >= cutoff_json]

    # --- 저장
    region_files = write_region_files(listings)
    write_json(OUT_DIR / "deals.json", deals_json)
    write_json(RAW_DIR / "listings_raw.json", raw)
    write_json(RAW_DIR / "deals_all.json", deals_all)
    write_csv(RAW_DIR / "listings.csv", listings)
    write_csv(RAW_DIR / "deals.csv", deals_all)

    def by(items, key):
        c: dict[str, int] = {}
        for it in items:
            k = it.get(key) or "기타"
            c[k] = c.get(k, 0) + 1
        return dict(sorted(c.items(), key=lambda x: -x[1]))

    site_counts: dict[str, int] = {}
    for r in listings:
        for sd in r.get("sites") or []:
            site_counts[sd["site"]] = site_counts.get(sd["site"], 0) + 1
    finished = now_kst()
    meta = {
        "collected_at": finished.strftime("%Y-%m-%d %H:%M:%S"), "collected_at_iso": finished.isoformat(), "timezone": "Asia/Seoul",
        "duration_sec": round((finished - started).total_seconds(), 1),
        "counts": {"listings": len(listings), "listings_raw": len(raw), "deals": len(deals_json), "deals_raw": len(deals_all), "suspect": n_suspect,
                   "movein_check": n_movein_check, "movein_excluded": len(excluded)},
        "movein_excluded_examples": excluded[:10],
        "listings_by_region": by(listings, "region"), "listings_by_type": by(listings, "type"), "listings_by_site": site_counts,
        "region_files": region_files,
        "deals_by_region": by(deals_json, "region"),
        "months_deals": months_deals, "months_in_json": months_json,
        "conversion_rate_default": cfg.get("conversion_rate_default", 5.5), "naver_gu": cfg.get("naver_gu", []),
        "sources": status.status,
    }
    write_json(OUT_DIR / "meta.json", meta)
    write_list_md(listings, meta, regions)

    log("== 결과")
    log(f"   현재매물 {len(listings)}건 (원본 {len(raw)}건, 중복 제거 {len(raw) - len(listings)}건)  지역 {meta['listings_by_region']}")
    log(f"   유형 {meta['listings_by_type']}  출처 {site_counts}")
    log(f"   실거래(최근 {months_json}개월) {len(deals_json)}건 / 원본 {len(deals_all)}건  {meta['deals_by_region']}")
    log(f"   저장: {OUT_DIR} (listings/r*.json {len(region_files)}개, deals/meta.json), {LIST_MD}, {RAW_DIR}")
    log(f"   완료 {finished:%Y-%m-%d %H:%M:%S %Z} ({meta['duration_sec']}s)")
    return meta


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--only", help="수집할 소스만 (쉼표 구분: naver,zigbang,dabang,seoul,molit)")
    p.add_argument("--skip", help="건너뛸 소스 (쉼표 구분)")
    p.add_argument("--skip-naver", action="store_true", help="(호환) 네이버 생략")
    p.add_argument("--skip-deals", action="store_true", help="(호환) 실거래 생략")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)
    extra = ([] if not args.skip_naver else ["naver"]) + ([] if not args.skip_deals else ["seoul", "molit"])
    if extra:
        args.skip = ",".join(filter(None, [args.skip] + extra))
    meta = run(args)
    failed = [k for k, v in meta["sources"].items() if not v["ok"]]
    if failed:
        log(f"!! 실패/생략 소스: {failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
