from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass

import numpy as np


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIB_PATH = os.environ.get(
    "MOJO_MARKDOWN_IT_LIB",
    os.path.join(ROOT, "dist", "libmojo-markdown-it-py.so"),
)
I = ctypes.c_int64
_SMALL_ESCAPE_THRESHOLD = 4_096
# Measured crossover: at 8 MiB the 4-worker chunked escape ties the serial loop;
# at 10 MiB it is ~1.25x faster and the lead grows to ~2.1x at 32 MiB. Below
# 8 MiB, worker launch costs more than the split saves, so the threshold sits
# above the whole crossover band.
_PARALLEL_ESCAPE_THRESHOLD = 12 * 1_048_576
_ESCAPE_CHUNK_SIZE = 1_048_576
_ESCAPE_WORKERS = 8

_py_bytes_as_string = ctypes.pythonapi.PyBytes_AsString
_py_bytes_as_string.argtypes = [ctypes.py_object]
_py_bytes_as_string.restype = ctypes.c_void_p
_py_unicode_decode_utf8 = ctypes.pythonapi.PyUnicode_DecodeUTF8
_py_unicode_decode_utf8.argtypes = [
    ctypes.c_void_p,
    ctypes.c_ssize_t,
    ctypes.c_char_p,
]
_py_unicode_decode_utf8.restype = ctypes.py_object


class LibraryError(RuntimeError):
    pass


_library: ctypes.CDLL | None = None


def lib() -> ctypes.CDLL:
    global _library
    if _library is None:
        if not os.path.exists(LIB_PATH):
            raise LibraryError("Mojo library is not built; run `pixi run build`")
        _library = ctypes.CDLL(LIB_PATH)
        _library.mmi_scan_lines.argtypes = [I, I, I, I, I, I]
        _library.mmi_scan_lines.restype = I
        _library.mmi_scan_specials.argtypes = [I, I, I, I]
        _library.mmi_scan_specials.restype = I
        _library.mmi_escape_html.argtypes = [I, I, I, I]
        _library.mmi_escape_html.restype = I
        _library.mmi_escape_html_write.argtypes = [I, I, I, I]
        _library.mmi_escape_html_write.restype = I
        _library.mmi_escape_html_size.argtypes = [I, I]
        _library.mmi_escape_html_size.restype = I
        _library.mmi_escape_html_offsets.argtypes = [I, I, I, I, I, I]
        _library.mmi_escape_html_offsets.restype = I
        _library.mmi_escape_html_chunks.argtypes = [I, I, I, I, I, I, I, I]
        _library.mmi_escape_html_chunks.restype = I
    return _library


def _bytes_address(data: bytes) -> int:
    address = _py_bytes_as_string(data)
    if not address:
        raise LibraryError("CPython returned a null bytes buffer")
    return int(address)


def _checked_count(name: str, value: int, capacity: int) -> int:
    if value < 0:
        raise LibraryError(f"{name} rejected its buffer arguments")
    if value > capacity:
        raise LibraryError(f"{name} returned {value}, exceeding capacity {capacity}")
    return value


def _checked_size(name: str, value: int) -> int:
    if value < 0:
        raise LibraryError(f"{name} rejected its buffer arguments")
    if value > sys.maxsize:
        raise LibraryError(f"{name} returned an unrepresentable size")
    return value


@dataclass(slots=True)
class Line:
    text: str
    number: int
    first: int


def scan_lines(text: str) -> list[Line]:
    if not text:
        return []
    parts = text.split("\n")
    if parts[-1] == "":
        parts.pop()
    result: list[Line] = []
    for number, line in enumerate(parts):
        if line.endswith("\r"):
            line = line[:-1]
        result.append(Line(line, number, len(line) - len(line.lstrip(" \t"))))
    return result


def scan_specials(text: str) -> np.ndarray:
    data = text.encode("utf-8")
    if not data:
        return np.empty(0, dtype=np.int64)
    positions = np.empty(len(data), dtype=np.int64)
    count = lib().mmi_scan_specials(
        _bytes_address(data), len(data), positions.ctypes.data, len(positions)
    )
    count = _checked_count("special scanner", count, len(positions))
    return positions[:count].copy()


def _escape_python(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _should_parallel_escape(size: int, parallel: bool | None) -> bool:
    return size >= _PARALLEL_ESCAPE_THRESHOLD and parallel is not False


def _escape_parallel(data: bytes, workers: int) -> str:
    """Chunked escape: sizing and write passes both run in Mojo workers."""
    library = lib()
    chunks = (len(data) + _ESCAPE_CHUNK_SIZE - 1) // _ESCAPE_CHUNK_SIZE
    offsets = np.empty(chunks + 1, dtype=np.int64)
    size = library.mmi_escape_html_offsets(
        _bytes_address(data),
        len(data),
        offsets.ctypes.data,
        _ESCAPE_CHUNK_SIZE,
        len(offsets),
        workers,
    )
    size = _checked_size("parallel HTML escape sizing", size)
    target = ctypes.create_string_buffer(size)
    written = library.mmi_escape_html_chunks(
        _bytes_address(data),
        len(data),
        ctypes.addressof(target),
        offsets.ctypes.data,
        _ESCAPE_CHUNK_SIZE,
        len(offsets),
        size,
        workers,
    )
    return written, target, size


def escape_html(text: str, *, parallel: bool | None = None) -> str:
    if "&" not in text and "<" not in text and ">" not in text and '"' not in text:
        return text
    if len(text) < _SMALL_ESCAPE_THRESHOLD:
        return _escape_python(text)
    data = text.encode("utf-8")
    library = lib()
    use_parallel = _should_parallel_escape(len(data), parallel)
    if use_parallel:
        chunks = (len(data) + _ESCAPE_CHUNK_SIZE - 1) // _ESCAPE_CHUNK_SIZE
        written, target, size = _escape_parallel(data, min(chunks, _ESCAPE_WORKERS))
    else:
        source = _bytes_address(data)
        size = library.mmi_escape_html_size(source, len(data))
        size = _checked_size("HTML escape sizing", size)
        target = ctypes.create_string_buffer(size)
        written = library.mmi_escape_html_write(
            source,
            len(data),
            ctypes.addressof(target),
            size,
        )
    if written < 0:
        raise LibraryError("HTML escape rejected its buffer arguments")
    if written != size:
        raise LibraryError(f"HTML escape wrote {written} bytes, expected {size}")
    return _py_unicode_decode_utf8(ctypes.addressof(target), written, None)
