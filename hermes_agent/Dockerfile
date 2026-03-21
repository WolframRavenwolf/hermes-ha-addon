ARG BUILD_FROM
FROM ${BUILD_FROM}

# ── System packages ──────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        # Python build deps
        python3 python3-dev python3-pip python3-venv \
        build-essential libffi-dev libssl-dev \
        # SQLite with FTS5
        sqlite3 libsqlite3-dev \
        # Media
        ffmpeg \
        # Chromium + Playwright system deps
        chromium \
        libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
        libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
        libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
        # Networking / dev tools
        nginx git curl wget jq ripgrep fd-find rsync \
        openssh-client vim nano procps less unzip \
        # Homebrew deps
        file locales \
    && rm -rf /var/lib/apt/lists/* \
    && locale-gen en_US.UTF-8

ENV LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8 \
    PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium

# ── uv (Astral) ─────────────────────────────────────────────────────
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy

# ── Node.js 22 ──────────────────────────────────────────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g npm@latest

# ── Go 1.22 ─────────────────────────────────────────────────────────
RUN ARCH=$(dpkg --print-architecture) \
    && curl -fsSL "https://go.dev/dl/go1.22.10.linux-${ARCH}.tar.gz" \
        | tar -C /usr/local -xz
ENV PATH="/usr/local/go/bin:${PATH}"

# ── ttyd (compiled from source for glibc forkpty) ────────────────────
ARG TTYD_VERSION=1.7.7
RUN apt-get update && apt-get install -y --no-install-recommends \
        cmake libjson-c-dev libwebsockets-dev \
    && git clone --branch "$TTYD_VERSION" --depth 1 \
        https://github.com/tsl0922/ttyd.git /tmp/ttyd \
    && cd /tmp/ttyd && mkdir build && cd build \
    && cmake .. -DCMAKE_BUILD_TYPE=Release \
    && make -j"$(nproc)" && make install \
    && rm -rf /tmp/ttyd \
    && apt-get purge -y cmake \
    && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* \
    && ttyd --version

# ── Homebrew (Linuxbrew) ────────────────────────────────────────────
RUN useradd -r -m -s /bin/bash -d /home/linuxbrew linuxbrew \
    && mkdir -p /opt/linuxbrew \
    && chown -R linuxbrew:linuxbrew /opt/linuxbrew
RUN su - linuxbrew -c \
    'NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"' \
    && /home/linuxbrew/.linuxbrew/bin/brew --version
ENV HOMEBREW_IMAGE_PREFIX="/home/linuxbrew/.linuxbrew"

# ── Addon files ──────────────────────────────────────────────────────
COPY run.sh /
COPY nginx.conf.tpl /etc/nginx/nginx.conf.tpl
COPY landing.html.tpl /var/www/landing.html.tpl
COPY brew-wrapper.sh /usr/local/bin/brew-wrapper
RUN chmod +x /run.sh /usr/local/bin/brew-wrapper

# ── nginx dirs ───────────────────────────────────────────────────────
RUN mkdir -p /var/www /var/log/nginx /run/nginx

CMD ["/run.sh"]
