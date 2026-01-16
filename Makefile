.PHONY: urw-smoke

urw-smoke:
	PYTHONPATH=src uv run python scripts/urw_smoke.py
