FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8765

WORKDIR /app

# The dashboard's own player: Chrome runs Spotify's official web player, and
# PulseAudio gives it a software sound card whose output the server records.
# Chrome is only published for amd64.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg pulseaudio pulseaudio-utils \
    && curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
       | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" \
       > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable \
    && apt-get purge -y curl gnupg && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /etc/apt/sources.list.d/google-chrome.list

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py spotify.py player.py screen.py docker-entrypoint.py ./
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
