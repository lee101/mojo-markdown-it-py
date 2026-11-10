from __future__ import annotations

import ctypes

import numpy as np
import pytest

from markdown_it.common.utils import escapeHtml as upstream_escape_html

from mojo_markdown_it._lib import (
    _PARALLEL_ESCAPE_THRESHOLD,
    _bytes_address,
    _should_parallel_escape,
    escape_html,
    lib,
    scan_lines,
    scan_specials,
)


# The Mojo kernels use `simd_width_of[DType.float64]()` = 4 lanes. The escape
# kernels work on 16-byte blocks (4 lanes of 8 bytes); the scanners work on
# 8-byte vectors. The lengths below pin the boundary at 4 lanes, 8 bytes, 16
# bytes and a row of other widths so the scalar tails run on any host width.
def _scan_widths() -> tuple[int, ...]:
    return (4, 8)


def _escape_widths() -> tuple[int, ...]:
    return (8, 16)


def _boundary_lengths(w: int) -> list[int]:
    # 0, 1, W-1, W, W+1 plus longer inputs that leave every remainder from 0 to
    # W-1, straddle whole blocks, and end on a large odd tail.
    return sorted({0, 1, w - 1, w, w + 1, 2 * w - 1, 2 * w, 2 * w + 1, 100_003, 1_000_003})


def _special_positions(data: bytes) -> np.ndarray:
    return np.array(
        [i for i, byte in enumerate(data) if byte in b"\n\r!&*<[\\_`"],
        dtype=np.int64,
    )


def test_line_scanner_handles_crlf_utf8_and_final_line():
    lines = scan_lines("alpha\r\n βeta\nlast")
    assert [(line.text, line.number, line.first) for line in lines] == [
        ("alpha", 0, 0),
        (" βeta", 1, 1),
        ("last", 2, 0),
    ]


def test_special_scanner_returns_utf8_byte_offsets():
    text = "plain *em* & <tag> `code`"
    data = text.encode()
    positions = scan_specials(text)
    assert np.array_equal(
        positions,
        np.array(
            [i for i, byte in enumerate(data) if byte in b"\n\r!&*<[\\_`"],
            dtype=np.int64,
        ),
    )


def test_mojo_html_escape_matches_upstream_semantics():
    assert escape_html('5 < 6 & "yes" > 0 — café') == (
        "5 &lt; 6 &amp; &quot;yes&quot; &gt; 0 — café"
    )


def test_simd_scanners_handle_vector_tails():
    source = "     abcdefghijk\r\n\t\tlast"
    lines = scan_lines(source)
    assert [(line.text, line.first) for line in lines] == [
        ("     abcdefghijk", 5),
        ("\t\tlast", 2),
    ]
    positions = scan_specials("abcdefg*tail&")
    assert np.array_equal(positions, np.array([7, 12], dtype=np.int64))


def test_serial_simd_escape_handles_utf8_and_tail():
    source = ('ab<&>"café' * 513) + "<x"
    assert len(source.encode()) % 4
    assert escape_html(source, parallel=False) == upstream_escape_html(source)


def test_simd_escape_size_handles_every_short_tail():
    library = lib()
    for length in range(1, 80):
        source = (b'ab<&>"' * 14)[:length]
        expected = len(upstream_escape_html(source.decode()).encode())
        assert library.mmi_escape_html_size(_bytes_address(source), length) == expected


def test_parallel_escape_threshold_and_output():
    assert not _should_parallel_escape(_PARALLEL_ESCAPE_THRESHOLD - 1, None)
    assert _should_parallel_escape(_PARALLEL_ESCAPE_THRESHOLD, None)
    assert not _should_parallel_escape(_PARALLEL_ESCAPE_THRESHOLD, False)
    source = ('ab<&>"café' * 800_000) + "<tail"
    assert len(source.encode()) >= _PARALLEL_ESCAPE_THRESHOLD
    assert escape_html(source, parallel=True) == upstream_escape_html(source)


def test_native_boundary_rejects_null_negative_and_short_buffers():
    library = lib()
    data = b"abc&"
    address = _bytes_address(data)
    one = np.empty(1, dtype=np.int64)
    assert library.mmi_scan_lines(address, len(data), one.ctypes.data, one.ctypes.data, one.ctypes.data, 0) == -1
    assert library.mmi_scan_specials(address, -1, one.ctypes.data, 1) == -1
    assert library.mmi_scan_specials(0, len(data), one.ctypes.data, 1) == -1
    target = ctypes.create_string_buffer(4)
    assert library.mmi_escape_html(address, len(data), ctypes.addressof(target), 4) == -1
    assert library.mmi_escape_html_size(0, len(data)) == -1


def test_native_parallel_boundary_rejects_invalid_metadata():
    library = lib()
    data = b"<value>"
    address = _bytes_address(data)
    offsets = np.zeros(2, dtype=np.int64)
    assert library.mmi_escape_html_offsets(address, len(data), offsets.ctypes.data, 0, 2) == -1
    assert library.mmi_escape_html_offsets(address, len(data), offsets.ctypes.data, 4, 2) == -1
    offsets[:] = [0, 100]
    target = ctypes.create_string_buffer(16)
    assert library.mmi_escape_html_chunks(
        address, 4, ctypes.addressof(target), offsets.ctypes.data, 4, 2, 16
    ) == -1


