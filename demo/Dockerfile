FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium \
    DEMO_WORK_ROOT=/tmp/link2news-demo

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       chromium ffmpeg fonts-noto-cjk ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY demo/requirements.lock /app/demo/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /app/demo/requirements.lock

COPY pyproject.toml README.md LICENSE /app/
COPY pipeline /app/pipeline
COPY demo /app/demo
RUN pip install --no-cache-dir --no-deps -e .

RUN useradd --create-home --uid 1000 user \
    && mkdir -p /tmp/link2news-demo \
    && chown -R user:user /app /tmp/link2news-demo
USER user

EXPOSE 7860
CMD ["uvicorn", "demo.app:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1"]
