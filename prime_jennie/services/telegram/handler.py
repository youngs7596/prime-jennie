"""Command Handler — 텔레그램 명령 처리기.

24개 명령 처리: 트레이딩 제어, 수동 매매, 포트폴리오 조회,
워치리스트 관리, 알림 제어, 설정 변경, 진단.
"""

import json
import logging
import time
from datetime import date, datetime, timezone
from typing import Callable, Optional

import redis
from sqlmodel import Session, select

from prime_jennie.domain.config import get_config
from prime_jennie.domain.enums import SignalType, TradeTier, MarketRegime, RiskTag
from prime_jennie.domain.trading import BuySignal, SellOrder
from prime_jennie.infra.database.models import StockMasterDB
from prime_jennie.infra.kis.client import KISClient

logger = logging.getLogger(__name__)

# Redis 키
KEY_PAUSE = "trading_flags:pause"
KEY_STOP = "trading_flags:stop"
KEY_DRYRUN = "trading_flags:dryrun"
KEY_MUTE_UNTIL = "notification:mute_until"
KEY_ALERTS = "price_alerts"
RATE_LIMIT_PREFIX = "telegram:rl:"
MANUAL_TRADE_PREFIX = "telegram:manual_trades:"

COMMAND_MIN_INTERVAL = 5  # seconds
MANUAL_TRADE_DAILY_LIMIT = 20

HELP_TEXT = """*Prime Jennie 명령어*

*매매 제어*
/pause [사유] — 자동매수 일시정지
/resume — 자동매수 재개
/stop 확인 — 전체 거래 긴급정지
/dryrun on|off — 시뮬레이션 모드

*수동 매매*
/buy 종목명 [수량] — 수동 매수
/sell 종목명 [수량|전량] — 수동 매도
/sellall 확인 — 전체 청산

*조회*
/status — 시스템 상태
/portfolio — 보유 종목
/pnl — 오늘 손익
/balance — 현금 잔고
/price 종목명|코드 — 현재가 조회

*워치리스트*
/watchlist — 워치리스트 조회
/watch 종목명 — 워치리스트 추가
/unwatch 종목명 — 워치리스트 제거

*알림*
/mute 분 — 알림 음소거
/unmute — 알림 재개
/alert 종목명 가격 — 가격 알림 설정
/alerts — 알림 목록

*설정*
/config — 현재 설정 조회
/maxbuy 횟수 — 일일 최대 매수 변경

*진단*
/diagnose — 시스템 진단

/help — 이 도움말
"""


