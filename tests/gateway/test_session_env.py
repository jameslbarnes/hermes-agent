import os
from unittest.mock import patch

from gateway.config import Platform
from gateway.run import GatewayRunner
from gateway.session import SessionContext, SessionSource


def test_set_session_env_includes_thread_id(monkeypatch):
    runner = object.__new__(GatewayRunner)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_name="Group",
        chat_type="group",
        thread_id="17585",
    )
    context = SessionContext(source=source, connected_platforms=[], home_channels={})

    monkeypatch.delenv("HERMES_SESSION_PLATFORM", raising=False)
    monkeypatch.delenv("HERMES_SESSION_CHAT_ID", raising=False)
    monkeypatch.delenv("HERMES_SESSION_CHAT_NAME", raising=False)
    monkeypatch.delenv("HERMES_SESSION_THREAD_ID", raising=False)

    runner._set_session_env(context)

    assert os.getenv("HERMES_SESSION_PLATFORM") == "telegram"
    assert os.getenv("HERMES_SESSION_CHAT_ID") == "-1001"
    assert os.getenv("HERMES_SESSION_CHAT_NAME") == "Group"
    assert os.getenv("HERMES_SESSION_THREAD_ID") == "17585"


def test_clear_session_env_removes_thread_id(monkeypatch):
    runner = object.__new__(GatewayRunner)

    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_CHAT_ID", "-1001")
    monkeypatch.setenv("HERMES_SESSION_CHAT_NAME", "Group")
    monkeypatch.setenv("HERMES_SESSION_THREAD_ID", "17585")

    runner._clear_session_env()

    assert os.getenv("HERMES_SESSION_PLATFORM") is None
    assert os.getenv("HERMES_SESSION_CHAT_ID") is None
    assert os.getenv("HERMES_SESSION_CHAT_NAME") is None
    assert os.getenv("HERMES_SESSION_THREAD_ID") is None


def _make_context(platform=Platform.TELEGRAM, chat_id="-1001", chat_name="Group"):
    source = SessionSource(
        platform=platform,
        chat_id=chat_id,
        chat_name=chat_name,
        chat_type="group",
    )
    return SessionContext(source=source, connected_platforms=[], home_channels={})


def test_chat_secrets_injected_for_matching_chat(monkeypatch):
    """chat_secrets config injects env vars matching platform:chat_id."""
    runner = object.__new__(GatewayRunner)
    context = _make_context(chat_id="-1001111")

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("HERMES_SESSION_PLATFORM", raising=False)
    monkeypatch.delenv("HERMES_SESSION_CHAT_ID", raising=False)

    secrets_config = {
        "chat_secrets": {
            "telegram:-1001111": {"GITHUB_TOKEN": "ghp_convent_token"},
            "telegram:-1002222": {"GITHUB_TOKEN": "ghp_other_token"},
        }
    }

    with patch("gateway.run._load_gateway_config", return_value=secrets_config):
        runner._set_session_env(context)

    assert os.getenv("GITHUB_TOKEN") == "ghp_convent_token"

    # Cleanup should remove the injected secret
    runner._clear_session_env()
    assert os.getenv("GITHUB_TOKEN") is None


def test_chat_secrets_no_match_leaves_env_clean(monkeypatch):
    """When no chat_secrets match, no extra env vars are set."""
    runner = object.__new__(GatewayRunner)
    context = _make_context(chat_id="-9999999")

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    secrets_config = {
        "chat_secrets": {
            "telegram:-1001111": {"GITHUB_TOKEN": "ghp_convent_token"},
        }
    }

    with patch("gateway.run._load_gateway_config", return_value=secrets_config):
        runner._set_session_env(context)

    assert os.getenv("GITHUB_TOKEN") is None


def test_chat_secrets_wildcard_platform(monkeypatch):
    """platform:* wildcard matches any chat on that platform."""
    runner = object.__new__(GatewayRunner)
    context = _make_context(chat_id="-9999999")

    monkeypatch.delenv("DEFAULT_ORG", raising=False)

    secrets_config = {
        "chat_secrets": {
            "telegram:*": {"DEFAULT_ORG": "etherea"},
        }
    }

    with patch("gateway.run._load_gateway_config", return_value=secrets_config):
        runner._set_session_env(context)

    assert os.getenv("DEFAULT_ORG") == "etherea"

    runner._clear_session_env()
    assert os.getenv("DEFAULT_ORG") is None


def test_chat_secrets_specific_overrides_wildcard(monkeypatch):
    """Specific chat_id entry overrides wildcard for the same key."""
    runner = object.__new__(GatewayRunner)
    context = _make_context(chat_id="-1001111")

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("SHARED_VAR", raising=False)

    secrets_config = {
        "chat_secrets": {
            "telegram:*": {"GITHUB_TOKEN": "ghp_default", "SHARED_VAR": "from_wildcard"},
            "telegram:-1001111": {"GITHUB_TOKEN": "ghp_specific"},
        }
    }

    with patch("gateway.run._load_gateway_config", return_value=secrets_config):
        runner._set_session_env(context)

    # Specific overrides wildcard
    assert os.getenv("GITHUB_TOKEN") == "ghp_specific"
    # Wildcard-only key still present
    assert os.getenv("SHARED_VAR") == "from_wildcard"

    runner._clear_session_env()
    assert os.getenv("GITHUB_TOKEN") is None
    assert os.getenv("SHARED_VAR") is None


def test_chat_secrets_empty_config_is_noop(monkeypatch):
    """Empty chat_secrets doesn't break anything."""
    runner = object.__new__(GatewayRunner)
    context = _make_context()

    with patch("gateway.run._load_gateway_config", return_value={"chat_secrets": {}}):
        runner._set_session_env(context)

    # Should still set standard vars
    assert os.getenv("HERMES_SESSION_PLATFORM") == "telegram"
    runner._clear_session_env()
