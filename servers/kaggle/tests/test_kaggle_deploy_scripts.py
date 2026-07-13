import os
import shutil
import subprocess
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
KAGGLE_FUNCDEPLOY = REPO_ROOT / "servers" / "kaggle" / "funcdeploy.ps1"


def _powershell_executable():
    candidate = shutil.which("pwsh") or shutil.which("powershell")
    if not candidate:
        raise RuntimeError("PowerShell executable not found")
    return candidate


def _write_fake_yc(fake_bin, log_path):
    script = textwrap.dedent(
        f"""\
        @echo off
        echo %*>> "{log_path}"
        exit /b 0
        """
    )
    (fake_bin / "yc.cmd").write_text(script, encoding="utf-8")


def _write_config(root):
    source_dir = root / "func"
    source_dir.mkdir()
    (source_dir / "index.py").write_text("def handler(event, context):\n    return {}\n", encoding="utf-8")
    (source_dir / "requirements.txt").write_text("kaggle==2.2.3\n", encoding="utf-8")

    config_path = root / "config.yaml"
    config_path.write_text(
        textwrap.dedent(
            """\
            function_name: test-kaggle-search
            service_account_id: sa-id
            runtime: python314
            source_dir: func
            include_files:
              - index.py
              - requirements.txt
            environment:
              STATIC_SETTING: configured
            """
        ),
        encoding="utf-8",
    )
    return config_path


def _run_wrapper(config_path, env_file, fake_bin):
    env = os.environ.copy()
    env.pop("KAGGLE_API_TOKEN", None)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        [
            _powershell_executable(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(KAGGLE_FUNCDEPLOY),
            "-Config",
            str(config_path),
            "-EnvFile",
            str(env_file),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )


def test_wrapper_maps_lowercase_token_and_merges_config_environment(tmp_path):
    config_path = _write_config(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text("kaggle_token=test-kaggle-token\n", encoding="utf-8")
    log_path = tmp_path / "yc.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_yc(fake_bin, log_path)

    result = _run_wrapper(config_path, env_file, fake_bin)

    assert result.returncode == 0, result.stderr or result.stdout
    log_text = log_path.read_text(encoding="utf-8")
    assert "--environment STATIC_SETTING=configured" in log_text
    assert "--environment KAGGLE_API_TOKEN=test-kaggle-token" in log_text
    assert "test-kaggle-token" not in result.stdout
    assert "test-kaggle-token" not in result.stderr


def test_wrapper_rejects_missing_token_before_calling_yc(tmp_path):
    config_path = _write_config(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text("UNRELATED=value\n", encoding="utf-8")
    log_path = tmp_path / "yc.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_yc(fake_bin, log_path)

    result = _run_wrapper(config_path, env_file, fake_bin)

    assert result.returncode != 0
    assert "Kaggle token not found" in (result.stdout + result.stderr)
    assert not log_path.exists()
