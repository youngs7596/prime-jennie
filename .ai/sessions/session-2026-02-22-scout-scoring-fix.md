# Session Handoff: Scout 스코어링 구조적 문제 수정

## 작업 날짜
2026-02-22 (토요일, 장 마감일)

## 작업 브랜치
`development`

## 완료된 작업

### 1. 조선/방산 섹터 독립 분류
- **문제**: 조선/방산 종목이 SectorGroup에서 "기타"에 매핑되어 섹터 모멘텀 희석
- **수정**: 
  - `SectorGroup.DEFENSE_SHIPBUILDING = "조선/방산"` 추가 (14→15개 대분류)
  - `sector_taxonomy.py`: 조선/우주항공과국방 → DEFENSE_SHIPBUILDING
  - DB: 24종목 sector_group '기타' → '조선/방산' UPDATE
- **결과**: 대한조선이 watchlist 1위 진입

### 2. ROE 데이터 전면 복구
- **문제**: 레거시 scout.py Phase 1.7이 매일 PER/PBR만 쓰고 ROE=None으로 덮어씀
  - KIS API snapshot에 ROE 필드가 없음
  - populate_fundamentals_from_quarterly.py END_DATE='2026-01-10' 이후 미실행
  - 결과: 2026-01-09부터 stock_fundamentals의 ROE가 전부 NULL
- **수정**:
  - FINANCIAL_METRICS_QUARTERLY 테이블에서 23종목 ROE backfill
  - 방산주 8종목 네이버 금융 직접 수집 (한화에어로 43.5%, 현대로템 28.8% 등)
  - 나머지 154종목 네이버 금융 일괄 수집 → 184/184 ROE 100% 채움
- **결과**: SK하이닉스(ROE 43.2%) watchlist 4위 진입

### 3. 성장주 Quant 스코어 보정
- **문제**: ROE=NULL → 0pt, PBR>4 → 0.5pt, PER>50 → 0pt로 성장주 구조적 저평가
- **수정** (quant.py):
  - ROE=NULL → 중립 5pt (quality 서브팩터, 데이터 없음 보정)
  - PBR>4 하한선: 0.5 → 1.0 (고PBR 성장주 보정)
  - PER>50 하한선: 0 → 0.5 (고PER 성장주 보정)

### 4. 이전 세션 수정사항 (이번 세션에서 배포/검증)
- sector_group enum 1,280종목 DB 수정
- BLOCKED trade_tier + is_tradable 불일치 31건 수정
- WatchlistEntry quant_score 필드 누락 수정
- KIS gateway WebSocket race condition 수정
- 투자자 매매동향 단위 불일치 데이터 삭제 (16만건)
- 5분봉 수집 재활성화 (stock_minute_prices 테이블 생성 + DAG)
- refresh_market_caps DAG unpause

## 커밋 내역
- `ee42669` fix: 조선/방산 섹터 독립 + 성장주 quant 스코어 보정

## Scout Watchlist (2026-02-22 최종)
| 순위 | 종목 | Quant | Hybrid | 섹터 |
|------|------|-------|--------|------|
| 1 | 대한조선 | 82.9 | 83.0 | 조선/방산 |
| 2 | 메리츠금융지주 | 82.1 | 82.0 | 금융 |
| 3 | 한국가스공사 | 85.2 | 82.0 | 화학/에너지 |
| 4 | SK하이닉스 | 74.2 | 78.0 | 반도체/IT |
| 5 | 기아 | 77.8 | 78.0 | 자동차 |
| 6 | 한화 | 80.2 | 78.0 | 기타 |
| 7 | 대한항공 | 83.3 | 78.0 | 운송/물류 |
| 8 | 강원랜드 | 82.0 | 78.0 | 음식료/생활 |
| 9 | 효성 | 85.0 | 78.0 | 기타 |
| 10 | 현대글로비스 | 76.8 | 77.0 | 운송/물류 |
| 11 | DB손해보험 | 76.5 | 77.0 | 금융 |
| 12 | 한국전력 | 79.0 | 75.0 | 유틸리티 |
| 13 | 오리온 | 79.7 | 75.0 | 음식료/생활 |
| 14 | 현대해상 | 80.2 | 75.0 | 금융 |
| 15 | KB금융 | 73.8 | 74.0 | 금융 |
| 16 | 크래프톤 | 73.9 | 74.0 | 미디어/엔터 |
| 17 | 한전KPS | 73.5 | 74.0 | 유틸리티 |
| 18 | 삼성전기 | 72.8 | 73.0 | 반도체/IT |
| 19 | 한국타이어 | 72.8 | 73.0 | 자동차 |
| 20 | 한전기술 | 72.8 | 73.0 | 유틸리티 |

## 미해결/후속 과제

### ROE 일일 갱신 Job 필요 (중요)
- 현재: stock_fundamentals에 ROE를 쓰는 정기 Job이 없음
- 레거시 scout.py Phase 1.7이 매일 ROE=None으로 덮어쓸 수 있음 (레거시 컨테이너 확인 필요)
- **해결안**: prime-jennie에 주간/월간 ROE 수집 Job 추가 (네이버 금융 분기 재무 크롤링)
- 또는: daily update 시 ROE가 NULL이면 기존값 유지하도록 수정

### 방산 대형주 미진입
- 한화에어로스페이스(Q=62.1), 현대로템(Q=58.2), HD현대중공업(Q=56.9)
- ROE는 채워졌지만 (각 43.5%, 28.8%, 21.7%), quant 총점이 아직 58-65 수준
- 원인: PBR 8-11 + PER 25-82 → Quality+Value에서 여전히 낮은 점수
- LLM이 BULLISH(78+)로 평가하면 hybrid 73-77 가능하지만 현재 watchlist 컷(73) 경쟁력 부족
- **고려사항**: 방산/조선은 현재 PBR/PER로 평가하기 어려운 구조적 고평가 섹터 → 별도 섹터 가산점 또는 테마 모멘텀 로직 검토

### FINANCIAL_METRICS_QUARTERLY 테이블 갱신
- 레거시 collect_quarterly_financials.py를 prime-jennie Job으로 이식 필요
- 분기 실적 발표 후 자동 갱신 (매 분기 1회)

## DB 직접 변경 내역
- `stock_masters`: 24종목 sector_group '기타' → '조선/방산'
- `stock_fundamentals`: ROE backfill (184종목 전체, 네이버 금융 기준)
