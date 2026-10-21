PYTHON ?= python
PIP_AUDIT_CACHE_DIR ?= .cache/pip-audit

.PHONY: install-dev format format-check lint security typecheck test compile check

install-dev:
	$(PYTHON) -m pip install -r requirements-dev.txt

format:
	$(PYTHON) -m ruff format app tests
	$(PYTHON) -m ruff check --fix app tests

format-check:
	$(PYTHON) -m ruff format --check app tests

lint:
	$(PYTHON) -m ruff check app tests

security:
	$(PYTHON) -m bandit -c bandit.yaml -r app
	$(PYTHON) -m pip_audit -r requirements.txt --cache-dir $(PIP_AUDIT_CACHE_DIR) --progress-spinner off

typecheck:
	$(PYTHON) -m mypy app

test:
	$(PYTHON) -m pytest --cov=app --cov-config=.coveragerc --cov-report=term-missing

compile:
	$(PYTHON) -m compileall -q app

check: format-check lint security typecheck test compile
