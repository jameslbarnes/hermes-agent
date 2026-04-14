FROM debian:13.4

# Install system dependencies in one layer, clear APT cache
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential nodejs npm python3 python3-pip ripgrep ffmpeg gcc python3-dev libffi-dev git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /opt/hermes

# Copy dependency files first — these change rarely, so Docker caches this layer
COPY requirements-all.lock pyproject.toml package.json package-lock.json ./
COPY scripts/whatsapp-bridge/package.json scripts/whatsapp-bridge/package-lock.json ./scripts/whatsapp-bridge/

# Install dependencies (cached unless requirements change)
RUN pip install --no-cache-dir --ignore-installed -r requirements-all.lock --break-system-packages && \
    npm install --prefer-offline --no-audit && \
    npx playwright install --with-deps chromium --only-shell && \
    cd /opt/hermes/scripts/whatsapp-bridge && \
    npm install --prefer-offline --no-audit && \
    npm cache clean --force

# Now copy the rest of the code (changes frequently, but deps are cached)
COPY . /opt/hermes

# Install the package in editable mode (fast — deps already installed)
RUN pip install --no-cache-dir --no-deps -e . --break-system-packages

RUN chmod +x /opt/hermes/docker/entrypoint.sh

ENV HERMES_HOME=/opt/data
ENTRYPOINT [ "/opt/hermes/docker/entrypoint.sh" ]
CMD ["gateway", "run"]
