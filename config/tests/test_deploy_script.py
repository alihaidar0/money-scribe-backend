"""scripts/deploy-staging.sh, run against stand-ins for ``docker`` and ``curl``.

Nothing here reaches GitHub or Render: the stand-ins answer from environment variables and record what they were
asked, so the test can check the digest lookup, the pinned ``imgURL``, the wait for ``X-Release`` and the failures.
"""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "deploy-staging.sh"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
DIGEST = "sha256:" + "ab" * 32
HOOK = "https://api.render.com/deploy/srv-example?key=SECRET-KEY-VALUE"

DOCKER_STUB = """#!/usr/bin/env bash
echo "$*" >> "$STUB_DIR/docker.log"
[ -n "${STUB_DIGEST:-}" ] || exit 1
echo "$STUB_DIGEST"
"""

# A POST is the deploy hook; any other call is a /healthz probe that answers the next release in STUB_RELEASES.
CURL_STUB = """#!/usr/bin/env bash
out=""
post=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
    [ "${args[$i]}" = "-X" ] && post="${args[$((i + 1))]}"
    [ "${args[$i]}" = "-o" ] && out="${args[$((i + 1))]}"
done
url="${args[$((${#args[@]} - 1))]}"
if [ "$post" = "POST" ]; then
    echo "$url" > "$STUB_DIR/hook_url"
    echo '{"deploy": {"id": "dep-1"}}' > "$out"
    printf '%s' "${STUB_HOOK_STATUS:-200}"
    exit 0
fi
count=$(cat "$STUB_DIR/probes" 2>/dev/null || echo 0)
echo $((count + 1)) > "$STUB_DIR/probes"
IFS=',' read -r -a releases <<< "${STUB_RELEASES:-}"
index=$count
[ "$index" -ge "${#releases[@]}" ] && index=$((${#releases[@]} - 1))
printf 'HTTP/2 200\\r\\ncontent-type: text/plain\\r\\n'
if [ "${#releases[@]}" -gt 0 ] && [ -n "${releases[$index]}" ]; then
    printf 'X-Release: %s\\r\\n' "${releases[$index]}"
fi
printf '\\r\\n'
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
    def summary(self) -> str:
        path = self.directory / "summary.md"
        return path.read_text() if path.exists() else ""

    def read(self, name: str) -> str:
        path = self.directory / name
        return path.read_text() if path.exists() else ""


def run_script(tmp_path: Path, **overrides: str) -> Run:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("docker", DOCKER_STUB), ("curl", CURL_STUB)):
        stub = bin_dir / name
        stub.write_text(body)
        stub.chmod(0o755)
    environment = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "STUB_DIR": str(tmp_path),
        "STUB_DIGEST": DIGEST,
        "STUB_RELEASES": COMMIT,
        "IMAGE_REPOSITORY": "Owner/Repo",
        "COMMIT": COMMIT,
        "RENDER_DEPLOY_HOOK_URL": HOOK,
        "STAGING_URL": "https://staging.example.test",
        "WAIT_SECONDS": "5",
        "POLL_SECONDS": "0",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        "HOME": str(tmp_path),
    }
    environment.update(overrides)
    environment = {
        key: value for key, value in environment.items() if value != "<unset>"
    }
    result = subprocess.run(  # noqa: S603 - a fixed script with stand-in programs
        ["bash", str(SCRIPT)],  # noqa: S607
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return Run(result, tmp_path)


def test_a_deploy_is_pinned_to_the_digest_and_confirmed_by_the_release(
    tmp_path: Path,
) -> None:
    run = run_script(tmp_path, STUB_RELEASES=f"{'f' * 40},{'f' * 40},{COMMIT}")

    assert run.result.returncode == 0, run.output
    assert "runs 0123456" in run.output
    assert (
        int(run.read("probes")) == 3
    )  # the old build answered twice before the new one
    summary = run.summary
    assert DIGEST in summary
    assert COMMIT in summary
    assert "ghcr.io/owner/repo" in summary


def test_the_image_is_looked_up_by_the_short_commit_tag_in_lower_case(
    tmp_path: Path,
) -> None:
    run = run_script(tmp_path)

    assert "ghcr.io/owner/repo:sha-0123456" in run.read("docker.log")


def test_the_hook_gets_the_digest_as_an_encoded_image_url(tmp_path: Path) -> None:
    run = run_script(tmp_path)

    url = run.read("hook_url").strip()
    assert url.startswith(HOOK + "&imgURL=")
    assert url.endswith("ghcr.io%2Fowner%2Frepo%40sha256%3A" + "ab" * 32)


def test_a_hook_without_a_key_query_gets_a_question_mark(tmp_path: Path) -> None:
    run = run_script(
        tmp_path, RENDER_DEPLOY_HOOK_URL="https://api.render.com/deploy/srv-example"
    )

    assert (
        run.read("hook_url")
        .strip()
        .startswith("https://api.render.com/deploy/srv-example?imgURL=")
    )


def test_the_hook_url_is_never_printed(tmp_path: Path) -> None:
    run = run_script(tmp_path)

    assert "SECRET-KEY-VALUE" not in run.output
    assert "SECRET-KEY-VALUE" not in run.summary


def test_a_short_commit_matches_the_full_release(tmp_path: Path) -> None:
    run = run_script(tmp_path, COMMIT=COMMIT[:7])

    assert run.result.returncode == 0, run.output


def test_a_digest_from_the_build_is_used_without_any_lookup(tmp_path: Path) -> None:
    given = "sha256:" + "cd" * 32

    run = run_script(tmp_path, IMAGE_DIGEST=given, STUB_DIGEST="")

    assert run.result.returncode == 0, run.output
    assert run.read("docker.log") == ""  # the registry was not asked
    assert run.read("hook_url").strip().endswith("%40sha256%3A" + "cd" * 32)


@pytest.mark.parametrize(
    "digest", ["latest", "sha256:abc", "sha1:" + "ab" * 20, "sha256:" + "AB" * 32]
)
def test_a_malformed_digest_is_rejected_before_the_hook(
    tmp_path: Path, digest: str
) -> None:
    run = run_script(tmp_path, IMAGE_DIGEST=digest)

    assert run.result.returncode == 1
    assert "Unexpected digest" in run.output
    assert run.read("hook_url") == ""


def test_a_missing_image_stops_before_the_hook_is_called(tmp_path: Path) -> None:
    run = run_script(tmp_path, STUB_DIGEST="")

    assert run.result.returncode == 1
    assert "No image ghcr.io/owner/repo:sha-0123456" in run.output
    assert run.read("hook_url") == ""


@pytest.mark.parametrize(
    ("status", "message"),
    [
        ("400", "refused the image (400)"),
        ("401", "hook key is wrong or old (401)"),
        ("404", "does not find the service (404)"),
        ("409", "suspended or deploys are blocked (409)"),
        ("500", "answered HTTP 500"),
    ],
)
def test_a_hook_failure_is_named(tmp_path: Path, status: str, message: str) -> None:
    run = run_script(tmp_path, STUB_HOOK_STATUS=status)

    assert run.result.returncode == 1
    assert message in run.output
    assert run.read("probes") == ""  # no waiting after a refused deploy


def test_a_deploy_queued_behind_another_is_accepted(tmp_path: Path) -> None:
    run = run_script(tmp_path, STUB_HOOK_STATUS="202")

    assert run.result.returncode == 0, run.output
    assert "another deploy is running" in run.output


def test_a_build_that_never_goes_live_fails_with_what_was_last_seen(
    tmp_path: Path,
) -> None:
    run = run_script(
        tmp_path, STUB_RELEASES="f" * 40, WAIT_SECONDS="1", POLL_SECONDS="1"
    )

    assert run.result.returncode == 1
    assert "did not report 0123456 within 1s" in run.output
    assert "last X-Release: " + "f" * 40 in run.output


def test_a_service_that_answers_without_a_release_header_never_confirms(
    tmp_path: Path,
) -> None:
    run = run_script(tmp_path, STUB_RELEASES="", WAIT_SECONDS="1", POLL_SECONDS="1")

    assert run.result.returncode == 1
    assert "(nothing yet)" in run.output


@pytest.mark.parametrize(
    "commit", ["", "not-hex", "123456", "0123456789abcdef0123456789abcdef012345678"]
)
def test_a_bad_commit_is_rejected(tmp_path: Path, commit: str) -> None:
    run = run_script(tmp_path, COMMIT=commit)

    assert run.result.returncode == 1
    assert run.read("docker.log") == ""


def test_the_staging_address_must_be_https(tmp_path: Path) -> None:
    run = run_script(tmp_path, STAGING_URL="http://staging.example.test")

    assert run.result.returncode == 1
    assert "must start with https://" in run.output


@pytest.mark.parametrize(
    "name", ["IMAGE_REPOSITORY", "COMMIT", "RENDER_DEPLOY_HOOK_URL", "STAGING_URL"]
)
def test_every_input_is_required(tmp_path: Path, name: str) -> None:
    run = run_script(tmp_path, **{name: "<unset>"})

    assert run.result.returncode != 0
    assert name in run.output
