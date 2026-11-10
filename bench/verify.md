# Verify pass

Date: 2026-11-10. Host: Intel Xeon E5-2697 v4 @ 2.30 GHz, Linux x86-64, shared
(load average 14-17 during these runs, 72 CPUs). Pinned Mojo nightly
`1.2.0.dev2026092605`, `markdown-it-py` 4.2.0. Nothing below was copied from the
README; every figure was produced in this session.

## Gate

```console
$ pixi run build && pixi run test
73 passed in 2.39s
```

Both green (exit 0). Re-run at the end of this pass (only README and this file
changed), also green; no source or test file changed.

## What blocked `pixi run bench`

`pixi run bench` wraps the body in `flock /tmp/mojo-bench.lock`. On this shared
factory host that lock was continuously held and queued behind by other jobs
(`mojo-karateclub` holding it, plus ten other waiting `flock` processes).
Three attempts were made:

- a plain `pixi run bench`: still waiting after ~8 minutes, killed;
- `timeout 280 pixi run bench`: timed out still waiting (exit 124);
- a background `timeout 1800 pixi run bench` started before the README edits:
  still waiting when this report was written, killed on timeout.

So the one bench body takes ~27 s, but the queue ahead of it is hours long. Every
benchmark number below therefore comes from running `bench/bench.py`
**directly, without the wrapper lock** (`PYTHONPATH=python pixi run python
bench/bench.py`). That is the same code path the `pixi run bench` task runs
after it acquires the lock, and the README already stated its numbers were taken
without the lock; this pass is explicit about it. Four runs of the body were
taken; the README table uses the per-row minimum across them, with the spread
noted.

## Main table (`bench/bench.py`)

Four runs, per-row minimum:

| Operation | Mojo port | markdown-it-py | Relative |
|---|---:|---:|---:|
| HTML escape | 24.72 ms | 38.63 ms | 1.56x faster |
| parse plain blocks | 172.33 ms | 379.46 ms | 2.20x faster |
| render plain blocks | 191.38 ms | 413.75 ms | 2.16x faster |
| parse mixed CommonMark | 607.94 ms | 841.23 ms | 1.38x faster |
| render mixed CommonMark | 594.27 ms | 822.81 ms | 1.38x faster |

All four runs, HTML escape row: 22.46 / 22.71 / 26.30 / 24.72 ms for the port,
38.65 / 38.19 / 39.39 / 38.63 ms upstream.

**Same-input and no-skipped-work check.** Both sides are timed on the same Python
`str` object in the same process. For HTML escape and both render rows the bench
asserts `mojo_result == upstream_result` before timing. The two parse rows also
produce equal token results in practice, but `bench/bench.py` only asserts
equality when the name starts with `HTML` or `render`; the parse rows are timed
around `parse()` only, on both sides, so neither side does rendering work inside
those timers. Nothing in the harness short-circuits the Mojo side: both call the
same-size input through each library's public entry point.

Not reproduced within 20%: the HTML escape row. The README said 21.80 ms / 1.80x.
The lowest the port's escape measured was 22.46 ms, 3% above that, but the
*ratio* measured 1.50-1.72x, not 1.80x, because upstream also came in at 38-39 ms
against 39.15 ms. Corrected to the measured minimum (24.72 ms / 1.56x) with the
range stated. The other four rows reproduced within 20% (parse plain 2.20x vs
2.13x, render plain 2.16x vs 2.10x, parse mixed 1.38x vs 1.35x, render mixed
1.38x vs 1.35x) and are unchanged. No row is slower than upstream, so none was
re-labeled.

## SIMD section (direct `mmi_escape_html_write`, 4.64 MB, best of 40)

| Buffer | README "After" | Measured |
|---|---:|---:|
| Mixed, 24% special | 6.26-6.51 ms | 6.27 ms |
| Plain | 1.67-1.70 ms | 1.67 ms |

Reproduced. The "Before" column (7.04-7.31 ms) was not re-run: that code no
longer exists in this checkout without reverting source, which the verify rules
forbid. The end-to-end escape figure in the same section (21.6-21.8 ms, and
"same as before") did not reproduce; replaced with the measured 22.46-26.30 ms.

## Parallel crossover (`bench/parallel_crossover.py`)

Ran unmodified. Spread against the README is 0-3% per cell; the table was
rewritten with the measured values rather than leaving sub-tolerance differences
in place. Example rows: 8 MiB w8 12.68 ms (was 13.04), 16 MiB w8 15.51 (was
15.88), 32 MiB w4 28.43 (was 29.95, 5%).

The README's "2 workers are worse than 4 everywhere and worse than serial below
16 MiB" does not hold in the measurement: w2 also beats w4 at 2 MiB (9.89 vs 21.08 ms;
w1 is the serial loop inside the timed kernel). The sentence was narrowed to
what the table shows.

## "Before and after" parallel table: not reproduced, direction reversed

