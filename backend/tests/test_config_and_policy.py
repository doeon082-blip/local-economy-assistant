from __future__ import annotations

from pathlib import Path
import os
import unittest
from unittest.mock import patch

from economy_assistant import _find_node_executable, config
from economy_assistant.security_policy import ActionRequest, SecurityPolicy, require_allowed


class ConfigAndPolicyTests(unittest.TestCase):
    def test_ollama_url_blocks_remote_by_default(self) -> None:
        with patch.dict(os.environ, {"OLLAMA_URL": "http://ollama:11434", "ALLOW_REMOTE_OLLAMA": "false"}, clear=False):
            with self.assertRaises(ValueError):
                config.ollama_url()

    def test_ollama_url_allows_remote_when_enabled_without_credentials(self) -> None:
        with patch.dict(os.environ, {"OLLAMA_URL": "http://ollama:11434", "ALLOW_REMOTE_OLLAMA": "true"}, clear=False):
            self.assertEqual(config.ollama_url(), "http://ollama:11434")

        with patch.dict(
            os.environ,
            {"OLLAMA_URL": "http://user:pass@ollama:11434", "ALLOW_REMOTE_OLLAMA": "true"},
            clear=False,
        ):
            with self.assertRaises(ValueError):
                config.ollama_url()

    def test_app_host_and_port_overrides(self) -> None:
        with patch.dict(os.environ, {"ALLOW_REMOTE_ACCESS": "true", "PORT": "9000"}, clear=False):
            self.assertEqual(config.app_host(), "0.0.0.0")
            self.assertEqual(config.app_port(), 9000)

        with patch.dict(os.environ, {"PORT": "99999"}, clear=False):
            with self.assertRaises(ValueError):
                config.app_port()

    def test_security_policy_fail_closed(self) -> None:
        policy = SecurityPolicy(
            allowed_tools=frozenset({"ecos_sync"}),
            approval_required_tools=frozenset({"ecos_sync"}),
            allow_external_sends=True,
            allow_data_changes=True,
        )
        with self.assertRaises(PermissionError):
            require_allowed(ActionRequest("unknown_tool"), policy, trusted_user_approved=True)
        with self.assertRaises(PermissionError):
            require_allowed(
                ActionRequest("ecos_sync", sends_data_outside=True, changes_data=True),
                policy,
                trusted_user_approved=False,
            )
        require_allowed(
            ActionRequest("ecos_sync", sends_data_outside=True, changes_data=True),
            policy,
            trusted_user_approved=True,
        )

    def test_find_node_executable_returns_valid_path(self) -> None:
        node_path = _find_node_executable(Path("frontend"))
        self.assertIsNotNone(node_path)
        self.assertTrue(Path(node_path).is_file())


if __name__ == "__main__":
    unittest.main()
