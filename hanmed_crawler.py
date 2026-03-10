#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hanmed News Monitor
한의학 신문(한의신문, 민족의학신문) 기사 크롤링 + Gemini 요약 + Slack 알림
"""

import os
import sys
import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

from config import (
    CRAWL_SOURCES,
    GEMINI_MODEL,
    GEMINI_TEMPERATURE,
    GEMINI_MAX_OUTPUT_TOKENS,
    SLACK_CHANNEL,
    COLLECTION_PERIOD_HOURS,
    BATCH_SIZE,
    HTTP_TIMEOUT,
    HTTP_HEADERS,
    SUMMARY_PROMPT_TEMPLATE,
    REPORT_TITLE,
)

# ─────────────────────────────────────────
# Gemini 초기화
# ─────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SLACK_USER_TOKEN = os.getenv("SLACK_USER_TOKEN")

_gemini_model = None

if GEMINI_API_KEY:
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        _gemini_model = genai.GenerativeModel(GEMINI_MODEL)
        print(f"Gemini 모델 로드 완료: {GEMINI_MODEL}")
    except Exception as e:
        print(f"Warning: Gemini 초기화 실패 — {e}")
else:
    print("Warning: GEMINI_API_KEY 없음. AI 요약 비활성화.")


# ─────────────────────────────────────────
# 크롤러: 한의신문 (akomnews.com)
# ─────────────────────────────────────────
def crawl_akomnews(cutoff: datetime) -> List[Dict]:
    """한의신문 기사 목록 크롤링"""
    source_info = next(s for s in CRAWL_SOURCES if s["source_key"] == "akomnews")
    url = source_info["url"]
    articles = []

    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # a[href*="wr_id"] 기사 링크를 기준으로 부모 li에서 파싱
        article_links = soup.select("a[href*='wr_id']")
        print(f"한의신문: {len(article_links)}개 행 발견")

        seen_urls = set()
        for a_tag in article_links:
            href = a_tag.get("href", "")
            if not href or href in seen_urls:
                continue
            seen_urls.add(href)

            parent_li = a_tag.find_parent("li")
            article = _parse_akomnews_row(a_tag, parent_li, cutoff)
            if article:
                articles.append(article)

    except Exception as e:
        print(f"한의신문 크롤링 실패: {e}")

    return articles


def _parse_akomnews_row(a_tag, parent_li, cutoff: datetime) -> Optional[Dict]:
    """한의신문 개별 행 파싱 — a 태그와 부모 li 기반"""
    try:
        href = a_tag.get("href", "")
        if not href:
            return None

        article_url = href if href.startswith("http") else "https://www.akomnews.com" + href

        # 제목: li 내 h2 또는 a 태그 텍스트
        if parent_li:
            h2 = parent_li.select_one("h2")
            title = h2.get_text(strip=True) if h2 else a_tag.get_text(strip=True)
        else:
            title = a_tag.get_text(strip=True)

        if not title:
            return None

        # 날짜: li.date (실제 구조: <li class="date">2026-03-05 17:08</li>)
        pub_date = None
        if parent_li:
            date_li = parent_li.select_one("li.date")
            if date_li:
                pub_date = _parse_date(date_li.get_text(strip=True))

        if pub_date and pub_date < cutoff:
            return None

        body_preview = _fetch_body_preview(article_url)

        return {
            "title": title,
            "url": article_url,
            "source_name": "한의신문",
            "published_date": pub_date.strftime("%Y-%m-%d") if pub_date else datetime.now().strftime("%Y-%m-%d"),
            "body_preview": body_preview,
            "summary": ""
        }

    except Exception:
        return None


# ─────────────────────────────────────────
# 크롤러: 민족의학신문 (mjmedi.com)
# ─────────────────────────────────────────
def crawl_mjmedi(cutoff: datetime) -> List[Dict]:
    """민족의학신문 기사 목록 크롤링"""
    source_info = next(s for s in CRAWL_SOURCES if s["source_key"] == "mjmedi")
    url = source_info["url"]
    articles = []

    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # a.links[href*="articleView"] 기사 링크 기준
        article_links = soup.select("a.links[href*='articleView']")
        if not article_links:
            # 폴백: 클래스 없이 href만 기준
            article_links = soup.select("a[href*='articleView']")

        print(f"민족의학신문: {len(article_links)}개 행 발견")

        seen_urls = set()
        for a_tag in article_links:
            href = a_tag.get("href", "")
            if not href or href in seen_urls:
                continue
            seen_urls.add(href)

            article = _parse_mjmedi_row(a_tag, cutoff)
            if article:
                articles.append(article)

    except Exception as e:
        print(f"민족의학신문 크롤링 실패: {e}")

    return articles


def _parse_mjmedi_row(a_tag, cutoff: datetime) -> Optional[Dict]:
    """민족의학신문 개별 행 파싱 — a 태그 기반"""
    try:
        href = a_tag.get("href", "")
        if not href:
            return None

        if href.startswith("http"):
            article_url = href
        elif href.startswith("/"):
            article_url = "http://www.mjmedi.com" + href
        else:
            article_url = "http://www.mjmedi.com/" + href

        # 제목: strong 또는 a 태그 텍스트
        strong = a_tag.select_one("strong")
        title = strong.get_text(strip=True) if strong else a_tag.get_text(strip=True)
        if not title:
            return None

        # 날짜: 같은 tr/li 행 내 .list-dated (형식: "기자명 | 2026-03-05 22:32")
        pub_date = None
        row_container = (
            a_tag.find_parent("tr")
            or a_tag.find_parent("li")
            or a_tag.find_parent("div", class_=lambda c: c and "list" in " ".join(c))
        )
        if row_container:
            date_tag = row_container.select_one(".list-dated")
            if date_tag:
                raw = date_tag.get_text(strip=True)
                # "기자명 | 2026-03-05 22:32" 형식에서 날짜 추출
                pub_date = _parse_date(raw)

        if pub_date and pub_date < cutoff:
            return None

        body_preview = _fetch_body_preview(article_url)

        return {
            "title": title,
            "url": article_url,
            "source_name": "민족의학신문",
            "published_date": pub_date.strftime("%Y-%m-%d") if pub_date else datetime.now().strftime("%Y-%m-%d"),
            "body_preview": body_preview,
            "summary": ""
        }

    except Exception:
        return None


# ─────────────────────────────────────────
# 공통 유틸
# ─────────────────────────────────────────
def _parse_date(date_str: str) -> Optional[datetime]:
    """다양한 날짜 포맷 파싱 — 시간 포함 시 시간도 반영"""
    if not date_str:
        return None

    # 날짜+시간: "2026-03-05 17:08" 또는 "2026.03.05 17:08"
    m = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})\s+(\d{1,2}):(\d{2})", date_str)
    if m:
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            hh, mm = int(m.group(4)), int(m.group(5))
            return datetime(y, mo, d, hh, mm)
        except Exception:
            pass

    # 날짜만: "2026-03-05" 또는 "2026.03.05"
    m = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", date_str)
    if m:
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            # 날짜만 있을 때는 23:59로 설정 — 당일 기사가 cutoff에 탈락하지 않도록
            return datetime(y, mo, d, 23, 59)
        except Exception:
            pass

    # YYYYMMDD: 20260305
    m = re.search(r"(\d{4})(\d{2})(\d{2})", date_str)
    if m:
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return datetime(y, mo, d, 23, 59)
        except Exception:
            pass

    return None


def _fetch_body_preview(url: str, max_chars: int = 500) -> str:
    """기사 본문 첫 500자 추출"""
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # 본문 영역 선택 (사이트별 클래스)
        content = (
            soup.select_one("#bo_v_con")                    # 그누보드 기본 (한의신문)
            or soup.select_one("#article-view-content-div") # 민족의학신문
            or soup.select_one(".view_con")
            or soup.select_one(".article-body")
            or soup.select_one(".view_content")
            or soup.select_one(".article-view-content")
        )

        if content:
            text = content.get_text(separator=" ", strip=True)
        else:
            text = soup.get_text(separator=" ", strip=True)

        # 공백 정리
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]

    except Exception:
        return ""


# ─────────────────────────────────────────
# Gemini 배치 요약
# ─────────────────────────────────────────
def summarize_batch(articles: List[Dict]) -> List[Dict]:
    """
    5개씩 묶어 Gemini 1회 API 호출로 3줄 요약 생성.
    실패 시 summary는 빈 문자열로 유지.
    """
    if not _gemini_model:
        print("Gemini 비활성화 — 요약 없이 제목만 사용")
        return articles

    total = len(articles)
    for batch_start in range(0, total, BATCH_SIZE):
        batch = articles[batch_start: batch_start + BATCH_SIZE]
        print(f"Gemini 배치 요약: {batch_start + 1}~{batch_start + len(batch)} / {total}")

        articles_block_parts = []
        for idx, art in enumerate(batch, 1):
            articles_block_parts.append(
                f"[기사 {idx}]\n"
                f"제목: {art['title']}\n"
                f"출처: {art['source_name']}\n"
                f"본문 일부: {art['body_preview']}"
            )
        articles_block = "\n\n".join(articles_block_parts)

        prompt = SUMMARY_PROMPT_TEMPLATE.format(articles_block=articles_block)

        try:
            response = _gemini_model.generate_content(
                prompt,
                generation_config={
                    "temperature": GEMINI_TEMPERATURE,
                    "max_output_tokens": GEMINI_MAX_OUTPUT_TOKENS,
                }
            )
            response_text = response.text.strip() if response.text else ""
            parsed = _parse_batch_summaries(response_text, len(batch))

            for i, art in enumerate(batch):
                art["summary"] = parsed[i] if i < len(parsed) else ""

        except Exception as e:
            print(f"Gemini 배치 요약 실패: {e}")
            # 실패 시 summary 빈 문자열 유지 — 제목만으로 Slack 전송

        # rate limit 방지
        if batch_start + BATCH_SIZE < total:
            time.sleep(1)

    return articles


def _parse_batch_summaries(response_text: str, count: int) -> List[str]:
    """[요약 N] 패턴으로 응답 분리"""
    pattern = r"\[요약\s*(\d+)\]\s*"
    parts = re.split(pattern, response_text)

    parsed = {}
    for i in range(1, len(parts), 2):
        if i + 1 < len(parts):
            num = int(parts[i])
            text = parts[i + 1].strip()
            parsed[num] = text

    result = []
    for n in range(1, count + 1):
        result.append(parsed.get(n, ""))

    return result


# ─────────────────────────────────────────
# JSON 저장
# ─────────────────────────────────────────
def save_to_json(articles: List[Dict], date_str: str) -> str:
    output_dir = os.path.join(os.path.dirname(__file__), "output")
    os.makedirs(output_dir, exist_ok=True)

    payload = {
        "date": date_str,
        "source": "hanmed-news",
        "articles": articles
    }

    filepath = os.path.join(output_dir, f"{date_str}.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"JSON 저장: {filepath}")
    return filepath


# ─────────────────────────────────────────
# Slack 전송
# ─────────────────────────────────────────
def send_slack(articles: List[Dict], date_str: str) -> None:
    if not SLACK_USER_TOKEN:
        print("SLACK_USER_TOKEN 없음 — Slack 전송 스킵")
        return

    if not articles:
        print("기사 없음 — Slack 전송 스킵")
        return

    api_url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {SLACK_USER_TOKEN}",
        "Content-Type": "application/json; charset=utf-8"
    }

    # 메인 메시지
    main_text = f"{REPORT_TITLE} | {date_str} ({len(articles)}건)"
    payload = {"channel": SLACK_CHANNEL, "text": main_text, "mrkdwn": True}

    try:
        resp = requests.post(api_url, headers=headers, json=payload, timeout=10)
        data = resp.json()
        if not data.get("ok"):
            print(f"Slack 메인 메시지 실패: {data.get('error')}")
            return
        thread_ts = data["ts"]
        print(f"Slack 메인 메시지 전송 완료 (ts: {thread_ts})")
    except Exception as e:
        print(f"Slack 메인 메시지 오류: {e}")
        return

    # 스레드: 기사별 요약
    for i, art in enumerate(articles, 1):
        summary_part = f"\n{art['summary']}" if art["summary"] else ""
        thread_text = (
            f"[{i}] {art['source_name']} | {art['published_date']}\n"
            f"{art['title']}\n"
            f"{art['url']}"
            f"{summary_part}"
        )
        thread_payload = {
            "channel": SLACK_CHANNEL,
            "text": thread_text,
            "thread_ts": thread_ts,
            "mrkdwn": True
        }
        try:
            resp = requests.post(api_url, headers=headers, json=thread_payload, timeout=10)
            data = resp.json()
            if not data.get("ok"):
                print(f"스레드 {i} 전송 실패: {data.get('error')}")
        except Exception as e:
            print(f"스레드 {i} 오류: {e}")

        time.sleep(0.5)

    print(f"Slack 전송 완료: {len(articles)}건")


# ─────────────────────────────────────────
# 메인
# ─────────────────────────────────────────
def main():
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)
    date_str = now.strftime("%Y-%m-%d")
    cutoff = now - timedelta(hours=COLLECTION_PERIOD_HOURS)

    print("=" * 50)
    print(f"Hanmed News Monitor — {date_str}")
    print(f"수집 기준: {cutoff.strftime('%Y-%m-%d %H:%M')} 이후")
    print("=" * 50)

    # 1. 크롤링 (사이트 접속 실패 시 스킵)
    articles = []

    print("\n[한의신문 크롤링]")
    akom_articles = crawl_akomnews(cutoff)
    print(f"  => {len(akom_articles)}건 수집")
    articles.extend(akom_articles)

    print("\n[민족의학신문 크롤링]")
    mjmedi_articles = crawl_mjmedi(cutoff)
    print(f"  => {len(mjmedi_articles)}건 수집")
    articles.extend(mjmedi_articles)

    print(f"\n총 {len(articles)}건 수집")

    if not articles:
        print("수집된 기사 없음. 종료.")
        # 빈 JSON 저장
        save_to_json([], date_str)
        return 0

    # 2. Gemini 배치 요약
    print("\n[Gemini 배치 요약]")
    articles = summarize_batch(articles)

    # 3. JSON 저장
    print("\n[JSON 저장]")
    json_path = save_to_json(articles, date_str)

    # 4. Slack 전송
    print("\n[Slack 전송]")
    send_slack(articles, date_str)

    print("\n완료.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
