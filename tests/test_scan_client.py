import pytest

from armor.scan_client import read_text_file


def test_file_limit_is_enforced_without_truncation(tmp_path):
    path = tmp_path / "large.txt"
    path.write_bytes(b"a" * 1_000_001)
    with pytest.raises(ValueError, match="1 MB"):
        read_text_file(path)


def test_invalid_utf8_is_not_silently_replaced(tmp_path):
    path = tmp_path / "binary.txt"
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(UnicodeDecodeError):
        read_text_file(path)
