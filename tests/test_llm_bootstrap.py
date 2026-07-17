"""Tests for llm_bootstrap utilities."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import httpx
import pytest

from truenex_memory.llm_bootstrap import (
    check_docker,
    has_nvidia_docker_runtime,
    has_nvidia_gpu,
    pick_profile,
    ensure_model,
)


class TestCheckDocker:
    def test_docker_running(self):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock:
            assert check_docker() is True
            mock.assert_called_once()

    def test_docker_not_installed(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert check_docker() is False

    def test_docker_daemon_down(self):
        with patch("subprocess.run", return_value=MagicMock(returncode=1)):
            assert check_docker() is False


class TestHasNvidiaGpu:
    def test_nvidia_smi_present(self):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock:
            assert has_nvidia_gpu() is True
            mock.assert_called_once_with(
                ["nvidia-smi"], capture_output=True, text=True, timeout=5
            )

    def test_nvidia_smi_missing(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert has_nvidia_gpu() is False


class TestHasNvidiaDockerRuntime:
    def test_runtime_present(self):
        with patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout="nvidia runc"),
        ):
            assert has_nvidia_docker_runtime() is True

    def test_runtime_missing(self):
        with patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout="runc"),
        ):
            assert has_nvidia_docker_runtime() is False

    def test_docker_not_available(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert has_nvidia_docker_runtime() is False


class TestPickProfile:
    def test_cuda_when_gpu_and_runtime(self):
        with patch(
            "truenex_memory.llm_bootstrap.has_nvidia_gpu", return_value=True
        ), patch(
            "truenex_memory.llm_bootstrap.has_nvidia_docker_runtime",
            return_value=True,
        ):
            assert pick_profile() == "cuda"

    def test_cpu_when_no_gpu(self):
        with patch(
            "truenex_memory.llm_bootstrap.has_nvidia_gpu", return_value=False
        ), patch(
            "truenex_memory.llm_bootstrap.has_nvidia_docker_runtime",
            return_value=True,
        ):
            assert pick_profile() == "cpu"

    def test_cpu_when_no_runtime(self):
        with patch(
            "truenex_memory.llm_bootstrap.has_nvidia_gpu", return_value=True
        ), patch(
            "truenex_memory.llm_bootstrap.has_nvidia_docker_runtime",
            return_value=False,
        ):
            assert pick_profile() == "cpu"


class TestEnsureModel:
    def test_skip_when_file_exists_and_large(self, tmp_path: Path):
        model_file = tmp_path / "model.gguf"
        model_file.write_bytes(b"x" * (5 * 1024 * 1024))  # 5 MB
        with patch("httpx.stream") as mock_stream:
            result = ensure_model(tmp_path, "owner/repo", "model.gguf")
            assert result == model_file
            mock_stream.assert_not_called()

    def test_download_when_file_missing_with_yes_flag(self, tmp_path: Path):
        model_file = tmp_path / "model.gguf"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-length": "10"}
        mock_response.iter_bytes.return_value = [b"0123456789"]
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=None)

        with patch("httpx.stream", return_value=mock_response) as mock_stream:
            result = ensure_model(tmp_path, "owner/repo", "model.gguf", yes=True)
            assert result == model_file
            assert model_file.read_bytes() == b"0123456789"
            mock_stream.assert_called_once()

    def test_resume_when_file_small(self, tmp_path: Path):
        model_file = tmp_path / "model.gguf"
        model_file.write_bytes(b"01234")  # 5 bytes, incomplete

        mock_response = MagicMock()
        mock_response.status_code = 206
        mock_response.headers = {"content-length": "5"}
        mock_response.iter_bytes.return_value = [b"56789"]
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=None)

        with patch("httpx.stream", return_value=mock_response) as mock_stream:
            result = ensure_model(tmp_path, "owner/repo", "model.gguf", yes=True)
            assert result == model_file
            # Resume appends new bytes to existing file
            assert model_file.read_bytes() == b"0123456789"
            # Should request with Range header
            call_kwargs = mock_stream.call_args[1]
            assert call_kwargs["headers"]["Range"] == "bytes=5-"

    def test_download_cancelled_by_user(self, tmp_path: Path):
        model_file = tmp_path / "model.gguf"
        mock_response = MagicMock()

        with patch("httpx.stream", return_value=mock_response) as mock_stream:
            with patch("builtins.input", return_value="n"):
                with pytest.raises(SystemExit) as exc_info:
                    ensure_model(tmp_path, "owner/repo", "model.gguf", yes=False)
                assert exc_info.value.code == 0
            mock_stream.assert_not_called()

    def test_download_confirmed_by_user(self, tmp_path: Path):
        model_file = tmp_path / "model.gguf"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-length": "10"}
        mock_response.iter_bytes.return_value = [b"0123456789"]
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=None)

        with patch("httpx.stream", return_value=mock_response) as mock_stream:
            with patch("builtins.input", return_value="y"):
                result = ensure_model(tmp_path, "owner/repo", "model.gguf", yes=False)
                assert result == model_file
                mock_stream.assert_called_once()
