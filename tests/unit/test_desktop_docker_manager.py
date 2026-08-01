import subprocess

import pytest

from desktop.docker_manager import DesktopStartupError, DockerManager


def test_compose_up_uses_project_compose_file(tmp_path):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    (tmp_path / "docker-compose.yml").write_text("services: {}", encoding="utf-8")
    manager = DockerManager(tmp_path, runner=runner, docker_executable="docker")
    manager.compose_up()

    assert calls[-1] == ["docker", "compose", "-f", str(tmp_path / "docker-compose.yml"), "up", "-d", "--wait"]


def test_missing_docker_cli_has_actionable_error(tmp_path):
    manager = DockerManager(tmp_path, docker_executable="")
    with pytest.raises(DesktopStartupError, match="Docker Desktop"):
        manager.ensure_ready(timeout=0)


def test_compose_stop_keeps_containers(tmp_path):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    (tmp_path / "docker-compose.yml").write_text("services: {}", encoding="utf-8")
    manager = DockerManager(tmp_path, runner=runner, docker_executable="docker")
    manager.compose_stop()

    assert calls[-1] == ["docker", "compose", "-f", str(tmp_path / "docker-compose.yml"), "stop"]
