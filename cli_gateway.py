import shutil
import subprocess
from pathlib import Path
from typing import Any


SKILLHUB_INSTALL_SCRIPT = "https://skillhub-1388575217.cos.ap-guangzhou.myqcloud.com/install/install.sh"

CLI_CANDIDATES: dict[str, list[str]] = {
    "openclaw": [
        "~/.local/bin/openclaw",
        "/usr/local/bin/openclaw",
        "/opt/homebrew/bin/openclaw",
        "openclaw",
    ],
    "skillhub": [
        "~/.local/bin/skillhub",
        "/usr/local/bin/skillhub",
        "/opt/homebrew/bin/skillhub",
        "skillhub",
    ],
}


def resolve_cli_binary(tool: str) -> str | None:
    candidates = CLI_CANDIDATES.get(tool, [])
    for candidate in candidates:
        expanded = str(Path(candidate).expanduser()) if candidate.startswith("~") else candidate
        if "/" in expanded:
            path = Path(expanded)
            if path.exists() and path.is_file():
                return str(path)
            continue
        found = shutil.which(expanded)
        if found:
            return found
    return None


def detect_cli_status() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for tool in CLI_CANDIDATES:
        path = resolve_cli_binary(tool)
        result[tool] = {"installed": bool(path), "path": path or ""}
    return result


def install_skillhub_cli(*, timeout_sec: int = 180, cli_only: bool = True) -> dict[str, Any]:
    cmd = f"curl -fsSL {SKILLHUB_INSTALL_SCRIPT} | bash"
    if cli_only:
        cmd += " -s -- --cli-only"
    completed = subprocess.run(
        ["bash", "-lc", cmd],
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )
    installed_path = resolve_cli_binary("skillhub") or ""
    return {
        "ok": completed.returncode == 0 and bool(installed_path),
        "exit_code": completed.returncode,
        "stdout": completed.stdout[-12000:],
        "stderr": completed.stderr[-12000:],
        "installed_path": installed_path,
    }


def run_cli_command(
    *,
    tool: str,
    args: list[str],
    cwd: str | None = None,
    timeout_sec: int = 60,
) -> dict[str, Any]:
    binary = resolve_cli_binary(tool)
    if not binary:
        raise FileNotFoundError(f"{tool} is not installed")

    run_cwd = str(Path(cwd).expanduser().resolve()) if cwd else str(Path.cwd())
    if not Path(run_cwd).exists() or not Path(run_cwd).is_dir():
        raise NotADirectoryError(f"cwd not found: {run_cwd}")

    command = [binary, *args]
    completed = subprocess.run(
        command,
        cwd=run_cwd,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )
    return {
        "tool": tool,
        "binary": binary,
        "command": command,
        "cwd": run_cwd,
        "exit_code": completed.returncode,
        "stdout": completed.stdout[-20000:],
        "stderr": completed.stderr[-20000:],
    }
