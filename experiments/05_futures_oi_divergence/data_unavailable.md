# 실험 5: 선물 미결제약정(OI) vs 현물 괴리 분석

## 실험 상태: SKIP (데이터 미가용)

## 데이터 가용성 조사 결과

### Prime Jennie 현재 상태
- DB에 선물 OI 테이블 없음 (`stock_minute_prices`, `stock_daily_prices` 등에 OI 컬럼 없음)
- KIS Gateway에 파생상품 API 미구현
- 선물 크롤러 없음

### 외부 데이터 소스 조사

| 소스 | 데이터 형태 | 접근 방법 | 비용 | 구현 난이도 | 비고 |
|------|-----------|---------|------|-----------|------|
| **KRX 정보데이터시스템** | 일별 OI, 투자자별 구분 | OPEN API (회원가입+키 발급) | 무료 | 중 | **권장 1순위** — 데이터 품질 최고 |
| **KIS OpenAPI** | 선물 시세+OI (실시간/일별) | REST API + WebSocket | 무료 (기존 계좌) | 낮 | **권장 2순위** — 기존 인프라 활용 |
| **네이버 금융** | 선물 시세 페이지 있으나 OI 미제공 | 크롤링 | 무료 | - | 사용 불가 |
| **Yahoo/yfinance** | KOSPI200 지수만, 선물 OI 없음 | API | 무료 | - | 사용 불가 |
| **FinanceDataReader** | 선물/파생 미지원 | Python 패키지 | 무료 | - | 사용 불가 |
| **pykrx** | 주식/지수 전용, 선물 미지원 | Python 패키지 | 무료 | - | 사용 불가 |
| **Investing.com** | 현재 OI만 표시, 히스토리컬 불가 | 크롤링 | 무료 | - | 비권장 |
| **TradingView** | 차트에 OI 표시 | 프로그래밍 접근 불가 | 유료 | - | 비권장 |

### 권장 사항

**최우선**: KIS OpenAPI 활용 (기존 계좌 + API 키 보유)
- `[국내선물옵션] 기본시세` 카테고리에 시세/OI API 존재
- WebSocket에 미결제약정수량 필드 확인
- kis-gateway에 파생상품 시세 엔드포인트 추가
- 예상 작업량: 12~18시간

**차선**: KRX 정보데이터시스템 OPEN API
- 일별 OI + 투자자별(외국인/기관/개인) 미결제약정 구분 가능
- 히스토리컬 데이터 다운로드 가능
- 회원가입 + API 키 발급 필요

### 구현 계획 (KIS OpenAPI 기준)

1. `prime_jennie/infra/crawlers/futures_market.py` — KOSPI200 선물 OI 크롤러
2. `prime_jennie/infra/database/models.py` — `FuturesOIDailyDB` 모델 추가
3. `prime_jennie/services/gateway/kis_api.py` — 파생상품 시세 API 추가
4. `dags/` — OI 수집 DAG 추가
5. 최소 60거래일(3개월) 데이터 축적 후 실험 재실행

## 실전 적용 가능성: 1/5 (데이터 미확보)

## 후속 작업 제안

1. KIS OpenAPI 파생상품 시세 API 연동 (kis-gateway 확장)
2. `futures_oi_daily` 테이블 생성 + 일별 수집 DAG 구축
3. 최소 60거래일 데이터 축적
4. 데이터 확보 후 본 실험 재실행
5. OI 데이터를 Council/Intraday Risk에 통합하는 것도 별도 검토
