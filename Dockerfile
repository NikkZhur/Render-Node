# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.8.14 AS uv-bin

FROM debian:bookworm-slim AS blender-build
ARG BLENDER_5_VERSION=5.2.0
ARG BLENDER_5_SHA256=96f6c181a30f4950607839dc84d42a354b250d8a0231b098b59b7bc69c351c48
ARG BLENDER_4_VERSION=4.1.1
ARG BLENDER_4_SHA256=ab2ea3fe991601a5e6bd2cda786ecaa919c0b39e0550e59978b5d40270c260d3
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
    && rm -rf /var/lib/apt/lists/*
RUN set -eux; \
    for pair in \
      "${BLENDER_5_VERSION}:Blender5.2:${BLENDER_5_SHA256}" \
      "${BLENDER_4_VERSION}:Blender4.1:${BLENDER_4_SHA256}"; do \
      version="${pair%%:*}"; remainder="${pair#*:}"; \
      series="${remainder%%:*}"; checksum="${remainder#*:}"; \
      archive="blender-${version}-linux-x64.tar.xz"; \
      curl --fail --location --proto '=https' --tlsv1.2 \
        "https://download.blender.org/release/${series}/${archive}" -o "/tmp/${archive}"; \
      echo "${checksum}  /tmp/${archive}" | sha256sum --check --strict; \
      mkdir -p "/opt/render-node/blender/${version}"; \
      tar --extract --xz --file "/tmp/${archive}" --strip-components=1 \
        --directory "/opt/render-node/blender/${version}"; \
      rm "/tmp/${archive}"; \
      test -x "/opt/render-node/blender/${version}/blender"; \
    done

FROM python:3.13.7-slim-bookworm AS release-build
COPY --from=uv-bin /uv /uvx /usr/local/bin/
ARG BUILD_VERSION=0.0.0
WORKDIR /opt/render-node
COPY dist/render-node-linux-x64.tar.gz /tmp/release.tar.gz
RUN mkdir -p releases \
    && tar -xzf /tmp/release.tar.gz -C /tmp \
    && test "$(cat /tmp/render-node/VERSION)" = "$BUILD_VERSION" \
    && mv /tmp/render-node "releases/${BUILD_VERSION}" \
    && ln -s "releases/${BUILD_VERSION}" current \
    && cd current/backend \
    && uv sync --frozen --no-dev --no-install-project --python /usr/local/bin/python \
    && rm /tmp/release.tar.gz

FROM python:3.13.7-slim-bookworm AS runtime
ARG VCS_REF=unknown
ARG BUILD_VERSION=0.0.0
LABEL org.opencontainers.image.title="Render Node" \
      org.opencontainers.image.description="Single-tenant Blender render node" \
      org.opencontainers.image.source="https://github.com/NikkZhur/Render-Node" \
      org.opencontainers.image.revision="$VCS_REF" \
      org.opencontainers.image.version="$BUILD_VERSION"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      apache2-utils ca-certificates curl gettext-base gosu nginx-light \
      libdbus-1-3 libegl1 libfontconfig1 libfreetype6 libgl1 libice6 libpulse0 \
      libsm6 libwayland-client0 libx11-6 libxcursor1 libxext6 libxfixes3 libxi6 \
      libxinerama1 libxkbcommon0 libxrandr2 libxrender1 libxxf86vm1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 rendernode \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /workspace \
      --shell /usr/sbin/nologin rendernode

COPY --from=release-build /opt/render-node /opt/render-node
COPY --from=blender-build /opt/render-node/blender /opt/render-node/blender
COPY deploy/container-entrypoint.sh /opt/render-node/container-entrypoint.sh
RUN chmod 0755 /opt/render-node/container-entrypoint.sh \
    && mkdir -p /workspace /run/render-node \
    && chown rendernode:rendernode /workspace /run/render-node \
    && /opt/render-node/blender/5.2.0/blender --version | grep -F 'Blender 5.2.0' \
    && /opt/render-node/blender/4.1.1/blender --version | grep -F 'Blender 4.1.1'

ENV PATH="/opt/render-node/current/backend/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    RENDER_NODE_WORKSPACE=/workspace \
    RENDER_NODE_DATABASE_URL=sqlite+aiosqlite:////workspace/database/render-node.sqlite3
VOLUME ["/workspace"]
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=60s --retries=6 \
  CMD curl --fail --silent http://127.0.0.1:8080/ready >/dev/null || exit 1
ENTRYPOINT ["/opt/render-node/container-entrypoint.sh"]
