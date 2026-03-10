#!/usr/bin/env python3
"""
Hanmed News Integrator
한의학 뉴스 + 지식인 Pro JSON을 병합하여 GitHub Pages HTML 생성 + Slack 알림
"""

import os
import sys
import json
import time
import datetime
from datetime import timezone, timedelta
import requests
from pathlib import Path
from urllib.parse import quote
from jinja2 import Environment, FileSystemLoader

# ── 경로 설정 ────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
DOCS_DIR = BASE_DIR / "docs"
ARCHIVE_DIR = DOCS_DIR / "archive"
TEMPLATES_DIR = BASE_DIR / "templates"

# soo-kin-monitor output 경로 (환경변수로 오버라이드 가능)
KIN_OUTPUT_DIR = Path(
    os.getenv("KIN_OUTPUT_DIR", str(Path.home() / "Downloads/soo-kin-monitor/output"))
)

# GitHub Pages URL (환경변수로 오버라이드 가능)
GITHUB_PAGES_URL = os.getenv(
    "GITHUB_PAGES_URL", "https://sejongkim-ctrl.github.io/hanmed-news-monitor"
)

# Slack 채널
SLACK_CHANNEL = os.getenv("SLACK_CHANNEL", "C09TZ32M2KZ")

# GA4 Measurement ID (환경변수로 설정, 없으면 추적 비활성화)
GA4_ID = os.getenv("GA4_MEASUREMENT_ID", "")

# 요일 한글 매핑
WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]


# ── 날짜 유틸 ────────────────────────────────────────────────────────────────

def get_today_str() -> str:
    """YYYY-MM-DD 형식 오늘 날짜 (KST)"""
    KST = timezone(timedelta(hours=9))
    return datetime.datetime.now(KST).strftime("%Y-%m-%d")


def get_date_label(date_str: str) -> str:
    """2026-03-05 → 2026.03.05 (수)"""
    d = datetime.date.fromisoformat(date_str)
    weekday = WEEKDAY_KO[d.weekday()]
    return f"{d.strftime('%Y.%m.%d')} ({weekday})"


def get_short_date_label(date_str: str) -> str:
    """2026-03-05 → 3/5(수)"""
    d = datetime.date.fromisoformat(date_str)
    weekday = WEEKDAY_KO[d.weekday()]
    return f"{d.month}/{d.day}({weekday})"


# ── JSON 로더 ────────────────────────────────────────────────────────────────

def load_news_json(date_str: str) -> list:
    """
    output/YYYY-MM-DD.json 로드.
    형식: [{"title": str, "url": str, "summary": str, "source": str, "published_at": str}, ...]
    없으면 빈 리스트 반환.
    """
    path = OUTPUT_DIR / f"{date_str}.json"
    if not path.exists():
        print(f"[INFO] 뉴스 JSON 없음: {path}")
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        # hanmed_crawler는 {"date": ..., "articles": [...]} 형식으로 저장
        if isinstance(data, dict) and "articles" in data:
            data = data["articles"]
        if not isinstance(data, list):
            data = []
        # 크롤러 JSON 필드명 정규화 (source_name → source, published_date → published_at)
        for item in data:
            if "source_name" in item and "source" not in item:
                item["source"] = item["source_name"]
            if "published_date" in item and "published_at" not in item:
                item["published_at"] = item["published_date"]
        print(f"[INFO] 뉴스 {len(data)}건 로드: {path}")
        return data
    except Exception as e:
        print(f"[WARN] 뉴스 JSON 파싱 실패: {e}")
        return []


