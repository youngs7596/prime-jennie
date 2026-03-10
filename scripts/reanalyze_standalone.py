#!/usr/bin/env python3
"""score=50 버그 기간(2026-02-20~03-02) 감성 재분석 — 독립 실행 스크립트.

Docker 컨테이너 외부에서 직접 실행. DB는 MariaDB로 직접 연결,
LLM은 vLLM HTTP API 직접 호출. 외부 라이브러리 없이 stdlib만 사용.

사용법:
    python3 scripts/reanalyze_standalone.py              # 전체 실행
    python3 scripts/reanalyze_standalone.py --limit 100   # 100건만
    python3 scripts/reanalyze_standalone.py --dry-run     # DB 미반영

환경변수 (.env에서 로드):
    DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

# ─── .env 로드 ─────────────────────────────────────────────────
def load_dotenv(path: str = ".env"):
    """간이 .env 로더."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key not in os.environ:
                os.environ[key] = val


# ─── 설정 ──────────────────────────────────────────────────────
VLLM_URL = "http://localhost:8001/v1"
MODEL = "LGAI-EXAONE/EXAONE-4.0-32B-AWQ"
CONCURRENCY = 20
BATCH_SIZE = 200
REQUEST_TIMEOUT = 60


def get_db_connection():
    """MariaDB 연결."""
    import mariadb
    return mariadb.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", "3306")),
        user=os.environ.get("DB_USER", "jennie"),
        password=os.environ["DB_PASSWORD"],
        database=os.environ.get("DB_NAME", "jennie_db"),
    )


def get_db_connection_mysql():
    """mysql-connector 폴백."""
    import mysql.connector
    return mysql.connector.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", "3306")),
        user=os.environ.get("DB_USER", "jennie"),
        password=os.environ["DB_PASSWORD"],
        database=os.environ.get("DB_NAME", "jennie_db"),
    )


def get_connection():
    """사용 가능한 DB 드라이버로 연결."""
    try:
        return get_db_connection()
    except ImportError:
        pass
    try:
        return get_db_connection_mysql()
    except ImportError:
        pass
    # PyMySQL
    import pymysql
    return pymysql.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", "3306")),
        user=os.environ.get("DB_USER", "jennie"),
        password=os.environ["DB_PASSWORD"],
        database=os.environ.get("DB_NAME", "jennie_db"),
        charset="utf8mb4",
    )


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


def fetch_targets(conn, limit: int | None = None) -> list[dict]:
    """재분석 대상 레코드 조회."""
    cursor = conn.cursor()
    query = (
        "SELECT id, stock_code, headline "
        "FROM stock_news_sentiments "
        "WHERE sentiment_score = 50 "
        "AND sentiment_reason = '분석 불가 — 기본 중립' "
        "AND news_date >= '2026-02-20' AND news_date <= '2026-03-02' "
        "ORDER BY id"
    )
    if limit:
        query += f" LIMIT {limit}"
    cursor.execute(query)
    rows = cursor.fetchall()
    cursor.close()
    return [{"id": r[0], "stock_code": r[1], "headline": r[2]} for r in rows]


def call_vllm(record: dict) -> dict:
    """vLLM API 동기 호출 (urllib 사용)."""
    prompt = (
        f"다음 한국 주식 뉴스의 감성을 분석하세요.\n"
        f"종목코드: {record['stock_code']}\n"
        f"헤드라인: {record['headline']}\n\n"
        f"score(0-100, 50=중립)와 reason(한국어 1문장)을 JSON으로 반환."
    )
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 150,
    }).encode()

    req = urllib.request.Request(
        f"{VLLM_URL}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read())
            content = data["choices"][0]["message"]["content"]
            result = _extract_json(content)
            if result and "score" in result:
                score = max(0, min(100, int(result["score"])))
                reason = result.get("reason", "")[:500]
                return {"id": record["id"], "score": score, "reason": reason, "ok": True}
    except Exception as e:
        return {"id": record["id"], "score": None, "reason": str(e)[:200], "ok": False}

    return {"id": record["id"], "score": None, "reason": "parse_failed", "ok": False}


def update_db(conn, results: list[dict], dry_run: bool = False) -> int:
    """분석 결과를 DB에 반영."""
    ok_results = [r for r in results if r["ok"]]
    if not ok_results or dry_run:
        return len(ok_results)

    cursor = conn.cursor()
    for r in ok_results:
        cursor.execute(
            "UPDATE stock_news_sentiments "
            "SET sentiment_score = %s, sentiment_reason = %s "
            "WHERE id = %s",
            (r["score"], r["reason"], r["id"]),
        )
    conn.commit()
    cursor.close()
    return len(ok_results)


def process_batch_threaded(batch: list[dict], max_workers: int) -> list[dict]:
    """ThreadPoolExecutor로 동시 처리."""
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(call_vllm, batch))


def main():
    parser = argparse.ArgumentParser(description="score=50 버그 기간 감성 재분석")
    parser.add_argument("--dry-run", action="store_true", help="DB 미반영 테스트")
    parser.add_argument("--limit", type=int, default=None, help="처리 건수 제한")
    args = parser.parse_args()

    # .env 로드
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    load_dotenv(os.path.join(project_root, ".env"))

    print("DB 연결 중...")
    conn = get_connection()

    print("대상 레코드 조회 중...")
    targets = fetch_targets(conn, args.limit)
    print(f"  → {len(targets):,}건 대상")

    if not targets:
        print("처리할 레코드 없음. 종료.")
        conn.close()
        return

    total = len(targets)
    ok_total = 0
    fail_total = 0
    t_start = time.monotonic()

    print(f"\n{'='*60}")
    print(f"재분석 시작: {total:,}건 | 동시성: {CONCURRENCY} | dry-run: {args.dry_run}")
    print(f"시작 시간: {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}\n")

    for i in range(0, total, BATCH_SIZE):
        batch = targets[i : i + BATCH_SIZE]
        results = process_batch_threaded(batch, CONCURRENCY)

        ok = sum(1 for r in results if r["ok"])
        fail = len(results) - ok
        update_db(conn, results, args.dry_run)

        ok_total += ok
        fail_total += fail

        elapsed = time.monotonic() - t_start
        progress = (i + len(batch)) / total * 100
        rps = (ok_total + fail_total) / elapsed if elapsed > 0 else 0
        eta_sec = (total - i - len(batch)) / rps if rps > 0 else 0
        eta_min = eta_sec / 60

        scores = [r["score"] for r in results if r["ok"]]
        avg_score = sum(scores) / len(scores) if scores else 0

        print(
            f"[{progress:5.1f}%] {i + len(batch):>6,}/{total:,} | "
            f"OK {ok_total:,} FAIL {fail_total} | "
            f"{rps:.1f}/s | ETA {eta_min:.0f}분 | "
            f"avg={avg_score:.0f}",
            flush=True,
        )

    conn.close()

    elapsed_total = time.monotonic() - t_start
    print(f"\n{'='*60}")
    print(f"완료: {ok_total:,}건 성공, {fail_total}건 실패")
    print(f"소요: {elapsed_total/60:.1f}분 ({elapsed_total/3600:.1f}시간)")
    print(f"평균: {(ok_total + fail_total) / elapsed_total:.2f} req/s")
    print(f"종료 시간: {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
