"""Fast tests for ``scripts/rlm-connect`` (T05 daemon launcher).

The launcher is a stdlib-only script with no ``.py`` suffix, so it is loaded
as a module through ``SourceFileLoader`` for its pure helpers, and run as a
subprocess against a fake ``docker`` on ``PATH`` for the engine-facing
behaviour. The fake records each invocation's argv (one line per call) and
answers ``inspect`` as absent, stopped or running from ``FAKE_STATE``, or
from the next line of the file named by ``FAKE_STATE_SEQ`` when that is set.
"""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "rlm-connect"

FAKE_DOCKER = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_LOG"
case "$1" in
  inspect)
    if [ -n "$FAKE_STATE_SEQ" ]; then
      FAKE_STATE=$(head -n 1 "$FAKE_STATE_SEQ")
      tail -n +2 "$FAKE_STATE_SEQ" > "$FAKE_STATE_SEQ.next"
      mv "$FAKE_STATE_SEQ.next" "$FAKE_STATE_SEQ"
    fi
    case "$FAKE_STATE" in
      running) echo true; exit 0 ;;
      stopped) echo false; exit 0 ;;
      *) echo "Error: No such container" >&2; exit 1 ;;
    esac ;;
  run)
    if [ -n "$FAKE_RUN_CONFLICT" ]; then
      echo 'docker: Error response from daemon: Conflict.' \\
        'The container name "/x" is already in use by container "abc".' >&2
      exit 125
    fi
    echo 0123456789abcdef; exit 0 ;;
  start) echo "$2"; exit 0 ;;
  exec) echo "EXEC-REACHED"; exit 0 ;;
esac
exit 0
"""


def _load_module() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("rlm_connect", str(SCRIPT))
    spec = importlib.util.spec_from_loader("rlm_connect", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rlm() -> ModuleType:
    return _load_module()


@pytest.fixture
def fake_env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(FAKE_DOCKER)
    fake.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "RLM_IDLE_TIMEOUT_SECONDS"}
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_LOG"] = str(tmp_path / "calls.log")
    return env


@pytest.fixture
def project(tmp_path: Path) -> Path:
    folder = tmp_path / "proj"
    folder.mkdir()
    return folder


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--engine", "docker", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _calls(env: dict[str, str]) -> list[str]:
    log = Path(env["FAKE_LOG"])
    return log.read_text().splitlines() if log.exists() else []


def test_script_is_executable() -> None:
    assert os.access(SCRIPT, os.X_OK)


def test_names_derive_from_real_path(rlm: ModuleType, project: Path) -> None:
    real, project_id, container, volume = rlm.derive_names(str(project))
    expected = hashlib.sha256(os.path.realpath(project).encode("utf-8")).hexdigest()[:12]
    assert real == os.path.realpath(project)
    assert project_id == expected
    assert container == f"rlm-{expected}"
    assert volume == f"rlm-{expected}-data"


def test_names_stable_across_symlinked_spellings(
    rlm: ModuleType, project: Path, tmp_path: Path
) -> None:
    link = tmp_path / "link"
    link.symlink_to(project)
    spellings = [str(project), f"{project}/", str(link), f"{link}/../proj"]
    names = {rlm.derive_names(s)[1:] for s in spellings}
    assert len(names) == 1


def test_run_args_shape(rlm: ModuleType, project: Path) -> None:
    real, pid, name, vol = rlm.derive_names(str(project))
    args = rlm.build_run_args(real, pid, name, vol, "rust-lsp-mcp", "none", environ={})
    joined = " ".join(args)
    assert args[:7] == ["run", "-d", "--init", "--stop-timeout", "15", "--name", name]
    for fragment in (
        "--network none",
        f"-e RLM_PROJECT_ID={pid}",
        "-e RLM_TRANSPORT=streamable-http",
        "-e RLM_HTTP_PORT=8000",
        "--memory 6g",
        "--cpus 4",
        f"-v {real}:/project:ro,z",
        f"-v {vol}:/data",
    ):
        assert fragment in joined
    assert args[-1] == "rust-lsp-mcp"


def test_network_default_drops_network_none(rlm: ModuleType, project: Path) -> None:
    real, pid, name, vol = rlm.derive_names(str(project))
    args = rlm.build_run_args(real, pid, name, vol, "img", "default", environ={})
    assert "--network" not in args


@pytest.mark.parametrize("network", ["none", "default"])
def test_run_args_set_15s_stop_timeout(rlm: ModuleType, project: Path, network: str) -> None:
    """Backlog line 87: a stop early in a cold start outlasts the engine's 10 s default."""
    real, pid, name, vol = rlm.derive_names(str(project))
    args = rlm.build_run_args(real, pid, name, vol, "img", network, environ={})
    i = args.index("--stop-timeout")
    assert args[i + 1] == "15"


