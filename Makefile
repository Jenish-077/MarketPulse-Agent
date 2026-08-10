.PHONY: install migrate seed demo eval api web worker

install:
	pip install -e ".[dev]"
	playwright install chromium

migrate:
	python -c "from packages.db.session import init_db; init_db()"

seed:
	python scripts/seed_demo.py

demo:
	python -c "from packages.db.session import init_db; init_db()"
	python scripts/seed_demo.py --run-cycle
	@echo "Demo complete. Start UI with: make web"

eval:
	python evals/run_eval.py

api:
	uvicorn apps.api.main:app --reload --host 0.0.0.0 --port 8000

web:
	streamlit run apps/web/app.py --server.port 8501

worker:
	python -m apps.worker.main