This table compares the current path against the Python-threaded path that
`fb9c6a0` removed. The removed code is fully visible in
`git show fb9c6a0 -- python/mojo_markdown_it/_lib.py`, so it was reconstructed
faithfully in a one-off script: sizing via `ThreadPoolExecutor` over
`mmi_escape_html_size`, cumsum, then writes via `ThreadPoolExecutor` over
`mmi_escape_html_write`, 8 workers, 4 MiB threshold; compared with
`escape_html(parallel=False)` and the shipped path, best of 11:

| MiB | serial | shipped | old Python-threaded |
|---|---:|---:|---:|
| 1 | 5.43 ms | 5.35 ms | 6.59 ms |
| 2 | 13.94 ms | 11.68 ms | 10.49 ms |
| 4 | 23.02 ms | 22.59 ms | 20.30 ms |
| 8 | 49.57 ms | 49.84 ms | 40.15 ms |
| 12 | 77.92 ms | 74.74 ms | 58.55 ms |
| 16 | 92.27 ms | 84.62 ms | 66.97 ms |

The README claimed the Mojo path was 1.3x faster at 12 MiB and 1.7x faster at
16 MiB. Measured here the old Python-threaded path is faster at every size. The
section now states that instead of claiming a win. Not a proof that the shipped
path is wrong (both paths are byte-identical, and the serial Mojo kernel is only
~2-8% behind shipped serial in this run at 12-16 MiB), but the README's
comparison is not substantiated and now says so. The README's serial column
(≈1.6 / 3.3 / 6.4 / 13.7 / 20.9 / 28.1 ms) was also not reproduced by this
script's serial reference (the Python wrapper around `escape_html(parallel=False)`,
5.43-92.27 ms); it is a different measurement (wrapper included) from the `w1`
crossover column, so the two are not comparable and the table now labels it as
the wrapper.

Separately, four runs of `escape_html` at 12 and 16 MiB gave serial 40-44 /
56-59 ms and shipped 39-41 / 48-56 ms. The shipped path is faster than serial
there, but by a smaller and noisier margin than the README implied.

Note on `_ESCAPE_WORKERS`: the README says the worker count "was raised to 8".
`git grep` at the pre-parallel commit `4178327` shows `_ESCAPE_WORKERS = 8` and a
4 MiB threshold already, so the claim that it was raised in `fb9c6a0` is not
supported by the diff. Left in prose; flagged here.

## GPU arithmetic-intensity table

Ran the three C ABI calls directly on 4.64 MB of the mixed input, best of 20:

| Kernel | README | Measured | README bandwidth | Measured |
|---|---:|---:|---:|---:|
| `mmi_escape_html_size` | 0.46 ms | 0.51 ms | 9.2 GB/s | 9.1 GB/s |
| `mmi_escape_html_write` | 5.89 ms | 6.31 ms | 1.4 GB/s | 1.24 GB/s |
| `mmi_scan_lines` | 1.71 ms | 0.96 ms | 2.5 GB/s | 4.8 GB/s |

`mmi_scan_lines` is 44% below the README figure, outside tolerance; the other two
are within 20% (11% and 7%). Table updated. The conclusion is unaffected: the
input is still 4.64 MB (README said "4 MiB buffers") and at most ~0.5
flops/byte.

GPU availability re-checked: `nvidia-smi --query-gpu=memory.free` reports
4559 MiB free (README: 4557 MiB; same device). `git log --all --
bench/analysis.md` is empty, as the README says. No GPU kernel attempted, so
nothing GPU was unverifiable beyond the device check.

## Tests: parity assertions

`git log --all -- tests/` touches `tests/` in five commits. Only two later
commits modify test files (`4178327` adds tests, `fb9c6a0` changes one input
size and extends call signatures). The only deleted lines in tests history are:

- the parallel-escape *input* size, raised from `800_000` to `1_300_000`
  repetitions so the input still clears the new 12 MiB threshold. The assertion
  (`== upstream_escape_html(source)`) is untouched;
- four kernel-call lines in the boundary tests whose signature gained a
  `workers` argument; each was replaced by a stricter assertion that also checks
  a negative worker count is rejected.

`tests/test_parity.py` has never been modified after its initial commit.
No `pytest.approx`, tolerance, `xfail` or `skip` markers exist anywhere in
`tests/`. Nothing was removed or loosened, so nothing was restored. Parity is
still asserted against upstream `markdown-it-py` in rendering, token streams,
inline rendering, options, environment, unsafe links and enable/disable rules.

## Changes made

- `README.md`: main table HTML-escape row corrected to measured values (the other
  four reproduced within 20% and are unchanged); SIMD end-to-end figure
  corrected; parallel crossover table re-measured; the "2 workers ..." sentence
  corrected; "Before and after" table replaced with this pass's measurements and
  its claim of a 1.3-1.7x win withdrawn; threshold-loss bullets re-numbered from
  the measured table; GPU table re-measured and its bandwidth line corrected.
- This file (`bench/verify.md`) added.
- No source, build, or test file was changed. No bug with a failing test was
  found, so per the rules no code was changed.
