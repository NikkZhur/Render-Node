FROM mcr.microsoft.com/devcontainers/python:1-3.13-bookworm

# === Системные зависимости (для matplotlib, seaborn, pandas, opencv, jupyter и т.д.) ===
RUN rm -f /etc/apt/sources.list.d/yarn.list \
    && apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get -y install --no-install-recommends \
        git curl wget build-essential \
        libssl-dev libffi-dev zlib1g-dev \
        libgl1-mesa-glx libglib2.0-0 libsm6 libxext6 libxrender-dev \
        libpng-dev libjpeg-dev libfreetype6-dev \
        libasound2 libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0 \
        libcups2 libgbm1 libnss3 libxcomposite1 libxdamage1 \
        libxfixes3 libxkbcommon0 libxrandr2 \
        fonts-liberation fonts-noto-color-emoji xvfb \
        graphviz ffmpeg \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# === Установка самого быстрого менеджера пакетов uv ===
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Настройки uv + виртуальное окружение
ENV UV_CACHE_DIR=/home/vscode/.cache/uv \
    UV_LINK_MODE=copy

USER vscode
WORKDIR /workspace

# Vite development server. VS Code forwards this port via devcontainer.json.
EXPOSE 5173

# Создаём чистое виртуальное окружение
RUN uv venv ~/.venv
ENV PATH="/home/vscode/.venv/bin:$PATH"

# (можно раскомментировать, если хочешь сразу установить базовые пакеты)
# COPY requirements.txt /tmp/
# RUN uv pip install -r /tmp/requirements.txt
