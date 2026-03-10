#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kin Crawler — 네이버 지식인 건강 질문 수집기
한의사가 답변하면 좋을 건강 관련 질문을 네이버 검색 API로 수집하여 JSON으로 저장
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

# API 호출당 결과 수, 키워드 간 딜레이(초)
DISPLAY_COUNT = 5
API_DELAY = 0.3

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


def strip_html(text: str) -> str:
    """HTML 태그 제거"""
    return re.sub(r'<[^>]+>', '', text).strip()


def parse_pubdate(pubdate_str: str) -> Optional[datetime]:
    """
    네이버 지식인 pubDate 파싱.
    예: "Mon, 10 Mar 2026 14:22:00 +0900"
    """
    try:
        # strptime은 %z 로 타임존 파싱 가능 (Python 3.2+)
        return datetime.strptime(pubdate_str, "%a, %d %b %Y %H:%M:%S %z")
    except Exception:
        return None


def fetch_kin_questions(keyword: str, category: str) -> list:
    """
    네이버 지식인 검색 API 호출, 필터링 후 질문 목록 반환.
    반환 형식: [{"title": str, "url": str, "category": str,
                "view_count": int, "answer_count": int, "pubdate": datetime}, ...]
    """
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    params = {
        "query": keyword,
        "display": DISPLAY_COUNT,
        "sort": "sim",  # 유사도 정렬
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
        # 날짜 파싱 실패 시 포함 (보수적으로 처리)
        if pubdate is not None and pubdate < cutoff:
            continue

        title = strip_html(item.get("title", ""))
        url = item.get("link", "")
        if not title or not url:
            continue

        results.append({
            "title": title,
            "url": url,
            "category": category,
            "view_count": 0,       # 검색 API에서는 제공 안 됨
            "answer_count": 0,     # 검색 API에서는 제공 안 됨
            "pubdate": pubdate,    # 정렬용, 저장 시 제거
        })

    return results


def collect_all_questions() -> list:
    """모든 키워드에 대해 API 호출 후 중복 제거, 우선순위 정렬하여 반환"""
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
        x["answer_count"],                                   # 답변 수 적은 것 우선
        -(x["pubdate"].timestamp() if x["pubdate"] else 0),  # 최신 우선
    ))

    # 카테고리별 최대 건수 제한 및 전체 최대 건수 제한
    category_counts: dict = {}
    result = []
    for item in all_items:
        cat = item["category"]
        count = category_counts.get(cat, 0)
        if count >= MAX_PER_CATEGORY:
            continue
        category_counts[cat] = count + 1
        result.append(item)
        if len(result) >= MAX_TOTAL:
            break

    return result


def save_output(items: list, date_str: str) -> Path:
    """output/kin_YYYY-MM-DD.json 으로 저장. pubdate 필드는 제거."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"kin_{date_str}.json"

    # 저장 시 내부용 pubdate 제거
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
