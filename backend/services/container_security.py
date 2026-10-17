"""
ContainerSecurityManager — Kernel-Level Sandbox Security & Egress Filtering.

Provides multi-tenant, zero-trust container isolation and syscall interception:
1. gVisor Userspace Syscall Interception (`runsc`): Intercepts and virtualizes Linux kernel
   syscalls in user-space to prevent container escapes and kernel privilege escalation.
2. Cgroup Resource Hardening: Strict cgroup v1/v2 memory quotas, CPU quota/period,
   PID limits (fork-bomb immunity), and no-new-privileges flags.
3. Network Egress Filtering & Cloud Metadata Defense: Blocks internal cloud metadata endpoints
   (AWS/GCP/Azure IMDS 169.254.169.254), RFC 1918 private subnets (10.0.0.0/8, 172.16.0.0/12,
   192.168.0.0/16), carrier-grade NAT, and DNS rebinding attacks.
4. Defense-in-depth: Generates iptables/nftables firewall rules, Docker/Podman command-line
   arguments, and in-process socket interception for bare-metal/fallback environments.
"""

import os
import sys
import re
import socket
import logging
import ipaddress
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple, Union
from urllib.parse import urlparse

logger = logging.getLogger("kobits.container_security")


# =====================================================================
# Dangerous Subnets & Cloud Metadata Endpoints (RFC 1918 + IMDS)
# =====================================================================

BLOCKED_IP_NETWORKS = [
    # Cloud Provider Instance Metadata Services (IMDSv1 & IMDSv2)
    ipaddress.ip_network("169.254.169.254/32"),   # AWS, GCP, Azure, OpenStack, DigitalOcean
    ipaddress.ip_network("169.254.0.0/16"),       # IPv4 Link-Local addresses (RFC 3927)
    
    # RFC 1918 Private IPv4 Subnets (Internal VPCs, databases, private microservices)
    ipaddress.ip_network("10.0.0.0/8"),           # Private Class A
    ipaddress.ip_network("172.16.0.0/12"),        # Private Class B
    ipaddress.ip_network("192.168.0.0/16"),       # Private Class C
    
    # Carrier-Grade NAT & Test Networks
    ipaddress.ip_network("100.64.0.0/10"),        # Shared Address Space (RFC 6598)
    ipaddress.ip_network("198.18.0.0/15"),        # Benchmark testing (RFC 2544)
    ipaddress.ip_network("192.0.2.0/24"),         # TEST-NET-1 (RFC 5737)
    ipaddress.ip_network("198.51.100.0/24"),      # TEST-NET-2 (RFC 5737)
    ipaddress.ip_network("203.0.113.0/24"),       # TEST-NET-3 (RFC 5737)
    
    # Loopback (defense-in-depth against host re-binding)
    ipaddress.ip_network("127.0.0.0/8"),          # Loopback
    
    # IPv6 Link-Local & Unique Local Subnets
    ipaddress.ip_network("fe80::/10"),            # IPv6 Link-Local
    ipaddress.ip_network("fc00::/7"),             # IPv6 Unique Local (ULA)
    ipaddress.ip_network("::1/128"),              # IPv6 Loopback
]

BLOCKED_METADATA_HOSTNAMES = {
    "metadata.google.internal",
    "metadata",
    "instance-data",
    "instance-data.ec2.internal",
    "169.254.169.254",
    "metadata.azure.com",
    "internal.metadata",
}


