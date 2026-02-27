# Session: pykrx → 네이버 금융 대체 (2026-02-27)

## 배경
KRX `data.krx.co.kr` 웹사이트 2026-02-27 개편으로 pykrx 1.2.4 전면 실패 (400/"LOGOUT").

## 완료

### macro collection (이번 세션)
- `naver_market.py` 신규 — `fetch_index_data()`, `fetch_investor_flows()` (네이버 모바일 API + HTML)
- `app.py` `macro_collect_global()` — pykrx → naver 전환
- `config.py` — `krx_open_api_key` 추가 (향후 전환용)
- `macro_dag.py` — description `(pykrx)` → `(naver)`
- `.env.example` — `KRX_OPEN_API_KEY=` 추가
- `test_naver_market.py` — 12/12 passed
- unit tests 596/596 passed

## TODO: 종목별 pykrx 대체 (월요일 장 전 완료 필요)

### 1. `collect-investor-trading` (300종목, 매일 18:30)
- **현재**: `pykrx_stock.get_market_trading_value_by_investor()` → 300/300 실패
- **저장 단위**: KRW 금액 (float)
- **하류 의존**:
  - `quant.py:365-376` — `foreign_net_buy_sum / 5e9` (50억 기준 점수화, 외인 6pt + 기관 8pt)
  - `analyst.py:117-130` — `< -3e9` (30억 이상 순매도 시 경고)
  - `enrichment.py:121-136` — 최근 N일 합산
- **대체 후보**:
  - 네이버 `sise_deal.naver?code={code}` — 투자자별 매매동향 (주 수 → 종가 × 주 수 = 금액 변환 필요)
  - **주의**: 반드시 KRW 금액 단위 유지 (주 수 X), quant scorer 스케일링 기준이 5e9

### 2. `collect-foreign-holding` (300종목, 매일 19:00)
- **현재**: `pykrx_stock.get_exhaustion_rates_of_foreign_investment_by_date()` → 300/300 실패
- **저장 필드**: `foreign_holding_ratio` (float, %)
- **대체 후보**:
  - 네이버 `frgn.naver?code={code}` — 외국인 보유율 (%) 직접 파싱
  - 단위 그대로 % → 쉬움

### 3. `collect-full-market-data` (300종목, 매일 16:00)
- **KIS API 사용** → pykrx 무관, **정상 동작 확인** (0 new = 이미 DB에 존재, failed=4 소수)

## 전체 Job 테스트 결과 (2026-02-27 장 마감 후)

| Job | 소스 | 결과 |
|-----|------|------|
| macro-collect-global | pykrx → **naver (전환 완료)** | FAIL (구버전 배포 상태) |
| macro-collect-korea | global 위임 | FAIL (동일) |
| macro-quick | pykrx → **naver (전환 완료)** | FAIL (동일) |
| macro-validate-store | Redis 검증 | OK (13 fields) |
| daily-asset-snapshot | KIS API | OK |
| analyst-feedback | DB 분석 | OK |
| analyze-ai-performance | DB 분석 | OK |
| contract-smoke-test | 네이버 크롤 | OK (5/5) |
| refresh-market-caps | KIS API | OK (292건) |
| cleanup-old-data | DB 정리 | OK |
| update-naver-sectors | 네이버 크롤 | OK (995건) |
| collect-quarterly-financials | 네이버 크롤 | OK (296/300) |
| weekly-factor-analysis | DB 분석 | OK |
| collect-consensus | FnGuide+네이버 | OK (300/300) |
| collect-naver-roe | 네이버 크롤 | OK (297/300) |
| collect-dart-filings | DART API | OK (0건) |
| collect-minute-chart | KIS API | OK (1620건) |
| report | 브리핑 서비스 | OK |
| **collect-investor-trading** | **pykrx** | **FAIL (0/300)** |
| **collect-foreign-holding** | **pykrx** | **FAIL (0/300)** |
| collect-full-market-data | KIS API | OK (정상, 0 new) |
