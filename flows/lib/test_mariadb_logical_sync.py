"""Unit tests for MariaDB logical sync helpers (no live DB)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from flows.lib.mariadb_logical_sync import (
    CompatibilityReport,
    MariaDBEndpoint,
    SpVersionInfo,
    assert_compatible,
    assert_not_same_endpoint,
)
from flows.lib.ssh_tunnel import SshTunnelConfig, _build_ssh_command, validate_ssh_tunnel_config


class AssertNotSameEndpointTests(unittest.TestCase):
    def test_rejects_identical_identity(self) -> None:
        src = MariaDBEndpoint("db.example", 3306, "specify", "u", "p", label="s")
        with self.assertRaises(RuntimeError):
            assert_not_same_endpoint(
                src,
                test_remote_host="db.example",
                test_remote_port=3306,
                test_db_name="specify",
            )

    def test_allows_different_host(self) -> None:
        src = MariaDBEndpoint("specify7-mariadb", 3306, "specify", "u", "p")
        assert_not_same_endpoint(
            src,
            test_remote_host="test-db.example",
            test_remote_port=3306,
            test_db_name="specify",
        )


class CompatibilityGateTests(unittest.TestCase):
    def _report(self, *, versions: bool, schemas: bool) -> CompatibilityReport:
        v = SpVersionInfo("7", "2.10", "1.0", 1)
        v2 = v if versions else SpVersionInfo("7", "2.11", "1.0", 1)
        return CompatibilityReport(
            source_version=v,
            target_version=v2,
            source_schema_fingerprint="aaa",
            target_schema_fingerprint="aaa" if schemas else "bbb",
            source_table_count=1,
            target_table_count=1,
            source_approx_data_bytes=1,
            target_approx_data_bytes=1,
            versions_match=versions,
            schemas_match=schemas,
        )

    def test_compatible_when_fingerprints_match(self) -> None:
        # spversion mismatch is ignored; fingerprint is the gate.
        assert_compatible(self._report(versions=False, schemas=True))

    def test_empty_target_version_ok_if_fingerprint_matches(self) -> None:
        empty = SpVersionInfo("", "", "", 0)
        report = CompatibilityReport(
            source_version=SpVersionInfo("6.8.03", "2.10", "", 1),
            target_version=empty,
            source_schema_fingerprint="aaa",
            target_schema_fingerprint="aaa",
            source_table_count=1,
            target_table_count=1,
            source_approx_data_bytes=1,
            target_approx_data_bytes=0,
            versions_match=False,
            schemas_match=True,
        )
        assert_compatible(report)

    def test_schema_mismatch_fails(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            assert_compatible(self._report(versions=True, schemas=False))
        self.assertIn("schema fingerprint mismatch", str(ctx.exception))

    def test_schema_mismatch_force_bypasses(self) -> None:
        assert_compatible(self._report(versions=True, schemas=False), force=True)


class SshCommandTests(unittest.TestCase):
    def test_build_includes_local_forward(self) -> None:
        cfg = SshTunnelConfig(
            bastion_host="bastion.example",
            bastion_user="tunnel",
            private_key_path="/tmp/key",
            remote_host="db.internal",
            remote_port=3306,
            local_port=13306,
            known_hosts_path="/tmp/known_hosts",
        )
        cmd = _build_ssh_command(cfg)
        self.assertIn("ssh", cmd[0])
        self.assertIn("127.0.0.1:13306:db.internal:3306", cmd)
        self.assertIn("UserKnownHostsFile=/tmp/known_hosts", " ".join(cmd))
        self.assertIn("tunnel@bastion.example", cmd)

    def test_validate_requires_key_file(self) -> None:
        cfg = SshTunnelConfig(
            bastion_host="b",
            bastion_user="u",
            private_key_path="/no/such/key",
            remote_host="db",
            remote_port=3306,
            local_port=13306,
        )
        with patch("flows.lib.ssh_tunnel.shutil.which", return_value="/usr/bin/ssh"):
            with self.assertRaises(RuntimeError) as ctx:
                validate_ssh_tunnel_config(cfg)
        self.assertIn("private key not found", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
