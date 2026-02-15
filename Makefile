.PHONY: install lint format test clean

VENV_DIR = .venv
UV = uv

install:
	$(UV) venv $(VENV_DIR) --seed
	$(UV) pip install -e ".[dev]"

lint: install
	$(UV) run ruff check --fix .

format: install
	$(UV) run ruff format .

test: install
	$(UV) run pytest tests/

clean:
	rm -rf $(VENV_DIR)
	find . -type f -name '*.py[co]' -delete
	find . -type d -name '__pycache__' -exec rm -rf {} +
