ROOT := ..

.PHONY: build run shell clean

# Build a fresh wheel from source, then build the Docker image.
# Clean dist first so the glob in the Dockerfile matches exactly one file.
build:
	cd $(ROOT) && rm -f dist/*.whl dist/*.tar.gz && uv build
	docker compose build

# Drop into an interactive shell as the 'developer' user
run: build
	docker compose run --rm sandbox

shell: run

# Remove the built image and named volume
clean:
	docker compose down --rmi local --volumes 2>/dev/null || true
