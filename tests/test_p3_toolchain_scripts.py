from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_p3_ppa_smoke_script_imports_from_arbitrary_cwd(tmp_path: Path):
    script = PROJECT_ROOT / "scripts" / "verify_p3_ppa_orfs.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "place and route after synthesis" in completed.stdout
