"""주간 환율 카드뉴스 파이프라인 진입점.

  python -m fx_newsletter.main --dry-run      # 카드만 렌더링하고 저장
  python -m fx_newsletter.main                # 렌더링 후 메일 발송
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

from . import cards, commentary as commentary_mod, fetch, indicators, mailer
from .config import HISTORY_DAYS, MailConfig

log = logging.getLogger("fx_newsletter")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="주간 환율 카드뉴스 생성 및 발송")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="메일을 보내지 않고 카드 이미지만 만든다",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Claude 해설을 건너뛰고 규칙 기반 문장만 쓴다",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("build/fx-cards"),
        help="카드 PNG를 저장할 디렉터리 (기본: build/fx-cards)",
    )
    parser.add_argument(
        "--date",
        type=dt.date.fromisoformat,
        default=None,
        help="발행 기준일 (YYYY-MM-DD). 기본은 오늘",
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    issued = args.date or dt.date.today()

    log.info("환율 시계열 수집 중 (기준일 %s, %d일치)", issued, HISTORY_DAYS)
    series_map = fetch.load_series(issued, HISTORY_DAYS)

    snapshots = indicators.build_snapshots(series_map)
    if not snapshots:
        log.error("계산된 스냅샷이 없습니다.")
        return 1
    log.info("지표 계산 완료: %s", ", ".join(s.display_name for s in snapshots))

    note = commentary_mod.build(snapshots, use_ai=not args.no_ai)
    log.info("해설 생성 완료 (source=%s): %s", note.source, note.headline)

    card_paths = cards.render_all(snapshots, note, issued, args.out)
    log.info("카드 %d장 생성 -> %s", len(card_paths), args.out)

    summary_path = args.out / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "issued": issued.isoformat(),
                "commentary": note.to_dict(),
                "pairs": [
                    {
                        "code": s.code,
                        "display_name": s.display_name,
                        "latest": round(s.latest, 4),
                        "latest_date": s.latest_date.isoformat(),
                        "week_pct": round(s.week.pct, 4) if s.week else None,
                        "trend": s.trend,
                    }
                    for s in snapshots
                ],
                "cards": [p.name for p in card_paths],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    if args.dry_run:
        log.info("--dry-run 이므로 메일은 보내지 않습니다.")
        return 0

    config = MailConfig.from_env()
    missing = config.validate()
    if missing:
        log.error("메일 설정 누락: %s", ", ".join(missing))
        return 2

    message = mailer.build_message(config, snapshots, note, issued, card_paths)
    mailer.send(config, message)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    args = parse_args(argv)
    try:
        return run(args)
    except Exception as exc:
        log.error("실패: %s: %s", type(exc).__name__, exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
