-- ============================================================
-- 수壽 Daily — 클릭 추적용 Supabase 테이블 설정
-- Supabase SQL Editor에서 실행
-- ============================================================

-- 1. 클릭 카운트 테이블
CREATE TABLE click_counts (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  article_url text NOT NULL,
  article_date text NOT NULL,
  click_count integer DEFAULT 1,
  last_clicked_at timestamptz DEFAULT now(),
  UNIQUE(article_url, article_date)
);

-- 2. RLS 활성화 + anon 접근 허용
ALTER TABLE click_counts ENABLE ROW LEVEL SECURITY;

CREATE POLICY "anon_read" ON click_counts
  FOR SELECT TO anon USING (true);

CREATE POLICY "anon_insert" ON click_counts
  FOR INSERT TO anon WITH CHECK (true);

CREATE POLICY "anon_update" ON click_counts
  FOR UPDATE TO anon USING (true);

-- 3. 원자적 클릭 증가 RPC 함수
CREATE OR REPLACE FUNCTION increment_click(p_url text, p_date text)
RETURNS void AS $$
BEGIN
  INSERT INTO click_counts (article_url, article_date)
  VALUES (p_url, p_date)
  ON CONFLICT (article_url, article_date)
  DO UPDATE SET
    click_count = click_counts.click_count + 1,
    last_clicked_at = now();
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
