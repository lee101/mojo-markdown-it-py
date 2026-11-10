"""Crossover measurement for the chunked HTML escape (Mojo-side timing).

Times the shipped `_chunk_offsets` + `_chunk_write` helpers through
`mmi_escape_html_chunks_timed`, best of N reps, inside Mojo's monotonic clock.
Python's clock on this host drifts by milliseconds, so it is only used for the
reference serial path.
"""
from __future__ import annotations

import ctypes
import math
import sys
import time

sys.path.insert(0, "python")

import numpy as np

import mojo_markdown_it._lib as L

UNIT = '5 < 6 & "quoted" > 0; café. '
CHUNK = 1_048_576


def main() -> None:
    lib = L.lib()
    lib.mmi_escape_html_chunks_timed.argtypes = [L.I] * 9
    lib.mmi_escape_html_chunks_timed.restype = L.I
    print(f"load average: {open('/proc/loadavg').read().split()[0]}")
    print("| Input | serial (Python clock) | w1 | w2 | w4 | w8 |")
    print("|---|---:|---:|---:|---:|---:|")
    for mib in (1, 2, 4, 6, 8, 10, 12, 14, 16, 32):
        n = int(mib * 1_048_576)
        text = (UNIT * (n // len(UNIT) + 1))[:n]
        data = text.encode()
        source = L._bytes_address(data)
        needed = lib.mmi_escape_html_size(source, len(data))
        target = ctypes.create_string_buffer(needed)
        taddr = ctypes.addressof(target)
        chunks = (len(data) + CHUNK - 1) // CHUNK
        offsets = np.empty(chunks + 1, dtype=np.int64)
        cells = []
        for workers in (1, 2, 4, 8):
            micros = lib.mmi_escape_html_chunks_timed(
                source, len(data), taddr, offsets.ctypes.data,
                CHUNK, len(offsets), needed, workers, 25,
            )
            assert micros > 0
            # The chunked result must byte-match the serial kernel.
            lib.mmi_escape_html_chunks(
                source, len(data), taddr, offsets.ctypes.data,
                CHUNK, len(offsets), needed, workers,
            )
            serial_buf = ctypes.create_string_buffer(needed)
            assert lib.mmi_escape_html_write(
                source, len(data), ctypes.addressof(serial_buf), needed
            ) == needed
            assert target.raw[:needed] == serial_buf.raw[:needed], (mib, workers)
            cells.append(micros / 1000)
        # Reference serial path, best of 9 with Python's clock.
        best = math.inf
        for _ in range(9):
            start = time.perf_counter()
            L.escape_html(text, parallel=False)
            best = min(best, time.perf_counter() - start)
        print(
            f"| {mib:g} MiB | {best * 1e3:.2f} ms | "
            + " | ".join(f"{c:.2f} ms" for c in cells)
            + " |"
        )


if __name__ == "__main__":
    main()
