import os
import shutil
import subprocess
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SHARED_FUNCDEPLOY = REPO_ROOT / "deploy" / "funcdeploy.ps1"
MARKDOWNPDF_FUNCDEPLOY = REPO_ROOT / "servers" / "markdownpdf" / "funcdeploy.ps1"


def _powershell_executable():
    candidate = shutil.which("pwsh") or shutil.which("powershell")
    if not candidate:
        raise RuntimeError("PowerShell executable not found")
    return candidate


def _write_fake_yc(fake_bin, log_path):
    script = textwrap.dedent(
        f"""\
        @echo off
        echo %*>> \"{log_path}\"
        exit /b 0
        """
    )
    (fake_bin / "yc.cmd").write_text(script, encoding="utf-8")


def _run_powershell(script_path, config_path, extra_env=None):
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [
            _powershell_executable(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-Config",
            str(config_path),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )


def _write_source_tree(root):
    source_dir = root / "func"
    source_dir.mkdir()
    (source_dir / "index.py").write_text("def handler(event, context):\n    return {}\n", encoding="utf-8")
    (source_dir / "requirements.txt").write_text("boto3>=1.34.0\n", encoding="utf-8")
    return source_dir


def _write_config(path, body):
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")


def test_shared_funcdeploy_adds_mount_arguments(tmp_path):
    _write_source_tree(tmp_path)
    log_path = tmp_path / "yc.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_yc(fake_bin, log_path)

    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        """
        function_name: test-func
        service_account_id: sa-id
        runtime: python312
        entrypoint: index.handler
        memory: 128m
        timeout: 10s
        source_dir: func
        include_files:
          - index.py
          - requirements.txt
        mounts:
          - bucket: sysbucket
            prefix: fonts
            mount_point: fonts
            type: object-storage
            mode: ro
        environment:
          MARKDOWNPDF_FONT_DIR: /function/storage/fonts
        """,
    )

    result = _run_powershell(
        SHARED_FUNCDEPLOY,
        config_path,
        extra_env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
    )

    assert result.returncode == 0, result.stderr or result.stdout
    log_text = log_path.read_text(encoding="utf-8")
    assert "--mount type=object-storage,mount-point=fonts,bucket=sysbucket,prefix=fonts,mode=ro" in log_text


def test_shared_funcdeploy_without_mounts_keeps_previous_behavior(tmp_path):
    _write_source_tree(tmp_path)
    log_path = tmp_path / "yc.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_yc(fake_bin, log_path)

    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        """
        function_name: test-func
        service_account_id: sa-id
        source_dir: func
        include_files:
          - index.py
          - requirements.txt
        """,
    )

    result = _run_powershell(
        SHARED_FUNCDEPLOY,
        config_path,
        extra_env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
    )

    assert result.returncode == 0, result.stderr or result.stdout
    log_text = log_path.read_text(encoding="utf-8")
    assert "--mount" not in log_text


def test_shared_funcdeploy_rejects_missing_mount_bucket(tmp_path):
    _write_source_tree(tmp_path)
    log_path = tmp_path / "yc.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_yc(fake_bin, log_path)

    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        """
        function_name: test-func
        service_account_id: sa-id
        source_dir: func
        include_files:
          - index.py
          - requirements.txt
        mounts:
          - mount_point: fonts
            prefix: fonts
        """,
    )

    result = _run_powershell(
        SHARED_FUNCDEPLOY,
        config_path,
        extra_env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
    )

    assert result.returncode != 0
    assert "mounts[1].bucket is required" in (result.stderr + result.stdout)


def test_markdownpdf_wrapper_rejects_font_dir_mismatch(tmp_path):
    _write_source_tree(tmp_path)
    log_path = tmp_path / "yc.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_yc(fake_bin, log_path)

    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        """
        function_name: test-func
        service_account_id: sa-id
        source_dir: func
        include_files:
          - index.py
          - requirements.txt
        mounts:
          - bucket: sysbucket
            prefix: fonts
            mount_point: fonts
            type: object-storage
            mode: ro
        environment:
          MARKDOWNPDF_FONT_DIR: /function/storage/not-fonts
          bucket: outputbucket
        """,
    )

    result = _run_powershell(
        MARKDOWNPDF_FUNCDEPLOY,
        config_path,
        extra_env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
    )

    assert result.returncode != 0
    assert "MARKDOWNPDF_FONT_DIR must equal '/function/storage/fonts'" in (result.stderr + result.stdout)
    assert not log_path.exists()


def test_markdownpdf_wrapper_rejects_same_bucket_for_mount_and_output(tmp_path):
    _write_source_tree(tmp_path)
    log_path = tmp_path / "yc.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_yc(fake_bin, log_path)

    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        """
        function_name: test-func
        service_account_id: sa-id
        source_dir: func
        include_files:
          - index.py
          - requirements.txt
        mounts:
          - bucket: samebucket
            prefix: fonts
            mount_point: fonts
            type: object-storage
            mode: ro
        environment:
          MARKDOWNPDF_FONT_DIR: /function/storage/fonts
          bucket: samebucket
        """,
    )

    result = _run_powershell(
        MARKDOWNPDF_FUNCDEPLOY,
        config_path,
        extra_env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
    )

    assert result.returncode != 0
    assert "font mount bucket must differ from environment.bucket" in (result.stderr + result.stdout)
    assert not log_path.exists()


def test_markdownpdf_wrapper_passes_valid_mounts_to_shared_deploy(tmp_path):
    _write_source_tree(tmp_path)
    log_path = tmp_path / "yc.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_yc(fake_bin, log_path)

    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        """
        function_name: test-func
        service_account_id: sa-id
        source_dir: func
        include_files:
          - index.py
          - requirements.txt
        mounts:
          - bucket: sysbucket
            prefix: fonts
            mount_point: fonts
            type: object-storage
            mode: ro
        environment:
          MARKDOWNPDF_FONT_DIR: /function/storage/fonts
          bucket: outputbucket
        """,
    )

    result = _run_powershell(
        MARKDOWNPDF_FUNCDEPLOY,
        config_path,
        extra_env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
    )

    assert result.returncode == 0, result.stderr or result.stdout
    log_text = log_path.read_text(encoding="utf-8")
    assert "--mount type=object-storage,mount-point=fonts,bucket=sysbucket,prefix=fonts,mode=ro" in log_text
