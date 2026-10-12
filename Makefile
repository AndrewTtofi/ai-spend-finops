.PHONY: install demo test lint fmt lock audit check-config build

install:
	pip install --require-hashes -r requirements-dev.txt && pip install --no-deps -e .

# Sample data and all four reports. Needs no credentials.
demo:
	finops demo

test:
	pytest -q

lint:
	ruff check finops tests && ruff format --check finops tests && mypy finops

fmt:
	ruff format finops tests && ruff check --fix finops tests

check-config:
	finops check-config

build:
	docker compose build

# Re-pin after editing requirements*.in (ARGS=--upgrade to bump everything).
PIP_COMPILE = pip-compile --quiet --strip-extras --generate-hashes --allow-unsafe --no-emit-index-url
lock:
	$(PIP_COMPILE) $(ARGS) -o requirements.txt requirements.in
	$(PIP_COMPILE) $(ARGS) -o requirements-dev.txt requirements-dev.in

audit:
	pip-audit -r requirements.txt --no-deps --disable-pip --progress-spinner off
