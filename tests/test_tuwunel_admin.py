"""Pytest-native tests for scripts/tuwunel_admin.py."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.tuwunel_admin import TuwunelAdmin, TuwunelAdminError, load_tuwunel_admin


def test_load_tuwunel_admin_reads_env(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "HOMESERVER_CONTAINER=custom_tuwunel\n"
        "MATRIX_DOMAIN=matrix.example.com\n"
        "REGISTRATION_SHARED_SECRET=secret-token\n"
    )

    admin = load_tuwunel_admin(env_path, project_root=tmp_path)

    assert admin.container == "custom_tuwunel"
    assert admin.base_url == "https://matrix.example.com"
    assert admin.registration_token == "secret-token"
    assert admin.project_root == tmp_path


def test_create_user_requires_base_url() -> None:
    admin = TuwunelAdmin(base_url="", registration_token="token")

    with pytest.raises(TuwunelAdminError, match="base URL"):
        admin.create_user("alice", "password")


def test_create_user_requires_registration_token() -> None:
    admin = TuwunelAdmin(base_url="https://matrix.example.com", registration_token="")

    with pytest.raises(TuwunelAdminError, match="REGISTRATION_SHARED_SECRET"):
        admin.create_user("alice", "password")


def test_create_user_posts_registration_payload() -> None:
    admin = TuwunelAdmin(
        base_url="https://matrix.example.com",
        registration_token="shared-secret",
    )
    response_body = json.dumps({"user_id": "@alice:example.com"})

    with patch("scripts.tuwunel_admin.urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_body.encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        result = admin.create_user("alice", "s3cret")

    assert result == response_body
    request = mock_urlopen.call_args.args[0]
    assert request.full_url == "https://matrix.example.com/_matrix/client/v3/register"
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["username"] == "alice"
    assert payload["password"] == "s3cret"
    assert payload["auth"]["token"] == "shared-secret"


def test_create_user_returns_body_when_user_already_exists() -> None:
    admin = TuwunelAdmin(
        base_url="https://matrix.example.com",
        registration_token="shared-secret",
    )
    error_body = json.dumps({"errcode": "M_USER_IN_USE", "error": "User ID taken"})

    with patch("scripts.tuwunel_admin.urllib.request.urlopen") as mock_urlopen:
        from urllib.error import HTTPError

        mock_urlopen.side_effect = HTTPError(
            url="https://matrix.example.com/_matrix/client/v3/register",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=MagicMock(read=MagicMock(return_value=error_body.encode("utf-8"))),
        )

        result = admin.create_user("alice", "s3cret")

    assert result == error_body


def test_create_user_raises_on_http_error() -> None:
    admin = TuwunelAdmin(
        base_url="https://matrix.example.com",
        registration_token="shared-secret",
    )
    error_body = json.dumps({"errcode": "M_FORBIDDEN", "error": "Denied"})

    with patch("scripts.tuwunel_admin.urllib.request.urlopen") as mock_urlopen:
        from urllib.error import HTTPError

        mock_urlopen.side_effect = HTTPError(
            url="https://matrix.example.com/_matrix/client/v3/register",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=MagicMock(read=MagicMock(return_value=error_body.encode("utf-8"))),
        )

        with pytest.raises(TuwunelAdminError, match="Registration failed"):
            admin.create_user("alice", "s3cret")


def test_list_users_parses_markdown_block(tmp_path: Path) -> None:
    admin = TuwunelAdmin(project_root=tmp_path)
    output = "Users:\n```\n@alice:example.com\n@bob:example.com\n```\n"

    with patch.object(admin, "_execute_offline", return_value=output):
        users = admin.list_users()

    assert users == ["@alice:example.com", "@bob:example.com"]


def test_list_users_returns_empty_when_no_block(tmp_path: Path) -> None:
    admin = TuwunelAdmin(project_root=tmp_path)

    with patch.object(admin, "_execute_offline", return_value="no users"):
        assert admin.list_users() == []


def test_reset_password_strips_mxid_localpart(tmp_path: Path) -> None:
    admin = TuwunelAdmin(project_root=tmp_path)

    with patch.object(admin, "_execute_offline", return_value="ok") as mock_execute:
        admin.reset_password("@alice:example.com", "new-pass")

    mock_execute.assert_called_once_with("users reset-password alice new-pass")


def test_execute_offline_requires_project_root() -> None:
    admin = TuwunelAdmin(project_root=None)

    with pytest.raises(TuwunelAdminError, match="Project root"):
        admin._execute_offline("users list")


def test_execute_offline_requires_tuwunel_config(tmp_path: Path) -> None:
    admin = TuwunelAdmin(project_root=tmp_path)

    with pytest.raises(TuwunelAdminError, match="Tuwunel config not found"):
        admin._execute_offline("users list")


def test_execute_offline_runs_docker_when_config_exists(tmp_path: Path) -> None:
    config_dir = tmp_path / "modules/core/tuwunel"
    data_dir = tmp_path / "modules/core/tuwunel_data"
    config_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    (config_dir / "tuwunel.toml").write_text("server_name = \"example.com\"\n")

    admin = TuwunelAdmin(project_root=tmp_path, image="tuwunel:test")

    with patch.object(admin, "_running", return_value=False):
        with patch("scripts.tuwunel_admin.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="```\n@alice:example.com\n```", stderr="")
            output = admin._execute_offline("users list")

    assert "@alice:example.com" in output
    docker_run = mock_run.call_args.args[0]
    assert docker_run[0:2] == ["docker", "run"]
    docker_args = " ".join(str(arg) for arg in docker_run)
    assert str(data_dir) in docker_args
    assert str(config_dir / "tuwunel.toml") in docker_args
    assert docker_run[-1] == "users list"
