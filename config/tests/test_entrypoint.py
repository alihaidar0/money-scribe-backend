"""scripts/entrypoint.prod.sh, run with a stand-in for ``gunicorn`` that prints the arguments it would receive."""

import importlib
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "entrypoint.prod.sh"

GUNICORN_STUB = """#!/usr/bin/env bash
echo "args=$*"
echo "cmd_args=${GUNICORN_CMD_ARGS:-}"
"""


def run_entrypoint(
    tmp_path: Path, command: str = "gunicorn", **environment: str
) -> subprocess.CompletedProcess[str]:
    stub = tmp_path / "gunicorn"
    stub.write_text(GUNICORN_STUB)
    stub.chmod(0o755)
    env = {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "RUN_MIGRATIONS": "false",
        **environment,
    }
    return subprocess.run(  # noqa: S603 - a fixed script with a stand-in program
        ["bash", str(SCRIPT), command, "config.wsgi:application"],  # noqa: S607
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


def test_default_arguments_bind_to_port_and_skip_the_control_socket(
    tmp_path: Path,
) -> None:
    result = run_entrypoint(tmp_path, PORT="10000")

    assert result.returncode == 0
    assert "--bind=0.0.0.0:10000" in result.stdout
    assert "--no-control-socket" in result.stdout


def test_explicit_arguments_are_kept_as_they_are(tmp_path: Path) -> None:
    result = run_entrypoint(tmp_path, GUNICORN_CMD_ARGS="--threads=2")

    assert "cmd_args=--threads=2\n" in result.stdout
    assert "--no-control-socket" not in result.stdout


def test_the_installed_gunicorn_knows_the_option() -> None:
    config = importlib.import_module("gunicorn.config").Config()
    options = config.parser().parse_args(["--no-control-socket"])

    assert options.control_socket_disable is True