def load_kin_json(date_str: str) -> list:
    """
    지식인 질문 JSON 로드. 우선순위:
    1) output/kin_YYYY-MM-DD.json (hanmed-news-monitor 자체 kin_crawler 산출물)
    2) KIN_OUTPUT_DIR/pro_YYYY-MM-DD.json (soo-kin-monitor 산출물)
    형식: [{"title": str, "url": str, "view_count": int, "answer_count": int, "category": str}, ...]
    없으면 빈 리스트 반환.
    """
    def _load_file(path: Path) -> list:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            # soo-kin-monitor는 {"date": ..., "questions": [...]} 형식으로 저장
            if isinstance(data, dict) and "questions" in data:
                data = data["questions"]
            if not isinstance(data, list):
                data = []
            print(f"[INFO] 지식인 {len(data)}건 로드: {path}")
            return data
        except Exception as e:
            print(f"[WARN] 지식인 JSON 파싱 실패: {e}")
            return []

    # 1) 로컬 kin_crawler 산출물 우선
    local_path = OUTPUT_DIR / f"kin_{date_str}.json"
    if local_path.exists():
        return _load_file(local_path)

    # 2) soo-kin-monitor fallback
    path = KIN_OUTPUT_DIR / f"pro_{date_str}.json"
    if not path.exists():
        print(f"[INFO] 지식인 JSON 없음: {local_path}, {path}")
        return []
    return _load_file(path)


# ── Bitly ────────────────────────────────────────────────────────────────────

def shorten_url(long_url: str, token: str) -> str:
    """Bitly API로 URL 단축. 실패하면 원본 반환."""
    try:
        resp = requests.post(
            "https://api-ssl.bitly.com/v4/shorten",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"long_url": long_url},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("link", long_url)
        print(f"[WARN] Bitly {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        print(f"[WARN] Bitly 실패: {e}")
    return long_url


def shorten_urls_batch(items: list, url_key: str, token: str) -> list:
    """
    items 리스트의 각 dict에서 url_key를 단축 URL로 교체.
    token 없으면 원본 유지.
    """
    if not token:
        return items
    result = []
    for item in items:
        original = item.get(url_key, "")
        if original:
            short = shorten_url(original, token)
            item = {**item, url_key: short, f"{url_key}_original": original}
            time.sleep(0.3)  # Bitly rate limit 방지
        result.append(item)
    return result


# ── 추적 URL 생성 ─────────────────────────────────────────────────────────────

def add_tracking_urls(items: list, date_str: str, content_type: str) -> list:
    """
    각 아이템에 go.html 경유 추적 URL(tracking_url)을 추가한다.
    content_type: "news" 또는 "kin"
    """
    result = []
    for idx, item in enumerate(items, 1):
        original_url = item.get("url", "")
        if original_url:
            tracking_url = (
                f"go.html?url={quote(original_url, safe='')}"
                f"&id={idx}&date={date_str}&type={content_type}"
            )
            item = {**item, "tracking_url": tracking_url}
        result.append(item)
    return result


# ── HTML 생성 ────────────────────────────────────────────────────────────────

def render_html(date_str: str, news_items: list, kin_items: list) -> str:
    """Jinja2로 daily_page.html 렌더링"""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
    )
    template = env.get_template("daily_page.html")
    return template.render(
        date_str=date_str,
        date_label=get_date_label(date_str),
        news_items=news_items,
        kin_items=kin_items,
        has_news=len(news_items) > 0,
        has_kin=len(kin_items) > 0,
        archive_url=f"{GITHUB_PAGES_URL}/archive/",
        generated_at=datetime.datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M"),
        ga_id=GA4_ID,
    )


