FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=UTC \
    LANG=C.UTF-8

# System packages needed to bootstrap devstuff and run its installers
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 \
    python3.12-venv \
    python3-pip \
    curl \
    git \
    sudo \
    ca-certificates \
    gnupg \
    lsb-release \
    && rm -rf /var/lib/apt/lists/*

# Non-root user with passwordless sudo — mirrors a real developer workstation
RUN useradd -m -s /bin/bash developer \
    && echo "developer ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

USER developer
WORKDIR /home/developer

# Install the pre-built wheel (built by 'make build' before docker build runs).
# The glob tracks the [project] name in pyproject.toml — 'devstuff', not the
# 'dev_setup' Python module the wheel installs.
COPY --chown=developer:developer dist/devstuff-*.whl /tmp/

RUN pip install --user --break-system-packages /tmp/devstuff-*.whl \
    && rm /tmp/devstuff-*.whl

ENV PATH="/home/developer/.local/bin:$PATH"

# Pre-create config dir so the tool has somewhere to write on first run
RUN mkdir -p /home/developer/.config/devstuff

CMD ["/bin/bash", "--login"]
