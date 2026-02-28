"""과거 매크로 수급 데이터 정정 — 1회성 backfill.

fetch_investor_flows의 off-by-one + "기관" 매칭 버그로 인해
daily_macro_insights 테이블의 kospi_foreign_net, kospi_institutional_net,
kospi_retail_net 값이 잘못 적재됨.

수정된 크롤러로 재호출하여 3개 컬럼을 정정한다.

Usage:
    uv run python scripts/backfill_macro_flows.py
"""

import time

from sqlmodel import Session, select

from prime_jennie.infra.crawlers.naver_market import fetch_investor_flows
from prime_jennie.infra.database.engine import get_engine
from prime_jennie.infra.database.models import DailyMacroInsightDB


def main():
    engine = get_engine()
    with Session(engine) as session:
        rows = session.exec(select(DailyMacroInsightDB).where(DailyMacroInsightDB.kospi_foreign_net.isnot(None))).all()

        print(f"Found {len(rows)} rows with investor flow data")

        updated = 0
        for row in rows:
            bizdate = row.insight_date.strftime("%Y%m%d")
            flows = fetch_investor_flows("kospi", bizdate)
            if flows is None:
                print(f"  SKIP {row.insight_date}: no data from Naver")
                continue

            old = (
                row.kospi_foreign_net,
                row.kospi_institutional_net,
                row.kospi_retail_net,
            )
            row.kospi_foreign_net = flows.foreign_net
            row.kospi_institutional_net = flows.institutional_net
            row.kospi_retail_net = flows.retail_net
            session.commit()

            new = (flows.foreign_net, flows.institutional_net, flows.retail_net)
            changed = "CHANGED" if old != new else "same"
            print(f"  {row.insight_date}: {old} → {new} [{changed}]")
            updated += 1
            time.sleep(0.3)

    print(f"Done: {updated}/{len(rows)} rows updated")


if __name__ == "__main__":
    main()
