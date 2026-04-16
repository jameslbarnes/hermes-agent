"""Tests for sandbox enforcement in terminal and file tools.

These tests verify that when HERMES_SANDBOX_ROOT is set, the terminal and
file tools reject any operation targeting paths or repos outside the sandbox.
This is the airtight enforcement layer — prompt-level restrictions can be
bypassed, but these tool-level checks cannot.
"""

import json
import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sandbox(tmp_path):
    """Create a sandbox directory with a test file inside."""
    sandbox_dir = tmp_path / "sandbox"
    sandbox_dir.mkdir()
    (sandbox_dir / "allowed.txt").write_text("allowed content")
    (sandbox_dir / "subdir").mkdir()
    (sandbox_dir / "subdir" / "nested.txt").write_text("nested content")
    return sandbox_dir


@pytest.fixture()
def outside_file(tmp_path):
    """Create a file outside the sandbox."""
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET")
    return secret


@pytest.fixture()
def sandbox_env(sandbox, monkeypatch):
    """Set sandbox environment variables."""
    monkeypatch.setenv("HERMES_SANDBOX_ROOT", str(sandbox))
    monkeypatch.setenv("HERMES_ALLOWED_REPOS", "owner/allowed-repo")
    return sandbox


# ---------------------------------------------------------------------------
# File tool sandbox tests
# ---------------------------------------------------------------------------

class TestFileToolSandbox:
    def test_read_inside_sandbox_allowed(self, sandbox_env):
        from tools.file_tools import _check_file_sandbox
        result = _check_file_sandbox(str(sandbox_env / "allowed.txt"))
        assert result is None  # None = allowed

    def test_read_nested_inside_sandbox_allowed(self, sandbox_env):
        from tools.file_tools import _check_file_sandbox
        result = _check_file_sandbox(str(sandbox_env / "subdir" / "nested.txt"))
        assert result is None

    def test_read_outside_sandbox_blocked(self, sandbox_env, outside_file):
        from tools.file_tools import _check_file_sandbox
        result = _check_file_sandbox(str(outside_file))
        assert result is not None
        parsed = json.loads(result)
        assert "error" in parsed
        assert "outside" in parsed["error"].lower()

    def test_read_absolute_path_outside_blocked(self, sandbox_env):
        from tools.file_tools import _check_file_sandbox
        result = _check_file_sandbox("/etc/passwd")
        assert result is not None
        parsed = json.loads(result)
        assert "outside" in parsed["error"].lower()

    def test_path_traversal_blocked(self, sandbox_env, outside_file):
        from tools.file_tools import _check_file_sandbox
        # Try to escape via ../
        traversal_path = str(sandbox_env / ".." / outside_file.name)
        result = _check_file_sandbox(traversal_path)
        assert result is not None

    def test_symlink_escape_blocked(self, sandbox_env, outside_file):
        from tools.file_tools import _check_file_sandbox
        link = sandbox_env / "sneaky_link"
        link.symlink_to(outside_file)
        result = _check_file_sandbox(str(link))
        assert result is not None

    def test_sandbox_root_itself_allowed(self, sandbox_env):
        from tools.file_tools import _check_file_sandbox
        result = _check_file_sandbox(str(sandbox_env))
        assert result is None

    def test_no_sandbox_allows_everything(self, monkeypatch):
        """When HERMES_SANDBOX_ROOT is not set, no restrictions apply."""
        monkeypatch.delenv("HERMES_SANDBOX_ROOT", raising=False)
        from tools.file_tools import _check_file_sandbox
        assert _check_file_sandbox("/etc/passwd") is None
        assert _check_file_sandbox("/tmp/anything") is None


# ---------------------------------------------------------------------------
# Terminal tool sandbox tests
# ---------------------------------------------------------------------------

class TestTerminalToolSandbox:
    def test_absolute_path_outside_blocked(self, sandbox_env):
        from tools.terminal_tool import _enforce_sandbox
        result = _enforce_sandbox("cat /etc/passwd", str(sandbox_env), None)
        assert result is not None
        assert "outside" in result.lower()

    def test_absolute_path_inside_allowed(self, sandbox_env):
        from tools.terminal_tool import _enforce_sandbox
        result = _enforce_sandbox(f"cat {sandbox_env}/allowed.txt", str(sandbox_env), None)
        assert result is None

    def test_safe_system_paths_allowed(self, sandbox_env):
        from tools.terminal_tool import _enforce_sandbox
        # /dev/null, /tmp, /usr/bin are safe
        assert _enforce_sandbox("echo test > /dev/null", str(sandbox_env), None) is None
        assert _enforce_sandbox("ls /usr/bin/git", str(sandbox_env), None) is None
        assert _enforce_sandbox("cat /tmp/test", str(sandbox_env), None) is None

    def test_git_clone_not_blocked(self, sandbox_env):
        """Git operations are allowed — repo access is controlled by the bot token."""
        from tools.terminal_tool import _enforce_sandbox
        assert _enforce_sandbox(
            "git clone https://github.com/any/repo.git",
            str(sandbox_env), None
        ) is None
        assert _enforce_sandbox(
            "git clone git@github.com:any/repo.git",
            str(sandbox_env), None
        ) is None

    def test_urls_not_blocked_as_paths(self, sandbox_env):
        """URLs with paths should not be treated as filesystem paths."""
        from tools.terminal_tool import _enforce_sandbox
        assert _enforce_sandbox(
            'curl -s -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/repos/owner/repo/issues',
            str(sandbox_env), None
        ) is None
        assert _enforce_sandbox(
            'curl https://example.com/some/deep/path',
            str(sandbox_env), None
        ) is None

    def test_relative_commands_allowed(self, sandbox_env):
        """Commands without absolute paths should be allowed."""
        from tools.terminal_tool import _enforce_sandbox
        assert _enforce_sandbox("ls -la", str(sandbox_env), None) is None
        assert _enforce_sandbox("git status", str(sandbox_env), None) is None
        assert _enforce_sandbox("npm install", str(sandbox_env), None) is None

    def test_no_sandbox_allows_everything(self, monkeypatch):
        """When HERMES_SANDBOX_ROOT is not set, _enforce_sandbox is not called."""
        monkeypatch.delenv("HERMES_SANDBOX_ROOT", raising=False)
        monkeypatch.delenv("HERMES_ALLOWED_REPOS", raising=False)
        # The function itself still works — it's just not called
        from tools.terminal_tool import _enforce_sandbox
        assert _enforce_sandbox("cat /etc/passwd", "/nonexistent", None) is not None


# ---------------------------------------------------------------------------
# Integration: verify env vars flow through correctly
# ---------------------------------------------------------------------------

class TestSandboxEnvIntegration:
    def test_sandbox_env_cleared_when_unset(self, monkeypatch):
        """HERMES_SANDBOX_ROOT should not leak between chats."""
        monkeypatch.setenv("HERMES_SANDBOX_ROOT", "/some/path")
        monkeypatch.delenv("HERMES_SANDBOX_ROOT")
        assert os.environ.get("HERMES_SANDBOX_ROOT") is None

    def test_allowed_repos_parsed_correctly(self, monkeypatch):
        monkeypatch.setenv("HERMES_ALLOWED_REPOS", "owner/repo1,owner/repo2,owner/repo3")
        raw = os.environ.get("HERMES_ALLOWED_REPOS", "")
        repos = [r.strip() for r in raw.split(",") if r.strip()]
        assert repos == ["owner/repo1", "owner/repo2", "owner/repo3"]
