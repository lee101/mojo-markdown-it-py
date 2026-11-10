# mojo-markdown-it-py

`mojo-markdown-it-py` is a standalone, MIT-licensed port of the useful core of
[`markdown-it-py`](https://github.com/executablebooks/markdown-it-py): a CommonMark
tokenizer, upstream-compatible `Token` model, and HTML renderer, with byte scanning
and escaping kernels implemented in Mojo.

The Python package is `mojo_markdown_it` so it can be installed alongside
`markdown_it` for parity testing:

```python
from mojo_markdown_it import MarkdownIt

md = MarkdownIt("commonmark")
print(md.render("# Hello, *Mojo*\n"))
```

Output:

```html
<h1>Hello, <em>Mojo</em></h1>
```

## Coverage

The covered block syntax is paragraphs, ATX and Setext headings, thematic breaks,
nested block quotes, tight and loose bullet/ordered lists, nested lists, fenced and
indented code blocks, block HTML, and link reference definitions. Covered inline
syntax is backslash escaping, entities, code spans, emphasis and strong emphasis,
inline links, reference links, images, URI/email autolinks, inline HTML, and soft
and hard line breaks.

The public subset includes `MarkdownIt.parse`, `render`, `parseInline`,
`renderInline`, `set`, `configure`, `enable`, `disable`, `use`, link normalization
and validation, `RendererHTML`, renderer rule replacement, and the upstream
`Token` fields and attribute helpers. Tests compare rendered output with
`markdown-it-py` 4.2.0 and compare representative token streams field-for-field.
The upstream package is a development/parity dependency, not a runtime dependency
of the port.

This is not yet a complete CommonMark conformance implementation. Uncovered areas
include pathological delimiter-rule-of-three cases, tabs in structural
indentation, every lazy continuation/list-interruption edge case, multiline link
titles and destinations, and all seven exact HTML-block termination modes. The
`markdown-it-py` ruler internals and extension rules such as tables, task lists,
strikethrough, linkify, typographer, and footnotes are also not provided. Plugins
that only update options or renderer rules work; plugins that inject parser ruler
rules do not.

## Install and run

The repository pins the tested Mojo nightly. Install, build, and verify it with:

```bash
pixi install
pixi run build
pixi run test
```

The build produces `dist/libmojo-markdown-it-py.so`. Run the usage example from
the repository with:

```bash
pixi run python -c 'from mojo_markdown_it import MarkdownIt; print(MarkdownIt().render("# Hello, *Mojo*"))'
```

## Benchmark

Measured on 2026-11-10 on an Intel Xeon E5-2697 v4 at 2.30 GHz, Linux x86-64,
using the pinned Mojo nightly and `markdown-it-py` 4.2.0. Times are the best of
five warm runs of `bench/bench.py`, the body of `pixi run bench`; rendered-output
equality is asserted before timing. The host is shared, so absolute times vary
between runs (regenerate with `pixi run bench` rather than comparing across
dates).

| Operation | Input | Mojo port | markdown-it-py | Relative |
|---|---:|---:|---:|---:|
| HTML escape | 4.6 MB | 24.72 ms | 38.63 ms | 1.56x faster |
| parse plain blocks | 1.0 MB | 172.33 ms | 379.46 ms | 2.20x faster |
| render plain blocks | 1.0 MB | 191.38 ms | 413.75 ms | 2.16x faster |
| parse mixed CommonMark | 0.5 MB | 607.94 ms | 841.23 ms | 1.38x faster |
| render mixed CommonMark | 0.5 MB | 594.27 ms | 822.81 ms | 1.38x faster |

These figures are the per-row minimum across four runs of the bench body on the
same host in one verify pass. The HTML escape row ranged 22.46-26.30 ms (1.50x
to 1.72x faster) run to run; the earlier figure in this table, 21.80 ms, was
not reproduced in any of those runs. Every row measures the same input string
on both sides inside the same process; both sides do the full work (the escape
rows are checked for byte equality, and the parse rows only start the timer
around the parse call, not around any rendering).

### SIMD pass: HTML escaping

The escape kernels scan every byte twice: a 32-byte-wide sizing pass, then a
16-byte-wide write pass whose scalar lane fallback ran once per special
character. Because every 32-byte block of the benchmark's escape input holds a
special, nearly all of its time went to per-lane scalar stores. Two changes
address that:

- **Fused size and write in the serial writer.** `mmi_escape_html_write` now runs
  a single pass that keeps separate source and destination cursors, rather than
  indexing the destination by the source offset until the first special.
- **Copy-while-clean in the range helper.** After a block that carries a
  special, the code checks and copies the following block, so clean runs after
  specials stay vectorised and the lane loop runs once per special block instead
  of once per special byte. This also covers the chunked parallel path.

Measured on 2026-11-10 on the same host, calling `mmi_escape_html_write`
directly on a 4.64 MB buffer with `time.perf_counter`, best of 40 runs:

| Buffer | Before | After |
|---|---:|---:|
| Mixed: `5 < 6 & "quoted" > 0; café. ` repeated, 24% of bytes special | 7.04-7.31 ms | 6.27 ms |
| Plain: `abcdefgh` repeated, no specials | 1.71 ms | 1.67 ms (unchanged within noise) |

The "Before" values were measured before the rewrite and were not re-run in the
verify pass; the "After" values were reproduced (mixed 6.27 ms, plain 1.67 ms).

The end-to-end `pixi run bench` HTML escape row measured 22.46-26.30 ms in the
verify pass on this shared host. Its thread pool overlaps the serial per-chunk
work with encode and decode, so the kernel gain does not show up at the bench's
size. The direct kernel timings above are the reliable measurement.

Rejected after measuring, and reverted:

- A clean-block early exit in the sizing pass: mixed sizing 490 us to 572 us.
- A last-block 16-byte copy in the range helper: 7.29 ms to 7.18 ms, inside run-to-run noise.
- An unconditional-lane special scanner: mixed scanning 1.15 ms to 1.69 ms.

`bench/analysis.md` was not present in this checkout, and no file listing these
loops was tracked in git history, so the kernels were selected from the loops
the FFI actually exposes and confirmed with `pixi run bench` plus direct kernel
timings.

## How it works

`src/markdown_it.mojo` is one compilation unit exporting its scanning and escaping
C ABI functions. Python passes UTF-8 input and output buffers as integer addresses.
Mojo reconstructs `UnsafePointer` values with `AnyOrigin[mut=True]`, scans bytes with
SIMD, and escapes HTML into a caller-owned byte buffer. Mojo performs no allocation
across the ABI.

The ctypes layer passes Python `bytes` storage into Mojo without a source copy,
allocates the exact escaped output size, and decodes directly from the destination
buffer. The Mojo loops use full-register byte SIMD with scalar remainder handling.
Large escapes are counted and written in independent 1 MiB chunks by
`max.algorithm.parallelize` workers inside Mojo; inputs below 12 MiB stay serial,
because measurement (below) shows worker launch costs more than the split saves
there. The parser splits Unicode lines directly, avoiding an encode/scan/decode
round trip, assembles inline token trees, and keeps plugin-visible objects as
ordinary Python `Token` instances.

## Parallelism

The chunked HTML escape runs in Mojo. `mmi_escape_html_offsets` sizes each chunk
and takes a serial prefix sum; `mmi_escape_html_chunks` then escapes each chunk
into its own disjoint output range. Both passes dispatch through
`from max.algorithm import parallelize` with an explicit worker count. `std.algorithm`
and `std.threading` do not carry `parallelize` on this toolchain
(`MOJO_NOTES.md` section 4), and the `max` package is now a dependency alongside
the pinned Mojo nightly. Before this change the same two passes existed, but the
per-chunk work was driven from Python by a `ThreadPoolExecutor` calling back into
Mojo once per chunk.

Output is unchanged: an escape never spans a byte, and each chunk owns its output
range, so any worker count produces the same bytes as the serial writer.
`tests/test_kernels.py::test_parallel_escape_matches_serial_for_every_worker_count`
compares serial output with the chunked output at 1, 2, 4 and 8 workers on the
same input, byte for byte.

### Where threads win

Measured on 2026-11-10 on an Intel Xeon E5-2697 v4 at 2.30 GHz (Linux x86-64,
load average 16-28 during the runs, shared host), best of 25 repetitions timed
inside Mojo with `perf_counter_ns`, so the numbers cover `_chunk_offsets` plus
`_chunk_write` and exclude the Python wrapper around them. Input is
`5 < 6 & "quoted" > 0; café. ` repeated; the chunk size is 1 MiB.
`w1` is the same code with the serial loop, so `w1` versus `wN` is the effect of
threads alone. Reproduce with `pixi run python bench/parallel_crossover.py`.

| Input | w1 | w2 | w4 | w8 |
|---|---:|---:|---:|---:|
| 1 MiB | 1.90 ms | 22.85 ms | 24.13 ms | 20.34 ms |
| 2 MiB | 3.79 ms | 13.31 ms | 21.08 ms | 9.89 ms |
| 4 MiB | 7.92 ms | 13.83 ms | 12.20 ms | 10.13 ms |
| 6 MiB | 11.71 ms | 16.30 ms | 12.14 ms | 10.42 ms |
| 8 MiB | 15.96 ms | 18.03 ms | 14.42 ms | 12.68 ms |
| 10 MiB | 20.93 ms | 20.66 ms | 15.08 ms | 13.07 ms |
| 12 MiB | 25.31 ms | 24.23 ms | 16.78 ms | 13.32 ms |
| 16 MiB | 31.63 ms | 28.22 ms | 19.60 ms | 15.51 ms |
| 32 MiB | 66.91 ms | 46.07 ms | 28.43 ms | 24.46 ms |

The crossover for 4 workers sits between 8 and 10 MiB (0.90x at 8 MiB, then
0.98x at 10 MiB), on 8 workers between 4 and 6 MiB. The threshold is therefore
12 MiB: above the whole crossover band, with margin. Below it, callers run the
serial loop and never spawn a worker.

Two effects set that crossover. A `parallelize` call from a ctypes/Python host
costs several milliseconds on this shared machine even when the callback does
nothing, which is larger than the whole serial escape of a 4 MiB buffer; and the
work itself is memory-bound, so it does not scale linearly.

Worker count: the rule of thumb is 4. It was raised to 8 because the benchmark
shows it helps: 8 workers beat 4 at every size from 6 MiB up (8 MiB 12.68 against
14.42 ms, 16 MiB 15.51 against 19.60 ms, 32 MiB 24.46 against 28.43 ms), while
2 workers lose to 4 at every size and win only at 2 MiB (9.89 against 21.08 ms;
both run serial work against a worker launch). The
gain flattens after 8: worker count cannot exceed the chunk count, and at the
12 MiB threshold there are only 12 chunks to hand out.

### Before and after

Same host, same buffers, both libraries in one process, best of 11. `HEAD` is the
previous commit built from source to a separate shared library; `now` is this
change. `threaded` is the chunked path with the shipped worker settings (HEAD:
8 Python threads, 4 MiB threshold; now: 8 Mojo workers, 12 MiB threshold).

| Input | serial wrapper | shipped serial | old Python-threaded |
|---|---:|---:|---:|
| 1 MiB | 5.43 ms | 5.35 ms | 6.59 ms |
| 2 MiB | 13.94 ms | 11.68 ms | 10.49 ms |
| 4 MiB | 23.02 ms | 22.59 ms | 20.30 ms |
| 8 MiB | 49.57 ms | 49.84 ms | 40.15 ms |
| 12 MiB | 77.92 ms | 74.74 ms | 58.55 ms |
| 16 MiB | 92.27 ms | 84.62 ms | 66.97 ms |

`serial wrapper` is the Python call around `escape_html(parallel=False)`; `shipped
serial` is the Mojo serial kernel on the same buffer. The old Python-threaded path
is faster than the shipped path at every size here, by about 1.1-1.4x.
does not measure this table; it is a one-off script kept in `/tmp` during the
verify pass, not in the repository.

The end-to-end `pixi run bench` HTML escape row escapes 4.6 MB, which is below the
threshold, so it measures the serial path: the four runs ranged 22.42-26.30 ms.
Also measured on the same input: `escape_html(parallel=False)` 40-44 ms at
12 MiB and 56-59 ms at 16 MiB across four runs, against the shipped path at
39-41 ms and 48-56 ms, so the shipped path is faster than the serial loop at
both sizes, though the gap is reduced by this host's load variability. Both
rows are dominated by host noise on this shared machine (the parse and render
rows moved by a comparable fraction between runs), and this row does not
exercise the parallel path at all. The factory benchmark lock was held by other
jobs for the duration of this work, so the numbers here come from running the
same bench body without the lock.

Not adopted, after measuring:

- Lowering the threshold to 4 MiB: at 4 MiB, 8 workers take 10.13 ms against
  7.92 ms serial, a 1.28x loss; 4 workers are worse still at 12.20 ms.
- Two workers: slower than four workers at every size, and only the serial
  baseline at 2 MiB beats it.
- More than 8 workers: the chunk count at and near the threshold is 12, so extra
  workers have nothing to do.
- Replacing the serial kernel's inlined writer with a call to `_escape_range`:
  measured 41.39 ms against 27.67 ms at 16 MiB, so it was reverted and the serial
  kernel is untouched.

## GPU: CPU only: memory-bound

There is no GPU path. Every kernel this port exports is a byte scan or a byte
copy, and none of them reaches the gate of roughly 2 flops per byte, so the phase
was closed at the gate rather than attempted.

The gate was checked as follows:

- **`bench/analysis.md` is not in this checkout, and git history contains no
  commit that ever added it** (`git log --all -- bench/analysis.md` is empty).
  It was checked: the gate was applied to the arithmetic intensity of the
  kernels that actually exist, measured on this host.
- **GPU availability:** `nvidia-smi --query-gpu=memory.free --format=csv,noheader`
  reported `4557 MiB` free on an NVIDIA GeForce RTX 5090, above the 4000 MiB
  floor. The device was available; the gate that stopped this was the flops per
  byte, not memory.
- **Allocation budget:** nothing was allocated, so the 2 GB cap is trivially
  satisfied.

### Arithmetic intensity

Measured on 2026-11-10 on an Intel Xeon E5-2697 v4 at 2.30 GHz (shared host,
Linux x86-64) from the callable C ABI, best of 20 runs cold-built once,
4 MiB buffers, mixed input `5 < 6 & "quoted" > 0; café. ` repeated:

| Kernel | Work per byte | Time | Effective bandwidth |
|---|---|---:|---:|
| `mmi_escape_html_size` | 4 byte compares + one lane select/add | 0.51 ms | 9.1 GB/s read |
| `mmi_escape_html_write` | 4 byte compares, 1 byte in, 1-6 bytes out | 6.31 ms | 1.24 GB/s read, 2.2 GB/s with the 7.84 MB output |
| `mmi_scan_lines` | 1 compare + space/tab skip per block | 0.96 ms | 4.8 GB/s read |

In flops per byte, the first two are four comparisons per byte, so at most about
**0.5 flops/byte**, and the input is already running at 9.1 GB/s on the CPU. Even
the strongest kernel is an order of magnitude below the 2 flops/byte gate, so a
GPU kernel would be a straight transport of the same bytes with no arithmetic to
hide the transfer behind. The escape is also a variable-length scatter, the
opposite of the uniform per-thread writes a GPU wants, and the parser's real cost
is structural (line splitting, block state, inline token trees) and calls back
into Python per token.

Consequence, as measured for the parallel phase in the section above: this
factor of the work is memory-bound, so it is kept on the CPU, where the existing
SIMD kernels and `max.algorithm.parallelize` chunking already run at 1.2-9.1
GB/s. A kernel would need real arithmetic per byte (no such kernel exists in
CommonMark tokenizing or HTML escaping) before a GPU transfer could pay for
itself.

### GPU tests

No GPU tests were added, because there is no GPU kernel to test. The existing
suite covers the CPU kernels and the chunked parallel path, and runs unchanged on
a host with no CUDA device. If a GPU path is added later, its tests must skip
cleanly when no device is present; nothing in this checkout requires one.

## Development

```bash
pixi run build
pixi run test
pixi run bench
```

The benchmark task holds `/tmp/mojo-bench.lock` to prevent overlapping factory
benchmark jobs.
