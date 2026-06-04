"""
test_iter72_vscode_extension_artifact.py — VS Code extension build lock.

Locks the extension's build artifact + key files so a future agent
can't accidentally delete it or break the compile.
"""
from __future__ import annotations

import json
import os


EXT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "vscode-extension")


def test_extension_folder_exists():
    assert os.path.isdir(EXT_DIR), "/app/vscode-extension/ must exist"


def test_required_files_exist():
    for f in ("package.json", "tsconfig.json", "README.md", "LICENSE",
              ".vscodeignore", "src/extension.ts",
              "assets/sidebar-icon.svg"):
        assert os.path.exists(os.path.join(EXT_DIR, f)), \
            f"vscode-extension/{f} must exist"


def test_package_json_has_required_fields():
    with open(os.path.join(EXT_DIR, "package.json"), encoding="utf-8") as fh:
        pkg = json.load(fh)
    assert pkg["name"] == "aurem-cto"
    assert pkg["publisher"] == "auremcto"
    assert pkg["main"] == "./out/extension.js"
    # 4 commands wired
    cmds = [c["command"] for c in pkg["contributes"]["commands"]]
    for c in ("aurem.openChat", "aurem.shipSelection",
              "aurem.login", "aurem.logout"):
        assert c in cmds, f"Command {c} missing from package.json"
    # serverUrl config defaults to auremcto.com
    sv = pkg["contributes"]["configuration"]["properties"]["aurem.serverUrl"]
    assert sv["default"] == "https://auremcto.com"


def test_extension_ts_uses_real_backend_endpoint():
    """No mocks — the extension must POST to the real submit endpoint."""
    with open(os.path.join(EXT_DIR, "src/extension.ts"), encoding="utf-8") as fh:
        src = fh.read()
    assert "/api/aurem-dev/cto/tasks/submit" in src
    # Auth header threaded through
    assert "Authorization" in src and "Bearer" in src
    # No 'mock' / 'fake' literals
    low = src.lower()
    assert "mock" not in low
    assert "// fake" not in low


def test_vsix_artifact_exists():
    """The packaged .vsix file should be present after a build."""
    candidates = [f for f in os.listdir(EXT_DIR) if f.endswith(".vsix")]
    assert candidates, (
        "No .vsix found in /app/vscode-extension/. Run:\n"
        "  cd /app/vscode-extension && npm run compile && "
        "npx vsce package --no-dependencies"
    )