def test_chunked_escape_offsets_and_write_match_the_serial_kernel():
    library = lib()
    source = ('ab<&>"café<&>' * 97) + "<tail<&>"
    data = source.encode()
    expected = upstream_escape_html(source).encode()
    for chunk_size in (1, 3, 5, 16, 64, len(data) - 1, len(data)):
        chunks = (len(data) + chunk_size - 1) // chunk_size
        offsets = np.zeros(chunks + 1, dtype=np.int64)
        total = library.mmi_escape_html_offsets(
            _bytes_address(data), len(data), offsets.ctypes.data, chunk_size, chunks + 1
        )
        assert total == len(expected)
        # Offsets are a non-decreasing prefix sum bounded by the escape total.
        assert offsets[0] == 0
        assert np.all(np.diff(offsets) >= 0)
        assert offsets[-1] == len(expected)
        target = ctypes.create_string_buffer(total)
        assert library.mmi_escape_html_chunks(
            _bytes_address(data),
            len(data),
            ctypes.addressof(target),
            offsets.ctypes.data,
            chunk_size,
            chunks + 1,
            total,
        ) == total
        assert target.raw[:total] == expected


@pytest.mark.parametrize("width", _escape_widths())
def test_simd_escape_handles_width_boundary_lengths(width):
    # A clean run carries the vector body, then a short mixed run follows, so
    # the escape kernel's copy path and its scalar lane loop both run. The
    # trailer is at least 16 bytes, so every non-zero `length % 16` remainder
    # lands in the scalar tail; the trailer holds no ">" so the sizing kernel
    # is exercised on clean blocks too.
    library = lib()
    for length in _boundary_lengths(width):
        clean_len = length - (length % 16)
        trailer = b'""&<a&<>' * 4
        data = (b"abcdefgh" * (clean_len // 8) + trailer)[:length]
        text = data.decode()
        expected = upstream_escape_html(text)
        assert escape_html(text, parallel=False) == expected
        assert escape_html(text) == expected
        assert library.mmi_escape_html_size(_bytes_address(data), length) == len(
            expected.encode()
        )
        target = ctypes.create_string_buffer(len(expected.encode()))
        written = library.mmi_escape_html_write(
            _bytes_address(data), length, ctypes.addressof(target), len(expected.encode())
        )
        assert written == len(expected.encode())
        assert target.raw[:written] == expected.encode()


@pytest.mark.parametrize("width", _escape_widths())
def test_simd_clean_and_mixed_runs_handle_width_boundary_lengths(width):
    library = lib()
    for length in _boundary_lengths(width):
        # A clean run followed by a special every 3 bytes exercises the
        # copy-while-clean path and the scalar lane loop on the same input.
        clean_len = length - (length % 16)
        data = (b"abcdefgh" * (clean_len // 8) + b"x<y" * (length + 8))[:length]
        text = data.decode()
        expected = upstream_escape_html(text)
        assert escape_html(text, parallel=False) == expected
        assert library.mmi_escape_html_size(_bytes_address(data), length) == len(
            expected.encode()
        )
        target = ctypes.create_string_buffer(len(expected.encode()))
        written = library.mmi_escape_html_write(
            _bytes_address(data), length, ctypes.addressof(target), len(expected.encode())
        )
        assert written == len(expected.encode())
        assert target.raw[:written] == expected.encode()


@pytest.mark.parametrize("width", _scan_widths())
def test_special_scanner_handles_width_boundary_lengths(width):
    alphabet = b"ab*&<>`[]\\_\n\r!"
    for length in _boundary_lengths(width):
        data = (alphabet * (length // len(alphabet) + 2))[:length]
        assert np.array_equal(scan_specials(data.decode()), _special_positions(data))


@pytest.mark.parametrize("width", _scan_widths())
def test_special_scanner_reports_truncation_at_every_boundary_length(width):
    library = lib()
    alphabet = b"a*b&c<d"
    for length in _boundary_lengths(width):
        data = (alphabet * (length // len(alphabet) + 2))[:length]
        expected = _special_positions(data)
        positions = np.empty(len(expected) + 1, dtype=np.int64)
        # Capacity is exactly enough: the kernel must fill it and return the count.
        count = library.mmi_scan_specials(
            _bytes_address(data), length, positions.ctypes.data, len(positions)
        )
        assert count == len(expected)
        assert np.array_equal(positions[:count], expected)
        if expected.size:
            # One short: the kernel must report failure rather than overrun.
            assert library.mmi_scan_specials(
                _bytes_address(data), length, positions.ctypes.data, len(expected) - 1
            ) == -1


@pytest.mark.parametrize("width", _scan_widths())
def test_line_scanner_handles_width_boundary_lengths(width):
    for length in _boundary_lengths(width):
        # 5 leading spaces, a "\r\n", then text: the indent skip, the CR strip
        # and the newline scan all have a SIMD loop plus a scalar tail.
        source = "     " + "xyz" * (length // 3 + 1) + "\r\n\tlast"
        lines = scan_lines(source)
        expected = source.split("\n")
        if expected[-1] == "":
            expected.pop()
        expected = [line[:-1] if line.endswith("\r") else line for line in expected]
        assert [line.text for line in lines] == expected
        assert [line.number for line in lines] == list(range(len(expected)))
        assert [line.first for line in lines] == [
            len(line) - len(line.lstrip(" \t")) for line in expected
        ]
