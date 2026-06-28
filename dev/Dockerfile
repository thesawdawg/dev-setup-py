FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=UTC \
    LANG=C.UTF-8

# System packages needed to bootstrap dev-setup and run its installers
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

# Install the pre-built wheel (built by 'make build' before docker build runs)
COPY --chown=developer:developer dist/dev_setup-*.whl /tmp/

RUN pip install --user --break-system-packages /tmp/dev_setup-*.whl \
    && rm /tmp/dev_setup-*.whl

ENV PATH="/home/developer/.local/bin:$PATH"

# Pre-create config dir so the tool has somewhere to write on first run
RUN mkdir -p /home/developer/.config/dev-setup

CMD ["/bin/bash", "--login"]