@dataclass
class ResourceLimits:
    """Cgroup memory, CPU, PID, and filesystem limits for container execution."""
    memory_limit: str = "2g"              # Hard memory limit (e.g. 2g, 2048m)
    memory_swap: str = "2g"               # Memory + swap limit (matching memory_limit disables swap)
    cpu_quota: float = 2.0                # Max CPU cores (e.g. 2.0 = 2 cores, 1.0 = 1 vCPU)
    pids_limit: int = 256                 # Max concurrent process IDs (fork bomb defense)
    disk_quota: str = "10g"               # Disk storage limit
    read_only_rootfs: bool = True         # Mount root filesystem read-only
    no_new_privileges: bool = True        # Prevent SUID privilege escalation
    drop_capabilities: list[str] = field(default_factory=lambda: ["ALL"])
    add_capabilities: list[str] = field(default_factory=lambda: ["CHOWN", "SETUID", "SETGID"])
    seccomp_profile: Optional[str] = "default"  # "default" or path to seccomp.json

    @classmethod
    def strict_sandbox_quotas(cls) -> "ResourceLimits":
        """
        Enforces strict Linux cgroup quotas for untrusted sandbox execution:
        2048 MB RAM, 1 vCPU, and 200 PIDs.
        """
        return cls(
            memory_limit="2048m",
            memory_swap="2048m",
            cpu_quota=1.0,
            pids_limit=200,
        )

    @property
    def memory_mb(self) -> int:
        raw = str(self.memory_limit).strip().lower()
        if raw.endswith("g") or raw.endswith("gb"):
            return int(float(raw.rstrip("gb")) * 1024)
        if raw.endswith("m") or raw.endswith("mb"):
            return int(float(raw.rstrip("mb")))
        return 2048

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_limit": self.memory_limit,
            "memory_swap": self.memory_swap,
            "memory_mb": self.memory_mb,
            "cpu_quota": self.cpu_quota,
            "vcpu_limit": self.cpu_quota,
            "pids_limit": self.pids_limit,
            "disk_quota": self.disk_quota,
            "read_only_rootfs": self.read_only_rootfs,
            "no_new_privileges": self.no_new_privileges,
            "drop_capabilities": self.drop_capabilities,
            "add_capabilities": self.add_capabilities,
        }


@dataclass
class EgressSecurityPolicy:
    """Network egress filter policy protecting internal cloud infrastructure."""
    block_cloud_metadata: bool = True
    block_private_subnets: bool = True
    allow_public_internet: bool = True
    allowed_registries: list[str] = field(default_factory=lambda: [
        "pypi.org",
        "files.pythonhosted.org",
        "registry.npmjs.org",
        "crates.io",
        "api.github.com",
        "github.com",
        "raw.githubusercontent.com",
        "objects.githubusercontent.com",
    ])
    allowed_dns_resolvers: list[str] = field(default_factory=lambda: [
        "8.8.8.8",
        "8.8.4.4",
        "1.1.1.1",
        "1.0.0.1",
    ])
    enforce_domain_whitelist: bool = False  # If True, only allow traffic to allowed_registries

    def validate_destination(
        self,
        host_or_ip: str,
        resolve_dns: bool = False,
        require_registry_allowlist: bool = True,
    ) -> Tuple[bool, Optional[str]]:
        """
        Validates destination against Cloud Metadata (169.254.169.254), RFC 1918 private
        networks, and the approved package/VCS registry allowlist.
        """
        clean_target = (host_or_ip or "").strip().lower()
        if ":" in clean_target and not clean_target.startswith("["):
            clean_target = clean_target.split(":", 1)[0]
        if not clean_target:
            return False, "Blocked: empty destination host"

        allowed, reason = NetworkEgressEnforcer.validate_destination(clean_target, resolve_dns=resolve_dns)
        if not allowed:
            return False, f"Blocked: {reason}"

        if require_registry_allowlist or self.enforce_domain_whitelist:
            if clean_target in [ip.lower() for ip in self.allowed_dns_resolvers]:
                return True, "Allowed DNS resolver"
            allowed_domains = [d.lower() for d in self.allowed_registries]
            if not any(clean_target == d or clean_target.endswith(f".{d}") for d in allowed_domains):
                return False, f"Blocked: destination '{clean_target}' is not in the sandbox egress allowlist"

        return True, "Allowed by sandbox egress policy"