def test_network_none_sets_cargo_net_offline(rlm: ModuleType, project: Path) -> None:
    """Backlog line 142: an isolated daemon passes CARGO_NET_OFFLINE=true; a networked one not."""
    real, pid, name, vol = rlm.derive_names(str(project))
    isolated = rlm.build_run_args(real, pid, name, vol, "img", "none", environ={})
    networked = rlm.build_run_args(real, pid, name, vol, "img", "default", environ={})
    i = isolated.index("CARGO_NET_OFFLINE=true")
    assert isolated[i - 1] == "-e"
    assert not any("CARGO_NET_OFFLINE" in a for a in networked)


def test_absent_container_is_run(fake_env: dict[str, str], project: Path) -> None:
    fake_env["FAKE_STATE"] = "absent"
    proc = _run(["--up", str(project)], fake_env)
    assert proc.returncode == 0, proc.stderr
    calls = _calls(fake_env)
    assert calls[0].startswith("inspect ")
    assert any(c.startswith("run -d --init --stop-timeout 15 --name rlm-") for c in calls)
    assert not any(c.startswith("start ") for c in calls)


def test_stopped_container_is_started(fake_env: dict[str, str], project: Path) -> None:
    fake_env["FAKE_STATE"] = "stopped"
    proc = _run(["--up", str(project)], fake_env)
    assert proc.returncode == 0, proc.stderr
    calls = _calls(fake_env)
    assert any(c.startswith("start rlm-") for c in calls)
    assert not any(c.startswith("run ") for c in calls)


def test_running_container_is_left_alone(fake_env: dict[str, str], project: Path) -> None:
    fake_env["FAKE_STATE"] = "running"
    proc = _run(["--up", str(project)], fake_env)
    assert proc.returncode == 0, proc.stderr
    calls = _calls(fake_env)
    assert [c.split()[0] for c in calls] == ["inspect"]


def test_up_prints_only_the_name_and_does_not_exec(
    rlm: ModuleType, fake_env: dict[str, str], project: Path
) -> None:
    fake_env["FAKE_STATE"] = "absent"
    proc = _run(["--up", str(project)], fake_env)
    _, _, container, _ = rlm.derive_names(str(project))
    assert proc.returncode == 0
    assert proc.stdout == f"{container}\n"
    assert "EXEC-REACHED" not in proc.stdout
    assert not any(c.startswith("exec ") for c in _calls(fake_env))


def test_print_names_makes_no_engine_call(
    rlm: ModuleType, fake_env: dict[str, str], project: Path
) -> None:
    proc = _run(["--print-names", str(project)], fake_env)
    _, _, container, volume = rlm.derive_names(str(project))
    assert proc.returncode == 0
    assert proc.stdout == f"{container}\n{volume}\n"
    assert _calls(fake_env) == []


def test_default_folder_is_cwd(rlm: ModuleType, fake_env: dict[str, str], project: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--print-names"],
        capture_output=True,
        text=True,
        env=fake_env,
        cwd=project,
        timeout=30,
    )
    _, _, container, volume = rlm.derive_names(str(project))
    assert proc.stdout == f"{container}\n{volume}\n"


def test_name_conflict_on_run_counts_as_success(
    fake_env: dict[str, str], project: Path, tmp_path: Path
) -> None:
    """The loser of a simultaneous start polls until the winner's container
    runs (backlog line 75): absent, then a name conflict, then STOPPED for two
    polls and RUNNING on the third, then the attach."""
    seq = tmp_path / "states"
    seq.write_text("absent\nstopped\nstopped\nrunning\n")
    fake_env["FAKE_STATE_SEQ"] = str(seq)
    fake_env["FAKE_RUN_CONFLICT"] = "1"
    proc = _run([str(project)], fake_env)
    assert proc.returncode == 0, proc.stderr
    assert "already in use" in proc.stderr
    assert "EXEC-REACHED" in proc.stdout
    calls = _calls(fake_env)
    verbs = [c.split(" ", 1)[0] for c in calls]
    # one inspect before the run, three polls after the conflict, then exec
    assert verbs == ["inspect", "run", "inspect", "inspect", "inspect", "exec"]


