#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kin Crawler — 네이버 지식인 건강 질문 수집기
한의사가 답변하면 좋을 건강 관련 질문을 네이버 검색 API로 수집하여 JSON으로 저장

필터링 조건:
  1) 최근 7일 이내 질문
  2) 한의학 관련 키워드가 제목/설명에 포함된 질문만
  3) 답변 채택 완료 또는 질문 마감된 건은 제외
  4) 검색 키워드(네이버 노출 키워드) 표기
"""

import os
import re
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

# ── KST 타임존 ────────────────────────────────────────────────────────────────
KST = timezone(timedelta(hours=9))

# ── 경로 설정 ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"

# ── Naver API 설정 ────────────────────────────────────────────────────────────
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")
KIN_API_URL = "https://openapi.naver.com/v1/search/kin.json"

# API 호출당 결과 수, 딜레이(초)
DISPLAY_COUNT = 10
API_DELAY = 0.3
PAGE_DELAY = 0.5  # 페이지 스크래핑 간 딜레이

# 최근 N일 이내 질문만 수집
RECENT_DAYS = 7

# 출력 최대 건수 및 카테고리별 최대 건수
MAX_TOTAL = 10
MAX_PER_CATEGORY = 2

# ── 키워드 → 카테고리 매핑 ────────────────────────────────────────────────────
KEYWORD_CATEGORIES = {
    "한약 효과": "한약_일반",
    "한의원 추천": "한의원_상담",
    "공진단 복용": "공진단",
    "경옥고 효능": "경옥고",
    "녹용 한약": "녹용",
    "보약 추천": "보약_일반",
    "침 치료 효과": "침_뜸",
    "추나 치료": "추나",
    "한방 다이어트": "다이어트",
    "한방 불면증": "수면_스트레스",
    "아토피 한방": "피부_아토피",
    "비염 한방치료": "비염_호흡기",
    "디스크 한방": "통증_근골격",
    "갱년기 한약": "갱년기_여성",
    "산후조리 한약": "산후_임산부",
    "수험생 보약": "수험생_집중력",
    "체질 진단": "체질_사상",
}

# ── 한의학 관련 키워드 (관련성 필터용) ─────────────────────────────────────────
KM_TERMS = {
    "한약", "한의원", "한의사", "한방", "침", "뜸", "추나", "부항",
    "공진단", "경옥고", "녹용", "보약", "보양", "사상체질",
    "소음인", "소양인", "태음인", "태양인", "체질",
    "동의보감", "본초", "처방", "약침", "한약재",
    "산후조리", "산후풍", "탕약", "환약", "첩약",
    "갱년기", "성장클리닉", "보약",
    "구안와사", "안면마비", "이명", "비염", "아토피",
    "디스크", "오십견", "관절", "허리",
    "한방병원", "한방치료", "약재", "경락", "경혈", "혈자리",
}

# ── 스크래핑용 헤더 ────────────────────────────────────────────────────────────
SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}


def strip_html(text: str) -> str:
    """HTML 태그 제거"""
    return re.sub(r'<[^>]+>', '', text).strip()


def parse_pubdate(pubdate_str: str) -> Optional[datetime]:
    """네이버 지식인 pubDate 파싱"""
    try:
        return datetime.strptime(pubdate_str, "%a, %d %b %Y %H:%M:%S %z")
    except Exception:
        return None


def is_km_related(title: str, description: str) -> bool:
    """제목 + 설명에 한의학 관련 키워드가 포함되어 있는지 확인"""
    text = f"{title} {description}"
    return any(term in text for term in KM_TERMS)


def is_answerable(url: str) -> bool:
    """
    질문 페이지를 방문하여 답변 가능 여부 확인.
    "질문마감" 상태인 경우만 제외. 채택된 답변이 있어도 추가 답변은 가능하다.
    """
    try:
        resp = requests.get(url, headers=SCRAPE_HEADERS, timeout=10)
        if resp.status_code != 200:
            return True  # 확인 불가 시 포함 (보수적)

        html = resp.text

        # 질문 마감 여부만 체크 (채택 답변 존재와 답변 가능은 별개)
        if "질문마감" in html:
            print(f"    [SKIP] 질문마감")
            return False

        return True
    except Exception as e:
        print(f"    [WARN] 페이지 확인 실패: {e}")
        return True


def fetch_kin_questions(keyword: str, category: str) -> list:
    """
    네이버 지식인 검색 API 호출 → 한의학 관련성 필터 적용 → 질문 목록 반환.
    각 항목에 검색 키워드(keyword) 필드 포함.
    """
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    params = {
        "query": keyword,
        "display": DISPLAY_COUNT,
        "sort": "date",  # 최신순 — 미채택 질문 확보율 향상
    }

    try:
        resp = requests.get(KIN_API_URL, headers=headers, params=params, timeout=10)
        if resp.status_code != 200:
            print(f"[WARN] 네이버 API 오류 ({keyword}): HTTP {resp.status_code}")
            return []
        data = resp.json()
    except Exception as e:
        print(f"[WARN] 네이버 API 요청 실패 ({keyword}): {e}")
        return []

    items = data.get("items", [])
    now_kst = datetime.now(KST)
    cutoff = now_kst - timedelta(days=RECENT_DAYS)

    results = []
    for item in items:
        pubdate = parse_pubdate(item.get("pubDate", ""))
        if pubdate is not None and pubdate < cutoff:
            continue

        title = strip_html(item.get("title", ""))
        description = strip_html(item.get("description", ""))
        url = item.get("link", "")
        if not title or not url:
            continue

        # 한의학 관련성 필터
        if not is_km_related(title, description):
            print(f"    [SKIP] 한의학 무관: {title[:40]}")
            continue

        results.append({
            "title": title,
            "url": url,
            "category": category,
            "keyword": keyword,
            "view_count": 0,
            "answer_count": 0,
            "pubdate": pubdate,
        })

    return results


def collect_all_questions() -> list:
    """모든 키워드에 대해 API 호출 → 관련성 필터 → 채택 필터 → 최종 목록 반환"""
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        print("[WARN] NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 미설정. 지식인 수집 건너뜀.")
        return []

    seen_urls: set = set()
    all_items: list = []

    for keyword, category in KEYWORD_CATEGORIES.items():
        print(f"  검색 중: {keyword} → {category}")
        items = fetch_kin_questions(keyword, category)
        for item in items:
            url = item["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            all_items.append(item)
        time.sleep(API_DELAY)

    # 우선순위 정렬: 답변 수 적은 것 → 최신순
    all_items.sort(key=lambda x: (
        x["answer_count"],
        -(x["pubdate"].timestamp() if x["pubdate"] else 0),
    ))

    # 카테고리별 최대 건수 제한 (채택 필터 전 여유분 확보)
    category_counts: dict = {}
    candidates = []
    for item in all_items:
        cat = item["category"]
        count = category_counts.get(cat, 0)
        if count >= MAX_PER_CATEGORY * 2:  # 채택 필터링 여유분
            continue
        category_counts[cat] = count + 1
        candidates.append(item)
        if len(candidates) >= MAX_TOTAL * 3:
            break

    # 채택/마감 필터링 (페이지 스크래핑)
    print(f"\n[INFO] 답변 가능 여부 확인 중... ({len(candidates)}건)")
    category_counts_final: dict = {}
    result = []
    for item in candidates:
        print(f"  확인: {item['title'][:50]}...")
        if not is_answerable(item["url"]):
            time.sleep(PAGE_DELAY)
            continue

        cat = item["category"]
        cat_count = category_counts_final.get(cat, 0)
        if cat_count >= MAX_PER_CATEGORY:
            time.sleep(PAGE_DELAY)
            continue
        category_counts_final[cat] = cat_count + 1

        result.append(item)
        if len(result) >= MAX_TOTAL:
            break
        time.sleep(PAGE_DELAY)

    return result


def save_output(items: list, date_str: str) -> Path:
    """output/kin_YYYY-MM-DD.json 으로 저장. pubdate 필드는 제거."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"kin_{date_str}.json"

    clean_items = [
        {k: v for k, v in item.items() if k != "pubdate"}
        for item in items
    ]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(clean_items, f, ensure_ascii=False, indent=2)

    print(f"[INFO] 지식인 {len(clean_items)}건 저장: {path}")
    return path


def main():
    now = datetime.now(KST)
    date_str = now.strftime("%Y-%m-%d")

    print("=" * 50)
    print(f"Kin Crawler — {date_str}")
    print("=" * 50)

    items = collect_all_questions()
    save_output(items, date_str)

    print(f"\n완료: {len(items)}건 수집")


if __name__ == "__main__":
    main()
