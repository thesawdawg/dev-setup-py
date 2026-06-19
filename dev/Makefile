IMAGE   := dev-setup-sandbox
ROOT    := ..

.PHONY: build run shell clean

# Build a fresh wheel from source, then build the Docker image.
# Clean dist first so the glob in the Dockerfile matches exactly one file.
build:
	cd $(ROOT) && rm -f dist/*.whl dist/*.tar.gz && uv build
	docker build -f dev/Dockerfile -t $(IMAGE) $(ROOT)

# Drop into an interactive shell as the 'developer' user
run: build
	docker run --rm -it $(IMAGE)

shell: run

# Remove the built image
clean:
	docker rmi $(IMAGE) 2>/dev/null || true
