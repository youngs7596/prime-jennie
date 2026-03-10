"""score=50 버그 기간(2026-02-20~03-02) 감성 재분석 배치 스크립트.

vLLM EXAONE으로 33,709건의 미분석 뉴스 감성을 재분석하여 DB 업데이트.
중단/재시작 가능 — 이미 처리된 레코드는 자동 스킵.

사용법 (job-worker 컨테이너 내부):
    python scripts/reanalyze_sentiment.py                # 전체 실행
    python scripts/reanalyze_sentiment.py --dry-run      # DB 미반영 테스트
    python scripts/reanalyze_sentiment.py --limit 500    # 500건만 처리
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime

import httpx
from sqlmodel import Session, text

from prime_jennie.infra.database.engine import get_engine

# ─── 설정 ──────────────────────────────────────────────────────
VLLM_URL = os.getenv("VLLM_LLM_URL", "http://localhost:8001/v1")
MODEL = "LGAI-EXAONE/EXAONE-4.0-32B-AWQ"
CONCURRENCY = 20
BATCH_SIZE = 200  # DB commit 단위
REQUEST_TIMEOUT = 60


def _extract_json(text_str: str) -> dict | None:
    """텍스트에서 JSON 추출."""
    m = re.search(r"```json\s*(.*?)```", text_str, re.DOTALL)
    if m:
        return json.loads(m.group(1).strip())
    m = re.search(r"```\s*(.*?)```", text_str, re.DOTALL)
    if m:
        candidate = m.group(1).strip()
        if candidate.startswith("{"):
            return json.loads(candidate)
    start = text_str.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text_str)):
            if text_str[i] == "{":
                depth += 1
            elif text_str[i] == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text_str[start : i + 1])
    return None


def fetch_targets(engine, limit: int | None = None) -> list[dict]:
    """재분석 대상 레코드 조회."""
    limit_clause = f"LIMIT {limit}" if limit else ""
    with Session(engine) as session:
        rows = session.exec(
            text(
                "SELECT id, stock_code, headline "
                "FROM stock_news_sentiments "
                "WHERE sentiment_score = 50 "
                "AND sentiment_reason = '분석 불가 — 기본 중립' "
                "AND news_date >= '2026-02-20' AND news_date <= '2026-03-02' "
                f"ORDER BY id {limit_clause}"
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
        try:
            resp = await client.post(
                f"{VLLM_URL}/chat/completions",
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 150,
                },
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            result = _extract_json(content)
            if result and "score" in result:
                score = max(0, min(100, int(result["score"])))
                reason = result.get("reason", "")[:500]
                return {"id": record["id"], "score": score, "reason": reason, "ok": True}
        except Exception as e:
            return {"id": record["id"], "score": None, "reason": str(e)[:200], "ok": False}

    return {"id": record["id"], "score": None, "reason": "unknown", "ok": False}


def update_db(engine, results: list[dict], dry_run: bool = False) -> int:
    """분석 결과를 DB에 반영."""
    ok_results = [r for r in results if r["ok"]]
    if not ok_results or dry_run:
        return len(ok_results)

    with Session(engine) as session:
        for r in ok_results:
            session.exec(
                text(
                    "UPDATE stock_news_sentiments "
                    "SET sentiment_score = :score, sentiment_reason = :reason "
                    "WHERE id = :id"
                ),
                params={"score": r["score"], "reason": r["reason"], "id": r["id"]},
            )
        session.commit()
    return len(ok_results)


async def process_batch(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    batch: list[dict],
) -> list[dict]:
    """배치 단위 동시 처리."""
    tasks = [analyze_one(client, sem, rec) for rec in batch]
    return await asyncio.gather(*tasks)


async def run(targets: list[dict], engine, dry_run: bool = False):
    """메인 실행 루프."""
    total = len(targets)
    sem = asyncio.Semaphore(CONCURRENCY)

    ok_total = 0
    fail_total = 0
    t_start = time.monotonic()

    print(f"\n{'='*60}")
    print(f"재분석 시작: {total:,}건 | 동시성: {CONCURRENCY} | dry-run: {dry_run}")
    print(f"시작 시간: {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}\n")

    async with httpx.AsyncClient() as client:
        for i in range(0, total, BATCH_SIZE):
            batch = targets[i : i + BATCH_SIZE]
            results = await process_batch(client, sem, batch)

            ok = sum(1 for r in results if r["ok"])
            fail = len(results) - ok
            updated = update_db(engine, results, dry_run)

            ok_total += ok
            fail_total += fail

            elapsed = time.monotonic() - t_start
            progress = (i + len(batch)) / total * 100
            rps = (ok_total + fail_total) / elapsed if elapsed > 0 else 0
            eta_sec = (total - i - len(batch)) / rps if rps > 0 else 0
            eta_min = eta_sec / 60

            # 점수 분포 샘플
            scores = [r["score"] for r in results if r["ok"]]
            avg_score = sum(scores) / len(scores) if scores else 0

            print(
                f"[{progress:5.1f}%] {i + len(batch):>6,}/{total:,} | "
                f"OK {ok_total:,} FAIL {fail_total} | "
                f"{rps:.1f}/s | ETA {eta_min:.0f}분 | "
                f"배치 avg={avg_score:.0f}"
            )

    elapsed_total = time.monotonic() - t_start
    print(f"\n{'='*60}")
    print(f"완료: {ok_total:,}건 성공, {fail_total}건 실패")
    print(f"소요: {elapsed_total/60:.1f}분 ({elapsed_total/3600:.1f}시간)")
    print(f"평균: {(ok_total + fail_total) / elapsed_total:.2f} req/s")
    print(f"종료 시간: {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="score=50 버그 기간 감성 재분석")
    parser.add_argument("--dry-run", action="store_true", help="DB 미반영 테스트")
    parser.add_argument("--limit", type=int, default=None, help="처리 건수 제한")
    args = parser.parse_args()

    engine = get_engine()

    print("대상 레코드 조회 중...")
    targets = fetch_targets(engine, args.limit)
    print(f"  → {len(targets):,}건 대상")

    if not targets:
        print("처리할 레코드 없음. 종료.")
        sys.exit(0)

    asyncio.run(run(targets, engine, args.dry_run))


if __name__ == "__main__":
    main()
