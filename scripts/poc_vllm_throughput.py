"""PoC: vLLM EXAONE 동시 처리량 측정.

DB에서 score=50 레코드 샘플을 가져와 다양한 동시성 수준에서
vLLM throughput을 측정한다.

사용법:
    python scripts/poc_vllm_throughput.py          # 로컬 (dev)
    ssh prime-jennie 'cd /home/youngs75/projects/prime-jennie && \
        source .venv/bin/activate && python scripts/poc_vllm_throughput.py'
"""

import asyncio
import json
import os
import re
import sys
import time

import httpx

# ─── 설정 ──────────────────────────────────────────────────────
VLLM_URL = os.getenv("VLLM_LLM_URL", "http://localhost:8001/v1")
MODEL = "LGAI-EXAONE/EXAONE-4.0-32B-AWQ"
SAMPLE_SIZE = 100  # 테스트할 샘플 수
CONCURRENCY_LEVELS = [1, 5, 10, 20, 30, 50]


def _extract_json(text: str) -> dict | None:
    """텍스트에서 JSON 추출."""
    m = re.search(r"```json\s*(.*?)```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1).strip())
    m = re.search(r"```\s*(.*?)```", text, re.DOTALL)
    if m:
        candidate = m.group(1).strip()
        if candidate.startswith("{"):
            return json.loads(candidate)
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start : i + 1])
    return None


def fetch_samples() -> list[dict]:
    """DB에서 버그 기간 score=50 샘플을 가져온다."""
    from sqlmodel import Session, text

    from prime_jennie.infra.database.engine import get_engine

    engine = get_engine()
    with Session(engine) as session:
        rows = session.exec(
            text(
                "SELECT id, stock_code, headline "
                "FROM stock_news_sentiments "
                "WHERE sentiment_score = 50 "
                "AND sentiment_reason = '분석 불가 — 기본 중립' "
                "AND news_date >= '2026-02-20' AND news_date <= '2026-03-02' "
                f"LIMIT {SAMPLE_SIZE}"
            )
        ).all()
    return [{"id": r[0], "stock_code": r[1], "headline": r[2]} for r in rows]


async def analyze_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    record: dict,
) -> dict:
    """단일 레코드 감성 분석."""
    prompt = (
        f"다음 한국 주식 뉴스의 감성을 분석하세요.\n"
        f"종목코드: {record['stock_code']}\n"
        f"헤드라인: {record['headline']}\n\n"
        f"score(0-100, 50=중립)와 reason(한국어 1문장)을 JSON으로 반환."
    )

    async with sem:
        t0 = time.monotonic()
        try:
            resp = await client.post(
                f"{VLLM_URL}/chat/completions",
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 150,
                },
                timeout=60,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            result = _extract_json(content)
            elapsed = time.monotonic() - t0
            score = None
            if result and "score" in result:
                score = max(0, min(100, int(result["score"])))
            return {"id": record["id"], "score": score, "elapsed": elapsed, "ok": score is not None}
        except Exception as e:
            elapsed = time.monotonic() - t0
            return {"id": record["id"], "score": None, "elapsed": elapsed, "ok": False, "error": str(e)[:80]}


async def run_benchmark(samples: list[dict], concurrency: int) -> dict:
    """특정 동시성 레벨에서 벤치마크 실행."""
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient() as client:
        t0 = time.monotonic()
        tasks = [analyze_one(client, sem, s) for s in samples]
        results = await asyncio.gather(*tasks)
        total_elapsed = time.monotonic() - t0

    ok_count = sum(1 for r in results if r["ok"])
    avg_latency = sum(r["elapsed"] for r in results) / len(results) if results else 0
    throughput = len(results) / total_elapsed if total_elapsed > 0 else 0

    return {
        "concurrency": concurrency,
        "total": len(results),
        "ok": ok_count,
        "fail": len(results) - ok_count,
        "total_sec": round(total_elapsed, 1),
        "avg_latency_sec": round(avg_latency, 2),
        "throughput_rps": round(throughput, 2),
    }


def main():
    print("=" * 60)
    print("vLLM EXAONE Throughput PoC")
    print(f"Endpoint: {VLLM_URL}")
    print(f"Model: {MODEL}")
    print("=" * 60)

    # 1. 샘플 로드
    print(f"\n[1/2] DB에서 {SAMPLE_SIZE}건 샘플 로딩...")
    samples = fetch_samples()
    print(f"  → {len(samples)}건 로드 완료")

    if not samples:
        print("ERROR: 샘플 없음. DB 연결 확인 필요.")
        sys.exit(1)

    # 2. 벤치마크
    print(f"\n[2/2] 벤치마크 시작 (동시성: {CONCURRENCY_LEVELS})")
    print("-" * 60)
    print(f"{'동시성':>6} | {'성공':>4} | {'실패':>4} | {'소요(초)':>8} | {'평균지연':>8} | {'처리량':>10}")
    print("-" * 60)

    for level in CONCURRENCY_LEVELS:
        result = asyncio.run(run_benchmark(samples, level))
        print(
            f"{result['concurrency']:>6} | "
            f"{result['ok']:>4} | "
            f"{result['fail']:>4} | "
            f"{result['total_sec']:>7.1f}s | "
            f"{result['avg_latency_sec']:>7.2f}s | "
            f"{result['throughput_rps']:>8.2f}/s"
        )

    print("-" * 60)
    print("\n33,709건 예상 소요시간:")
    # 마지막 결과 기반 추정
    last = asyncio.run(run_benchmark(samples[:10], CONCURRENCY_LEVELS[-1]))
    est_hours = 33709 / last["throughput_rps"] / 3600
    print(f"  동시성 {CONCURRENCY_LEVELS[-1]}: ~{est_hours:.1f}시간 ({last['throughput_rps']:.2f} req/s)")


if __name__ == "__main__":
    main()
