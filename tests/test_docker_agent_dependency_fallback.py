from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INIT_SH = (REPO_ROOT / "docker_init.bash").read_text(encoding="utf-8")


def test_docker_init_agent_dependency_failure_is_non_fatal():
    assert 'error_exit "Failed to install hermes-agent' not in INIT_SH
    assert "!! WARNING: Failed to install hermes-agent's requirements." in INIT_SH
    assert "!! The WebUI will start with reduced functionality" in INIT_SH
