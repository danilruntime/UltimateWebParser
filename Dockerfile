# syntax=docker/dockerfile:1.7
# UltimateWebParser: bot + worker + fingerprint-chromium in one image.

ARG PYTHON_VERSION=3.12
ARG UWP_BROWSER_VERSION=150.0.7871.186
ARG UWP_BROWSER_SHA256=48eff860053efd9ad498d51370f0a1f59f983df39c02a72ad2a8682cdead4055

# --- browser -----------------------------------------------------------------
FROM debian:bookworm-slim AS browser

ARG UWP_BROWSER_VERSION
ARG UWP_BROWSER_SHA256

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/uwp
RUN curl -fsSL -o browser.tar.xz \
        "https://github.com/adryfish/fingerprint-chromium/releases/download/${UWP_BROWSER_VERSION}/ungoogled-chromium-${UWP_BROWSER_VERSION}-1-x86_64_linux.tar.xz" \
    && echo "${UWP_BROWSER_SHA256}  browser.tar.xz" | sha256sum -c - \
    && mkdir -p /opt/uwp/browser \
    && tar -xJf browser.tar.xz --strip-components=1 -C /opt/uwp/browser \
    && rm browser.tar.xz \
    && chmod +x /opt/uwp/browser/chrome

# --- runtime -----------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UWP_BROWSER_PATH=/opt/uwp/browser/chrome \
    UWP_NO_SANDBOX=true \
    UWP_DATA_DIR=/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        xvfb x11vnc novnc websockify fluxbox tini util-linux \
        ca-certificates curl \
        fonts-liberation fonts-noto-color-emoji \
        libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libgbm1 libasound2 \
        libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
        libpango-1.0-0 libcairo2 libatspi2.0-0 \
        libatomic1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 uwp \
    && useradd --uid 1000 --gid uwp --create-home --shell /bin/bash uwp

# ``setpriv`` (util-linux) is how the entrypoint drops root after fixing the
# data volume; ``getent``/``id`` come with libc-bin in the base image.

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install . \
    && rm -rf /app/src /root/.cache
ENV PATH="/opt/venv/bin:${PATH}"

COPY --from=browser /opt/uwp/browser /opt/uwp/browser
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh /opt/uwp/browser/chrome \
    && mkdir -p /data \
    && chown -R uwp:uwp /data /opt/uwp

# The entrypoint starts as root only to make a fresh volume writable (Docker
# creates bind mounts as root), then drops to uid 1000 before running the CLI.
VOLUME ["/data"]
EXPOSE 6000-6100

# Through the entrypoint so the check runs as uid 1000, not as root.
HEALTHCHECK --interval=60s --timeout=30s --start-period=30s --retries=3 \
    CMD ["/entrypoint.sh", "doctor", "--quiet"]

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
CMD ["run"]
