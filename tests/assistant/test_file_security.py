"""Tests for the file-access security helper used by read_file / grep tools."""

from __future__ import annotations

import os

import pytest

from openlist_ani.assistant.tool.builtin import _file_security as fs


@pytest.fixture
def in_project_root(tmp_path, monkeypatch):
    """Switch CWD to a fake project root containing a whitelisted layout."""
    for d in fs.WHITELIST_DIRS:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestResolveSafePath:
    def test_relative_path_inside_whitelist_resolves(self, in_project_root):
        f = in_project_root / "src" / "hello.py"
        f.write_text("print('hi')\n")
        out = fs.resolve_safe_path("src/hello.py")
        assert out == f.resolve()

    def test_absolute_path_inside_whitelist_resolves(self, in_project_root):
        f = in_project_root / "skills" / "x.md"
        f.write_text("ok")
        out = fs.resolve_safe_path(str(f))
        assert out == f.resolve()

    def test_path_outside_whitelist_rejected(self, in_project_root):
        # config.toml-style file at project root, not in any whitelist dir
        (in_project_root / "config.toml").write_text("secret = 1")
        with pytest.raises(fs.FileAccessDenied):
            fs.resolve_safe_path("config.toml")

    def test_dotdot_traversal_rejected(self, in_project_root):
        with pytest.raises(fs.FileAccessDenied):
            fs.resolve_safe_path("src/../../etc/passwd")

    def test_absolute_outside_rejected(self, in_project_root):
        with pytest.raises(fs.FileAccessDenied):
            fs.resolve_safe_path("/etc/passwd")

    def test_symlink_pointing_outside_rejected(self, in_project_root):
        outside = in_project_root.parent / "outside.txt"
        outside.write_text("nope")
        link = in_project_root / "src" / "link.txt"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unsupported on this platform")
        with pytest.raises(fs.FileAccessDenied):
            fs.resolve_safe_path("src/link.txt")

    @pytest.mark.parametrize(
        "name",
        [
            ".env",
            ".env.production",
            "secrets.json",
            "api_key.txt",
            "tokens.yaml",
            "credentials.json",
            "id_rsa",
            "server.pem",
            "server.key",
            "cookies.txt",
            "private_key.pem",
        ],
    )
    def test_sensitive_filename_rejected(self, in_project_root, name):
        f = in_project_root / "data" / name
        f.write_text("dummy")
        with pytest.raises(fs.FileAccessDenied):
            fs.resolve_safe_path(f"data/{name}")

    def test_empty_path_rejected(self, in_project_root):
        with pytest.raises(fs.FileAccessDenied):
            fs.resolve_safe_path("")


class TestRedactSecrets:
    def test_returns_zero_for_clean_text(self):
        text = "def add(a, b):\n    return a + b"
        out, n = fs.redact_secrets(text)
        assert out == text
        assert n == 0

    def test_redacts_api_key_assignment(self):
        text = "openai_api_key = 'sk-1234567890abcdefghij'"
        out, n = fs.redact_secrets(text)
        assert n >= 1
        assert "sk-1234567890abcdefghij" not in out
        assert "<REDACTED>" in out

    def test_redacts_bearer_header(self):
        text = "Authorization: Bearer abc.def.ghi.jkl.mno"
        out, n = fs.redact_secrets(text)
        assert n >= 1
        assert "abc.def.ghi.jkl.mno" not in out

    def test_redacts_aws_access_key(self):
        text = "AKIAIOSFODNN7EXAMPLE"
        out, n = fs.redact_secrets(text)
        assert n >= 1
        assert text not in out

    def test_redacts_github_pat(self):
        text = "token: ghp_abcdefghijklmnopqrstuvwxyz0123456789"
        out, n = fs.redact_secrets(text)
        assert n >= 1
        assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in out

    def test_redacts_pem_block(self):
        text = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEpAIBAAKCAQEA...\n"
            "-----END RSA PRIVATE KEY-----"
        )
        out, n = fs.redact_secrets(text)
        assert n >= 1
        assert "MIIEpAIBAAKCAQEA" not in out

    def test_redacts_telegram_bot_token(self):
        text = "bot_token = 123456789:AAEhBP0av28Vnabcdefghij_klmnopqrst"
        out, n = fs.redact_secrets(text)
        assert n >= 1
        assert "AAEhBP0av28Vnabcdefghij_klmnopqrst" not in out


class TestIsLikelyBinary:
    def test_text_sample(self):
        assert fs.is_likely_binary(b"hello world\n") is False

    def test_null_byte(self):
        assert fs.is_likely_binary(b"hello\x00world") is True

    def test_empty(self):
        assert fs.is_likely_binary(b"") is False