def write_html(html: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[INFO] HTML 저장: {path}")


def build_archive_index(docs_dir: Path) -> None:
    """archive/ 하위 HTML 파일 목록으로 archive/index.html 생성"""
    archive_dir = docs_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(archive_dir.glob("????-??-??.html"), reverse=True)

    links_html = "\n".join(
        f'<li><a href="{f.name}">{f.stem}</a></li>'
        for f in files
        if f.name != "index.html"
    )
    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>수壽 Daily — 아카이브</title>
  <style>
    body {{ font-family: 'Noto Sans KR', sans-serif; max-width: 700px; margin: 40px auto; padding: 0 20px; }}
    h1 {{ color: #891C21; }}
    ul {{ list-style: none; padding: 0; }}
    li {{ margin: 8px 0; }}
    a {{ color: #891C21; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <h1>수壽 Daily 아카이브</h1>
  <ul>{links_html}</ul>
  <p><a href="../index.html">← 오늘의 브리핑으로</a></p>
</body>
</html>"""
    with open(archive_dir / "index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[INFO] 아카이브 인덱스 갱신: {len(files)}건")


# ── Slack ────────────────────────────────────────────────────────────────────

def send_slack(
    token: str,
    channel: str,
    news_count: int,
    kin_count: int,
    date_str: str,
    page_url: str,
) -> None:
    """Slack chat.postMessage로 알림 전송"""
    if not token:
        print("[INFO] SLACK_USER_TOKEN 없음 — Slack 전송 스킵")
        return

    short_date = get_short_date_label(date_str)
    main_text = (
        f"[수壽 Daily] {get_date_label(date_str)}\n"
        f"오늘의 한의계 뉴스 {news_count}건"
        + (f" + 환자 질문 {kin_count}건" if kin_count else "")
        + f" 정리했습니다.\n"
        f"👉 {page_url}"
    )

    copy_text = (
        f"단톡방 전달용 복사 텍스트:\n"
        f"[수壽 Daily] {short_date}\n"
        f"오늘의 한의계 뉴스 {news_count}건"
        + (f" + 환자 질문 {kin_count}건" if kin_count else "")
        + f"\n👉 {page_url}"
    )

    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    # 메인 메시지
    resp = requests.post(
        url,
        headers=headers,
        json={"channel": channel, "text": main_text, "mrkdwn": True},
        timeout=10,
    )
    data = resp.json()
    if not data.get("ok"):
        print(f"[ERROR] Slack 전송 실패: {data.get('error')}")
        return

    message_ts = data.get("ts")
    print(f"[INFO] Slack 메인 메시지 전송 완료 (ts: {message_ts})")

    # 스레드 — 복사용 텍스트
    time.sleep(1)
    resp2 = requests.post(
        url,
        headers=headers,
        json={
            "channel": channel,
            "text": copy_text,
            "thread_ts": message_ts,
            "mrkdwn": True,
        },
        timeout=10,
    )
    if resp2.json().get("ok"):
        print("[INFO] Slack 스레드(복사용) 전송 완료")
    else:
        print(f"[WARN] Slack 스레드 전송 실패: {resp2.json().get('error')}")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    date_str = os.getenv("TARGET_DATE", get_today_str())
    bitly_token = os.getenv("BITLY_TOKEN", "")
    slack_token = os.getenv("SLACK_USER_TOKEN", "")

    print(f"[START] integrator.py — {date_str}")

    # 1. JSON 로드
    news_items = load_news_json(date_str)
    kin_items = load_kin_json(date_str)

    if not news_items and not kin_items:
        print("[WARN] 뉴스/지식인 데이터 모두 없음 — placeholder 페이지 생성")

    # 2. URL 단축 (Bitly)
    if bitly_token:
        print("[INFO] Bitly URL 단축 시작...")
        news_items = shorten_urls_batch(news_items, "url", bitly_token)
        kin_items = shorten_urls_batch(kin_items, "url", bitly_token)

    # 3. 추적 URL 생성 (go.html 경유)
    news_items = add_tracking_urls(news_items, date_str, "news")
    kin_items = add_tracking_urls(kin_items, date_str, "kin")

    # 4. HTML 렌더링
    html = render_html(date_str, news_items, kin_items)

    # 5. docs/index.html (최신) + docs/archive/YYYY-MM-DD.html 저장
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    write_html(html, DOCS_DIR / "index.html")
    write_html(html, ARCHIVE_DIR / f"{date_str}.html")

    # 6. 아카이브 인덱스 갱신
    build_archive_index(DOCS_DIR)

    # 7. GitHub Pages URL 결정 (Bitly 단축)
    page_url = GITHUB_PAGES_URL
    if bitly_token:
        page_url = shorten_url(GITHUB_PAGES_URL, bitly_token)

    # 8. Slack 알림
    send_slack(
        token=slack_token,
        channel=SLACK_CHANNEL,
        news_count=len(news_items),
        kin_count=len(kin_items),
        date_str=date_str,
        page_url=page_url,
    )

    print(f"[DONE] 완료 — 뉴스 {len(news_items)}건, 지식인 {len(kin_items)}건")


if __name__ == "__main__":
    main()
