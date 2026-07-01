FROM python:3.11-slim

WORKDIR /app

# Iter 212m-166 — add nodejs + eslint alongside ruff so the LoopEngine's
# Verify phase (`services/loop_verify.py`) can actually lint the files
# it generates before Ship.  When either binary is missing the loop
# gracefully soft-skips (rc=127) but the design intent is to LINT — a
# missing linter silently masks syntax errors on generated code.
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Verify runtimes (fails the build early if apt served a broken node/npm)
RUN node --version && npm --version

# Install ruff (Python linter for Loop Verify — .py files)
RUN pip install --no-cache-dir ruff \
    && ruff --version

# Install eslint (JS/TS linter for Loop Verify — .js/.jsx/.ts/.tsx files)
# Pinned to v8 because v9+ requires a flat-config file that Loop
# doesn't provide (Loop invokes eslint with --no-eslintrc / --no-config-lookup).
RUN npm install -g eslint@8 \
    && eslint --version

# Install Python deps (emergentintegrations needs Emergent's extra index)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    --extra-index-url https://d33sy5i8bnduwe.cloudfront.net/simple/

# Copy application code
COPY . .

# Expose port
EXPOSE 8002

# Healthcheck
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8002/api/health || exit 1

# Run application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]