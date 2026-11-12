# go-librespot: the open-source Spotify Connect client that is the dashboard's
# own player. Pinned to a release and to the checksum of its archive.
FROM python:3.13-slim AS player
ARG TARGETARCH
ARG GO_LIBRESPOT_VERSION=0.10.5
ARG GO_LIBRESPOT_SHA256=f08647217d1dced339375680d731bb5fc2410a9d693f2e1235303511c35a051c
ADD https://github.com/devgianlu/go-librespot/releases/download/v${GO_LIBRESPOT_VERSION}/go-librespot_linux_x86_64.tar.gz /tmp/player.tar.gz
RUN test "${TARGETARCH:-amd64}" = "amd64" \
      || { echo "only linux/amd64 is set up; add the archive and checksum for ${TARGETARCH}"; exit 1; } \
    && echo "${GO_LIBRESPOT_SHA256}  /tmp/player.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/player.tar.gz -C /usr/local/bin go-librespot \
    && chmod 755 /usr/local/bin/go-librespot

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8765

WORKDIR /app

# The player is linked against ALSA even though its sound goes to a pipe here.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libasound2 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=player /usr/local/bin/go-librespot /usr/local/bin/go-librespot

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py spotify.py player.py docker-entrypoint.py ./
COPY web ./web

# The service runs as this unprivileged user. The container itself starts as
# root so the entrypoint can make the mounted data folder writable first.
RUN useradd --uid 1000 --no-create-home app \
    && mkdir -p /app/data && chown app /app/data

EXPOSE 8765

# 127.0.0.1, not localhost: the server binds IPv4 only.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT','8765'), timeout=4)"

ENTRYPOINT ["python", "docker-entrypoint.py"]
CMD ["python", "server.py"]
