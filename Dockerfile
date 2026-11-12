FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8765

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py spotify.py docker-entrypoint.py ./
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
