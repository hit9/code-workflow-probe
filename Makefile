UV ?= uv
MODULE := code_workflow_probe
CACHE ?= .code-workflow-probe.json

.PHONY: help install sync test check smoke status build clean version

help:
	@printf '%s\n' \
		'Targets:' \
		'  make install   Sync the uv environment' \
		'  make sync      Alias for install' \
		'  make test      Run pytest' \
		'  make check     Run syntax checks and tests' \
		'  make smoke     Run a small CLI/API smoke test' \
		'  make status    Show an AI-focused project workflow summary' \
		'  make build     Build package artifacts' \
		'  make clean     Remove local test/build/probe artifacts' \
		'  make version   Print package version'

install:
	$(UV) sync

sync: install

test:
	$(UV) run pytest

check:
	$(UV) run python -m py_compile $(MODULE).py tests/test_code_workflow_probe.py
	$(UV) run pytest

smoke:
	@tmp=$$(mktemp -d); \
	trap 'rm -rf "$$tmp"' EXIT; \
	mkdir -p "$$tmp/tests"; \
	printf '%s\n' \
		'[project]' \
		'name = "sample"' \
		'version = "0.1.0"' \
		'dependencies = ["pytest", "ruff"]' \
		'[tool.pytest.ini_options]' \
		'testpaths = ["tests"]' \
		'[tool.ruff]' \
		'line-length = 100' > "$$tmp/pyproject.toml"; \
	printf '%s\n' 'def test_ok():' '    assert True' > "$$tmp/tests/test_sample.py"; \
	$(UV) run python $(MODULE).py sync --root "$$tmp" --format json --compact >/dev/null; \
	$(UV) run python $(MODULE).py status --root "$$tmp" >/dev/null; \
	$(UV) run python $(MODULE).py affected --root "$$tmp" --changed tests/test_sample.py >/dev/null

status:
	$(UV) run python $(MODULE).py status --root . --detail standard --depth 2 --limit 20

build:
	$(UV) build

clean:
	rm -rf .pytest_cache __pycache__ tests/__pycache__
	rm -rf build dist *.egg-info
	rm -f $(CACHE)

version:
	@$(UV) run python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])'
