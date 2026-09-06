.PHONY: help setup doctor test backtest simulate lint clean kill unkill

PY ?= python3

help:
	@echo "make setup      의존성 설치"
	@echo "make doctor     환경·설정·연결 점검"
	@echo "make test       전체 테스트"
	@echo "make backtest   샘플 데이터 백테스트"
	@echo "make simulate   오프라인 엔진 리허설"
	@echo "make kill       킬 스위치 ON (즉시 전체 중단)"
	@echo "make unkill     킬 스위치 OFF"

setup:
	$(PY) -m pip install -r requirements.txt

doctor:
	$(PY) -m autotrade.cli doctor

test:
	$(PY) -m pytest

backtest:
	$(PY) -m autotrade.cli backtest --trades

simulate:
	$(PY) -m autotrade.cli simulate --cycles 200 --warmup 25

kill:
	$(PY) -m autotrade.cli kill --reason "makefile"

unkill:
	$(PY) -m autotrade.cli unkill

clean:
	rm -rf var/journal.jsonl var/kis_token.json .pytest_cache
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
