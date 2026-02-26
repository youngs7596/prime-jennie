"""DeepSeek API 동시 요청 한계 PoC — Scout analyst와 동일 패턴.

사용법:
    uv run python scripts/poc_concurrency_test.py

동시성 레벨 5, 10, 15, 20, 25, 30으로 각각 N건 요청을 보내고
성공률, 평균/P95 지연시간, 에러 유형을 측정한다.
"""

import asyncio
import json
import os
import re
import statistics
import sys
import time

import httpx

# --- Config ---
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-reasoner"
CONCURRENCY_LEVELS = [5, 10, 15, 20, 25, 30]
REQUESTS_PER_LEVEL = 20  # 레벨당 요청 수


# Scout analyst와 유사한 프롬프트 (간소화)
def _build_prompt(idx: int) -> str:
    return f"""## 종목 분석: 테스트종목{idx:03d} (00{idx:04d})
시장: KOSPI, 섹터: IT

### Quant Score: {50 + idx % 30:.1f}/100
  모멘텀: 12.0/20
  품질: 10.0/20
  가치: 8.0/20
  기술: 7.0/10
  뉴스: 6.0/10
  수급: 7.0/20

### 요청
위 데이터를 종합적으로 분석하여 0-100점 사이의 투자 매력도 점수를 부여하세요.
score, grade(S/A/B/C/D), reason(한국어)을 JSON으로 응답하세요."""


SCHEMA_INSTRUCTION = """

You MUST respond with a JSON object that follows this exact schema:
```json
{
  "type": "object",
  "properties": {
    "score": {"type": "integer", "minimum": 0, "maximum": 100},
    "grade": {"type": "string", "enum": ["S", "A", "B", "C", "D"]},
    "reason": {"type": "string", "minLength": 20, "maxLength": 500}
  },
  "required": ["score", "grade", "reason"]
}
```
Use exactly the field names shown above. Do not use different key names."""


def _extract_json(text: str) -> dict | None:
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1).strip())
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        return json.loads(m.group(0))
    return None


async def _call_once(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    idx: int,
) -> dict:
    """단일 API 호출. 결과 dict 반환."""
    prompt = _build_prompt(idx) + SCHEMA_INSTRUCTION
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "당신은 한국 주식 애널리스트입니다. Always respond with valid JSON."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 4096,
    }

    async with sem:
        t0 = time.monotonic()
        try:
            resp = await client.post(
                f"{BASE_URL}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {API_KEY}"},
                timeout=120.0,
            )
            elapsed = time.monotonic() - t0

            if resp.status_code != 200:
                return {"ok": False, "elapsed": elapsed, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            parsed = _extract_json(content)
            if not parsed or "score" not in parsed:
                return {"ok": False, "elapsed": elapsed, "error": "JSON parse failed"}

            return {"ok": True, "elapsed": elapsed, "score": parsed.get("score")}

        except httpx.TimeoutException:
            elapsed = time.monotonic() - t0
            return {"ok": False, "elapsed": elapsed, "error": "timeout"}
        except Exception as e:
            elapsed = time.monotonic() - t0
            return {"ok": False, "elapsed": elapsed, "error": str(e)[:100]}


async def run_level(concurrency: int, n_requests: int) -> dict:
    """특정 동시성 레벨로 n_requests 건 실행."""
    sem = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient() as client:
        tasks = [_call_once(client, sem, i) for i in range(n_requests)]
        t0 = time.monotonic()
        results = await asyncio.gather(*tasks)
        wall_time = time.monotonic() - t0

    ok_results = [r for r in results if r["ok"]]
    fail_results = [r for r in results if not r["ok"]]
    latencies = [r["elapsed"] for r in ok_results]

    summary = {
        "concurrency": concurrency,
        "total": n_requests,
        "success": len(ok_results),
        "fail": len(fail_results),
        "wall_time": round(wall_time, 1),
        "avg_latency": round(statistics.mean(latencies), 1) if latencies else 0,
        "p95_latency": round(sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0, 1),
        "errors": {},
    }

    for r in fail_results:
        err = r["error"][:50]
        summary["errors"][err] = summary["errors"].get(err, 0) + 1

    return summary


async def main():
    if not API_KEY:
        print("ERROR: DEEPSEEK_API_KEY 환경변수가 설정되지 않았습니다.")
        sys.exit(1)

    print(f"DeepSeek API 동시성 테스트 — model={MODEL}, {REQUESTS_PER_LEVEL}건/레벨")
    print(f"테스트 레벨: {CONCURRENCY_LEVELS}")
    print("=" * 70)

    all_results = []
    for level in CONCURRENCY_LEVELS:
        print(f"\n▶ concurrency={level} 시작 ({REQUESTS_PER_LEVEL}건)...")
        result = await run_level(level, REQUESTS_PER_LEVEL)
        all_results.append(result)

        print(
            f"  성공: {result['success']}/{result['total']} | "
            f"wall: {result['wall_time']}s | "
            f"avg: {result['avg_latency']}s | "
            f"p95: {result['p95_latency']}s"
        )
        if result["errors"]:
            for err, cnt in result["errors"].items():
                print(f"  ⚠ {err}: {cnt}건")

        # 레벨 간 쿨다운
        if level != CONCURRENCY_LEVELS[-1]:
            print("  (10초 쿨다운...)")
            await asyncio.sleep(10)

    # --- Summary ---
    print("\n" + "=" * 70)
    print(f"{'동시성':>6} | {'성공':>4} | {'실패':>4} | {'wall(s)':>8} | {'avg(s)':>7} | {'p95(s)':>7} | 비고")
    print("-" * 70)
    for r in all_results:
        note = ", ".join(f"{e[:20]}:{c}" for e, c in r["errors"].items()) if r["errors"] else "OK"
        print(
            f"{r['concurrency']:>6} | {r['success']:>4} | {r['fail']:>4} | "
            f"{r['wall_time']:>8} | {r['avg_latency']:>7} | {r['p95_latency']:>7} | {note}"
        )


if __name__ == "__main__":
    asyncio.run(main())
