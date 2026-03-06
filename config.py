"""
Hanmed News Monitor Configuration
한의학 신문 기사 크롤링 및 요약 설정
"""

# 크롤링 대상 사이트
CRAWL_SOURCES = [
    {
        "name": "한의신문",
        "url": "https://www.akomnews.com/bbs/board.php?bo_table=news",
        "source_key": "akomnews"
    },
    {
        "name": "민족의학신문",
        "url": "http://www.mjmedi.com/news/articleList.html",
        "source_key": "mjmedi"
    }
]

# Gemini AI 설정
GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_TEMPERATURE = 0.3
GEMINI_MAX_OUTPUT_TOKENS = 4096

# Slack 설정
SLACK_CHANNEL = "C09TZ32M2KZ"  # 김세종_개인업무-채널

# 수집 기간 (시간 단위)
COLLECTION_PERIOD_HOURS = 24

# 배치 처리 크기 (Gemini API quota 절감)
BATCH_SIZE = 5

# HTTP 요청 설정
HTTP_TIMEOUT = 15
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# 원장님 관점 요약 프롬프트 (배치용)
SUMMARY_PROMPT_TEMPLATE = """당신은 한의원 원장님에게 최신 한의학 트렌드와 산업 동향을 브리핑하는 전문 어시스턴트입니다.

아래 한의학 신문 기사들을 원장님 관점에서 3줄 이내로 요약해주세요.

요약 원칙:
- 원장님이 임상에 활용하거나 환자 상담에 쓸 수 있는 내용 중심
- 수가, 정책, 보험 변화는 진료 운영에 직결되므로 반드시 포함
- 마케팅/경쟁사 동향은 차별화 포인트로 연결
- 전문 용어는 그대로 사용 (원장님 대상이므로)
- 볼드(**) 사용 금지, 이모지 금지
- 각 요약은 반드시 '[요약 N]' 형태로 시작

{articles_block}

[답변 형식]
[요약 1]
(기사 1 요약 — 3줄 이내)

[요약 2]
(기사 2 요약 — 3줄 이내)
...
"""

# Slack 메인 메시지 제목
REPORT_TITLE = "한의학 뉴스 브리핑"
