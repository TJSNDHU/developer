"""
Iter 212m-166 (extended) — Dockerfile + boot linter-probe wiring.

Guards:
  • Dockerfile installs nodejs, npm, ruff, eslint@8 in the correct order.
  • Dockerfile pins eslint to v8 (v9+ needs flat-config which Loop doesn't provide).
  • main.py::lifespan probes ruff + eslint at boot, non-blocking.
  • `/api/health` surfaces `loop_linters_missing` for founder-dashboard use.
"""

import pathlib


def test_dockerfile_installs_nodejs():
    df = pathlib.Path("/app/backend/Dockerfile").read_text()
    assert "nodejs" in df
    assert "npm" in df
    assert "apt-get install" in df


def test_dockerfile_installs_ruff():
    df = pathlib.Path("/app/backend/Dockerfile").read_text()
    assert "pip install --no-cache-dir ruff" in df
    # Version verification step must exist so a broken pip install
    # fails the build early instead of at first Loop run.
    assert "ruff --version" in df


def test_dockerfile_installs_eslint_v8_pinned():
    """eslint v9+ requires a flat config file that Loop does not
    provide (Loop invokes eslint with --no-eslintrc / --no-config-lookup).
    v8 accepts those legacy flags — pin the version so a future
    `npm install -g eslint` doesn't silently break Loop Verify."""
    df = pathlib.Path("/app/backend/Dockerfile").read_text()
    assert "npm install -g eslint@8" in df
    assert "eslint --version" in df


def test_dockerfile_layer_order_correct():
    """ruff/eslint must install BEFORE `COPY . .` so a code change
    doesn't invalidate the linter layer's cache."""
    df = pathlib.Path("/app/backend/Dockerfile").read_text()
    ruff_idx   = df.find("pip install --no-cache-dir ruff")
    eslint_idx = df.find("npm install -g eslint")
    copy_idx   = df.find("COPY . .")
    assert 0 < ruff_idx   < copy_idx
    assert 0 < eslint_idx < copy_idx


def test_main_boot_probes_loop_linters():
    src = pathlib.Path("/app/backend/main.py").read_text()
    assert "_probe_loop_linters" in src
    # Must use shutil.which for portability, not shell subprocess.
    assert "shutil.which" in src
    # Must probe both linters.
    assert '("ruff", "eslint")' in src
    # Must be fired as a non-blocking background task.
    assert "_asyncio.create_task(_probe_loop_linters())" in src


def test_main_boot_probe_stores_state_for_health_endpoint():
    src = pathlib.Path("/app/backend/main.py").read_text()
    assert "app.state.loop_linters_missing" in src


def test_health_endpoint_surfaces_loop_linters_missing():
    src = pathlib.Path("/app/backend/main.py").read_text()
    # Slice the /api/health handler so we assert against that block only.
    start = src.find('@app.get("/api/health")')
    end   = src.find("@app.get(", start + 10)
    block = src[start:end]
    assert '"loop_linters_missing"' in block
    assert 'getattr(app.state, "loop_linters_missing"' in block


def test_boot_warning_message_actionable():
    """Iter 212m-172 — Boot probe now actively INSTALLS ruff + eslint
    at runtime (previous versions only warned).  Verify the subprocess
    calls are present."""
    src = pathlib.Path("/app/backend/main.py").read_text()
    # Subprocess installs the missing binaries automatically.
    assert 'subprocess.run' in src
    assert '"pip"' in src and '"ruff"' in src
    assert '"npm"' in src and '"eslint@8"' in src
