from __future__ import annotations

from molmanager.memory_usage import (
    current_process_rss_bytes,
    format_memory_bytes,
    format_process_memory_status,
)


def test_format_memory_bytes() -> None:
    assert format_memory_bytes(0) == "0 B"
    assert format_memory_bytes(512) == "512 B"
    assert format_memory_bytes(1024) == "1 KiB"
    assert format_memory_bytes(1536) == "2 KiB"
    assert format_memory_bytes(5 * 1024 * 1024) == "5.0 MiB"
    assert format_memory_bytes(3 * 1024 * 1024 * 1024) == "3.0 GiB"


def test_current_process_rss_bytes_positive() -> None:
    rss = current_process_rss_bytes()
    assert rss is None or rss > 0


def test_format_process_memory_status() -> None:
    text = format_process_memory_status()
    assert text is None or text.startswith("Mem: ")
