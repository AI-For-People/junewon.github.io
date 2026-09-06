"""명령줄 진입점.

  python -m autotrade.cli doctor          환경·설정 점검 (제일 먼저 실행)
  python -m autotrade.cli backtest ...    과거 데이터로 전략 검증
  python -m autotrade.cli simulate ...    오프라인 시뮬레이터로 엔진 전체 리허설
  python -m autotrade.cli quote ...       증권사 API 현재가 조회 (연결 확인용)
  python -m autotrade.cli balance         잔고 조회
  python -m autotrade.cli fetch ...       일봉을 CSV로 저장
  python -m autotrade.cli run ...         실제 운영 루프 (기본 드라이런)
  python -m autotrade.cli kill / unkill   킬 스위치 조작
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import timedelta
from pathlib import Path

from . import __version__
from .backtest import load_candles_csv, run_backtest
from .brokers import get_broker
from .brokers.paper import PaperBroker, PriceFeed
from .clock import MarketCalendar, now_kst
from .config import LIVE_ACK_ENV, LIVE_ACK_VALUE, Settings
from .engine import TradingEngine
from .errors import AutoTradeError
from .journal import Journal, setup_logging
from .risk import RiskGuard
from .strategies import REGISTRY

OK, WARN, BAD = "  ✅", "  ⚠️ ", "  ❌"


def _build_strategy(args) -> object:
    cls = REGISTRY[args.strategy]
    if args.strategy == "sma_cross":
        return cls(fast=args.fast, slow=args.slow, target_notional=args.notional)
    return cls(target_notional=args.notional)


def _make_engine(settings: Settings, args, broker) -> TradingEngine:
    journal = Journal(settings.journal_path)
    calendar = MarketCalendar(holiday_file=settings.holiday_file)
    risk = RiskGuard(
        settings.risk,
        calendar=calendar,
        kill_switch_path=settings.kill_switch_path,
        trade_only_in_session=settings.trade_only_in_session,
    )
    return TradingEngine(
        settings, broker, _build_strategy(args), risk, journal,
        calendar=calendar,
        use_market_order=getattr(args, "market_order", False),
        limit_mode=getattr(args, "limit_mode", "aggressive"),
    )


# ------------------------------------------------------------------ doctor
def cmd_doctor(args) -> int:
    print(f"autotrade v{__version__} 환경 점검\n" + "=" * 56)
    problems = 0   # 고쳐야 동작하는 것
    warnings = 0   # 알고 넘어가야 하는 것

    try:
        settings = Settings.load()
        print(f"{OK} 설정 로드 성공")
        for key, value in settings.describe().items():
            print(f"      {key}: {value}")
    except AutoTradeError as exc:
        print(f"{BAD} 설정 오류:\n      {exc}")
        return 1

    print("\n[의존성]")
    try:
        import requests  # noqa: F401
        print(f"{OK} requests 설치됨")
    except ImportError:
        print(f"{BAD} requests 미설치 — pip install -r requirements.txt")
        problems += 1

    print("\n[시간/달력]")
    cal = MarketCalendar(holiday_file=settings.holiday_file)
    now = now_kst()
    print(f"{OK} 현재 KST {now:%Y-%m-%d %H:%M:%S}, 장 상태: {cal.session_state(now)}")
    if not cal.holidays_cover_year(now.year):
        print(f"{WARN} {now.year}년 휴장일 데이터가 없습니다 — data/krx_holidays.txt 갱신 필요")
        warnings += 1
    else:
        print(f"{OK} 휴장일 {len(cal.holidays)}건 로드 (KRX 공지와 대조 권장)")

    print("\n[파일/권한]")
    for label, path in (
        ("감사 로그", settings.journal_path),
        ("토큰 캐시", settings.token_cache_path),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        writable = path.parent.exists()
        print(f"{OK if writable else BAD} {label} 경로 {path}")
        problems += 0 if writable else 1
    env_file = settings.base_dir / ".env"
    if env_file.exists():
        mode = env_file.stat().st_mode & 0o777
        if mode & 0o077:
            print(f"{WARN} .env 파일 권한이 {oct(mode)} 입니다 — chmod 600 .env 권장")
            warnings += 1
        else:
            print(f"{OK} .env 권한 {oct(mode)}")
    else:
        print(f"{WARN} .env 없음 (paper 모드는 없어도 동작). cp .env.example .env")
        warnings += 1

    print("\n[안전장치]")
    if settings.dry_run:
        print(f"{OK} DRY_RUN=on — 주문이 실제로 나가지 않습니다")
    else:
        print(f"{WARN} DRY_RUN=off — 실제 주문이 전송됩니다")
        warnings += 1
    if settings.kill_switch_path.exists():
        print(f"{WARN} 킬 스위치가 올라가 있습니다: {settings.kill_switch_path}")
        warnings += 1
    else:
        print(f"{OK} 킬 스위치 내려감")
    if not settings.risk.symbol_whitelist:
        print(f"{WARN} 거래 허용 종목이 비어 있어 모든 주문이 차단됩니다")
        warnings += 1
    else:
        print(f"{OK} 허용 종목: {', '.join(settings.risk.symbol_whitelist)}")
    if settings.is_real_money:
        print(f"{WARN} 실계좌 모드({settings.mode})입니다. 손실은 전부 본인 부담입니다.")
        warnings += 1

    print("\n[증권사 연결]")
    if settings.mode == "paper":
        print(f"{OK} 오프라인 시뮬레이터 — 연결 불필요")
    elif settings.mode == "meritz":
        from .brokers.meritz import DEFAULT_SPEC_PATH, status
        if DEFAULT_SPEC_PATH.exists():
            print(f"{OK} 메리츠 스펙 파일 존재: {DEFAULT_SPEC_PATH}")
        else:
            print(f"{WARN} 메리츠 공개 API 미확인 상태")
            warnings += 1
            for line in status().splitlines():
                print(f"      {line}")
    else:
        try:
            broker = get_broker(settings)
            token = broker.access_token()
            print(f"{OK} 접근토큰 발급 성공 (len={len(token)})")
            acct = broker.get_account()
            print(f"{OK} 잔고 조회 성공 — 현금 {acct.cash:,.0f}원, 보유 {len(acct.positions)}종목")
            broker.close()
        except AutoTradeError as exc:
            print(f"{BAD} 연결 실패: {exc}")
            problems += 1
        except Exception as exc:  # 네트워크 차단 등
            print(f"{BAD} 연결 실패(네트워크/방화벽 확인): {exc!r}")
            problems += 1

    print("\n" + "=" * 56)
    if problems:
        print(f"점검 완료 — 오류 {problems}건 ❌, 경고 {warnings}건 ⚠️  (오류를 먼저 해결하십시오)")
        return 2
    if warnings:
        print(f"점검 완료 — 오류 없음 ✅, 경고 {warnings}건 ⚠️  (내용을 확인하고 넘어가십시오)")
        return 0
    print("점검 완료 — 문제 없음 ✅")
    return 0


# ---------------------------------------------------------------- backtest
def cmd_backtest(args) -> int:
    settings = Settings.load()
    candles = load_candles_csv(args.csv)
    if not candles:
        print(f"CSV 에서 봉을 읽지 못했습니다: {args.csv}")
        return 1
    result = run_backtest(
        candles,
        _build_strategy(args),
        symbol=args.symbol,
        starting_cash=args.cash,
        settings=settings,
    )
    print(result.summary())
    if args.trades:
        print("\n[체결 내역]")
        for t in result.trades:
            print(
                f"  {t.ts:%Y-%m-%d} {t.side.value:4s} {t.qty:>5,d}주 @ {t.price:>10,.0f} "
                f"비용 {t.cost:>8,.0f} | {t.reason}"
            )
    print(
        "\n※ 합성/과거 데이터 결과이며 미래 수익을 보장하지 않습니다. "
        "수수료·세금·슬리피지 가정을 본인 계좌 기준으로 반드시 교체하십시오."
    )
    return 0


# ---------------------------------------------------------------- simulate
def cmd_simulate(args) -> int:
    settings = Settings.load()
    settings.mode = "paper"
    settings.trade_only_in_session = False   # 리허설은 장 시간과 무관하게 돌린다
    settings.dry_run = False                 # 시뮬레이터 안에서는 실제로 체결시킨다
    if not settings.risk.symbol_whitelist:
        settings.risk.symbol_whitelist = (args.symbol,)

    candles = load_candles_csv(args.csv) if args.csv else []
    feed = PriceFeed()
    if candles:
        feed.set_candles(args.symbol, candles)
        feed.set_cursor(args.symbol, min(args.warmup, len(candles) - 1))
    broker = PaperBroker(settings, feed=feed)

    engine = _make_engine(settings, args, broker)
    engine.risk.trade_only_in_session = False
    engine.calendar = MarketCalendar(holidays=set())

    print(f"오프라인 리허설 시작 — {args.symbol}, 전략 {args.strategy}, {args.cycles}사이클")
    # 일봉 리허설이므로 사이클마다 하루씩 시뮬레이션 시각을 진행시킨다.
    # (실시간처럼 같은 초에 몰아 돌리면 재발주 쿨다운에 전부 걸린다.)
    sim_start = now_kst().replace(hour=10, minute=0, second=0, microsecond=0)
    for i in range(args.cycles):
        result = engine.run_once([args.symbol], when=sim_start + timedelta(days=i))
        for order in result.orders:
            print(
                f"  [{i:>3d}] {order.side.value:4s} {order.qty:>4,d}주 @ "
                f"{order.avg_fill_price or order.price:>9,.0f} → {order.status.value} | {order.reason}"
            )
        for order, reason in result.blocked:
            print(f"  [{i:>3d}] 차단 {order.side.value} {order.symbol}: {reason}")
        if candles and not feed.advance(args.symbol):
            print("  데이터 끝")
            break

    equity = broker.equity()
    print("-" * 56)
    print(f"최종 현금 {broker.cash:,.0f}원 / 평가 {equity:,.0f}원 "
          f"/ 실현손익 {broker.realized_pnl:,.0f}원 / 비용 {broker.total_fees + broker.total_tax:,.0f}원")
    print(f"감사 로그: {settings.journal_path}")
    return 0


# ------------------------------------------------------------- quote/balance
def cmd_quote(args) -> int:
    settings = Settings.load()
    broker = get_broker(settings)
    try:
        q = broker.get_quote(args.symbol)
        print(f"{args.symbol}  현재가 {q.price:,.0f}원  (조회 {q.ts:%Y-%m-%d %H:%M:%S})")
    finally:
        broker.close()
    return 0


def cmd_balance(args) -> int:
    settings = Settings.load()
    broker = get_broker(settings)
    try:
        acct = broker.get_account()
        print(f"현금(주문가능) : {acct.cash:,.0f} 원")
        if not acct.positions:
            print("보유 종목 없음")
        for sym, pos in acct.positions.items():
            print(f"  {sym}  {pos.qty:>6,d}주  평균 {pos.avg_price:>10,.0f}원")
    finally:
        broker.close()
    return 0


def cmd_fetch(args) -> int:
    import csv as _csv

    settings = Settings.load()
    broker = get_broker(settings)
    try:
        candles = broker.get_candles(args.symbol, args.count)
    finally:
        broker.close()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        for c in candles:
            w.writerow(c.as_row())
    print(f"{len(candles)}봉 저장 → {out}")
    print("※ 시세 데이터는 재배포가 금지되는 경우가 많습니다. 외부 공개·공유 금지.")
    return 0


# --------------------------------------------------------------------- run
def cmd_run(args) -> int:
    settings = Settings.load()
    if args.dry_run is not None:
        settings.dry_run = args.dry_run
    symbols = args.symbols or list(settings.risk.symbol_whitelist)
    if not symbols:
        print("거래할 종목이 없습니다. --symbols 또는 AUTOTRADE_SYMBOL_WHITELIST 를 지정하십시오.")
        return 1

    if settings.is_real_money and not settings.dry_run:
        print("=" * 56)
        print("  실계좌 실거래 모드입니다. 손실은 전액 본인 부담입니다.")
        print(f"  모드 {settings.mode} / 종목 {', '.join(symbols)}")
        print(f"  1주문한도 {settings.risk.max_order_notional:,.0f}원 / "
              f"일손실한도 {settings.risk.daily_loss_limit:,.0f}원")
        print("=" * 56)
        if not args.yes:
            if input("  계속하려면 'START' 를 입력하십시오: ").strip() != "START":
                print("중단했습니다.")
                return 1

    broker = get_broker(settings, journal=Journal(settings.journal_path))
    engine = _make_engine(settings, args, broker)
    cycles = engine.run_forever(symbols, max_cycles=args.max_cycles)
    print(f"종료 — {cycles}사이클 실행. 감사 로그: {settings.journal_path}")
    return 0


# ------------------------------------------------------------- kill switch
def cmd_kill(args) -> int:
    settings = Settings.load()
    path = settings.kill_switch_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{now_kst().isoformat()} manual: {args.reason}\n", encoding="utf-8")
    print(f"킬 스위치 ON → {path}\n이후 모든 주문이 차단됩니다.")
    return 0


def cmd_unkill(args) -> int:
    settings = Settings.load()
    if settings.kill_switch_path.exists():
        settings.kill_switch_path.unlink()
        print("킬 스위치 OFF")
    else:
        print("킬 스위치는 이미 내려가 있습니다.")
    return 0


# -------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="autotrade",
        description="증권사 API 연동 자동매매 프레임워크 (기본값: 시뮬레이션 + 드라이런)",
    )
    p.add_argument("--verbose", "-v", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    def add_strategy_args(sp):
        sp.add_argument("--strategy", choices=sorted(REGISTRY), default="sma_cross")
        sp.add_argument("--fast", type=int, default=5)
        sp.add_argument("--slow", type=int, default=20)
        sp.add_argument("--notional", type=float, default=200_000.0, help="1회 목표 매수금액(원)")

    def add_limit_mode(sp):
        sp.add_argument(
            "--limit-mode", choices=("aggressive", "passive", "nearest"), default="aggressive",
            dest="limit_mode",
            help="지정가 호가 보정 방향. aggressive=체결 우선(기본), passive=가격 우선",
        )

    sp = sub.add_parser("doctor", help="환경·설정·연결 점검")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("backtest", help="CSV 일봉으로 전략 검증")
    sp.add_argument("--csv", default="data/sample_synthetic_daily.csv")
    sp.add_argument("--symbol", default="SAMPLE")
    sp.add_argument("--cash", type=float, default=10_000_000.0)
    sp.add_argument("--trades", action="store_true", help="체결 내역 출력")
    add_strategy_args(sp)
    sp.set_defaults(func=cmd_backtest)

    sp = sub.add_parser("simulate", help="오프라인 시뮬레이터로 엔진 전체 리허설")
    sp.add_argument("--symbol", default="SAMPLE")
    sp.add_argument("--csv", default="data/sample_synthetic_daily.csv")
    sp.add_argument("--cycles", type=int, default=200)
    sp.add_argument("--warmup", type=int, default=30)
    sp.add_argument("--market-order", action="store_true")
    add_strategy_args(sp)
    add_limit_mode(sp)
    sp.set_defaults(func=cmd_simulate)

    sp = sub.add_parser("quote", help="현재가 조회")
    sp.add_argument("--symbol", required=True)
    sp.set_defaults(func=cmd_quote)

    sp = sub.add_parser("balance", help="잔고 조회")
    sp.set_defaults(func=cmd_balance)

    sp = sub.add_parser("fetch", help="일봉을 CSV로 저장")
    sp.add_argument("--symbol", required=True)
    sp.add_argument("--count", type=int, default=300)
    sp.add_argument("--out", required=True)
    sp.set_defaults(func=cmd_fetch)

    sp = sub.add_parser("run", help="운영 루프 실행")
    sp.add_argument("--symbols", nargs="*", default=None)
    sp.add_argument("--max-cycles", type=int, default=None)
    sp.add_argument("--market-order", action="store_true")
    sp.add_argument("--yes", action="store_true", help="실계좌 확인 프롬프트 생략")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--dry-run", dest="dry_run", action="store_true", default=None)
    g.add_argument("--live-orders", dest="dry_run", action="store_false",
                   help="실제 주문 전송(드라이런 해제)")
    add_strategy_args(sp)
    add_limit_mode(sp)
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("kill", help="킬 스위치 ON (즉시 전체 중단)")
    sp.add_argument("--reason", default="manual stop")
    sp.set_defaults(func=cmd_kill)

    sp = sub.add_parser("unkill", help="킬 스위치 OFF")
    sp.set_defaults(func=cmd_unkill)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(logging.DEBUG if args.verbose else logging.INFO)
    try:
        return args.func(args)
    except AutoTradeError as exc:
        print(f"\n오류: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n중단됨")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
