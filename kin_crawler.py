#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kin Crawler — 네이버 통합검색 지식인 상위 노출 질문 수집기

네이버에서 한의학 관련 키워드를 검색했을 때, 통합검색 결과에서
지식iN 블록에 상위 노출되고 있는 질문을 수집한다.

필터링 조건:
  1) 네이버 통합검색 상위에 지식인 블록이 노출된 질문만 수집
  2) 질문마감 상태인 건은 제외
  3) 한의학과 무관한 질문은 제외
  4) 해당 질문이 노출된 검색 키워드 표기
"""

import os
import re
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

# ── KST 타임존 ────────────────────────────────────────────────────────────────
KST = timezone(timedelta(hours=9))

# ── 경로 설정 ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"

# ── 설정 ──────────────────────────────────────────────────────────────────────
SEARCH_DELAY = 0.7    # 네이버 검색 간 딜레이(초)
PAGE_DELAY = 0.5      # 질문 페이지 확인 딜레이(초)
MAX_TOTAL = 15        # 최종 출력 최대 건수

# ── 검색 키워드 → 카테고리 매핑 ───────────────────────────────────────────────
# 실제 사용자가 네이버에 검색하는 키워드 기반
SEARCH_KEYWORDS = {
    # 공진단
    "이정재공진단": "공진단",
    "사향공진단 효과": "공진단",
    "공진단 효능": "공진단",
    "공진단 가격": "공진단",
    "총명공진단": "공진단",
    # 경옥고
    "경옥고 효능": "경옥고",
    "녹용경옥고": "경옥고",
    "경옥고 먹는법": "경옥고",
    # 녹용
    "녹용한약 효과": "녹용",
    "녹용 가격": "녹용",
    "아이 녹용한약": "녹용",
    # 한약 일반
    "다이어트 한약": "다이어트",
    "다이어트한약 효과": "다이어트",
    "보약 추천": "보약",
    "수험생 보약": "수험생",
    "산후조리 한약": "산후조리",
    "갱년기 한약": "갱년기",
    # 한의원 치료
    "비염 한방치료": "비염_호흡기",
    "아토피 한의원": "피부_아토피",
    "추나치료 효과": "추나",
    "디스크 한의원": "통증_근골격",
    "침치료 효과": "침_뜸",
    "구안와사 한의원": "구안와사",
    # 한의원 추천
    "한의원 추천": "한의원_상담",
    "한방병원 추천": "한의원_상담",
    # 체질
    "사상체질 검사": "체질",
    "체질 진단": "체질",
}

# ── 한의학 관련 키워드 (관련성 필터용) ─────────────────────────────────────────
KM_TERMS = {
    "한약", "한의원", "한의사", "한방", "침", "뜸", "추나", "부항",
    "공진단", "경옥고", "녹용", "보약", "보양", "사상체질",
    "소음인", "소양인", "태음인", "태양인", "체질",
    "동의보감", "본초", "처방", "약침", "한약재",
    "산후조리", "산후풍", "탕약", "환약", "첩약",
    "갱년기", "성장클리닉",
    "구안와사", "안면마비", "이명", "비염", "아토피",
    "디스크", "오십견", "관절", "허리",
    "한방병원", "한방치료", "약재", "경락", "경혈",
}

# ── 스크래핑용 헤더 ────────────────────────────────────────────────────────────
SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}


def is_km_related(title: str) -> bool:
    """제목에 한의학 관련 키워드가 포함되어 있는지 확인"""
    return any(term in title for term in KM_TERMS)


def is_answerable(url: str) -> bool:
    """
    질문 페이지를 방문하여 답변 가능 여부 확인.
    "질문마감" 상태인 경우만 제외.
    """
    try:
        resp = requests.get(url, headers=SCRAPE_HEADERS, timeout=10)
        if resp.status_code != 200:
            return True

        if "질문마감" in resp.text:
            print(f"    [SKIP] 질문마감")
            return False
        return True
    except Exception as e:
        print(f"    [WARN] 페이지 확인 실패: {e}")
        return True


def fetch_kin_from_naver_search(keyword: str, category: str) -> list:
    """
    네이버 통합검색에서 키워드 검색 후,
    지식iN 블록에 상위 노출된 질문을 수집한다.
    """
    url = f"https://search.naver.com/search.naver?query={quote(keyword)}"
    try:
        resp = requests.get(url, headers=SCRAPE_HEADERS, timeout=10)
        if resp.status_code != 200:
            print(f"  [WARN] 네이버 검색 실패 ({keyword}): HTTP {resp.status_code}")
            return []
    except Exception as e:
        print(f"  [WARN] 네이버 검색 요청 실패 ({keyword}): {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")

    # kin.naver.com/qna/detail 링크 수집
    kin_links = soup.find_all(
        "a", href=lambda h: h and "kin.naver.com/qna/detail" in h
    )

    # docId 기준 중복 제거 + 제목 추출
    seen_docs: dict = {}
    for link in kin_links:
        href = link.get("href", "")
        text = link.get_text(strip=True)

        parsed = urlparse(href)
        params = parse_qs(parsed.query)
        doc_id = params.get("docId", [""])[0]
        if not doc_id:
            continue

        # 답변 링크 제외
        if "answerNo" in params:
            continue

        # 의미 없는 텍스트 제외
        if not text or text == "네이버 지식iN" or len(text) < 5:
            continue

        # 첫 번째 의미 있는 텍스트를 제목으로, 더 짧은 것으로 갱신
        if doc_id not in seen_docs:
            seen_docs[doc_id] = {"title": text, "url": href, "doc_id": doc_id}
        elif len(text) < len(seen_docs[doc_id]["title"]):
            seen_docs[doc_id]["title"] = text

    results = []
    for item in seen_docs.values():
        title = item["title"]

        # 한의학 관련성 체크
        if not is_km_related(title):
            print(f"    [SKIP] 한의학 무관: {title[:40]}")
            continue

        results.append({
            "title": title,
            "url": item["url"],
            "category": category,
            "keyword": keyword,
        })

    return results


def collect_all_questions() -> list:
    """모든 키워드로 네이버 통합검색 → 지식인 블록 수집 → 필터링"""
    seen_urls: set = set()
    all_items: list = []

    for keyword, category in SEARCH_KEYWORDS.items():
        print(f"  검색: {keyword} → {category}")
        items = fetch_kin_from_naver_search(keyword, category)

        for item in items:
            url = item["url"]
            # docId 기준 중복 제거 (다른 키워드에서 같은 질문이 나올 수 있음)
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            doc_id = params.get("docId", [""])[0]

            if doc_id in seen_urls:
                continue
            seen_urls.add(doc_id)
            all_items.append(item)

        if items:
            print(f"    → 지식인 블록 {len(items)}건 발견")
        time.sleep(SEARCH_DELAY)

    # 질문마감 필터링
    print(f"\n[INFO] 답변 가능 여부 확인 중... ({len(all_items)}건)")
    result = []
    for item in all_items:
        print(f"  확인: {item['title'][:50]}...")
        if not is_answerable(item["url"]):
            time.sleep(PAGE_DELAY)
            continue

        result.append(item)
        if len(result) >= MAX_TOTAL:
            break
        time.sleep(PAGE_DELAY)

    return result


def save_output(items: list, date_str: str) -> Path:
    """output/kin_YYYY-MM-DD.json 으로 저장"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"kin_{date_str}.json"

    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    print(f"[INFO] 지식인 {len(items)}건 저장: {path}")
    return path


def main():
    now = datetime.now(KST)
    date_str = now.strftime("%Y-%m-%d")

    print("=" * 50)
    print(f"Kin Crawler — {date_str}")
    print("네이버 통합검색 지식인 상위 노출 질문 수집")
    print("=" * 50)

    items = collect_all_questions()
    save_output(items, date_str)

    print(f"\n완료: {len(items)}건 수집")


if __name__ == "__main__":
    main()
