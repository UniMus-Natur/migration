"""OpenSSH LocalForward helper for Prefect flows (bastion → remote MariaDB)."""

from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SshTunnelConfig:
    bastion_host: str
    bastion_user: str
    private_key_path: str
    remote_host: str
    remote_port: int
    local_port: int
    bastion_port: int = 22
    strict_host_key_checking: str = "accept-new"  # yes | no | accept-new
    known_hosts_path: str | None = None
    connect_timeout_s: int = 30
    ready_timeout_s: int = 60


def ssh_tunnel_config_from_env() -> SshTunnelConfig:
    """Build tunnel config from ``TEST_DB_SSH_*`` / ``TEST_DB_*`` / ``TEST_DB_TUNNEL_*``."""
    key_path = (os.environ.get("TEST_DB_SSH_PRIVATE_KEY_PATH") or "").strip()
    known = (os.environ.get("TEST_DB_SSH_KNOWN_HOSTS_PATH") or "").strip() or None
    strict = (os.environ.get("TEST_DB_SSH_STRICT_HOST_KEY_CHECKING") or "accept-new").strip()
    return SshTunnelConfig(
        bastion_host=(os.environ.get("TEST_DB_SSH_HOST") or "").strip(),
        bastion_user=(os.environ.get("TEST_DB_SSH_USER") or "").strip(),
        private_key_path=key_path,
        remote_host=(os.environ.get("TEST_DB_HOST") or "").strip(),
        remote_port=int(os.environ.get("TEST_DB_PORT") or "3306"),
        local_port=int(os.environ.get("TEST_DB_TUNNEL_LOCAL_PORT") or "13306"),
        bastion_port=int(os.environ.get("TEST_DB_SSH_PORT") or "22"),
        strict_host_key_checking=strict,
        known_hosts_path=known,
    )


def validate_ssh_tunnel_config(cfg: SshTunnelConfig) -> None:
    missing: list[str] = []
    if not cfg.bastion_host:
        missing.append("TEST_DB_SSH_HOST")
    if not cfg.bastion_user:
        missing.append("TEST_DB_SSH_USER")
    if not cfg.private_key_path:
        missing.append("TEST_DB_SSH_PRIVATE_KEY_PATH")
    if not cfg.remote_host:
        missing.append("TEST_DB_HOST")
    if missing:
        raise RuntimeError(f"SSH tunnel config incomplete; set: {', '.join(missing)}")
    if not os.path.isfile(cfg.private_key_path):
        raise RuntimeError(
            f"SSH private key not found at {cfg.private_key_path!r} "
            "(mount prefect.devWorker.sshKeySecret on the worker)"
        )
    mode = os.stat(cfg.private_key_path).st_mode & 0o777
    if mode & 0o077:
        logger.warning(
            "SSH private key %s mode is %o (prefer 0400/0600)",
            cfg.private_key_path,
            mode,
        )
    if shutil.which("ssh") is None:
        raise RuntimeError(
            "openssh-client not found on PATH (rebuild migration image with openssh-client)"
        )


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def _build_ssh_command(cfg: SshTunnelConfig) -> list[str]:
    # Forward local → remote as seen from the bastion.
    forward = f"127.0.0.1:{cfg.local_port}:{cfg.remote_host}:{cfg.remote_port}"
    cmd = [
        "ssh",
        "-N",
        "-o",
        "BatchMode=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        f"ConnectTimeout={cfg.connect_timeout_s}",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        f"StrictHostKeyChecking={cfg.strict_host_key_checking}",
    ]
    if cfg.known_hosts_path:
        cmd.extend(["-o", f"UserKnownHostsFile={cfg.known_hosts_path}"])
    cmd.extend(
        [
            "-i",
            cfg.private_key_path,
            "-p",
            str(cfg.bastion_port),
            "-L",
            forward,
            f"{cfg.bastion_user}@{cfg.bastion_host}",
        ]
    )
    return cmd


@contextmanager
def ssh_local_forward(cfg: SshTunnelConfig) -> Iterator[SshTunnelConfig]:
    """Context manager: start ``ssh -L``, wait until local port accepts connections, then stop."""
    validate_ssh_tunnel_config(cfg)
    if _port_open("127.0.0.1", cfg.local_port):
        raise RuntimeError(
            f"Local port 127.0.0.1:{cfg.local_port} already in use; "
            "set TEST_DB_TUNNEL_LOCAL_PORT to a free port"
        )

    cmd = _build_ssh_command(cfg)
    logger.info(
        "Starting SSH LocalForward 127.0.0.1:%s → %s:%s via %s@%s:%s",
        cfg.local_port,
        cfg.remote_host,
        cfg.remote_port,
        cfg.bastion_user,
        cfg.bastion_host,
        cfg.bastion_port,
    )
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + max(5, int(cfg.ready_timeout_s))
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                err = (proc.stderr.read() if proc.stderr else "") or ""
                raise RuntimeError(
                    f"SSH tunnel exited early (code={proc.returncode}): {err.strip()[:2000]}"
                )
            if _port_open("127.0.0.1", cfg.local_port):
                logger.info("SSH tunnel ready on 127.0.0.1:%s", cfg.local_port)
                yield cfg
                return
            time.sleep(0.25)
        err = ""
        if proc.poll() is not None and proc.stderr:
            err = proc.stderr.read() or ""
        raise RuntimeError(
            f"SSH tunnel did not become ready on 127.0.0.1:{cfg.local_port} "
            f"within {cfg.ready_timeout_s}s{(': ' + err.strip()[:1000]) if err else ''}"
        )
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        # Drain stderr for diagnostics if failed earlier
        if proc.stderr:
            try:
                leftover = proc.stderr.read()
                if leftover and proc.returncode not in (0, None, -15, -9):
                    logger.warning("SSH tunnel stderr: %s", leftover.strip()[:2000])
            except Exception:  # noqa: BLE001
                pass