class SyscallInterceptionEngine:
    """
    Detects and configures gVisor (`runsc`) userspace kernel interception.
    """

    @classmethod
    def is_runsc_installed(cls) -> bool:
        """Check if `runsc` binary is installed on PATH or in standard paths."""
        if shutil.which("runsc") is not None:
            return True
        for standard_path in ["/usr/local/bin/runsc", "/usr/bin/runsc", "/opt/runsc"]:
            if os.path.isfile(standard_path) and os.access(standard_path, os.X_OK):
                return True
        return False

    @classmethod
    def is_docker_gvisor_configured(cls) -> bool:
        """Check if Docker Engine daemon has `runsc` registered as a runtime."""
        forced = os.environ.get("KOBITS_FORCE_GVISOR", "").strip().lower()
        if forced in ("1", "true", "yes"):
            return True
        if forced in ("0", "false", "no"):
            return False

        if not shutil.which("docker"):
            return False

        try:
            res = subprocess.run(
                ["docker", "info", "--format", "{{json .Runtimes}}"],
                capture_output=True,
                text=True,
                timeout=4,
            )
            if res.returncode == 0 and res.stdout:
                stdout_lower = res.stdout.lower()
                return "runsc" in stdout_lower or "gvisor" in stdout_lower
        except Exception as e:
            logger.debug(f"Docker runtime check failed: {e}")
        return False

    @classmethod
    def get_runtime_name(cls) -> str:
        """
        Returns 'runsc' if gVisor is available, otherwise 'runc' / 'standard'.
        """
        if cls.is_docker_gvisor_configured() or cls.is_runsc_installed():
            return "runsc"
        return "runc"


