import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autotrade.clock import KST  # noqa: E402
from autotrade.config import CostModel, RiskLimits, Settings  # noqa: E402

# 테스트는 환경변수에 영향받지 않아야 한다.
for _key in [k for k in os.environ if k.startswith(("AUTOTRADE_", "KIS_", "MERITZ_"))]:
    os.environ.pop(_key, None)

# 테스트에서는 유량 제한 대기로 시간을 낭비하지 않는다(가짜 세션이므로 무해).
os.environ["KIS_MAX_CALLS_PER_SEC"] = "10000"

#: 정규장 한복판 (2026-09-07 은 월요일)
TRADING_TIME = datetime(2026, 9, 7, 10, 30, tzinfo=KST)


@pytest.fixture
def limits():
    return RiskLimits(
        max_order_notional=300_000,
        max_position_notional=1_000_000,
        max_gross_exposure=3_000_000,
        daily_loss_limit=100_000,
        max_orders_per_day=5,
        max_orders_per_minute=3,
        min_seconds_between_same_signal=60,
        symbol_whitelist=("005930",),
    )


@pytest.fixture
def settings(tmp_path, limits):
    s = Settings(
        mode="paper",
        dry_run=False,
        base_dir=tmp_path,
        journal_path=tmp_path / "journal.jsonl",
        token_cache_path=tmp_path / "token.json",
        kill_switch_path=tmp_path / "KILL_SWITCH",
        risk=limits,
        costs=CostModel(fee_rate=0.00015, sell_tax_rate=0.0015, slippage_bps=0.0),
        paper_starting_cash=10_000_000,
    )
    return s
