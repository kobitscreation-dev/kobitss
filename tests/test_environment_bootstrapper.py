import os
import shutil
import pytest
import platform
import subprocess
from backend.services.environment_bootstrapper import EnvironmentBootstrapper

def has_docker():
    return shutil.which("docker") is not None

@pytest.fixture
def dummy_sandbox(tmpdir):
    sandbox_dir = str(tmpdir.mkdir("sandbox"))
    # Write dummy requirements
    with open(os.path.join(sandbox_dir, "requirements.txt"), "w") as f:
        f.write("requests==2.31.0\n")
    return sandbox_dir

@pytest.fixture
def node_sandbox(tmpdir):
    sandbox_dir = str(tmpdir.mkdir("node_sandbox"))
    # Write dummy package.json
    with open(os.path.join(sandbox_dir, "package.json"), "w") as f:
        f.write('{"dependencies": {"lodash": "4.17.21"}}\n')
    return sandbox_dir

@pytest.fixture
def service_sandbox(tmpdir):
    sandbox_dir = str(tmpdir.mkdir("service_sandbox"))
    with open(os.path.join(sandbox_dir, "docker-compose.yml"), "w") as f:
        f.write('version: "3"\nservices:\n  db:\n    image: postgres:15\n')
    return sandbox_dir

def test_fingerprint_generation(dummy_sandbox, node_sandbox, service_sandbox):
    fp_py = EnvironmentBootstrapper._fingerprint_environment(dummy_sandbox)
    fp_node = EnvironmentBootstrapper._fingerprint_environment(node_sandbox)
    
    assert fp_py != fp_node
    
    with open(os.path.join(dummy_sandbox, "requirements.txt"), "a") as f:
        f.write("pytest\n")
    assert fp_py != EnvironmentBootstrapper._fingerprint_environment(dummy_sandbox)

def test_dockerfile_generation(dummy_sandbox):
    exec_cmd, use_shell, script_path = EnvironmentBootstrapper.prepare_execution(dummy_sandbox, "pytest", has_docker=False)
    assert os.path.exists(script_path)
    with open(script_path, "r") as f:
        content = f.read()
    if platform.system() == "Windows":
        assert "venv" in content
    else:
        assert "venv" in content

@pytest.mark.skipif(not has_docker(), reason="Requires Docker")
def test_docker_execution_isolation(dummy_sandbox):
    # Test strict isolation for simple python
    exec_cmd, use_shell, script_path = EnvironmentBootstrapper.prepare_execution(dummy_sandbox, "pytest", has_docker=True)
    assert "--network" in exec_cmd
    net_index = exec_cmd.index("--network")
    assert exec_cmd[net_index + 1] == "none"
    
    proc = subprocess.run(exec_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert not os.path.exists(os.path.join(dummy_sandbox, ".venv")), "Host machine must not be polluted"

@pytest.mark.skipif(not has_docker(), reason="Requires Docker")
def test_docker_node_isolation(node_sandbox):
    # Test Node.js isolation
    exec_cmd, use_shell, script_path = EnvironmentBootstrapper.prepare_execution(node_sandbox, "npm test", has_docker=True)
    proc = subprocess.run(exec_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert not os.path.exists(os.path.join(node_sandbox, "node_modules")), "Host machine must not be polluted"

@pytest.mark.skipif(not has_docker(), reason="Requires Docker")
def test_docker_service_network(service_sandbox):
    # Test that docker-compose gives bridge network
    exec_cmd, use_shell, script_path = EnvironmentBootstrapper.prepare_execution(service_sandbox, "pytest", has_docker=True)
    assert "--network" in exec_cmd
    net_index = exec_cmd.index("--network")
    assert exec_cmd[net_index + 1] == "bridge"
