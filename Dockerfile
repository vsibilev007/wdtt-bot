FROM python:3.13-slim AS base

WORKDIR /app

RUN groupadd --gid 10001 appuser && \
    useradd --uid 10001 --gid appuser --create-home appuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chown -R appuser:appuser /app
USER appuser

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD [ -f /tmp/healthy ] || exit 1

CMD ["python", "bot.py"]