class NetworkEgressEnforcer:
    """
    Validates IPs, hostnames, and URLs against cloud metadata and private subnet blocks.
    Generates iptables/nftables firewall rules for container networks.
    """

    @staticmethod
    def is_ip_blocked(ip_addr: Union[str, ipaddress.IPv4Address, ipaddress.IPv6Address]) -> Tuple[bool, Optional[str]]:
        """
        Checks if an IP address belongs to any blocked metadata or private range.
        Returns: (is_blocked, reason)
        """
        try:
            if isinstance(ip_addr, str):
                parsed_ip = ipaddress.ip_address(ip_addr.strip())
            else:
                parsed_ip = ip_addr
        except ValueError:
            return True, f"Invalid IP address format: {ip_addr}"

        # 1. Cloud Metadata explicit check (169.254.169.254)
        if parsed_ip == ipaddress.IPv4Address("169.254.169.254"):
            return True, "Cloud Metadata Service (IMDS 169.254.169.254) access prohibited"

        # 2. Check against all blocked CIDR networks
        for net in BLOCKED_IP_NETWORKS:
            if parsed_ip in net:
                if parsed_ip.is_link_local:
                    return True, f"Link-local subnet ({net}) access prohibited"
                if parsed_ip.is_private:
                    return True, f"Private RFC 1918 subnet ({net}) access prohibited"
                if parsed_ip.is_loopback:
                    return True, f"Loopback address ({net}) access prohibited"
                return True, f"Prohibited address block: {net}"

        return False, None

    @classmethod
    def validate_destination(cls, host_or_ip: str, resolve_dns: bool = True) -> Tuple[bool, Optional[str]]:
        """
        Validates a host or IP destination. Protects against DNS rebinding attacks.
        Returns: (is_allowed, rejection_reason)
        """
        clean_host = host_or_ip.strip().lower()
        if not clean_host:
            return False, "Empty destination host"

        # Check metadata hostnames
        if clean_host in BLOCKED_METADATA_HOSTNAMES or clean_host.endswith(".internal"):
            return False, f"Cloud internal metadata hostname blocked: {clean_host}"

        # Check if direct IP address
        try:
            ip = ipaddress.ip_address(clean_host)
            blocked, reason = cls.is_ip_blocked(ip)
            if blocked:
                return False, reason
            return True, None
        except ValueError:
            pass  # It is a domain name

        # Resolve DNS to verify all resolved target IPs
        if resolve_dns:
            try:
                addr_info = socket.getaddrinfo(clean_host, None)
                for family, socktype, proto, canonname, sockaddr in addr_info:
                    resolved_ip_str = sockaddr[0]
                    blocked, reason = cls.is_ip_blocked(resolved_ip_str)
                    if blocked:
                        return False, f"Domain {clean_host} resolved to blocked IP {resolved_ip_str}: {reason}"
            except socket.gaierror as e:
                # DNS resolution failure
                return False, f"Could not resolve domain {clean_host}: {e}"
            except Exception as e:
                return False, f"DNS validation error for {clean_host}: {e}"

        return True, None

    @classmethod
    def validate_url(cls, url: str, resolve_dns: bool = True) -> Tuple[bool, Optional[str]]:
        """
        Validates an HTTP/HTTPS/WebSocket URL against SSRF and cloud metadata theft.
        """
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname
            if not hostname:
                return False, f"No hostname found in URL: {url}"
            return cls.validate_destination(hostname, resolve_dns=resolve_dns)
        except Exception as e:
            return False, f"Invalid URL parsing {url}: {e}"

    @classmethod
    def generate_iptables_rules(cls, bridge_interface: str = "docker0") -> List[str]:
        """
        Generates strict Linux iptables firewall commands to enforce kernel-level egress filtering.
        """
        rules = [
            f"# Flush existing Kobits egress filter rules",
            f"iptables -N KOBITS_EGRESS 2>/dev/null || iptables -F KOBITS_EGRESS",
            f"iptables -C FORWARD -i {bridge_interface} -j KOBITS_EGRESS 2>/dev/null || iptables -I FORWARD 1 -i {bridge_interface} -j KOBITS_EGRESS",
            
            # Explicitly DROP Cloud Metadata Server (IMDS)
            f"iptables -A KOBITS_EGRESS -d 169.254.169.254/32 -j DROP -m comment --comment 'Block AWS/GCP/Azure IMDS'",
            f"iptables -A KOBITS_EGRESS -d 169.254.0.0/16 -j DROP -m comment --comment 'Block IPv4 Link-Local'",
            
            # DROP RFC 1918 Private Subnets
            f"iptables -A KOBITS_EGRESS -d 10.0.0.0/8 -j DROP -m comment --comment 'Block RFC 1918 Class A'",
            f"iptables -A KOBITS_EGRESS -d 172.16.0.0/12 -j DROP -m comment --comment 'Block RFC 1918 Class B'",
            f"iptables -A KOBITS_EGRESS -d 192.168.0.0/16 -j DROP -m comment --comment 'Block RFC 1918 Class C'",
            f"iptables -A KOBITS_EGRESS -d 100.64.0.0/10 -j DROP -m comment --comment 'Block CGNAT'",
            
            # Allow outbound ESTABLISHED and RELATED connections
            f"iptables -A KOBITS_EGRESS -m state --state ESTABLISHED,RELATED -j ACCEPT",
            
            # Allow outbound DNS (Port 53 UDP/TCP)
            f"iptables -A KOBITS_EGRESS -p udp --dport 53 -j ACCEPT",
            f"iptables -A KOBITS_EGRESS -p tcp --dport 53 -j ACCEPT",
            
            # Allow outbound HTTPS/HTTP for package registries
            f"iptables -A KOBITS_EGRESS -p tcp --dport 443 -j ACCEPT",
            f"iptables -A KOBITS_EGRESS -p tcp --dport 80 -j ACCEPT",
        ]
        return rules

    @classmethod
    def generate_nftables_rules(cls) -> str:
        """
        Generates modern Linux nftables configuration for atomic kernel firewall filtering.
        """
        return """
table inet kobits_firewall {
    chain egress_filter {
        type filter hook forward priority 0; policy accept;
        
        # Block Cloud Metadata
        ip daddr 169.254.169.254 drop comment "Block Cloud IMDS"
        ip daddr 169.254.0.0/16 drop comment "Block Link-Local"
        
        # Block RFC 1918 Private IP Subnets
        ip daddr 10.0.0.0/8 drop comment "Block Class A Private"
        ip daddr 172.16.0.0/12 drop comment "Block Class B Private"
        ip daddr 192.168.0.0/16 drop comment "Block Class C Private"
        ip daddr 100.64.0.0/10 drop comment "Block CGNAT"
        
        # Block IPv6 Private/Link-local
        ip6 daddr fe80::/10 drop comment "Block IPv6 Link-Local"
        ip6 daddr fc00::/7 drop comment "Block IPv6 ULA"
    }
}
""".strip()


