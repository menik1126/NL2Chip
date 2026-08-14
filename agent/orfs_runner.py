"""Minimal OpenROAD Flow Scripts Docker runner used by the evaluator."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def run_docker_command(
    command: str | list[str],
    *,
    image: str = "openroad/orfs:latest",
    cwd: str = "/OpenROAD-flow-scripts/flow",
    workspace_path: str | None = None,
    volumes: list[str] | None = None,
    timeout: int = 3600,
) -> dict:
    """Run one ORFS command with a workspace mounted at ``/workspace``."""
    workspace = Path(workspace_path or "workspace").resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    command_text = " ".join(command) if isinstance(command, list) else command

    host_workspace = os.environ.get("HOST_WORKSPACE")
    workspace_text = str(workspace)
    extra_volumes = list(volumes or [])
    if host_workspace and workspace_text.startswith("/workspace"):
        workspace_text = workspace_text.replace("/workspace", host_workspace, 1)
        extra_volumes = [
            volume.replace("/workspace", host_workspace, 1)
            for volume in extra_volumes
        ]

    docker_command = [
        "docker", "run", "--rm",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "-e", "HOME=/tmp",
        "-v", f"{workspace_text}:/workspace",
    ]
    for volume in extra_volumes:
        docker_command.extend(["-v", volume])
    docker_command.extend([
        "-w", cwd,
        image,
        "bash", "-c", command_text,
    ])

    try:
        proc = subprocess.run(
            docker_command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "success": False,
            "stdout": exc.stdout or "",
            "stderr": f"ORFS Docker command timed out after {timeout}s",
            "command": docker_command,
        }
    except (FileNotFoundError, OSError) as exc:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"ORFS Docker execution failed: {exc}",
            "command": docker_command,
        }

    return {
        "success": proc.returncode == 0,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "command": docker_command,
        "returncode": proc.returncode,
    }
