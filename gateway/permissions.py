"""
Chat Permissions System

Per-chat tool permission management and resource sandboxing. By default,
only the owner's DM (home channel) gets tools. Other chats start with no
tools and the owner grants them via /allow from their home channel.

Each chat with tools gets a sandboxed workspace directory. File and terminal
tools are restricted to that directory. Git operations are restricted to
repos explicitly granted via /repos.

Storage: ~/.hermes/permissions/
"""

import json
import logging
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Dict, List, Optional, Set

from hermes_constants import get_hermes_dir, get_hermes_home

PERMISSIONS_DIR = get_hermes_dir("platforms/permissions", "permissions")
WORKSPACES_ROOT = get_hermes_home() / "workspaces"

logger = logging.getLogger(__name__)
_lock = threading.RLock()


def _secure_write(path: Path, data: str) -> None:
    """Atomic write with restrictive permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _index_path() -> Path:
    return PERMISSIONS_DIR / "chat_permissions.json"


def _load_index() -> Dict[str, dict]:
    """Load the permissions index.

    Returns {chat_key: {toolsets, repos, sandbox_root, chat_name, ...}}
    """
    path = _index_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_index(index: Dict[str, dict]) -> None:
    _secure_write(_index_path(), json.dumps(index, indent=2, ensure_ascii=False))


def _chat_key(platform: str, chat_id: str) -> str:
    return f"{platform}:{chat_id}"


# ---------------------------------------------------------------------------
# Toolset management
# ---------------------------------------------------------------------------

def get_chat_toolsets(platform: str, chat_id: str) -> Optional[List[str]]:
    """Get allowed toolsets for a chat. Returns None if chat has no permissions entry."""
    with _lock:
        index = _load_index()
        entry = index.get(_chat_key(platform, chat_id))
        if entry is None:
            return None
        return entry.get("toolsets", [])


def set_chat_toolsets(platform: str, chat_id: str, toolsets: List[str],
                      chat_name: str = "", chat_type: str = "") -> None:
    """Set allowed toolsets for a chat."""
    with _lock:
        index = _load_index()
        key = _chat_key(platform, chat_id)
        existing = index.get(key, {})
        existing["toolsets"] = toolsets
        if chat_name:
            existing["chat_name"] = chat_name
        if chat_type:
            existing["chat_type"] = chat_type
        index[key] = existing
        _save_index(index)


# ---------------------------------------------------------------------------
# Repo / sandbox management
# ---------------------------------------------------------------------------

def get_chat_sandbox(platform: str, chat_id: str) -> Optional[str]:
    """Get the sandbox root directory for a chat. Returns None if not set."""
    with _lock:
        index = _load_index()
        entry = index.get(_chat_key(platform, chat_id))
        if entry is None:
            return None
        return entry.get("sandbox_root")


def get_chat_repos(platform: str, chat_id: str) -> List[str]:
    """Get allowed repos for a chat (e.g. ['owner/repo1', 'owner/repo2'])."""
    with _lock:
        index = _load_index()
        entry = index.get(_chat_key(platform, chat_id))
        if entry is None:
            return []
        return entry.get("repos", [])


def add_chat_repos(platform: str, chat_id: str, repos: List[str]) -> dict:
    """Add repos to a chat's allowlist and clone them into the sandbox.

    Returns {added: [...], already_present: [...], sandbox_root: str, errors: [...]}
    """
    key = _chat_key(platform, chat_id)
    sandbox_root = WORKSPACES_ROOT / key
    sandbox_root.mkdir(parents=True, exist_ok=True)

    with _lock:
        index = _load_index()
        entry = index.get(key, {})
        existing_repos = set(entry.get("repos", []))
        entry["sandbox_root"] = str(sandbox_root)

        added = []
        already_present = []
        errors = []

        for repo in repos:
            repo = repo.strip().strip("/")
            if not repo or "/" not in repo:
                errors.append(f"Invalid repo format: '{repo}' (expected owner/name)")
                continue

            if repo in existing_repos:
                already_present.append(repo)
                continue

            # Clone the repo into the sandbox
            repo_name = repo.split("/")[-1]
            clone_target = sandbox_root / repo_name

            if not clone_target.exists():
                try:
                    # Use GITHUB_BOT_TOKEN for clone if available, so the bot
                    # operates under its own identity rather than the owner's.
                    bot_token = os.environ.get("GITHUB_BOT_TOKEN", "").strip()
                    if bot_token:
                        clone_url = f"https://x-access-token:{bot_token}@github.com/{repo}.git"
                    else:
                        clone_url = f"https://github.com/{repo}.git"
                    subprocess.run(
                        ["git", "clone", clone_url, str(clone_target)],
                        check=True, capture_output=True, text=True, timeout=120,
                    )
                    # Configure git identity in the cloned repo
                    if bot_token:
                        subprocess.run(
                            ["git", "config", "user.name", "hermes-bot"],
                            cwd=str(clone_target), capture_output=True,
                        )
                        subprocess.run(
                            ["git", "config", "user.email", "hermes-bot@noreply.github.com"],
                            cwd=str(clone_target), capture_output=True,
                        )
                        # Set the remote to use the bot token for push/pull
                        subprocess.run(
                            ["git", "remote", "set-url", "origin", clone_url],
                            cwd=str(clone_target), capture_output=True,
                        )
                except subprocess.CalledProcessError as e:
                    errors.append(f"Failed to clone {repo}: {e.stderr.strip()}")
                    continue
                except subprocess.TimeoutExpired:
                    errors.append(f"Clone timed out for {repo}")
                    continue

            existing_repos.add(repo)
            added.append(repo)

        entry["repos"] = sorted(existing_repos)
        index[key] = entry
        _save_index(index)

    return {
        "added": added,
        "already_present": already_present,
        "sandbox_root": str(sandbox_root),
        "errors": errors,
    }


def remove_chat_repos(platform: str, chat_id: str, repos: List[str]) -> dict:
    """Remove repos from a chat's allowlist. Does not delete cloned files.

    Returns {removed: [...], not_found: [...]}
    """
    key = _chat_key(platform, chat_id)
    with _lock:
        index = _load_index()
        entry = index.get(key, {})
        existing_repos = set(entry.get("repos", []))

        removed = []
        not_found = []
        for repo in repos:
            repo = repo.strip().strip("/")
            if repo in existing_repos:
                existing_repos.discard(repo)
                removed.append(repo)
            else:
                not_found.append(repo)

        entry["repos"] = sorted(existing_repos)
        index[key] = entry
        _save_index(index)

    return {"removed": removed, "not_found": not_found}


# ---------------------------------------------------------------------------
# Sandbox enforcement helpers
# ---------------------------------------------------------------------------

def is_path_in_sandbox(path: str, sandbox_root: str) -> bool:
    """Check if a path is inside the sandbox. Resolves symlinks and traversal."""
    try:
        sandbox = Path(sandbox_root).resolve()
        target = Path(path).resolve()
        # The path must be the sandbox itself or a child of it
        return target == sandbox or sandbox in target.parents
    except (OSError, ValueError):
        return False


def is_repo_allowed(repo_url: str, allowed_repos: List[str]) -> bool:
    """Check if a git remote URL matches an allowed repo.

    Handles HTTPS and SSH URL formats:
      https://github.com/owner/repo.git
      git@github.com:owner/repo.git
      owner/repo
    """
    if not allowed_repos:
        return False

    # Normalize the URL to owner/repo format
    normalized = _normalize_repo_url(repo_url)
    if not normalized:
        return False

    return normalized in {_normalize_repo_url(r) or r for r in allowed_repos}


def _normalize_repo_url(url: str) -> Optional[str]:
    """Normalize a git URL to 'owner/repo' format."""
    url = url.strip().rstrip("/")

    # Remove .git suffix
    if url.endswith(".git"):
        url = url[:-4]

    # SSH format: git@github.com:owner/repo
    if url.startswith("git@"):
        parts = url.split(":", 1)
        if len(parts) == 2:
            return parts[1].strip("/")
        return None

    # HTTPS format: https://github.com/owner/repo
    for prefix in ("https://github.com/", "http://github.com/"):
        if url.lower().startswith(prefix):
            return url[len(prefix):].strip("/")

    # Already owner/repo format
    if "/" in url and not url.startswith("/") and ":" not in url and "." not in url.split("/")[0]:
        return url

    return None


# ---------------------------------------------------------------------------
# Chat tracking
# ---------------------------------------------------------------------------

def record_chat(platform: str, chat_id: str, chat_name: str = "",
                chat_type: str = "", user_name: str = "") -> bool:
    """Record a chat we've seen. Returns True if this is a NEW chat (first time seen)."""
    with _lock:
        index = _load_index()
        key = _chat_key(platform, chat_id)
        is_new = key not in index
        if is_new:
            index[key] = {
                "toolsets": [],
                "repos": [],
                "chat_name": chat_name,
                "chat_type": chat_type,
                "user_name": user_name,
            }
            _save_index(index)
        return is_new


def list_chats(platform: str = None) -> Dict[str, dict]:
    """List all known chats, optionally filtered by platform."""
    with _lock:
        index = _load_index()
        if platform is None:
            return index
        prefix = f"{platform}:"
        return {k: v for k, v in index.items() if k.startswith(prefix)}


def get_chat_listen_mode(platform: str, chat_id: str) -> bool:
    """Check if a chat has active listener mode enabled."""
    with _lock:
        index = _load_index()
        entry = index.get(_chat_key(platform, chat_id))
        if entry is None:
            return False
        return entry.get("listen_mode", False)


def set_chat_listen_mode(platform: str, chat_id: str, enabled: bool) -> None:
    """Enable or disable active listener mode for a chat."""
    with _lock:
        index = _load_index()
        key = _chat_key(platform, chat_id)
        entry = index.get(key, {})
        entry["listen_mode"] = enabled
        index[key] = entry
        _save_index(index)


def remove_chat(platform: str, chat_id: str) -> bool:
    """Remove a chat from the permissions index."""
    with _lock:
        index = _load_index()
        key = _chat_key(platform, chat_id)
        if key in index:
            del index[key]
            _save_index(index)
            return True
        return False
