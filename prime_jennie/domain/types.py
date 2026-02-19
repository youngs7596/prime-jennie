"""기본 타입 정의 — 서비스 전체에서 공유하는 Annotated 타입."""

from typing import Annotated

from pydantic import Field

# 종목코드: 6자리 숫자 문자열 (예: "005930")
StockCode = Annotated[str, Field(pattern=r"^\d{6}$", examples=["005930", "000660"])]

# 점수: 0~100 범위 실수
Score = Annotated[float, Field(ge=0, le=100)]

# 양의 정수 (수량)
Quantity = Annotated[int, Field(gt=0)]

# 양의 금액
PositiveAmount = Annotated[int, Field(gt=0)]

# 배율: 0.3~2.0 범위 (포지션 사이징 등)
Multiplier = Annotated[float, Field(ge=0.3, le=2.0)]

# 퍼센트: 0~100 범위
Percent = Annotated[float, Field(ge=0, le=100)]
