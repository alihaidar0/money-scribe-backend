"""scripts/check-staging.sh, run against a stand-in for ``curl``.

Nothing here reaches Render: the stand-in answers from environment variables and records what it was asked, so the test
can check the token handling, the retries and the failures.
"""

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check-staging.sh"
TOKEN = "test-only-readiness-token-0123456789abcdef0123456789abcdef"  # invented, not a real secret

# The header arrives on stdin (curl --config -). The n-th call answers the n-th status of STUB_STATUSES (the last one
# repeats); 000 is a call that never got an answer.
CURL_STUB = """#!/usr/bin/env bash
cat > "$STUB_DIR/curl_config"
echo "$*" >> "$STUB_DIR/curl_args"
out=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
    [ "${args[$i]}" = "-o" ] && out="${args[$((i + 1))]}"
done
count=$(cat "$STUB_DIR/calls" 2>/dev/null || echo 0)
echo $((count + 1)) > "$STUB_DIR/calls"
IFS=',' read -r -a statuses <<< "$STUB_STATUSES"
IFS='|' read -r -a bodies <<< "${STUB_BODIES:-}"
index=$count
[ "$index" -ge "${#statuses[@]}" ] && index=$((${#statuses[@]} - 1))
printf '%s' "${bodies[$index]:-}" > "$out"
printf '%s' "${statuses[$index]}"
[ "${statuses[$index]}" = "000" ] && exit 28
exit 0
"""


class Run:
    def __init__(
        self, result: subprocess.CompletedProcess[str], directory: Path
    ) -> None:
        self.result = result
        self.directory = directory

    @property
    def output(self) -> str:
        return self.result.stdout + self.result.stderr

    @property
    def calls(self) -> int:
        return int(self.read("calls") or 0)

    def read(self, name: str) -> str:
        path = self.directory / name
        return path.read_text() if path.exists() else ""


def run_script(tmp_path: Path, **overrides: str) -> Run:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "curl"
    stub.write_text(CURL_STUB)
    stub.chmod(0o755)
    environment = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "STUB_DIR": str(tmp_path),
        "STUB_STATUSES": "200",
        "STAGING_URL": "https://staging.example.test",
        "READINESS_TOKEN": TOKEN,
        "ATTEMPTS": "4",
        "PAUSE_SECONDS": "0",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        "HOME": str(tmp_path),
    }
    environment.update(overrides)
    environment = {
        key: value for key, value in environment.items() if value != "<unset>"
    }
    result = subprocess.run(  # noqa: S603 - a fixed script with a stand-in program
        ["bash", str(SCRIPT)],  # noqa: S607
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return Run(result, tmp_path)


def test_a_staging_that_answers_succeeds_on_the_first_try(tmp_path: Path) -> None:
    run = run_script(tmp_path)

    assert run.result.returncode == 0, run.output
    assert run.calls == 1
    assert "answered" in run.output
    assert "Staging answers" in run.read("summary.md")
    assert "https://staging.example.test/readyz" in run.read("curl_args")


def test_the_token_travels_in_a_header_and_in_no_argument_or_output(
    tmp_path: Path,
) -> None:
    run = run_script(tmp_path)

    assert f"Authorization: Bearer {TOKEN}" in run.read("curl_config")
    assert TOKEN not in run.read("curl_args")
    assert TOKEN not in run.output
    assert TOKEN not in run.read("summary.md")


def test_a_sleeping_service_is_asked_again_until_it_answers(tmp_path: Path) -> None:
    run = run_script(tmp_path, STUB_STATUSES="000,503,200", STUB_BODIES="|database|")

    assert run.result.returncode == 0, run.output
    assert run.calls == 3
    assert "try 1: HTTP 000" in run.output
    assert "not answering: database" in run.output


def test_a_dependency_that_never_answers_fails_after_every_try(
    tmp_path: Path,
) -> None:
    run = run_script(
        tmp_path, STUB_STATUSES="503", STUB_BODIES="database, cache", ATTEMPTS="3"
    )

    assert run.result.returncode == 1
    assert run.calls == 3
    assert "after 3 tries" in run.output
    assert "not answering: database, cache" in run.output


def test_a_page_from_the_platform_is_not_echoed(tmp_path: Path) -> None:
    run = run_script(
        tmp_path,
        STUB_STATUSES="503",
        STUB_BODIES="<html>Service waking up</html>",
        ATTEMPTS="2",
    )

    assert run.result.returncode == 1
    assert "Service waking" not in run.output
    assert "HTTP 503" in run.output


def test_a_refused_token_fails_at_once(tmp_path: Path) -> None:
    run = run_script(tmp_path, STUB_STATUSES="401")

    assert run.result.returncode == 1
    assert run.calls == 1
    assert "READINESS_TOKEN" in run.output
    assert "401" in run.output


def test_a_check_that_does_not_exist_fails_at_once(tmp_path: Path) -> None:
    run = run_script(tmp_path, STUB_STATUSES="404")

    assert run.result.returncode == 1
    assert run.calls == 1
    assert "404" in run.output


def test_a_plain_http_address_is_refused_before_any_call(tmp_path: Path) -> None:
    run = run_script(tmp_path, STAGING_URL="http://staging.example.test")

    assert run.result.returncode == 1
    assert run.calls == 0
    assert "https://" in run.output


def test_a_missing_token_is_refused_before_any_call(tmp_path: Path) -> None:
    run = run_script(tmp_path, READINESS_TOKEN="<unset>")

    assert run.result.returncode != 0
    assert run.calls == 0
    assert "READINESS_TOKEN is required" in run.output
