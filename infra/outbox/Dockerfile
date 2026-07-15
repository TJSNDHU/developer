FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY worker.py .

# Iter 212m-227 — CIS Docker Benchmark 4.1 + 4.6.
# Run as non-root and expose a healthcheck so orchestrators can
# detect a wedged outbox worker.
RUN groupadd --system --gid 1001 worker \
    && useradd --system --uid 1001 --gid worker --home-dir /app --shell /bin/false worker \
    && chown -R worker:worker /app
USER worker

# Worker touches this file every heartbeat; missing / stale => unhealthy.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os,time,sys; sys.exit(0 if os.path.exists('/tmp/worker_heartbeat') and (time.time()-os.path.getmtime('/tmp/worker_heartbeat'))<120 else 1)"

CMD ["python", "worker.py"]