def test_name_conflict_loser_returns_true_after_third_poll(
    rlm: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    states = [rlm.ABSENT, rlm.STOPPED, rlm.STOPPED, rlm.RUNNING]
    seen: list[str] = []

    def fake_inspect(engine: str, container: str) -> str:
        seen.append(states[len(seen)])
        return seen[-1]

    conflict = subprocess.CompletedProcess(
        args=["docker"], returncode=125, stdout="", stderr='name "/rlm-x" is already in use'
    )
    monkeypatch.setattr(rlm, "inspect_state", fake_inspect)
    monkeypatch.setattr(rlm, "_run_engine", lambda engine, args: conflict)
    monkeypatch.setattr(rlm, "CONFLICT_POLL_SECONDS", 0.0)
    assert rlm.ensure_running("docker", "/p", "x", "rlm-x", "rlm-x-data", "img", "none") is True
    # the first inspect precedes the run; the RUNNING answer is the third poll
    assert seen == states


def test_name_conflict_loser_gives_up_when_never_running(
    rlm: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []

    def fake_inspect(engine: str, container: str) -> str:
        calls.append(container)
        return rlm.ABSENT if len(calls) == 1 else rlm.STOPPED

    conflict = subprocess.CompletedProcess(
        args=["docker"], returncode=125, stdout="", stderr='name "/rlm-x" is already in use'
    )
    monkeypatch.setattr(rlm, "inspect_state", fake_inspect)
    monkeypatch.setattr(rlm, "_run_engine", lambda engine, args: conflict)
    monkeypatch.setattr(rlm, "CONFLICT_POLL_SECONDS", 0.01)
    monkeypatch.setattr(rlm, "CONFLICT_WAIT_SECONDS", 0.1)
    assert rlm.ensure_running("docker", "/p", "x", "rlm-x", "rlm-x-data", "img", "none") is False
    assert len(calls) > 2  # polled more than once before giving up
    err = capsys.readouterr().err
    assert (
        "rlm-x was created by a simultaneous start but is not running after 0.1 s;"
        " run rlm-connect again"
    ) in err


def test_idle_timeout_env_reaches_run_argv(fake_env: dict[str, str], project: Path) -> None:
    fake_env["FAKE_STATE"] = "absent"
    fake_env["RLM_IDLE_TIMEOUT_SECONDS"] = "7"
    assert _run(["--up", str(project)], fake_env).returncode == 0
    run_calls = [c for c in _calls(fake_env) if c.startswith("run ")]
    assert len(run_calls) == 1
    assert "-e RLM_IDLE_TIMEOUT_SECONDS=7" in run_calls[0]


def test_idle_timeout_unset_adds_nothing(fake_env: dict[str, str], project: Path) -> None:
    fake_env["FAKE_STATE"] = "absent"
    assert _run(["--up", str(project)], fake_env).returncode == 0
    run_calls = [c for c in _calls(fake_env) if c.startswith("run ")]
    assert len(run_calls) == 1
    assert "RLM_IDLE_TIMEOUT_SECONDS" not in run_calls[0]


@pytest.mark.parametrize("value", ["", "   "], ids=["empty", "whitespace"])
def test_idle_timeout_blank_adds_nothing(
    fake_env: dict[str, str], project: Path, value: str
) -> None:
    """An empty or whitespace-only value is not forwarded (backlog line 74)."""
    fake_env["FAKE_STATE"] = "absent"
    fake_env["RLM_IDLE_TIMEOUT_SECONDS"] = value
    assert _run(["--up", str(project)], fake_env).returncode == 0
    run_calls = [c for c in _calls(fake_env) if c.startswith("run ")]
    assert len(run_calls) == 1
    assert "RLM_IDLE_TIMEOUT_SECONDS" not in run_calls[0]


def test_attach_execs_the_bridge(fake_env: dict[str, str], project: Path) -> None:
    """Without --up the launcher replaces itself with `<engine> exec -i <name> … mcp-bridge`."""
    fake_env["FAKE_STATE"] = "running"
    proc = _run([str(project)], fake_env)
    assert proc.returncode == 0, proc.stderr
    exec_calls = [c for c in _calls(fake_env) if c.startswith("exec ")]
    assert len(exec_calls) == 1
    assert exec_calls[0].endswith("/app/.venv/bin/rust-lsp mcp-bridge")
    assert exec_calls[0].startswith("exec -i rlm-")
    # stdout belongs to the exec'd bridge; the launcher itself wrote nothing there.
    assert proc.stdout == "EXEC-REACHED\n"


def test_run_failure_exits_nonzero(fake_env: dict[str, str], project: Path, tmp_path: Path) -> None:
    failing = tmp_path / "bin" / "docker"
    failing.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$FAKE_LOG"\n'
        '[ "$1" = run ] && { echo "boom" >&2; exit 125; }\nexit 1\n'
    )
    proc = _run(["--up", str(project)], fake_env)
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert "boom" in proc.stderr
