"""Tests for the chat permissions system (gateway/permissions.py).

Verifies per-chat tool grants, repo allowlists, sandbox path enforcement,
and repo URL normalization. These tests are security-critical — they ensure
that non-owner chats cannot access resources outside their sandbox.
"""

import json
import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_permissions(tmp_path, monkeypatch):
    """Point the permissions module at a temp directory."""
    perm_dir = tmp_path / "permissions"
    perm_dir.mkdir()
    monkeypatch.setattr("gateway.permissions.PERMISSIONS_DIR", perm_dir)
    monkeypatch.setattr("gateway.permissions.WORKSPACES_ROOT", tmp_path / "workspaces")


# ---------------------------------------------------------------------------
# Toolset management
# ---------------------------------------------------------------------------

class TestToolsets:
    def test_new_chat_has_no_toolsets(self):
        from gateway.permissions import get_chat_toolsets
        assert get_chat_toolsets("telegram", "-100TEST") is None

    def test_record_chat_creates_empty_entry(self):
        from gateway.permissions import record_chat, get_chat_toolsets
        is_new = record_chat("telegram", "-100TEST", chat_name="Test Group")
        assert is_new is True
        assert get_chat_toolsets("telegram", "-100TEST") == []

    def test_record_chat_not_new_on_second_call(self):
        from gateway.permissions import record_chat
        record_chat("telegram", "-100TEST")
        assert record_chat("telegram", "-100TEST") is False

    def test_set_and_get_toolsets(self):
        from gateway.permissions import set_chat_toolsets, get_chat_toolsets
        set_chat_toolsets("telegram", "-100TEST", ["terminal", "file", "web"])
        result = get_chat_toolsets("telegram", "-100TEST")
        assert set(result) == {"terminal", "file", "web"}

    def test_overwrite_toolsets(self):
        from gateway.permissions import set_chat_toolsets, get_chat_toolsets
        set_chat_toolsets("telegram", "-100TEST", ["terminal", "file"])
        set_chat_toolsets("telegram", "-100TEST", ["web"])
        assert get_chat_toolsets("telegram", "-100TEST") == ["web"]

    def test_list_chats_filters_by_platform(self):
        from gateway.permissions import record_chat, list_chats
        record_chat("telegram", "-100A", chat_name="A")
        record_chat("discord", "999", chat_name="B")
        tg = list_chats("telegram")
        assert "telegram:-100A" in tg
        assert "discord:999" not in tg


# ---------------------------------------------------------------------------
# Repo management
# ---------------------------------------------------------------------------

class TestRepos:
    def test_no_repos_by_default(self):
        from gateway.permissions import record_chat, get_chat_repos
        record_chat("telegram", "-100TEST")
        assert get_chat_repos("telegram", "-100TEST") == []

    def test_add_repos_invalid_format(self):
        from gateway.permissions import record_chat, add_chat_repos
        record_chat("telegram", "-100TEST")
        result = add_chat_repos("telegram", "-100TEST", ["not-a-repo"])
        assert len(result["errors"]) == 1
        assert "Invalid repo format" in result["errors"][0]

    def test_remove_repos(self):
        from gateway.permissions import record_chat, add_chat_repos, remove_chat_repos, get_chat_repos
        record_chat("telegram", "-100TEST")
        # Manually set repos without cloning (clone will fail in test)
        from gateway.permissions import _load_index, _save_index, _chat_key
        idx = _load_index()
        key = _chat_key("telegram", "-100TEST")
        idx[key]["repos"] = ["owner/repo1", "owner/repo2"]
        _save_index(idx)

        result = remove_chat_repos("telegram", "-100TEST", ["owner/repo1"])
        assert "owner/repo1" in result["removed"]
        assert get_chat_repos("telegram", "-100TEST") == ["owner/repo2"]

    def test_remove_nonexistent_repo(self):
        from gateway.permissions import record_chat, remove_chat_repos
        record_chat("telegram", "-100TEST")
        result = remove_chat_repos("telegram", "-100TEST", ["owner/nope"])
        assert "owner/nope" in result["not_found"]


