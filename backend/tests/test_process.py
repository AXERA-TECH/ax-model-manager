from pathlib import Path

from ax_model_manager.process import ProcessSpec, RuntimeHandle, SystemdRunManager


def test_systemd_run_uses_transient_unit_and_log_properties(monkeypatch, tmp_path: Path) -> None:
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[0] == "systemctl":
            return type("Completed", (), {"stdout": "12345\n"})()
        return None

    monkeypatch.setattr("ax_model_manager.process.subprocess.run", fake_run)
    manager = SystemdRunManager()
    spec = ProcessSpec(
        argv=("/bin/sleep", "1"),
        cwd=tmp_path,
        log_path=tmp_path / "runtime.log",
        env={"EXAMPLE": "1"},
        runtime_id="ax-model-test",
    )
    handle = manager.start(spec)

    assert handle == RuntimeHandle(runtime_id="ax-model-test", pid=12345)
    assert commands[0][0] == "systemd-run"
    assert "--unit=ax-model-test" in commands[0]
    assert "--property=StandardOutput=append:" in " ".join(commands[0])
    assert "--setenv=EXAMPLE=1" in commands[0]
    assert commands[1] == ["systemctl", "show", "ax-model-test", "--property=MainPID", "--value"]


def test_systemd_stop_does_not_use_unsupported_timeout_option(monkeypatch) -> None:
    commands = []

    def fake_run(command, **kwargs):
        commands.append((command, kwargs))
        return type("Completed", (), {"stdout": "0\n"})()

    monkeypatch.setattr("ax_model_manager.process.subprocess.run", fake_run)
    manager = SystemdRunManager()
    exit_code = manager.stop(RuntimeHandle(runtime_id="ax-model-test", pid=123), timeout=3.0)

    assert exit_code == 0
    assert commands[0][0] == ["systemctl", "stop", "ax-model-test"]
    assert commands[0][1]["timeout"] == 3.0


def test_systemd_lists_and_parses_transient_runtimes(monkeypatch) -> None:
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if "list-units" in command:
            return type("Completed", (), {"stdout": "ax-model-test-job.service loaded active running test\n"})
        return type("Completed", (), {
            "stdout": "MainPID=123\nActiveState=active\nSubState=running\nExecStart=/bin/sleep --port 18080\n"
        })

    monkeypatch.setattr("ax_model_manager.process.subprocess.run", fake_run)
    manager = SystemdRunManager()
    runtimes = manager.list_runtimes()
    assert runtimes == [{
        "runtime_id": "ax-model-test-job",
        "unit": "ax-model-test-job.service",
        "pid": 123,
        "active_state": "active",
        "sub_state": "running",
        "command": "/bin/sleep --port 18080",
    }]
    assert commands[0] == [
        "systemctl", "list-units", "ax-model-*.service", "--no-legend", "--plain"
    ]
    assert "--value" not in commands[1]
