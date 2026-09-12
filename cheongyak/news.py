"""과천 청약 소식 수집기 (Google News RSS).

- 검색어별 RSS 를 받아 제목 정규화로 중복 제거, 키워드 규칙으로 분류(물량·일정·제도·과천시·LH국토부).
- 기존 docs/cheongyak/data/news.json 과 병합해 365일 보관. 날짜는 Asia/Seoul.
- 개인정보 없음. 키 불필요.

사용:  python cheongyak/news.py [-v]
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

KST = ZoneInfo("Asia/Seoul")
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "cheongyak" / "data" / "news.json"
QUERIES = ["과천과천지구", "과천 청약", "과천지식정보타운", "과천 3기 신도시", "과천 주택 공급", "3기 신도시 사전청약",
           "과천시 분양", "LH 과천", "청약제도 개편", "분양가상한제 공공택지"]
RELEVANT = re.compile(r"과천|청약|3기|LH|국토부|국토교통부|분양|신도시|택지")
CATS = [   # (이름, 정규식) — 여러 개 해당 가능, 첫 매치가 대표
    ("물량", re.compile(r"세대|가구|물량|공급\s?규모|만\s?호|천\s?호|공급량|배정")),
    ("일정", re.compile(r"공고|사전청약|본청약|일정|착공|입주|연기|지연|모집|접수|시기|년\s?분양|재공급|공급한다")),
    ("제도", re.compile(r"제도|특별공급|특공|가점|추첨|분양가상한제|상한제|개편|개정|법안|규정|자격|청약통장|무주택|실거주|전매")),
    ("과천시", re.compile(r"과천시(?!민)|과천시장|과천시청|시의회|과천\s?시")),
    ("LH·국토부", re.compile(r"\bLH\b|한국토지주택공사|국토교통부|국토부|장관")),
]
KEEP_DAYS = 365
VERBOSE = "-v" in sys.argv


def log(*a):
    print(*a, flush=True)


def norm_title(t: str) -> str:
    return re.sub(r"[\s\W_]+", "", t).lower()


def strip_html(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).replace("\xa0", " ").strip()


def fetch(q: str) -> list[dict]:
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(q) + "&hl=ko&gl=KR&ceid=KR:ko"
    r = requests.get(url, timeout=40, headers={"User-Agent": "Mozilla/5.0 (geunchae-family news)"})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for it in root.findall(".//item"):
        title = (it.findtext("title") or "").strip()
        source = (it.findtext("source") or "").strip()
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3].strip()
        link = (it.findtext("link") or "").strip()
        try:
            dt = parsedate_to_datetime(it.findtext("pubDate") or "").astimezone(KST)
        except Exception:
            dt = datetime.now(KST)
        desc = strip_html(it.findtext("description") or "")
        for junk in (title, source):
            if junk:
                desc = desc.replace(junk, " ")
        desc = re.sub(r"\s+", " ", desc).strip(" -·|")[:80]
        out.append({"id": hashlib.sha1(link.encode()).hexdigest()[:12], "title": title, "link": link, "source": source or "출처 미상",
                    "date": dt.strftime("%Y-%m-%d"), "desc": desc, "q": q})
    return out


def classify(title: str, desc: str) -> list[str]:
    txt = f"{title} {desc}"
    cats = [name for name, rx in CATS if rx.search(txt)]
    return cats or ["기타"]


def main() -> int:
    prev = {}
    if OUT.exists():
        try:
            for it in json.loads(OUT.read_text(encoding="utf-8")).get("items", []):
                prev[it["id"]] = it
        except Exception:
            pass
    now = datetime.now(KST)
    fetched: list[dict] = []
    per_q: dict[str, int] = {}
    for q in QUERIES:
        try:
            items = fetch(q)
        except Exception as e:
            log(f"[news] {q}: 실패 {e}")
            per_q[q] = -1
            continue
        per_q[q] = len(items)
        fetched.extend(items)
        time.sleep(1.0)
    seen_titles: dict[str, dict] = {}
    merged: dict[str, dict] = dict(prev)
    n_new = 0
    for it in sorted(fetched, key=lambda x: x["date"], reverse=True):
        if not RELEVANT.search(it["title"]):
            continue
        key = norm_title(it["title"])[:40]
        if key in seen_titles:
            continue
        seen_titles[key] = it
        if it["id"] in merged:
            continue
        if any(norm_title(p["title"])[:40] == key for p in prev.values()):
            continue
        it["cats"] = classify(it["title"], it["desc"])
        it["found"] = now.strftime("%Y-%m-%d")
        merged[it["id"]] = it
        n_new += 1
    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    items = [it for it in merged.values() if it.get("date", "") >= cutoff]
    for it in items:   # 구버전 레코드 보정
        it.setdefault("cats", classify(it["title"], it.get("desc", "")))
        it.setdefault("found", it["date"])
    items.sort(key=lambda x: (x["date"], x.get("found", "")), reverse=True)
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    counts = {"total": len(items), "new": n_new, "this_week": sum(1 for it in items if it["date"] >= week_ago),
              "by_cat": {c: sum(1 for it in items if c in it["cats"]) for c in [c for c, _ in CATS] + ["기타"]}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"collected_at": now.strftime("%Y-%m-%d %H:%M:%S"), "timezone": "Asia/Seoul", "queries": per_q,
                               "counts": counts, "items": items}, ensure_ascii=False, indent=0), encoding="utf-8")
    log(f"[news] 검색어 {len(QUERIES)}개 → 수집 {len(fetched)}건, 새 기사 {n_new}건, 보관 {len(items)}건 (최근 7일 {counts['this_week']}건)")
    log(f"[news] 분류: {counts['by_cat']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
