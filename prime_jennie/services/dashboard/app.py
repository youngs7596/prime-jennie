"""Dashboard Backend API — 대시보드용 통합 REST API.

모든 트레이딩 시스템 데이터를 단일 API로 제공:
- 포트폴리오 상태 / 보유 종목 / 자산 히스토리
- 매크로 인사이트 / 시장 국면
- 워치리스트 (Redis 실시간 + DB 히스토리)
- 거래 기록 / 성과 분석
- LLM 사용량 통계
- 시스템 헬스 체크
"""

from contextlib import asynccontextmanager

from fastapi.middleware.cors import CORSMiddleware

from prime_jennie.services.base import create_app

from .routers import llm_stats, macro, portfolio, system, trades, watchlist


@asynccontextmanager
async def lifespan(app):
    yield


app = create_app(
    "dashboard",
    version="1.0.0",
    lifespan=lifespan,
    dependencies=["redis", "db"],
)

# CORS (로컬 프론트엔드 허용)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://localhost:80", "http://127.0.0.1"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록
app.include_router(portfolio.router, prefix="/api")
app.include_router(macro.router, prefix="/api")
app.include_router(watchlist.router, prefix="/api")
app.include_router(trades.router, prefix="/api")
app.include_router(llm_stats.router, prefix="/api")
app.include_router(system.router, prefix="/api")