# ---------------------------------------------------------------------------
# Repo URL normalization
# ---------------------------------------------------------------------------

class TestRepoNormalization:
    def test_https_url(self):
        from gateway.permissions import _normalize_repo_url
        assert _normalize_repo_url("https://github.com/owner/repo.git") == "owner/repo"

    def test_https_url_no_git_suffix(self):
        from gateway.permissions import _normalize_repo_url
        assert _normalize_repo_url("https://github.com/owner/repo") == "owner/repo"

    def test_ssh_url(self):
        from gateway.permissions import _normalize_repo_url
        assert _normalize_repo_url("git@github.com:owner/repo.git") == "owner/repo"

    def test_plain_owner_repo(self):
        from gateway.permissions import _normalize_repo_url
        assert _normalize_repo_url("owner/repo") == "owner/repo"

    def test_trailing_slash(self):
        from gateway.permissions import _normalize_repo_url
        assert _normalize_repo_url("https://github.com/owner/repo/") == "owner/repo"

    def test_invalid_url(self):
        from gateway.permissions import _normalize_repo_url
        assert _normalize_repo_url("not-a-url") is None


class TestRepoAllowlist:
    def test_https_matches_plain(self):
        from gateway.permissions import is_repo_allowed
        assert is_repo_allowed("https://github.com/owner/repo.git", ["owner/repo"]) is True

    def test_ssh_matches_plain(self):
        from gateway.permissions import is_repo_allowed
        assert is_repo_allowed("git@github.com:owner/repo.git", ["owner/repo"]) is True

    def test_disallowed_repo(self):
        from gateway.permissions import is_repo_allowed
        assert is_repo_allowed("https://github.com/evil/repo", ["owner/repo"]) is False

    def test_empty_allowlist(self):
        from gateway.permissions import is_repo_allowed
        assert is_repo_allowed("https://github.com/owner/repo", []) is False

    def test_case_sensitivity(self):
        from gateway.permissions import is_repo_allowed
        # GitHub URLs are case-sensitive in practice
        assert is_repo_allowed("https://github.com/Owner/Repo", ["owner/repo"]) is False


# ---------------------------------------------------------------------------
# Path sandbox enforcement
# ---------------------------------------------------------------------------

class TestPathSandbox:
    def test_path_inside_sandbox(self):
        from gateway.permissions import is_path_in_sandbox
        assert is_path_in_sandbox("/sandbox/repo/file.py", "/sandbox") is True

    def test_path_is_sandbox_root(self):
        from gateway.permissions import is_path_in_sandbox
        assert is_path_in_sandbox("/sandbox", "/sandbox") is True

    def test_path_outside_sandbox(self):
        from gateway.permissions import is_path_in_sandbox
        assert is_path_in_sandbox("/etc/passwd", "/sandbox") is False

    def test_path_traversal_blocked(self, tmp_path):
        from gateway.permissions import is_path_in_sandbox
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        # ../../etc/passwd resolves outside
        assert is_path_in_sandbox(str(sandbox / "../../etc/passwd"), str(sandbox)) is False

    def test_symlink_escape_blocked(self, tmp_path):
        from gateway.permissions import is_path_in_sandbox
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        secret = tmp_path / "secret.txt"
        secret.write_text("sensitive data")
        link = sandbox / "escape"
        link.symlink_to(secret)
        # The symlink resolves to outside the sandbox
        assert is_path_in_sandbox(str(link), str(sandbox)) is False

    def test_nested_path_inside(self, tmp_path):
        from gateway.permissions import is_path_in_sandbox
        sandbox = tmp_path / "sandbox"
        (sandbox / "deep" / "nested").mkdir(parents=True)
        assert is_path_in_sandbox(str(sandbox / "deep" / "nested" / "file.py"), str(sandbox)) is True
