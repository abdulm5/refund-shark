.PHONY: setup demo test check build eval smoke screenshots fixtures fetch-sources verify-sources

setup:
	UV_CACHE_DIR=.cache/uv uv sync --frozen
	cd web && npm ci --cache ../.cache/npm

demo:
	@test -x .venv/bin/python || (echo "Run make setup first." && exit 1)
	.venv/bin/python scripts/dev.py

test:
	.venv/bin/python -m pytest -q

eval:
	.venv/bin/python -m eval.run_eval

check: test eval
	cd web && npm run typecheck

build:
	cd web && NEXT_TELEMETRY_DISABLED=1 npm run build

# Headless-Chrome check that the three demo outcomes render through the UI.
# It rebuilds web/.next against a temporary port; run `make build` before `npm start`.
smoke:
	.venv/bin/python scripts/ui_smoke.py

screenshots:
	.venv/bin/python scripts/ui_smoke.py --screenshots docs/screenshots

# Regenerate the bundled scenarios (and, with the private snapshot cache, the excerpts).
fixtures:
	.venv/bin/python scripts/create_demo_fixtures.py

fetch-sources:
	.venv/bin/python -m shark.cli fetch-sources

verify-sources:
	.venv/bin/python -m shark.cli verify-sources