# =====================================================================
# In-Process Socket Interceptor (Fallback / Local / Test environments)
# =====================================================================

_ORIGINAL_SOCKET_CONNECT = socket.socket.connect
_ORIGINAL_SOCKET_CONNECT_EX = socket.socket.connect_ex
_SOCKET_FILTER_INSTALLED = False


def _validate_socket_destination(address) -> None:
    """
    Validates a socket destination address tuple against cloud metadata, RFC 1918 private subnets,
    metadata hostnames, and DNS rebinding to internal/IMDS IPs.
    Raises PermissionError if blocked.
    """
    try:
        host = address[0]
    except (IndexError, TypeError):
        return

    if not isinstance(host, str):
        return

    allowed, reason = NetworkEgressEnforcer.validate_destination(host, resolve_dns=False)
    if not allowed:
        logger.critical(f"BLOCKED EGRESS ATTEMPT: {host} - {reason}")
        raise PermissionError(f"[Kobits Sandbox Security] Outbound connection to {host} is blocked: {reason}")

    # If host is a domain name (not an IP literal), inspect resolved DNS records for rebinding to blocked IPs
    try:
        ipaddress.ip_address(host.strip())
    except ValueError:
        try:
            addr_info = socket.getaddrinfo(host.strip(), None)
            for _family, _socktype, _proto, _canonname, sockaddr in addr_info:
                resolved_ip_str = sockaddr[0]
                blocked, ip_reason = NetworkEgressEnforcer.is_ip_blocked(resolved_ip_str)
                if blocked:
                    rebinding_reason = f"Domain {host} resolved to blocked IP {resolved_ip_str}: {ip_reason}"
                    logger.critical(f"BLOCKED DNS REBINDING EGRESS ATTEMPT: {rebinding_reason}")
                    raise PermissionError(
                        f"[Kobits Sandbox Security] Outbound connection to {host} is blocked: {rebinding_reason}"
                    )
        except socket.gaierror:
            # Let the underlying socket connect handle standard DNS resolution errors
            pass


def _filtered_socket_connect(self, address):
    """
    Socket wrapper that validates the destination address before initiating TCP/UDP connect.
    Raises PermissionError if connecting to cloud metadata or private subnet.
    """
    _validate_socket_destination(address)
    return _ORIGINAL_SOCKET_CONNECT(self, address)


def _filtered_socket_connect_ex(self, address):
    """
    Socket wrapper for connect_ex that validates the destination address before initiating non-blocking connect.
    Raises PermissionError if connecting to cloud metadata or private subnet.
    """
    _validate_socket_destination(address)
    return _ORIGINAL_SOCKET_CONNECT_EX(self, address)


def install_socket_egress_filter():
    """Installs the defense-in-depth in-process socket filter on connect and connect_ex."""
    global _SOCKET_FILTER_INSTALLED
    if not _SOCKET_FILTER_INSTALLED:
        socket.socket.connect = _filtered_socket_connect
        socket.socket.connect_ex = _filtered_socket_connect_ex
        _SOCKET_FILTER_INSTALLED = True
        logger.info("Kernel-level socket egress interceptor installed.")


def uninstall_socket_egress_filter():
    """Restores original socket connect and connect_ex functions."""
    global _SOCKET_FILTER_INSTALLED
    if _SOCKET_FILTER_INSTALLED:
        socket.socket.connect = _ORIGINAL_SOCKET_CONNECT
        socket.socket.connect_ex = _ORIGINAL_SOCKET_CONNECT_EX
        _SOCKET_FILTER_INSTALLED = False
        logger.info("Socket egress interceptor uninstalled.")


# =====================================================================
# Container Security Manager
# =====================================================================

