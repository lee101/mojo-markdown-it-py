from __future__ import annotations

import ctypes

import numpy as np

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
