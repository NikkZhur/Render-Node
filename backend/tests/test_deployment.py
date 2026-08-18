from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import tarfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BLENDER_CHECKSUMS = {
    "5.2.0": "96f6c181a30f4950607839dc84d42a354b250d8a0231b098b59b7bc69c351c48",
    "4.1.1": "ab2ea3fe991601a5e6bd2cda786ecaa919c0b39e0550e59978b5d40270c260d3",
}


def run(
    command: list[str], *, env: dict[str, str] | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603
        command,
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed ({result.returncode}): {command!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def add_file(bundle: tarfile.TarFile, name: str, content: bytes, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mode = mode
    info.mtime = 0
    bundle.addfile(info, fileobj=__import__("io").BytesIO(content))


@dataclass
class Harness:
    root: Path
    install: Path
    state: Path
    runtime: Path
    workspace: Path
    management: Path
    releases: Path
    control: Path
    fake_bin: Path
    env: dict[str, str]

    def create_release(self, version: str, *, malicious: str | None = None) -> None:
        release = self.releases / version
        release.mkdir(parents=True, exist_ok=True)
        archive = release / "render-node-linux-x64.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            if malicious == "path":
                add_file(bundle, "../escape", b"bad")
            elif malicious == "symlink":
                info = tarfile.TarInfo("render-node/escape")
                info.type = tarfile.SYMTYPE
                info.linkname = "../../escape"
                bundle.addfile(info)
            else:
                files = {
                    "render-node/VERSION": f"{version}\n".encode(),
                    "render-node/backend/app/__init__.py": b"",
                    "render-node/backend/migrations/env.py": b"",
                    "render-node/backend/alembic.ini": b"[alembic]\n",
                    "render-node/backend/pyproject.toml": b"[project]\nname='fixture'\n",
                    "render-node/backend/uv.lock": b"version = 1\n",
                    "render-node/frontend/dist/index.html": b"fixture",
                    "render-node/deploy/nginx.conf.template": (
                        ROOT / "deploy/nginx.conf.template"
                    ).read_bytes(),
                    "render-node/deploy/supervisor.sh": (
                        ROOT / "deploy/supervisor.sh"
                    ).read_bytes(),
                    "render-node/scripts/render-node": (ROOT / "scripts/render-node").read_bytes(),
                }
                for name, content in files.items():
                    mode = 0o755 if name.endswith(("supervisor.sh", "render-node")) else 0o644
                    add_file(bundle, name, content, mode)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (release / "render-node-linux-x64.tar.gz.sha256").write_text(
            f"{digest}  render-node-linux-x64.tar.gz\n"
        )

    def install_release(
        self, *arguments: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return run(["/bin/bash", str(ROOT / "install.sh"), *arguments], env=self.env, check=check)

    def manage(self, command: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return run([str(self.management), command], env=self.env, check=check)

    @property
    def current_version(self) -> str:
        return (self.install / "current" / "VERSION").read_text().strip()


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    fake_bin = tmp_path / "fake-bin"
    releases = tmp_path / "release-assets"
    control = tmp_path / "control"
    install = tmp_path / "opt" / "render-node"
    state = tmp_path / "workspace" / ".render-node"
    runtime = tmp_path / "run" / "render-node"
    workspace = tmp_path / "workspace"
    management = tmp_path / "bin" / "render-node"
    for path in (fake_bin, releases, control, management.parent):
        path.mkdir(parents=True, exist_ok=True)
    (control / "latest").write_text("1.0.0")
    (control / "fail-version").write_text("")

    uv_source = control / "uv"
    executable(
        uv_source,
        f"""#!/usr/bin/env bash
set -Eeuo pipefail
echo "$*" >> {control / "uv.log"}
case "${{1:-}}" in
  python) exit 0 ;;
  sync)
    mkdir -p .venv/bin
    cat > .venv/bin/alembic <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
    cat > .venv/bin/uvicorn <<'EOF'
#!/usr/bin/env bash
env > {control / "child.env"}
trap 'exit 0' INT TERM
while :; do sleep 1; done
EOF
    chmod 0755 .venv/bin/alembic .venv/bin/uvicorn
    ;;
esac
""",
    )
    uv_installer = control / "install-uv.sh"
    uv_installer.write_text(
        "#!/usr/bin/env sh\n"
        'mkdir -p "$UV_INSTALL_DIR"\n'
        'cp "$FAKE_UV_SOURCE" "$UV_INSTALL_DIR/uv"\n'
        'chmod 0755 "$UV_INSTALL_DIR/uv"\n'
    )

    for version in BLENDER_CHECKSUMS:
        archive = control / f"blender-{version}-linux-x64.tar.xz"
        with tarfile.open(archive, "w:xz") as bundle:
            add_file(
                bundle,
                f"blender-{version}-linux-x64/blender",
                f"#!/usr/bin/env bash\necho 'Blender {version}'\n".encode(),
                0o755,
            )

    executable(fake_bin / "nvidia-smi", "#!/usr/bin/env bash\nexit 0\n")
    executable(
        fake_bin / "sha256sum",
        """#!/usr/bin/env bash
if [[ "${1:-}" == "--check" ]]; then
  read -r _ path
  [[ "$path" == *blender-* ]] && exit 0
fi
exec /usr/bin/sha256sum "$@"
""",
    )
    executable(
        fake_bin / "nginx",
        "#!/usr/bin/env bash\ntrap 'exit 0' INT TERM\nwhile :; do sleep 1; done\n",
    )
    executable(
        fake_bin / "curl",
        f"""#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\n' "$*" >> {control / "curl-args.log"}
if [[ "${{1:-}}" == "--config" && "${{2:-}}" == "-" ]]; then
  payload="$(cat)"
  expected="$(sed -n 's/^RENDER_NODE_AUTH_TOKEN=//p' {state / "render-node.env"} | head -n 1)"
  [[ "$payload" == *'http://127.0.0.1:8000/api/v1/access/reset'* ]]
  [[ "$payload" == *"Authorization: Bearer $expected"* ]]
  printf 'reset\n' >> {control / "reset.log"}
  exit 0
fi
url=""; output=""
while (($#)); do
  case "$1" in
    -o) output="$2"; shift 2 ;;
    --proto|--tlsv1.2) [[ "$1" == "--proto" ]] && shift 2 || shift ;;
    --fail|--location|--silent) shift ;;
    *) url="$1"; shift ;;
  esac
done
printf '%s\n' "$url" >> {control / "curl.log"}
if [[ -z "$output" ]]; then
  current=""
  [[ -r {install / "current" / "VERSION"} ]] && current="$(<{install / "current" / "VERSION"})"
  fail="$(<{control / "fail-version"})"
  [[ -z "$fail" || "$current" != "$fail" ]]
  exit
fi
case "$url" in
  *releases/latest/download*) version="$(<{control / "latest"})" ;;
  *releases/download/v*)
    version="${{url#*releases/download/v}}"; version="${{version%%/*}}" ;;
  *astral.sh*) cp {uv_installer} "$output"; exit 0 ;;
  *download.blender.org*) cp {control}/"${{url##*/}}" "$output"; exit 0 ;;
  *) exit 22 ;;
esac
cp {releases}/"$version"/"${{url##*/}}" "$output"
""",
    )

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RENDER_NODE_TEST_MODE": "1",
        "RENDER_NODE_INSTALL_ROOT": str(install),
        "RENDER_NODE_STATE_ROOT": str(state),
        "RENDER_NODE_RUNTIME_ROOT": str(runtime),
        "RENDER_NODE_MANAGEMENT_BIN": str(management),
        "RENDER_NODE_WORKSPACE_ROOT": str(workspace),
        "RENDER_NODE_TEST_BIN": str(fake_bin),
        "RENDER_NODE_SKIP_PACKAGES": "1",
        "RENDER_NODE_READY_ATTEMPTS": "2",
        "RENDER_NODE_STOP_ATTEMPTS": "20",
        "RUNPOD_API_KEY": "must-not-leak",
        "CUDA_VISIBLE_DEVICES": "0",
        "FAKE_UV_SOURCE": str(uv_source),
    }
    for obsolete_name in (
        "RENDER_NODE_ALLOWED_ORIGINS",
        "RENDER_NODE_PUBLIC_ORIGIN",
        "RUNPOD_POD_ID",
        "RENDER_NODE_ADMIN_USERNAME",
        "RENDER_NODE_ADMIN_PASSWORD",
        "RENDER_NODE_ADMIN_HTPASSWD_B64",
    ):
        env.pop(obsolete_name, None)
    result = Harness(
        root=tmp_path,
        install=install,
        state=state,
        runtime=runtime,
        workspace=workspace,
        management=management,
        releases=releases,
        control=control,
        fake_bin=fake_bin,
        env=env,
    )
    result.create_release("1.0.0")
    yield result
    if management.exists():
        result.manage("stop", check=False)


@pytest.mark.parametrize(
    "relative_path",
    [
        "install.sh",
        "scripts/render-node",
        "scripts/build-release-bundle.sh",
        "deploy/cgroup-delegation.sh",
        "deploy/container-entrypoint.sh",
        "deploy/supervisor.sh",
    ],
)
def test_shell_syntax_and_safe_help(relative_path: str, tmp_path: Path) -> None:
    syntax = run(["/bin/bash", "-n", str(ROOT / relative_path)])
    assert syntax.returncode == 0
    if relative_path in {"install.sh", "scripts/render-node"}:
        before = set(tmp_path.iterdir())
        result = run(["/bin/bash", str(ROOT / relative_path), "--help"])
        assert "Usage:" in result.stdout
        assert set(tmp_path.iterdir()) == before


def test_latest_and_explicit_release_urls(harness: Harness) -> None:
    harness.install_release()
    urls = (harness.control / "curl.log").read_text()
    assert "/releases/latest/download/render-node-linux-x64.tar.gz" in urls
    harness.manage("stop")
    shutil.rmtree(harness.install)
    (harness.control / "curl.log").write_text("")
    harness.install_release("--version", "1.0.0")
    urls = (harness.control / "curl.log").read_text()
    assert "/releases/download/v1.0.0/render-node-linux-x64.tar.gz" in urls


def test_bad_checksum_and_unsafe_tar_do_not_create_installation(harness: Harness) -> None:
    checksum = harness.releases / "1.0.0" / "render-node-linux-x64.tar.gz.sha256"
    checksum.write_text(f"{'0' * 64}  render-node-linux-x64.tar.gz\n")
    result = harness.install_release(check=False)
    assert result.returncode != 0
    assert "checksum mismatch" in result.stderr
    assert not harness.install.exists()

    for malicious in ("path", "symlink"):
        harness.create_release("1.0.0", malicious=malicious)
        result = harness.install_release(check=False)
        assert result.returncode != 0
        assert "Unsafe" in result.stderr
        assert not harness.install.exists()


def test_bundle_manifest_is_explicit_and_clean(tmp_path: Path) -> None:
    output = tmp_path / "dist"
    run(["/bin/bash", str(ROOT / "scripts/build-release-bundle.sh"), "1.2.3", str(output)])
    archive = output / "render-node-linux-x64.tar.gz"
    checksum = output / "render-node-linux-x64.tar.gz.sha256"
    assert hashlib.sha256(archive.read_bytes()).hexdigest() in checksum.read_text()
    with tarfile.open(archive, "r:gz") as bundle:
        names = set(bundle.getnames())
    required = {
        "render-node/VERSION",
        "render-node/backend/pyproject.toml",
        "render-node/backend/uv.lock",
        "render-node/frontend/dist/index.html",
        "render-node/deploy/nginx.conf.template",
        "render-node/deploy/cgroup-delegation.sh",
        "render-node/deploy/supervisor.sh",
        "render-node/scripts/render-node",
    }
    assert required <= names
    forbidden = ("node_modules", ".venv", "__pycache__", "tests/", "frontend/src", ".blend")
    assert not any(any(part in name for part in forbidden) for name in names)
    second = tmp_path / "second"
    run(["/bin/bash", str(ROOT / "scripts/build-release-bundle.sh"), "1.2.3", str(second)])
    assert archive.read_bytes() == (second / archive.name).read_bytes()


def test_fresh_noop_update_and_repair_preserve_data_and_internal_token(
    harness: Harness,
) -> None:
    fresh = harness.install_release()
    assert "HTTPS address assigned by your provider" in fresh.stdout
    assert "first browser" in fresh.stdout
    assert not (harness.state / "credentials").exists()
    assert not (harness.state / "admin.htpasswd").exists()
    token = next(
        line
        for line in (harness.state / "render-node.env").read_text().splitlines()
        if line.startswith("RENDER_NODE_AUTH_TOKEN=")
    )
    environment = (harness.state / "render-node.env").read_text()
    assert "RENDER_NODE_ALLOWED_ORIGINS" not in environment
    assert "RENDER_NODE_ADMIN_" not in environment
    job = harness.workspace / "jobs" / "kept.txt"
    database = harness.workspace / "database" / "kept.sqlite3"
    runtime = harness.workspace / "blender" / "versions" / "custom" / "kept.txt"
    runtime.parent.mkdir(parents=True)
    job.write_text("kept")
    database.write_text("kept")
    runtime.write_text("kept")
    (harness.state / "credentials").write_text("USERNAME=legacy\nPASSWORD=legacy\n")
    (harness.state / "admin.htpasswd").write_text("legacy:$2y$fixture\n")
    unrelated = harness.state / "credentials.backup"
    unrelated.write_text("unrelated")
    uv_calls = (harness.control / "uv.log").read_text()

    harness.install_release()
    assert (harness.control / "uv.log").read_text() == uv_calls
    assert job.read_text() == "kept"
    assert database.read_text() == "kept"
    assert runtime.read_text() == "kept"
    assert not (harness.state / "credentials").exists()
    assert not (harness.state / "admin.htpasswd").exists()
    assert unrelated.read_text() == "unrelated"

    harness.create_release("2.0.0")
    (harness.control / "latest").write_text("2.0.0")
    harness.install_release()
    assert token in (harness.state / "render-node.env").read_text()
    assert job.read_text() == "kept"
    assert database.read_text() == "kept"
    assert runtime.read_text() == "kept"

    harness.manage("stop")
    shutil.rmtree(harness.install)
    harness.install_release()
    assert token in (harness.state / "render-node.env").read_text()
    assert job.read_text() == "kept"
    assert database.read_text() == "kept"
    assert runtime.read_text() == "kept"


def test_atomic_update_and_readiness_rollback(harness: Harness) -> None:
    harness.install_release()
    harness.create_release("2.0.0")
    (harness.control / "latest").write_text("2.0.0")
    harness.install_release()
    assert harness.current_version == "2.0.0"
    assert {path.name for path in (harness.install / "releases").iterdir()} == {"1.0.0", "2.0.0"}

    legacy_credentials = harness.state / "credentials"
    legacy_htpasswd = harness.state / "admin.htpasswd"
    legacy_credentials.write_text("legacy credentials needed by rollback")
    legacy_htpasswd.write_text("legacy htpasswd needed by rollback")
    harness.create_release("3.0.0")
    (harness.control / "latest").write_text("3.0.0")
    (harness.control / "fail-version").write_text("3.0.0")
    failed = harness.install_release(check=False)
    assert failed.returncode != 0
    assert "previous release was restored" in failed.stderr
    assert harness.current_version == "2.0.0"
    assert "RENDER_NODE_INSTALLED_VERSION=2.0.0" in (harness.state / "render-node.env").read_text()
    assert not (harness.install / "releases" / "3.0.0").exists()
    assert legacy_credentials.exists()
    assert legacy_htpasswd.exists()
    assert harness.manage("status").returncode == 0


def test_partial_failure_cleans_staging(harness: Harness) -> None:
    harness.install_release()
    harness.create_release("2.0.0")
    checksum = harness.releases / "2.0.0" / "render-node-linux-x64.tar.gz.sha256"
    checksum.write_text(f"{'f' * 64}  render-node-linux-x64.tar.gz\n")
    (harness.control / "latest").write_text("2.0.0")
    result = harness.install_release(check=False)
    assert result.returncode != 0
    assert harness.current_version == "1.0.0"
    assert not any(
        path.name.startswith(".staging") for path in (harness.install / "releases").iterdir()
    )


def test_management_rejects_foreign_and_reused_pid_and_manages_group(harness: Harness) -> None:
    harness.install_release()
    harness.manage("stop")
    sleep = shutil.which("sleep")
    assert sleep is not None
    foreign = subprocess.Popen([sleep, "30"])  # noqa: S603
    try:
        start = Path(f"/proc/{foreign.pid}/stat").read_text().split()[21]
        harness.runtime.mkdir(parents=True, exist_ok=True)
        (harness.runtime / "service.pid").write_text(f"{foreign.pid} {start}\n")
        assert harness.manage("status", check=False).returncode == 1
        assert foreign.poll() is None

        (harness.runtime / "service.pid").write_text(f"{foreign.pid} {int(start) + 1}\n")
        assert harness.manage("status", check=False).returncode == 1
        assert foreign.poll() is None
    finally:
        foreign.terminate()
        foreign.wait(timeout=5)

    harness.manage("start")
    assert harness.manage("status").returncode == 0
    harness.manage("restart")
    assert harness.manage("status").returncode == 0
    harness.manage("stop")
    assert not (harness.runtime / "service.pid").exists()


def test_reset_access_uses_private_backend_without_restart_or_token_leak(
    harness: Harness,
) -> None:
    harness.install_release()
    identity_before = (harness.runtime / "service.pid").read_text()
    token = next(
        line.split("=", 1)[1]
        for line in (harness.state / "render-node.env").read_text().splitlines()
        if line.startswith("RENDER_NODE_AUTH_TOKEN=")
    )

    reset = harness.manage("reset-access")

    assert reset.stdout == "Access reset; the next browser can claim this node.\n"
    assert token not in reset.stdout
    assert token not in reset.stderr
    assert (harness.runtime / "service.pid").read_text() == identity_before
    assert (harness.control / "reset.log").read_text() == "reset\n"
    assert token not in (harness.control / "curl-args.log").read_text()
    assert harness.manage("status").returncode == 0


def test_internal_token_is_private_and_cloud_secrets_are_removed(harness: Harness) -> None:
    harness.install_release()
    assert not (harness.state / "credentials").exists()
    assert not (harness.state / "admin.htpasswd").exists()

    environment = ""
    for _ in range(50):
        child_env = harness.control / "child.env"
        if child_env.exists():
            environment = child_env.read_text()
            if "RENDER_NODE_AUTH_TOKEN=" in environment:
                break
        time.sleep(0.02)
    assert "RUNPOD_API_KEY" not in environment
    assert "must-not-leak" not in environment
    assert "RENDER_NODE_AUTH_TOKEN=" in environment
    assert "CUDA_VISIBLE_DEVICES=0" in environment


def test_nginx_substitution_keeps_runtime_variables() -> None:
    envsubst = shutil.which("envsubst")
    if envsubst is None:
        pytest.skip("envsubst is unavailable")
    template = (ROOT / "deploy/nginx.conf.template").read_text()
    result = subprocess.run(  # noqa: S603
        [
            envsubst,
            "${RENDER_NODE_FRONTEND_ROOT} ${RENDER_NODE_STATE_ROOT} ${RENDER_NODE_RUNTIME_ROOT}",
        ],
        input=template,
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "RENDER_NODE_FRONTEND_ROOT": "/release/frontend/dist",
            "RENDER_NODE_STATE_ROOT": "/state",
            "RENDER_NODE_RUNTIME_ROOT": "/run/render-node",
        },
    )
    assert "auth_basic" not in result.stdout
    assert "proxy_set_header Authorization" not in result.stdout
    assert "proxy_set_header Host $http_host;" in result.stdout
    assert "$http_upgrade" in result.stdout
    assert "access_log off" in result.stdout


def test_nginx_config_when_binary_is_available(tmp_path: Path) -> None:
    nginx = shutil.which("nginx")
    envsubst = shutil.which("envsubst")
    if nginx is None or envsubst is None:
        pytest.skip("system nginx or envsubst is unavailable")
    state = tmp_path / "state"
    runtime = tmp_path / "run"
    frontend = tmp_path / "frontend"
    for path in (state / "logs", runtime, frontend):
        path.mkdir(parents=True)
    config = runtime / "nginx.conf"
    with config.open("w") as output:
        subprocess.run(  # noqa: S603
            [
                envsubst,
                "${RENDER_NODE_FRONTEND_ROOT} ${RENDER_NODE_STATE_ROOT} "
                "${RENDER_NODE_RUNTIME_ROOT}",
            ],
            stdin=(ROOT / "deploy/nginx.conf.template").open(),
            stdout=output,
            check=True,
            text=True,
            env={
                **os.environ,
                "RENDER_NODE_FRONTEND_ROOT": str(frontend),
                "RENDER_NODE_STATE_ROOT": str(state),
                "RENDER_NODE_RUNTIME_ROOT": str(runtime),
            },
        )
    result = subprocess.run(  # noqa: S603
        [nginx, "-t", "-c", str(config)], check=False, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_native_flow_has_no_node_docker_systemd_and_image_is_pinned() -> None:
    installer = (ROOT / "install.sh").read_text().lower()
    assert "nodejs" not in installer
    assert "npm " not in installer
    assert "docker " not in installer
    assert "systemctl" not in installer
    assert "apache2-utils" not in installer
    assert "--username" not in installer
    assert "render_node_admin_" not in installer
    dockerfile = (ROOT / "Dockerfile").read_text()
    for version, checksum in BLENDER_CHECKSUMS.items():
        assert version in dockerfile
        assert checksum in dockerfile
    assert "python:3.13.7-slim-bookworm" in dockerfile
    assert "apache2-utils" not in dockerfile
    entrypoint = (ROOT / "deploy/container-entrypoint.sh").read_text()
    assert "RENDER_NODE_ALLOWED_ORIGINS" not in entrypoint
    assert "RENDER_NODE_ADMIN_" not in entrypoint
    assert "htpasswd -" not in entrypoint
    assert "exec env -i" in entrypoint
    assert "RENDER_NODE_RESOURCE_ISOLATION_MODE=strict" in entrypoint
    assert "prepare_render_node_cgroup" in entrypoint

    management = (ROOT / "scripts/render-node").read_text()
    assert "credentials)" not in management
    assert "reset-access" in management
    assert "http://127.0.0.1:8000/api/v1/access/reset" in management

    supervisor = (ROOT / "deploy/supervisor.sh").read_text()
    assert "app.main:create_app --factory --host 127.0.0.1 --port 8000" in supervisor
    nginx = (ROOT / "deploy/nginx.conf.template").read_text()
    assert "auth_basic" not in nginx
    assert "proxy_set_header Authorization" not in nginx
    assert "proxy_set_header Host $http_host;" in nginx
    assert "proxy_set_header Upgrade $http_upgrade;" in nginx
    assert "proxy_set_header Connection $connection_upgrade;" in nginx


def test_publish_job_is_gated_by_all_checks() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text()
    publish = workflow.split("  publish:", maxsplit=1)[1]
    assert "needs: [backend-checks, frontend-checks]" in publish
    assert "contents: write" in publish
    assert "packages: write" in publish
    assert "gh release create" in publish
    assert "--draft=false --prerelease=false" in publish
    assert workflow.index("backend-checks:") < workflow.index("publish:")
    assert workflow.index("frontend-checks:") < workflow.index("publish:")
