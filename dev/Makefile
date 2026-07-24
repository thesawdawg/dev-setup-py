ROOT := ..

# Pass TOOL=<key> to run-tests to test a single tool, e.g. make run-tests TOOL=uv
# Pass PYTEST_ARGS="..." for arbitrary pytest flags, e.g. make run-tests PYTEST_ARGS="-x"
TOOL     ?=
PYTEST_ARGS ?=

.PHONY: build build-ci run shell run-tests smoke-agent clean

# Build a fresh wheel from source, then build the Docker image.
# Clean dist first so the glob in the Dockerfile matches exactly one file.
build:
	cd $(ROOT) && rm -f dist/*.whl dist/*.tar.gz && uv build
	docker compose build

# Build the CI image (sandbox + pytest + integration tests baked in)
build-ci: build
	docker build -f Dockerfile.ci -t dev-setup-ci ..

# Drop into an interactive shell as the 'developer' user
run: build
	docker compose run --rm sandbox

shell: run

# Run integration tests inside the CI container.
# Examples:
#   make run-tests               — run all integration tests
#   make run-tests TOOL=uv       — run only the uv test
#   make run-tests PYTEST_ARGS="-x --tb=long"
run-tests: build-ci
	$(eval _NODESEL := $(if $(TOOL),tests/integration/test_tools.py::test_install[$(TOOL)],tests/integration/))
	docker run --rm dev-setup-ci \
		pytest $(_NODESEL) -m integration -v --tb=short $(PYTEST_ARGS)

# Smoke-test `devstuff agent` against a live Ollama daemon.
# Runs on the HOST, not in Docker -- the CI image has no daemon, and yours may be
# on another machine. Skips cleanly if nothing is reachable.
#   make smoke-agent
#   make smoke-agent AGENT_HOST=http://192.168.1.69:11434 AGENT_MODEL=gemma4:latest
AGENT_HOST  ?=
AGENT_MODEL ?=
smoke-agent:
	cd $(ROOT) && \
	$(if $(AGENT_HOST),DEVSTUFF_AGENT_HOST=$(AGENT_HOST)) \
	$(if $(AGENT_MODEL),DEVSTUFF_AGENT_MODEL=$(AGENT_MODEL)) \
	uv run pytest tests/integration/test_agent_smoke.py -m integration -v $(PYTEST_ARGS)

# Remove the built images and named volume
clean:
	docker compose down --rmi local --volumes 2>/dev/null || true
	docker rmi dev-setup-ci 2>/dev/null || true
