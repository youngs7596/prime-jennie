# News Pipeline 상시 운영 + Job Worker 스텁 전체 구현

> 선행 작업: `14926de` feat: BULL 국면 Scale-Out L0(+3%) 스킵
> 브랜치: `development`
> 상태: **완료**

---

## 완료 사항

### 1. News Pipeline 상시 수집 루프
- **파일:** `prime_jennie/services/news/app.py`
- lifespan + daemon thread 패턴 적용 (scanner/monitor과 동일 구조)
- `_news_loop()`: collect → analyze → (3회마다) archive
- 수집 주기: 장중 10분, 장외 30분 (07:00~16:00 기준)
- 기존 HTTP 엔드포인트 유지 (수동 트리거/디버깅용)
- `_loop_running` 플래그로 graceful shutdown (join timeout=10s)

### 2. Job Worker 스텁 8개 → 실제 구현
- **파일:** `prime_jennie/services/jobs/app.py`

| 엔드포인트 | 구현 내용 |
|-----------|----------|
| `collect-foreign-holding` | pykrx `get_exhaustion_rates_of_foreign_investment_by_date()` → `StockInvestorTradingDB.foreign_holding_ratio` 업데이트 |
| `collect-dart-filings` | OpenDartReader로 최근 7일 공시 조회 → `StockDisclosureDB` 저장. `DART_API_KEY` 환경변수 사용 |
| `collect-minute-chart` | KIS Gateway `/api/market/minute-prices` → `StockMinutePriceDB` 저장 (배치 30종목 제한) |
| `analyze-ai-performance` | TradeLogDB 최근 30일 매도 분석: sell_reason별/국면별/점수구간별 승률 → Redis `ai:performance:latest` |
| `analyst-feedback` | AI 성과 데이터 기반 마크다운 리포트 생성 → Redis `analyst:feedback:summary` |
| `macro-collect-korea` | `macro_collect_global()` 위임 (한국 데이터 이미 포함) + 명시적 로그 |
| `macro-validate-store` | Redis 스냅샷 필수 필드 검증 (kospi_index, kosdaq_index) + 값 범위 체크 |
| `weekly-factor-analysis` | IC(스피어만 상관) 계산 + 조건부 성과 분석 (뉴스+수급 조합) → Redis `factor:analysis:latest` |

### 3. KIS 분봉 API 체인
- `kis_api.py`: `get_minute_prices()` — TR_ID: FHKST03010200
- `gateway/app.py`: `POST /api/market/minute-prices` 엔드포인트
- `client.py`: `get_minute_prices()` 프록시 메서드
- `domain/stock.py`: `MinutePrice` 도메인 모델

### 4. DB 모델 + 마이그레이션
- `StockDisclosureDB`: stock_code, disclosure_date, title, report_type, receipt_no, corp_name
- `StockMinutePriceDB`: stock_code, price_datetime, OHLCV
- 마이그레이션: `003_add_disclosures_and_minute_prices.py`

### 5. 의존성
- `pyproject.toml`에 `opendartreader>=0.2` 추가

---

## 변경 파일 목록

| 파일 | 변경 |
|------|------|
| `prime_jennie/services/news/app.py` | 상시 daemon loop 추가 |
| `prime_jennie/services/jobs/app.py` | 스텁 8개 → 실제 구현 |
| `prime_jennie/infra/database/models.py` | `StockDisclosureDB`, `StockMinutePriceDB` |
| `prime_jennie/services/gateway/kis_api.py` | `get_minute_prices()` |
| `prime_jennie/services/gateway/app.py` | `/api/market/minute-prices` |
| `prime_jennie/infra/kis/client.py` | `get_minute_prices()` 프록시 |
| `prime_jennie/domain/stock.py` | `MinutePrice` 모델 |
| `prime_jennie/domain/__init__.py` | MinutePrice export |
| `migrations/versions/003_*.py` | 테이블 2개 생성 |
| `migrations/env.py` | 새 모델 import |
| `pyproject.toml` | opendartreader 의존성 |

---

## 검증

- **522 tests passed** (0 failures)
- `ruff format` + `ruff check` 통과

---

## 배포 후 확인 사항

```bash
# 마이그레이션 실행
docker exec prime-jennie-job-worker-1 alembic upgrade head

# news-pipeline 상시 동작 확인
docker logs prime-jennie-news-pipeline-1 --tail 20

# job-worker 수동 테스트
curl -X POST http://localhost:8095/jobs/collect-foreign-holding
curl -X POST http://localhost:8095/jobs/collect-dart-filings
curl -X POST http://localhost:8095/jobs/collect-minute-chart
curl -X POST http://localhost:8095/jobs/analyze-ai-performance
curl -X POST http://localhost:8095/jobs/analyst-feedback
curl -X POST http://localhost:8095/jobs/macro-validate-store
curl -X POST http://localhost:8095/jobs/weekly-factor-analysis
```
