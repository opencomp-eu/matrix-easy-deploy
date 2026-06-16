"""Direct unit tests for scripts/med_admin.py."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts import med_admin


def assert_dies_with(capsys: pytest.CaptureFixture[str], fn, *args, message: str, **kwargs) -> None:
    """med_admin.die() prints to stderr and raises SystemExit(code), not a message."""
    with pytest.raises(SystemExit):
        fn(*args, **kwargs)
    assert message in capsys.readouterr().err


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".env").write_text(
        "SERVER_NAME=example.com\n"
        "MATRIX_DOMAIN=matrix.example.com\n"
        "REGISTRATION_SHARED_SECRET=shared-secret\n"
        "ADMIN_USERNAME=admin\n"
    )
    return tmp_path


@pytest.fixture
def ctx(repo_root: Path) -> med_admin.Context:
    script_dir = repo_root / "scripts"
    return med_admin.Context(
        repo_dir=repo_root,
        script_dir=script_dir,
        env_path=repo_root / ".env",
    )


def test_to_user_id_accepts_full_mxid() -> None:
    assert med_admin.to_user_id("@alice:example.com", "other.com") == "@alice:example.com"


def test_to_user_id_builds_mxid_from_localpart() -> None:
    assert med_admin.to_user_id("alice", "example.com") == "@alice:example.com"
    assert med_admin.to_user_id("@alice", "example.com") == "@alice:example.com"


def test_load_env_file_ignores_comments_and_blanks(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# comment\n\nFOO=bar\nBAZ=\n")

    assert med_admin.load_env_file(env) == {"FOO": "bar", "BAZ": ""}


def test_upsert_env_value_updates_existing_key(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("FOO=old\nBAR=keep\n")

    med_admin.upsert_env_value(env, "FOO", "new")

    assert env.read_text() == "FOO=new\nBAR=keep\n"


def test_upsert_env_value_appends_missing_key(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("FOO=old")

    med_admin.upsert_env_value(env, "BAR", "added")

    assert "BAR=added\n" in env.read_text()


def test_upsert_env_value_rejects_newlines(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env = tmp_path / ".env"
    env.write_text("")

    assert_dies_with(capsys, med_admin.upsert_env_value, env, "FOO", "bad\nvalue", message="cannot contain newlines")


def test_ensure_min_password_len_rejects_short_password(capsys: pytest.CaptureFixture[str]) -> None:
    assert_dies_with(capsys, med_admin.ensure_min_password_len, "short", message="at least 12 characters")


def test_generate_password_has_expected_length() -> None:
    assert len(med_admin.generate_password(24)) == 24


def test_context_read_deploy_env_populates_fields(ctx: med_admin.Context) -> None:
    ctx.read_deploy_env()

    assert ctx.server_name == "example.com"
    assert ctx.base_url == "https://matrix.example.com"
    assert ctx.shared_secret == "shared-secret"
    assert ctx.auth_username == "admin"


def test_context_read_deploy_env_honors_tuwunel(ctx: med_admin.Context) -> None:
    ctx.env_path.write_text("SERVER_IMPLEMENTATION=tuwunel\nMATRIX_DOMAIN=matrix.example.com\n")
    ctx.read_deploy_env()

    assert ctx.is_tuwunel()
    assert ctx.server_implementation == "tuwunel"


def test_context_require_synapse_admin_api_blocks_tuwunel(ctx: med_admin.Context, capsys: pytest.CaptureFixture[str]) -> None:
    ctx.server_implementation = "tuwunel"

    assert_dies_with(
        capsys,
        ctx.require_synapse_admin_api,
        "list-accounts",
        message="not available for Tuwunel",
    )


def test_context_get_admin_token_uses_existing_access_token(ctx: med_admin.Context) -> None:
    ctx.access_token = "tok-123"

    assert ctx.get_admin_token() == "tok-123"


def test_context_get_admin_token_logs_in(ctx: med_admin.Context) -> None:
    ctx.base_url = "https://matrix.example.com"
    ctx.auth_username = "admin"
    ctx.auth_password = "averylongsecret"

    response = json.dumps({"access_token": "fresh-token"}).encode("utf-8")

    with patch("scripts.med_admin.urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = response
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        token = ctx.get_admin_token()

    assert token == "fresh-token"
    assert ctx.access_token == "fresh-token"


def test_context_admin_api_uses_synapse_prefix(ctx: med_admin.Context) -> None:
    ctx.base_url = "https://matrix.example.com"
    ctx.access_token = "tok-123"

    with patch.object(ctx, "_request_json", return_value={"users": []}) as mock_request:
        ctx.admin_api("GET", "v2/users")

    mock_request.assert_called_once_with(
        method="GET",
        endpoint="v2/users",
        payload=None,
        api_prefix="_synapse/admin",
    )


def test_reorder_argv_for_argparse_moves_global_flags_before_command() -> None:
    argv = [
        "list-accounts",
        "--base-url",
        "https://matrix.example.com",
        "--limit",
        "10",
    ]

    reordered = med_admin._reorder_argv_for_argparse(argv)

    assert reordered == [
        "--base-url",
        "https://matrix.example.com",
        "list-accounts",
        "--limit",
        "10",
    ]


def test_reorder_argv_for_argparse_dies_on_missing_flag_value(capsys: pytest.CaptureFixture[str]) -> None:
    assert_dies_with(
        capsys,
        med_admin._reorder_argv_for_argparse,
        ["list-accounts", "--base-url"],
        message="Missing value for --base-url",
    )


def test_cmd_bootstrap_synapse_delegates_to_create_account(ctx: med_admin.Context) -> None:
    create_script = ctx.script_dir / "create-account.sh"
    create_script.write_text("#!/bin/sh\nexit 0\n")
    args = argparse.Namespace(
        username="med-admin",
        password="averylongsecret123",
        generate_password=False,
        shared_secret="",
    )

    with patch("scripts.med_admin.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        med_admin.cmd_bootstrap(ctx, args)

    cmd = mock_run.call_args.args[0]
    assert cmd[0] == "bash"
    assert str(create_script) in cmd
    assert "--username" in cmd
    assert "--admin" in cmd
    assert "MED_ADMIN_USERNAME=med-admin" in ctx.env_path.read_text()


def test_cmd_bootstrap_tuwunel_uses_registration_api(ctx: med_admin.Context) -> None:
    ctx.env_path.write_text(
        "SERVER_IMPLEMENTATION=tuwunel\n"
        "MATRIX_DOMAIN=matrix.example.com\n"
        "REGISTRATION_SHARED_SECRET=token\n"
    )
    args = argparse.Namespace(
        username="med-admin",
        password="averylongsecret123",
        generate_password=False,
        shared_secret="",
    )
    mock_admin = MagicMock()

    with patch("scripts.med_admin._load_tuwunel_admin_module") as mock_load:
        mock_load.return_value.load_tuwunel_admin.return_value = mock_admin
        med_admin.cmd_bootstrap(ctx, args)

    mock_admin.create_user.assert_called_once_with("med-admin", "averylongsecret123", grant_admin=True)
    assert "MED_ADMIN_USERNAME=med-admin" in ctx.env_path.read_text()


def test_cmd_bootstrap_rejects_password_and_generate_password(ctx: med_admin.Context, capsys: pytest.CaptureFixture[str]) -> None:
    args = argparse.Namespace(
        username="med-admin",
        password="secret",
        generate_password=True,
        shared_secret="",
    )

    assert_dies_with(
        capsys,
        med_admin.cmd_bootstrap,
        ctx,
        args,
        message="not both",
    )


def test_cmd_list_accounts_synapse_queries_admin_api(ctx: med_admin.Context, capsys: pytest.CaptureFixture[str]) -> None:
    ctx.base_url = "https://matrix.example.com"
    ctx.access_token = "tok-123"
    args = argparse.Namespace(filter="alice", limit="25", from_token="")

    with patch.object(ctx, "admin_api", return_value={"users": [{"name": "@alice:example.com", "admin": True}]}) as mock_api:
        med_admin.cmd_list_accounts(ctx, args, admins_only=False)

    mock_api.assert_called_once_with("GET", "v2/users?limit=25&guests=false&name=alice")
    assert "@alice:example.com" in capsys.readouterr().out


def test_cmd_list_accounts_tuwunel_lists_offline_users(ctx: med_admin.Context, capsys: pytest.CaptureFixture[str]) -> None:
    ctx.env_path.write_text("SERVER_IMPLEMENTATION=tuwunel\nMATRIX_DOMAIN=matrix.example.com\n")
    args = argparse.Namespace(filter="", limit="100", from_token="")
    mock_admin = MagicMock()
    mock_admin.list_users.return_value = ["@alice:example.com"]

    with patch("scripts.med_admin._load_tuwunel_admin_module") as mock_load:
        mock_load.return_value.load_tuwunel_admin.return_value = mock_admin
        med_admin.cmd_list_accounts(ctx, args, admins_only=False)

    assert "@alice:example.com" in capsys.readouterr().out


def test_cmd_get_account_tuwunel_checks_offline_user_list(ctx: med_admin.Context, capsys: pytest.CaptureFixture[str]) -> None:
    ctx.env_path.write_text(
        "SERVER_IMPLEMENTATION=tuwunel\n"
        "SERVER_NAME=example.com\n"
        "MATRIX_DOMAIN=matrix.example.com\n"
    )
    args = argparse.Namespace(target="alice")
    mock_admin = MagicMock()
    mock_admin.list_users.return_value = ["@alice:example.com"]

    with patch("scripts.med_admin._load_tuwunel_admin_module") as mock_load:
        mock_load.return_value.load_tuwunel_admin.return_value = mock_admin
        med_admin.cmd_get_account(ctx, args)

    assert "User ID:      @alice:example.com" in capsys.readouterr().out


def test_cmd_get_account_tuwunel_missing_user_dies(ctx: med_admin.Context, capsys: pytest.CaptureFixture[str]) -> None:
    ctx.env_path.write_text(
        "SERVER_IMPLEMENTATION=tuwunel\n"
        "SERVER_NAME=example.com\n"
        "MATRIX_DOMAIN=matrix.example.com\n"
    )
    args = argparse.Namespace(target="missing")
    mock_admin = MagicMock()
    mock_admin.list_users.return_value = ["@alice:example.com"]

    with patch("scripts.med_admin._load_tuwunel_admin_module") as mock_load:
        mock_load.return_value.load_tuwunel_admin.return_value = mock_admin
        assert_dies_with(
            capsys,
            med_admin.cmd_get_account,
            ctx,
            args,
            message="User not found: @missing:example.com",
        )


def test_cmd_reset_password_tuwunel_calls_offline_reset(ctx: med_admin.Context) -> None:
    ctx.env_path.write_text(
        "SERVER_IMPLEMENTATION=tuwunel\n"
        "SERVER_NAME=example.com\n"
        "MATRIX_DOMAIN=matrix.example.com\n"
    )
    args = argparse.Namespace(target="alice", password="averylongsecret123", yes=True)
    mock_admin = MagicMock()

    with patch("scripts.med_admin._load_tuwunel_admin_module") as mock_load:
        mock_load.return_value.load_tuwunel_admin.return_value = mock_admin
        med_admin.cmd_reset_password(ctx, args)

    mock_admin.reset_password.assert_called_once_with("@alice:example.com", "averylongsecret123")


def test_cmd_reset_password_synapse_posts_admin_api(ctx: med_admin.Context) -> None:
    ctx.base_url = "https://matrix.example.com"
    ctx.access_token = "tok-123"
    args = argparse.Namespace(target="@alice:example.com", password="averylongsecret123", yes=True)

    with patch.object(ctx, "admin_api") as mock_api:
        med_admin.cmd_reset_password(ctx, args)

    endpoint = mock_api.call_args.args[1]
    assert endpoint.startswith("v1/reset_password/")
    assert "%40alice%3Aexample.com" in endpoint


def test_cmd_create_room_posts_client_api(ctx: med_admin.Context, capsys: pytest.CaptureFixture[str]) -> None:
    ctx.base_url = "https://matrix.example.com"
    ctx.access_token = "tok-123"
    args = argparse.Namespace(
        name="Care Team",
        alias="care-team",
        topic="Coordination",
        visibility="private",
        invite=["alice", "@bob:example.com"],
        direct=False,
        yes=True,
    )

    with patch.object(ctx, "client_api", return_value={"room_id": "!room:example.com"}) as mock_api:
        med_admin.cmd_create_room(ctx, args)

    payload = mock_api.call_args.args[2]
    assert payload["name"] == "Care Team"
    assert payload["room_alias_name"] == "care-team"
    assert payload["invite"] == ["@alice:example.com", "@bob:example.com"]
    assert "Room created." in capsys.readouterr().out


def test_parse_invites_splits_comma_separated_values() -> None:
    invites = med_admin._parse_invites(["alice,@bob:example.com", " carol "], "example.com")

    assert invites == ["@alice:example.com", "@bob:example.com", "@carol:example.com"]


def test_main_routes_create_room_command(repo_root: Path) -> None:
    argv = [
        "create-room",
        "--name",
        "Ops",
        "--private",
        "--yes",
        "--base-url",
        "https://matrix.example.com",
        "--access-token",
        "tok-123",
    ]

    with patch("scripts.med_admin.cmd_create_room") as mock_cmd:
        rc = med_admin.main(argv)

    assert rc == 0
    mock_cmd.assert_called_once()


def test_main_reorders_global_flags_before_subcommand(repo_root: Path) -> None:
    argv = [
        "list-accounts",
        "--base-url",
        "https://matrix.example.com",
        "--access-token",
        "tok-123",
        "--limit",
        "5",
    ]

    with patch("scripts.med_admin.cmd_list_accounts") as mock_cmd:
        rc = med_admin.main(argv)

    assert rc == 0
    mock_cmd.assert_called_once()


def test_resolve_room_id_returns_none_on_404(ctx: med_admin.Context) -> None:
    ctx.base_url = "https://matrix.example.com"
    ctx.access_token = "tok-123"

    with patch.object(ctx, "client_api_status", return_value=(404, {})):
        assert med_admin._resolve_room_id(ctx, "#welcome:example.com") is None


def test_provision_auto_join_room_creates_and_messages(ctx: med_admin.Context) -> None:
    ctx.base_url = "https://matrix.example.com"
    ctx.access_token = "tok-123"
    spec = {
        "alias": "#welcome:example.com",
        "name": "Welcome",
        "topic": "Intro",
        "message": "Hello there",
        "handover": [],
        "federated": False,
    }

    with (
        patch.object(med_admin, "_resolve_room_id", return_value=None),
        patch.object(med_admin, "_create_room_from_spec", return_value="!room:example.com") as mock_create,
        patch.object(med_admin, "_room_has_messages", return_value=False),
        patch.object(med_admin, "_send_text_message", return_value="$evt") as mock_send,
    ):
        med_admin._provision_auto_join_room(
            ctx,
            spec,
            force_message=False,
        )

    mock_create.assert_called_once_with(ctx, spec)
    mock_send.assert_called_once_with(ctx, "!room:example.com", "Hello there")


def test_provision_auto_join_room_updates_existing_without_resending_message(ctx: med_admin.Context) -> None:
    ctx.base_url = "https://matrix.example.com"
    ctx.access_token = "tok-123"
    ctx.server_name = "example.com"
    spec = {
        "alias": "#welcome:example.com",
        "name": "Welcome",
        "topic": "",
        "message": "Hello there",
        "handover": ["alice"],
        "federated": False,
    }

    with (
        patch.object(med_admin, "_resolve_room_id", return_value="!room:example.com"),
        patch.object(med_admin, "_ensure_in_room"),
        patch.object(med_admin, "_set_room_name") as mock_name,
        patch.object(med_admin, "_apply_handover") as mock_handover,
        patch.object(med_admin, "_room_has_messages", return_value=True),
        patch.object(med_admin, "_send_text_message") as mock_send,
    ):
        med_admin._provision_auto_join_room(
            ctx,
            spec,
            force_message=False,
        )

    mock_name.assert_called_once_with(ctx, "!room:example.com", "Welcome")
    mock_handover.assert_called_once()
    mock_send.assert_not_called()


def test_cmd_setup_auto_join_rooms_reads_deploy_yaml(ctx: med_admin.Context, repo_root: Path) -> None:
    deploy_yaml = repo_root / "deploy.yaml"
    deploy_yaml.write_text(
        "matrix:\n"
        "  domain: matrix.example.com\n"
        "  server_name: example.com\n"
        "  admin_username: admin\n"
        "features:\n"
        "  auto_join:\n"
        "    rooms:\n"
        "      - alias: welcome\n"
        "        name: Welcome\n"
        "        message: Hi\n"
        "        handover:\n"
        "          - alice\n"
    )
    ctx.base_url = "https://matrix.example.com"
    ctx.access_token = "tok-123"
    args = argparse.Namespace(deploy_yaml=str(deploy_yaml), yes=True, force_message=False)

    with patch.object(med_admin, "_provision_auto_join_room") as mock_provision:
        med_admin.cmd_setup_auto_join_rooms(ctx, args)

    assert mock_provision.call_count == 1
    spec = mock_provision.call_args.args[1]
    assert spec["alias"] == "#welcome:example.com"
    assert spec["name"] == "Welcome"
    assert spec["message"] == "Hi"


def test_main_routes_setup_auto_join_rooms_command() -> None:
    argv = [
        "setup-auto-join-rooms",
        "--yes",
        "--base-url",
        "https://matrix.example.com",
        "--access-token",
        "tok-123",
    ]

    with patch("scripts.med_admin.cmd_setup_auto_join_rooms") as mock_cmd:
        rc = med_admin.main(argv)

    assert rc == 0
    mock_cmd.assert_called_once()
