# prime-jennie Design Documents

> my-prime-jennie 2개월 운영 경험 + 4개 감사 보고서를 바탕으로 작성된 설계 문서.

## 문서 목록

| # | 문서 | 내용 | 상태 |
|---|------|------|------|
| 00 | [Design Principles](00-design-principles.md) | 재설계 배경, 핵심 원칙 7개, 기술 스택, 마이그레이션 전략 | Complete |
| 01 | [System Architecture](01-system-architecture.md) | 서비스 카탈로그, 통신 패턴, 저장소 설계, 장애 대응 | Complete |
| 02 | [Domain Models](02-domain-models.md) | Pydantic v2 모델 전체 (13개 모듈, 40+ 모델) | Complete |
| 03 | [Database Schema](03-database-schema.md) | SQLModel 테이블 정의, 인덱스 전략, 마이그레이션 | Complete |
| 04 | [Service Contracts](04-service-contracts.md) | HTTP API, Redis Stream, Cache 프로토콜 | Complete |
| 05 | [Data Pipelines](05-data-pipelines.md) | 5개 파이프라인 단계별 타입 명세, 에러 처리 | Complete |

## 핵심 설계 변경 (my-prime-jennie → prime-jennie)

| 영역 | Before | After |
|------|--------|-------|
| 서비스 계약 | `dict.get("field", default)` | Pydantic BaseModel |
| DB 모델 | SQLAlchemy + Raw SQL 혼용 | SQLModel 단일 |
| 에러 처리 | `except ImportError: pass` | Fail-fast + 구조화 로그 |
| 설정 | 109개 Registry dict | Pydantic Settings 그룹 |
| Redis 메시지 | JSON string (검증 없음) | Pydantic 직렬화/역직렬화 |
| 로깅 | `f"..."` 포맷 | structlog JSON |
| 공유 코드 | shared/ 38개 모듈 | domain/ (모델) + infra/ (클라이언트) |

## 감사 기반

이 설계 문서는 다음 4개 감사 보고서를 기반으로 작성되었습니다:

1. **DB 감사**: 30+ ORM 모델, 3개 Raw SQL 테이블, 40+ Redis 키 패턴, 스키마 이슈
2. **서비스 경계 감사**: 18개 마이크로서비스 API 계약, Stream 아키텍처
3. **Shared 모듈 감사**: 38개 공유 모듈, 109개 설정 키, 7개 Airflow DAG, 10+ 프롬프트
4. **데이터 흐름 감사**: 5개 파이프라인 120+ 필드, 7개 Silent Failure, 20+ 안전장치

---

*Created: 2026-02-19*
