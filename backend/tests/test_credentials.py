"""Private local credential loading and terminal-output boundaries; synthetic values only."""

from types import SimpleNamespace

import pytest

from aigc_workbench import config
from aigc_workbench.c01_cli import main, model_run_report


@pytest.fixture
def private_config(tmp_path, monkeypatch):
    path = tmp_path / "backend" / ".env.local"
    path.parent.mkdir()
    monkeypatch.setattr(config, "BACKEND_ENV_FILE", path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AIGC_ENABLE_C01_REAL", raising=False)
    return path


def write_private(path, content):
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    path.chmod(0o600)


def test_missing_config_is_missing_without_creating_any_file(private_config):
    assert config.load_openai_credentials() == "KEY_MISSING"
    assert not private_config.exists() and "OPENAI_API_KEY" not in config.os.environ


@pytest.mark.parametrize("content", ["", "# User fills this file locally\n", "OPENAI_API_KEY=\n", 'OPENAI_API_KEY=""\n'])
def test_blank_private_key_is_missing(private_config, content):
    write_private(private_config, content)
    assert config.load_openai_credentials() == "KEY_MISSING"
    assert "OPENAI_API_KEY" not in config.os.environ


@pytest.mark.parametrize("quote", ["", "'", '"'])
def test_loads_only_key_without_rewriting_private_file_or_returning_secret(private_config, quote):
    dummy = "synthetic-credential-for-offline-test"
    content = f"# private\nOPENAI_API_KEY={quote}{dummy}{quote}\n"
    write_private(private_config, content)
    assert config.load_openai_credentials() == "KEY_CONFIGURED"
    assert config.os.environ["OPENAI_API_KEY"] == dummy
    assert private_config.read_text() == content
    assert private_config.stat().st_mode & 0o777 == 0o600


def test_process_environment_wins_and_does_not_read_or_overwrite_file(private_config, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "existing-synthetic-process-credential")
    def forbidden(*args, **kwargs): raise AssertionError("must not read local credential file")
    monkeypatch.setattr(config.os, "open", forbidden)
    assert config.load_openai_credentials() == "KEY_CONFIGURED"
    assert config.os.environ["OPENAI_API_KEY"] == "existing-synthetic-process-credential"


def test_blank_process_environment_falls_back(private_config, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    write_private(private_config, "OPENAI_API_KEY=synthetic-file-credential\n")
    assert config.load_openai_credentials() == "KEY_CONFIGURED"
    assert config.os.environ["OPENAI_API_KEY"] == "synthetic-file-credential"


def test_invalid_process_key_fails_without_silently_replacing_it(private_config, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic\ninvalid")
    write_private(private_config, "OPENAI_API_KEY=synthetic-file-credential\n")
    with pytest.raises(config.CredentialConfigError) as exc: config.load_openai_credentials()
    assert "synthetic" not in str(exc.value)
    assert config.os.environ["OPENAI_API_KEY"] == "synthetic\ninvalid"


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o666, 0o400])
def test_insecure_or_non_writable_mode_rejected_without_reading_contents(private_config, mode):
    write_private(private_config, "OPENAI_API_KEY=synthetic-secret-must-not-appear")
    private_config.chmod(mode)
    with pytest.raises(config.CredentialConfigError) as exc: config.load_openai_credentials()
    assert "synthetic-secret" not in str(exc.value)
    assert "OPENAI_API_KEY" not in config.os.environ


def test_symlink_rejected(private_config):
    target = private_config.parent / "private-target"
    write_private(target, "OPENAI_API_KEY=synthetic-secret")
    private_config.symlink_to(target)
    with pytest.raises(config.CredentialConfigError): config.load_openai_credentials()
    assert "OPENAI_API_KEY" not in config.os.environ


def test_wrong_owner_rejected(private_config, monkeypatch):
    write_private(private_config, "OPENAI_API_KEY=synthetic-secret")
    info = private_config.stat()
    monkeypatch.setattr(config.os, "fstat", lambda _: SimpleNamespace(
        st_mode=info.st_mode, st_uid=config.os.getuid() + 1, st_size=info.st_size))
    with pytest.raises(config.CredentialConfigError): config.load_openai_credentials()
    assert "OPENAI_API_KEY" not in config.os.environ


@pytest.mark.parametrize("content", [
    "OPENAI_API_KEY=synthetic-one\nOPENAI_API_KEY=synthetic-two",
    "export OPENAI_API_KEY=synthetic-secret",
    "OPENAI_API_KEY=synthetic secret",
    "OPENAI_API_KEY='synthetic-secret",
    "OPENAI_API_KEY=$(synthetic-command)",
    "OPENAI_API_KEY=synthetic-secret\nAIGC_ENABLE_C01_REAL=1",
    "OPENAI_API_KEY=" + "x" * 16384,
    b"OPENAI_API_KEY=\xffsynthetic-secret",
])
def test_malformed_or_overbroad_config_fails_without_leaking_content(private_config, content):
    write_private(private_config, content)
    with pytest.raises(config.CredentialConfigError) as exc: config.load_openai_credentials()
    assert "synthetic" not in str(exc.value)
    assert "OPENAI_API_KEY" not in config.os.environ
    assert config.os.environ.get("AIGC_ENABLE_C01_REAL") != "1"


def test_settings_and_disabled_cli_do_not_load_private_file(private_config, monkeypatch, capsys):
    write_private(private_config, "OPENAI_API_KEY=synthetic-secret")
    monkeypatch.setenv("AIGC_ENABLE_C01_TESTING", "0")
    monkeypatch.setenv("AIGC_DATA_DIR", str(private_config.parent / "nonexistent-runtime"))
    config.Settings.from_environment()
    assert "OPENAI_API_KEY" not in config.os.environ
    assert main(["credential-status"]) == 2
    output = capsys.readouterr()
    assert "DEVELOPER_ENTRY_DISABLED" in output.err and "synthetic" not in output.err
    assert "OPENAI_API_KEY" not in config.os.environ


def test_cli_status_is_only_status_and_does_not_access_business_database(private_config, monkeypatch, capsys):
    write_private(private_config, "OPENAI_API_KEY=synthetic-private-credential")
    monkeypatch.setenv("AIGC_ENV", "development")
    monkeypatch.setenv("AIGC_ENABLE_C01_TESTING", "1")
    runtime = private_config.parent / "not-created"
    monkeypatch.setenv("AIGC_DATA_DIR", str(runtime))
    assert main(["credential-status"]) == 0
    output = capsys.readouterr()
    assert output.out == "KEY_CONFIGURED\n" and output.err == ""
    assert not runtime.exists()


def test_cli_missing_key_is_only_status(private_config, monkeypatch, capsys):
    monkeypatch.setenv("AIGC_ENV", "development")
    monkeypatch.setenv("AIGC_ENABLE_C01_TESTING", "1")
    assert main(["credential-status"]) == 2
    assert capsys.readouterr().out == "KEY_MISSING\n"


def test_terminal_model_summary_omits_request_raw_response_and_base64():
    record = {"run_id": "test-run", "status": "FAILED", "model_api_call_attempted": True,
              "planned_model_input": {"image_url": "data:image/jpeg;base64,synthetic-bytes"},
              "actual_model_input": {"image_url": "data:image/jpeg;base64,synthetic-bytes"},
              "raw_response_text": "synthetic-private-response", "actual_model_response": {"x": "private"},
              "structured_output": {"x": "private"},
              "error": {"code": "MODEL_HTTP_ERROR", "stage": "MODEL_TRANSPORT", "message": "private"}}
    summary = model_run_report(record)
    assert summary["run_id"] == "test-run" and summary["status"] == "FAILED"
    assert summary["error"] == {"code": "MODEL_HTTP_ERROR", "stage": "MODEL_TRANSPORT"}
    assert "synthetic" not in str(summary) and "private" not in str(summary)
    assert "data:image" not in str(summary)
