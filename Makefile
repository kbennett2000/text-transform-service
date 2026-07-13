.PHONY: dev test test-gpu lint sync

# Run the service with auto-reload (DESIGN §9: default bind 0.0.0.0:8712).
dev:
	uv run uvicorn tts.app:app --reload --host $${TTS_HOST:-0.0.0.0} --port $${TTS_PORT:-8712}

# Non-GPU test suite — runs anywhere (Ollama mocked with respx).
test:
	uv run pytest -m "not gpu"

# GPU test suite — run only on the 5070 box with Ollama up.
test-gpu:
	uv run pytest -m gpu

# Lint.
lint:
	uv run ruff check .

# Install/refresh the environment.
sync:
	uv sync
