"""
Tests for ContainerSecurityManager, gVisor Syscall Interception, and Network Egress Filtering.
"""

import os
import socket
import pytest
from pathlib import Path

from backend.services.container_security import (
    ResourceLimits,
    EgressSecurityPolicy,
    SyscallInterceptionEngine,
    NetworkEgressEnforcer,
    ContainerSecurityManager,
    install_socket_egress_filter,
    uninstall_socket_egress_filter,
    BLOCKED_METADATA_HOSTNAMES,
)


def test_resource_limits_defaults():
    limits = ResourceLimits()
    assert limits.memory_limit == "2g"
    assert limits.memory_swap == "2g"
    assert limits.cpu_quota == 2.0
    assert limits.pids_limit == 256
    assert limits.read_only_rootfs is True
    assert limits.no_new_privileges is True
    assert "ALL" in limits.drop_capabilities

    data = limits.to_dict()
    assert data["memory_limit"] == "2g"
    assert data["pids_limit"] == 256


def test_network_egress_blocks_cloud_imds():
    """Asserts that 169.254.169.254 (AWS/GCP/Azure IMDS) is strictly blocked."""
    blocked, reason = NetworkEgressEnforcer.is_ip_blocked("169.254.169.254")
    assert blocked is True
    assert "Cloud Metadata" in reason or "169.254" in reason


def test_network_egress_blocks_rfc1918_private_subnets():
    """Asserts that RFC 1918 private subnets are blocked."""
    # Class A (10.0.0.0/8)
    blocked, reason = NetworkEgressEnforcer.is_ip_blocked("10.0.4.12")
    assert blocked is True
    assert "Private" in reason or "10.0.0.0/8" in reason

    # Class B (172.16.0.0/12)
    blocked, reason = NetworkEgressEnforcer.is_ip_blocked("172.20.10.5")
    assert blocked is True

    # Class C (192.168.0.0/16)
    blocked, reason = NetworkEgressEnforcer.is_ip_blocked("192.168.1.100")
    assert blocked is True

    # Link-Local (169.254.0.0/16)
    blocked, reason = NetworkEgressEnforcer.is_ip_blocked("169.254.10.20")
    assert blocked is True

    # Loopback (127.0.0.0/8)
    blocked, reason = NetworkEgressEnforcer.is_ip_blocked("127.0.0.1")
    assert blocked is True


def test_network_egress_allows_public_internet_ips():
    """Asserts that valid public IPs (Google DNS, Cloudflare, GitHub) are allowed."""
    blocked, _ = NetworkEgressEnforcer.is_ip_blocked("8.8.8.8")
    assert blocked is False

    blocked, _ = NetworkEgressEnforcer.is_ip_blocked("1.1.1.1")
    assert blocked is False

    blocked, _ = NetworkEgressEnforcer.is_ip_blocked("140.82.121.3")  # GitHub public IP
    assert blocked is False


def test_destination_and_url_validation():
    """Tests URL validation against metadata theft and internal addresses."""
    # Metadata URL
    allowed, reason = NetworkEgressEnforcer.validate_url("http://169.254.169.254/latest/meta-data/", resolve_dns=False)
    assert allowed is False
    assert "Prohibited" in reason or "169.254" in reason or "Cloud Metadata" in reason

    # Metadata hostname
    allowed, reason = NetworkEgressEnforcer.validate_url("http://metadata.google.internal/computeMetadata/v1/", resolve_dns=False)
    assert allowed is False
    assert "metadata" in reason.lower()

    # Private internal API
    allowed, reason = NetworkEgressEnforcer.validate_url("http://10.20.30.40:5000/api", resolve_dns=False)
    assert allowed is False

    # Valid public URL
    allowed, _ = NetworkEgressEnforcer.validate_url("https://pypi.org/simple", resolve_dns=False)
    assert allowed is True


def test_iptables_firewall_rule_generation():
    """Tests generation of Linux kernel iptables rules."""
    rules = NetworkEgressEnforcer.generate_iptables_rules("docker0")
    joined = "\n".join(rules)
    assert "169.254.169.254/32 -j DROP" in joined
    assert "10.0.0.0/8 -j DROP" in joined
    assert "172.16.0.0/12 -j DROP" in joined
    assert "192.168.0.0/16 -j DROP" in joined
    assert "--dport 53 -j ACCEPT" in joined
    assert "--dport 443 -j ACCEPT" in joined


def test_nftables_firewall_rule_generation():
    """Tests generation of atomic nftables rules."""
    nft = NetworkEgressEnforcer.generate_nftables_rules()
    assert "ip daddr 169.254.169.254 drop" in nft
    assert "ip daddr 10.0.0.0/8 drop" in nft
    assert "ip daddr 192.168.0.0/16 drop" in nft


def test_container_security_manager_docker_args(tmp_path):
    """Tests generation of secure production docker run argument vector."""
    ws_dir = str(tmp_path / "sandbox")
    os.makedirs(ws_dir, exist_ok=True)

    mgr = ContainerSecurityManager(force_runtime="runsc")
    assert mgr.is_gvisor_active is True
    assert mgr.runtime == "runsc"

    args = mgr.build_docker_run_args(
        container_name="kobits-vm-test-123",
        image="python:3.12-slim",
        host_workspace_dir=ws_dir,
        environment_vars={"TEST_ENV": "1"},
    )

    assert "--runtime" in args
    assert "runsc" in args
    assert "--memory" in args
    assert "2g" in args
    assert "--cpus" in args
    assert "2.0" in args
    assert "--pids-limit" in args
    assert "256" in args
    assert "--security-opt" in args
    assert "no-new-privileges:true" in args
    assert "--read-only" in args
    assert "--tmpfs" in args
    assert "-e" in args
    assert "TEST_ENV=1" in args
    assert "python:3.12-slim" in args


def test_security_audit_report():
    """Tests audit report generation for security telemetry."""
    mgr = ContainerSecurityManager(force_runtime="runsc")
    report = mgr.audit_security_posture()
    assert report["selected_runtime"] == "runsc"
    assert report["is_gvisor_active"] is True
    assert report["cloud_metadata_blocked"] is True
    assert report["rfc1918_subnets_blocked"] is True
    assert report["cgroup_limits"]["pids_limit"] == 256


def test_in_process_socket_egress_filter():
    """Tests defense-in-depth socket hook blocking metadata requests."""
    install_socket_egress_filter()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with pytest.raises(PermissionError) as exc_info:
            s.connect(("169.254.169.254", 80))
        assert "blocked" in str(exc_info.value).lower()
        s.close()

        # Test RFC 1918 IP block
        s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with pytest.raises(PermissionError) as exc_info2:
            s2.connect(("10.0.0.1", 80))
        assert "blocked" in str(exc_info2.value).lower()
        s2.close()
    finally:
        uninstall_socket_egress_filter()
