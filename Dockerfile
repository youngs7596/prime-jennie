# prime-jennie — 공통 서비스 이미지
# 모든 서비스가 동일 이미지를 공유하고, CMD로 서비스 선택
FROM python:3.12-slim AS base

WORKDIR /app

# 시스템 패키지
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 의존성 먼저 (캐시 최적화)
COPY pyproject.toml /app/
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps -e . && \
    pip install -r <(pip freeze) 2>/dev/null || true

# 패키지 소스
COPY prime_jennie/ /app/prime_jennie/

# 패키지 설치 (editable)
RUN pip install -e . --no-deps

# 헬스체크 기본값
HEALTHCHECK --interval=10s --timeout=5s --retries=3 --start-period=15s \
    CMD curl -f http://localhost:${PORT:-8080}/health || exit 1

# 기본 실행
CMD ["uvicorn", "prime_jennie.services.gateway.app:app", "--host", "0.0.0.0", "--port", "8080"]