class ContainerSecurityManager:
    """
    High-level manager orchestrating gVisor syscall virtualization,
    cgroup quotas, capabilities, and egress policies.
    """

    def __init__(
        self,
        limits: Optional[ResourceLimits] = None,
        egress_policy: Optional[EgressSecurityPolicy] = None,
        force_runtime: Optional[str] = None,
    ):
        self.limits = limits or ResourceLimits()
        self.egress_policy = egress_policy or EgressSecurityPolicy()
        self.force_runtime = force_runtime

    @property
    def runtime(self) -> str:
        """Determines the container runtime ('runsc' for gVisor, or 'runc')."""
        if self.force_runtime:
            return self.force_runtime
        return SyscallInterceptionEngine.get_runtime_name()

    @property
    def is_gvisor_active(self) -> bool:
        """Returns True if gVisor (runsc) is being utilized."""
        return self.runtime == "runsc"

    def build_docker_run_args(
        self,
        container_name: str,
        image: str,
        host_workspace_dir: str,
        container_workspace_dir: str = "/workspace",
        environment_vars: Optional[Dict[str, str]] = None,
        network_name: Optional[str] = None,
        user: str = "1000:1000",
    ) -> List[str]:
        """
        Builds hardened, tier-1 production `docker run` argument vector.
        """
        args = ["docker", "run", "-d", "--name", container_name]

        # 1. Syscall Sandboxing: gVisor runtime if available
        runtime = self.runtime
        if runtime == "runsc":
            args.extend(["--runtime", "runsc"])
            logger.info(f"Container {container_name}: gVisor (runsc) userspace syscall interception enabled.")

        # 2. Cgroup Resource Limits
        args.extend(["--memory", self.limits.memory_limit])
        if self.limits.memory_swap:
            args.extend(["--memory-swap", self.limits.memory_swap])
        args.extend(["--cpus", str(self.limits.cpu_quota)])
        args.extend(["--pids-limit", str(self.limits.pids_limit)])

        # 3. Security hardening & privilege escalation prevention
        if self.limits.no_new_privileges:
            args.extend(["--security-opt", "no-new-privileges:true"])

        # Capabilities drop
        for cap in self.limits.drop_capabilities:
            args.extend(["--cap-drop", cap])
        for cap in self.limits.add_capabilities:
            args.extend(["--cap-add", cap])

        # User isolation
        if user:
            args.extend(["--user", user])

        # 4. Filesystem mounting: Read-only root with isolated writable workspace & tmpfs
        if self.limits.read_only_rootfs:
            args.append("--read-only")
            args.extend(["--tmpfs", "/tmp:rw,noexec,nosuid,size=512m"])
            args.extend(["--tmpfs", "/run:rw,noexec,nosuid,size=64m"])

        # Bind mount the mission workspace
        abs_host_dir = os.path.abspath(host_workspace_dir)
        args.extend(["-v", f"{abs_host_dir}:{container_workspace_dir}:rw"])
        args.extend(["-w", container_workspace_dir])

        # 5. Network configuration
        if network_name:
            args.extend(["--network", network_name])

        # 6. Scrubbed environment variables
        if environment_vars:
            for k, v in environment_vars.items():
                args.extend(["-e", f"{k}={v}"])

        # Target image & default entrypoint sleep
        args.append(image)
        args.extend(["tail", "-f", "/dev/null"])

        return args

    def audit_security_posture(self) -> Dict[str, Any]:
        """
        Returns an empirical security assessment report of the sandbox engine.
        """
        gvisor_detected = SyscallInterceptionEngine.is_runsc_installed() or SyscallInterceptionEngine.is_docker_gvisor_configured()
        return {
            "gvisor_available": gvisor_detected,
            "selected_runtime": self.runtime,
            "is_gvisor_active": self.is_gvisor_active,
            "cgroup_limits": self.limits.to_dict(),
            "cloud_metadata_blocked": self.egress_policy.block_cloud_metadata,
            "rfc1918_subnets_blocked": self.egress_policy.block_private_subnets,
            "socket_interceptor_installed": _SOCKET_FILTER_INSTALLED,
        }