class CommandHandler:
    """텔레그램 명령 처리기."""

    def __init__(
        self,
        redis_client: redis.Redis,
        kis_client: KISClient,
        db_session_factory: Callable[[], Session],
    ):
        self._redis = redis_client
        self._kis = kis_client
        self._session_factory = db_session_factory
        self._config = get_config()

        self._handlers: dict[str, Callable] = {
            "/help": self._handle_help,
            "/status": self._handle_status,
            "/pause": self._handle_pause,
            "/resume": self._handle_resume,
            "/stop": self._handle_stop,
            "/dryrun": self._handle_dryrun,
            "/buy": self._handle_buy,
            "/sell": self._handle_sell,
            "/sellall": self._handle_sellall,
            "/portfolio": self._handle_portfolio,
            "/pnl": self._handle_pnl,
            "/balance": self._handle_balance,
            "/price": self._handle_price,
            "/watchlist": self._handle_watchlist,
            "/watch": self._handle_watch,
            "/unwatch": self._handle_unwatch,
            "/mute": self._handle_mute,
            "/unmute": self._handle_unmute,
            "/alert": self._handle_alert,
            "/alerts": self._handle_alerts,
            "/config": self._handle_config,
            "/maxbuy": self._handle_maxbuy,
            "/diagnose": self._handle_diagnose,
            "/report": self._handle_diagnose,  # alias
        }

    def process_command(
        self, command: str, args: str, chat_id: str | int, username: str = ""
    ) -> str:
        """명령 처리 후 응답 텍스트 반환."""
        # 레이트 리밋
        if self._is_rate_limited(str(chat_id)):
            return "너무 빠릅니다. 잠시 후 다시 시도하세요."

        handler = self._handlers.get(command)
        if not handler:
            return f"알 수 없는 명령: {command}\n/help 로 명령 목록 확인"

        try:
            return handler(args, chat_id=chat_id, username=username)
        except Exception as e:
            logger.exception("Command %s failed", command)
            return f"명령 실행 실패: {e}"

    # ─── Rate Limiting ──────────────────────────────

    def _is_rate_limited(self, chat_id: str) -> bool:
        """커맨드 레이트 리밋 (5초 간격)."""
        key = f"{RATE_LIMIT_PREFIX}{chat_id}"
        try:
            if self._redis.exists(key):
                return True
            self._redis.setex(key, COMMAND_MIN_INTERVAL, "1")
            return False
        except Exception:
            return False

    def _check_manual_trade_limit(self, chat_id: str) -> bool:
        """일일 수동 매매 한도 확인. True=허용."""
        key = f"{MANUAL_TRADE_PREFIX}{date.today().isoformat()}:{chat_id}"
        try:
            count = int(self._redis.get(key) or 0)
            return count < MANUAL_TRADE_DAILY_LIMIT
        except Exception:
            return True

    def _increment_manual_trade(self, chat_id: str) -> None:
        key = f"{MANUAL_TRADE_PREFIX}{date.today().isoformat()}:{chat_id}"
        try:
            pipe = self._redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, 86400)
            pipe.execute()
        except Exception:
            pass

    # ─── Stock Resolution ──────────────────────────

    def _resolve_stock(self, name_or_code: str) -> Optional[tuple[str, str]]:
        """종목코드 또는 이름으로 (code, name) 반환."""
        name_or_code = name_or_code.strip()
        if not name_or_code:
            return None

        try:
            with self._session_factory() as session:
                # 6자리 코드
                if name_or_code.isdigit() and len(name_or_code) == 6:
                    stmt = select(StockMasterDB).where(
                        StockMasterDB.stock_code == name_or_code
                    )
                    stock = session.exec(stmt).first()
                    if stock:
                        return stock.stock_code, stock.stock_name
                    return name_or_code, name_or_code  # 코드만 반환

                # 이름 검색
                stmt = select(StockMasterDB).where(
                    StockMasterDB.stock_name == name_or_code
                )
                stock = session.exec(stmt).first()
                if stock:
                    return stock.stock_code, stock.stock_name

                return None
        except Exception:
            # 6자리 숫자면 코드로 간주
            if name_or_code.isdigit() and len(name_or_code) == 6:
                return name_or_code, name_or_code
            return None

    # ─── Handlers: Trading Control ─────────────────

    def _handle_help(self, args: str, **kwargs) -> str:
        return HELP_TEXT

    def _handle_status(self, args: str, **kwargs) -> str:
        paused = self._redis.get(KEY_PAUSE)
        stopped = self._redis.get(KEY_STOP)
        dryrun = self._redis.get(KEY_DRYRUN)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        lines = [
            "*시스템 상태*",
            f"일시정지: {'예' if paused else '아니오'}",
            f"긴급정지: {'예' if stopped else '아니오'}",
            f"DRY\\_RUN: {'ON' if dryrun else 'OFF'}",
            f"트레이딩 모드: {self._config.trading_mode}",
            f"시각: {now}",
        ]
        return "\n".join(lines)

    def _handle_pause(self, args: str, **kwargs) -> str:
        reason = args or "수동 일시정지"
        self._redis.set(KEY_PAUSE, reason)
        return f"자동매수를 일시정지했습니다.\n사유: {reason}"

    def _handle_resume(self, args: str, **kwargs) -> str:
        self._redis.delete(KEY_PAUSE)
        self._redis.delete(KEY_STOP)
        return "자동매수를 재개합니다."

    def _handle_stop(self, args: str, **kwargs) -> str:
        if args.strip() not in ("확인", "긴급"):
            return "긴급정지: `/stop 확인` 또는 `/stop 긴급` 으로 실행"
        self._redis.set(KEY_STOP, "1")
        self._redis.set(KEY_PAUSE, "emergency_stop")
        return "전체 거래를 긴급 정지했습니다.\n재개: /resume"

    def _handle_dryrun(self, args: str, **kwargs) -> str:
        arg = args.strip().lower()
        if arg == "on":
            self._redis.set(KEY_DRYRUN, "1")
            return "DRY\\_RUN 모드: ON (시뮬레이션)"
        elif arg == "off":
            self._redis.delete(KEY_DRYRUN)
            return "DRY\\_RUN 모드: OFF (실거래)"
        return "사용법: `/dryrun on` 또는 `/dryrun off`"

    # ─── Handlers: Manual Trading ─────────────────

    def _handle_buy(self, args: str, chat_id: str | int = "", **kwargs) -> str:
        if not self._check_manual_trade_limit(str(chat_id)):
            return "일일 수동매매 한도에 도달했습니다."

        parts = args.strip().split()
        if not parts:
            return "사용법: `/buy 종목명 [수량]`"

        stock = self._resolve_stock(parts[0])
        if not stock:
            return f"종목을 찾을 수 없습니다: {parts[0]}"

        code, name = stock
        quantity = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None

        # 수량 미지정 시 현금 20% 기반 자동 계산
        if quantity is None:
            try:
                cash = self._kis.get_cash_balance()
                price = self._kis.get_price(code)
                if price > 0:
                    quantity = int((cash * 0.20) / price)
            except Exception:
                pass

        if not quantity or quantity <= 0:
            return "수량을 계산할 수 없습니다. 수량을 직접 입력하세요."

        # Redis Stream 발행
        signal = {
            "source": "telegram-manual",
            "stock_code": code,
            "stock_name": name,
            "quantity": quantity,
            "signal_type": "MANUAL",
            "dry_run": bool(self._redis.get(KEY_DRYRUN)),
        }
        try:
            self._redis.xadd("stream:buy-signals", signal)
            self._increment_manual_trade(str(chat_id))
            return f"매수 요청 접수: {name}({code}) {quantity}주"
        except Exception as e:
            return f"매수 요청 실패: {e}"

    def _handle_sell(self, args: str, chat_id: str | int = "", **kwargs) -> str:
        if not self._check_manual_trade_limit(str(chat_id)):
            return "일일 수동매매 한도에 도달했습니다."

        parts = args.strip().split()
        if not parts:
            return "사용법: `/sell 종목명 [수량|전량]`"

        stock = self._resolve_stock(parts[0])
        if not stock:
            return f"종목을 찾을 수 없습니다: {parts[0]}"

        code, name = stock
        qty_str = parts[1] if len(parts) > 1 else "전량"
        is_full = qty_str in ("전량", "all")
        quantity = 0 if is_full else (int(qty_str) if qty_str.isdigit() else 0)

        signal = {
            "source": "telegram-manual",
            "stock_code": code,
            "stock_name": name,
            "quantity": str(quantity),
            "sell_all": str(is_full),
            "sell_reason": "MANUAL",
            "dry_run": str(bool(self._redis.get(KEY_DRYRUN))),
        }
        try:
            self._redis.xadd("stream:sell-orders", signal)
            self._increment_manual_trade(str(chat_id))
            label = "전량" if is_full else f"{quantity}주"
            return f"매도 요청 접수: {name}({code}) {label}"
        except Exception as e:
            return f"매도 요청 실패: {e}"

    def _handle_sellall(self, args: str, **kwargs) -> str:
        if args.strip() != "확인":
            return "전체 청산: `/sellall 확인` 으로 실행"

        signal = {
            "source": "telegram-manual",
            "action": "liquidate_all",
            "sell_reason": "MANUAL",
            "dry_run": str(bool(self._redis.get(KEY_DRYRUN))),
        }
        try:
            self._redis.xadd("stream:sell-orders", signal)
            return "전체 포트폴리오 청산 요청을 접수했습니다."
        except Exception as e:
            return f"청산 요청 실패: {e}"

    # ─── Handlers: Portfolio & Status ──────────────

    def _handle_portfolio(self, args: str, **kwargs) -> str:
        try:
            positions = self._kis.get_positions()
            if not positions:
                return "보유 종목이 없습니다."

            lines = [f"*보유 포트폴리오* ({len(positions)}종목)\n"]
            for p in positions:
                lines.append(
                    f"  {p.stock_name} ({p.stock_code})\n"
                    f"  {p.quantity}주 | 평균: {p.average_buy_price:,}원"
                )
            return "\n".join(lines)
        except Exception as e:
            return f"포트폴리오 조회 실패: {e}"

    def _handle_pnl(self, args: str, **kwargs) -> str:
        try:
            from prime_jennie.infra.database.models import TradeLogDB

            with self._session_factory() as session:
                today = date.today()
                stmt = select(TradeLogDB).where(TradeLogDB.trade_date >= today)
                trades = list(session.exec(stmt).all())

            buys = [t for t in trades if t.trade_type == "BUY"]
            sells = [t for t in trades if t.trade_type == "SELL"]
            realized = sum(
                float(t.profit_pct or 0) for t in sells
            )

            return (
                f"*오늘 매매*\n"
                f"매수: {len(buys)}건\n"
                f"매도: {len(sells)}건\n"
                f"실현 수익률 합계: {realized:+.1f}%"
            )
        except Exception as e:
            return f"PnL 조회 실패: {e}"

    def _handle_balance(self, args: str, **kwargs) -> str:
        try:
            cash = self._kis.get_cash_balance()
            return f"현금 잔고: {cash:,}원"
        except Exception as e:
            return f"잔고 조회 실패: {e}"

    def _handle_price(self, args: str, **kwargs) -> str:
        name_or_code = args.strip()
        if not name_or_code:
            return "사용법: `/price 종목명|코드`"

        stock = self._resolve_stock(name_or_code)
        if not stock:
            return f"종목을 찾을 수 없습니다: {name_or_code}"

        code, name = stock
        try:
            snapshot = self._kis.get_snapshot(code)
            return (
                f"*{name}* ({code})\n"
                f"현재가: {snapshot.price:,}원\n"
                f"시가: {snapshot.open_price:,}원\n"
                f"등락: {snapshot.change_pct:+.2f}%\n"
                f"고가: {snapshot.high_price:,}원\n"
                f"저가: {snapshot.low_price:,}원"
            )
        except Exception as e:
            return f"가격 조회 실패: {e}"

    # ─── Handlers: Watchlist ──────────────────────

    def _handle_watchlist(self, args: str, **kwargs) -> str:
        try:
            from prime_jennie.infra.database.repositories import WatchlistRepository

            with self._session_factory() as session:
                items = WatchlistRepository.get_latest(session)

            if not items:
                return "워치리스트가 비어있습니다."

            lines = [f"*워치리스트* ({len(items)}종목)\n"]
            for w in items[:20]:
                score = w.hybrid_score or 0
                emoji = "🔥" if score >= 80 else ("📈" if score >= 60 else "➖")
                lines.append(
                    f"  {emoji} #{w.rank} {w.stock_name} "
                    f"({score:.0f}점, {w.trade_tier})"
                )
            return "\n".join(lines)
        except Exception as e:
            return f"워치리스트 조회 실패: {e}"

    def _handle_watch(self, args: str, **kwargs) -> str:
        name_or_code = args.strip()
        if not name_or_code:
            return "사용법: `/watch 종목명|코드`"

        stock = self._resolve_stock(name_or_code)
        if not stock:
            return f"종목을 찾을 수 없습니다: {name_or_code}"

        code, name = stock
        try:
            self._redis.hset("watchlist:manual", code, name)
            return f"워치리스트에 추가: {name}({code})"
        except Exception as e:
            return f"추가 실패: {e}"

    def _handle_unwatch(self, args: str, **kwargs) -> str:
        name_or_code = args.strip()
        if not name_or_code:
            return "사용법: `/unwatch 종목명|코드`"

        stock = self._resolve_stock(name_or_code)
        if not stock:
            return f"종목을 찾을 수 없습니다: {name_or_code}"

        code, name = stock
        try:
            self._redis.hdel("watchlist:manual", code)
            return f"워치리스트에서 제거: {name}({code})"
        except Exception as e:
            return f"제거 실패: {e}"

    # ─── Handlers: Notifications ──────────────────

    def _handle_mute(self, args: str, **kwargs) -> str:
        try:
            minutes = int(args.strip())
        except (ValueError, TypeError):
            return "사용법: `/mute 분` (예: /mute 30)"

        until = int(time.time()) + minutes * 60
        self._redis.set(KEY_MUTE_UNTIL, str(until))
        return f"알림을 {minutes}분간 음소거합니다."

    def _handle_unmute(self, args: str, **kwargs) -> str:
        self._redis.delete(KEY_MUTE_UNTIL)
        return "알림이 재개됩니다."

    def _handle_alert(self, args: str, **kwargs) -> str:
        parts = args.strip().split()
        if len(parts) < 2:
            return "사용법: `/alert 종목명 가격`"

        stock = self._resolve_stock(parts[0])
        if not stock:
            return f"종목을 찾을 수 없습니다: {parts[0]}"

        try:
            target_price = int(parts[1].replace(",", ""))
        except ValueError:
            return "가격은 숫자로 입력하세요."

        code, name = stock
        alert = {
            "stock_code": code,
            "stock_name": name,
            "target_price": target_price,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._redis.hset(KEY_ALERTS, f"{code}:{target_price}", json.dumps(alert))
        self._redis.expire(KEY_ALERTS, 7 * 86400)  # 7일 TTL

        try:
            current = self._kis.get_price(code)
            direction = "이상" if target_price > current else "이하"
            diff_pct = abs(target_price - current) / current * 100
            return (
                f"가격 알림 설정: {name}({code})\n"
                f"목표: {target_price:,}원 {direction}\n"
                f"현재가 대비: {diff_pct:.1f}% 차이"
            )
        except Exception:
            return f"가격 알림 설정: {name}({code}) → {target_price:,}원"

    def _handle_alerts(self, args: str, **kwargs) -> str:
        try:
            alerts = self._redis.hgetall(KEY_ALERTS)
            if not alerts:
                return "설정된 알림이 없습니다."

            lines = ["*가격 알림 목록*\n"]
            for key, val in alerts.items():
                data = json.loads(val)
                lines.append(
                    f"  {data['stock_name']}({data['stock_code']}) "
                    f"→ {data['target_price']:,}원"
                )
            return "\n".join(lines)
        except Exception as e:
            return f"알림 조회 실패: {e}"

    # ─── Handlers: Configuration ─────────────────

    def _handle_config(self, args: str, **kwargs) -> str:
        paused = self._redis.get(KEY_PAUSE)
        stopped = self._redis.get(KEY_STOP)
        dryrun = self._redis.get(KEY_DRYRUN)
        mute_until = self._redis.get(KEY_MUTE_UNTIL)

        mute_str = "OFF"
        if mute_until:
            remaining = int(mute_until) - int(time.time())
            if remaining > 0:
                mute_str = f"{remaining // 60}분 남음"

        return (
            f"*현재 설정*\n"
            f"일시정지: {paused or 'OFF'}\n"
            f"긴급정지: {'ON' if stopped else 'OFF'}\n"
            f"DRY\\_RUN: {'ON' if dryrun else 'OFF'}\n"
            f"알림 음소거: {mute_str}\n"
            f"트레이딩 모드: {self._config.trading_mode}\n"
            f"최대 포트폴리오: {self._config.risk.max_portfolio_size}종목\n"
            f"일일 최대 매수: {self._config.risk.max_buy_count_per_day}회"
        )

    def _handle_maxbuy(self, args: str, **kwargs) -> str:
        try:
            val = int(args.strip())
            if not 0 <= val <= 20:
                return "0~20 사이 값을 입력하세요."
            self._redis.set("config:max_buy_count", str(val))
            return f"일일 최대 매수: {val}회로 변경"
        except (ValueError, TypeError):
            return "사용법: `/maxbuy 횟수` (0~20)"

    # ─── Handlers: Diagnostics ───────────────────

    def _handle_diagnose(self, args: str, **kwargs) -> str:
        checks = []

        # Redis 연결
        try:
            self._redis.ping()
            checks.append("Redis: OK")
        except Exception:
            checks.append("Redis: FAIL")

        # DB 연결
        try:
            with self._session_factory() as session:
                session.exec(select(StockMasterDB).limit(1)).first()
            checks.append("DB: OK")
        except Exception:
            checks.append("DB: FAIL")

        # KIS Gateway
        try:
            self._kis.get_cash_balance()
            checks.append("KIS Gateway: OK")
        except Exception:
            checks.append("KIS Gateway: FAIL")

        # 플래그
        paused = "YES" if self._redis.get(KEY_PAUSE) else "NO"
        stopped = "YES" if self._redis.get(KEY_STOP) else "NO"

        return (
            "*시스템 진단*\n\n"
            + "\n".join(checks)
            + f"\n\n일시정지: {paused}\n긴급정지: {stopped}"
        )
