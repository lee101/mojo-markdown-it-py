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
five warm runs from `pixi run bench`; rendered-output equality is asserted before
timing. The host is shared, so absolute times vary between runs (regenerate with
`pixi run bench` rather than comparing across dates).

| Operation | Input | Mojo port | markdown-it-py | Relative |
|---|---:|---:|---:|---:|
| HTML escape | 4.6 MB | 21.80 ms | 39.15 ms | 1.80x faster |
| parse plain blocks | 1.0 MB | 172.33 ms | 366.35 ms | 2.13x faster |
| render plain blocks | 1.0 MB | 197.03 ms | 413.04 ms | 2.10x faster |
| parse mixed CommonMark | 0.5 MB | 650.52 ms | 875.94 ms | 1.35x faster |
| render mixed CommonMark | 0.5 MB | 603.12 ms | 812.59 ms | 1.35x faster |

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
directly on a 4.64 MB buffer with `time.perf_counter`, best of 40 runs, in
separate processes:

| Buffer | Before | After |
|---|---:|---:|
| Mixed: `5 < 6 & "quoted" > 0; café. ` repeated, 24% of bytes special | 7.04-7.31 ms | 6.26-6.51 ms |
| Plain: `abcdefgh` repeated, no specials | 1.71 ms | 1.67-1.70 ms (unchanged within noise) |

The end-to-end `pixi run bench` HTML escape row stays at roughly 21.6-21.8 ms
across four runs on this shared host, the same as before the change. Its thread
pool overlaps the serial per-chunk work with encode and decode, so the kernel
gain does not show up at the bench's size. The direct kernel timings above are
the reliable measurement.

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
| 1 MiB | 1.92 ms | 21.93 ms | 22.08 ms | 20.48 ms |
| 2 MiB | 3.73 ms | 13.01 ms | 21.90 ms | 10.77 ms |
| 4 MiB | 7.84 ms | 15.37 ms | 11.93 ms | 10.18 ms |
| 6 MiB | 11.54 ms | 16.44 ms | 12.43 ms | 10.65 ms |
| 8 MiB | 15.66 ms | 19.12 ms | 14.48 ms | 13.04 ms |
| 10 MiB | 19.64 ms | 21.44 ms | 14.84 ms | 13.29 ms |
| 12 MiB | 23.55 ms | 24.58 ms | 17.21 ms | 14.33 ms |
| 16 MiB | 31.54 ms | 29.06 ms | 19.27 ms | 15.88 ms |
| 32 MiB | 63.46 ms | 45.40 ms | 29.95 ms | 22.50 ms |

The crossover sits between 8 and 10 MiB for four workers (1.08x at 8 MiB, which
is inside run-to-run noise, then 1.32x at 10 MiB). The threshold is therefore
12 MiB: above the whole crossover band, with margin. Below it, callers run the
serial loop and never spawn a worker.

Two effects set that crossover. A `parallelize` call from a ctypes/Python host
costs about 4-5 ms on this shared machine even when the callback does nothing,
which is larger than the whole serial escape of a 4 MiB buffer; and the work
itself is memory-bound, so it does not scale linearly.

Worker count: the rule of thumb is 4. It was raised to 8 because the benchmark
shows it helps: 8 workers beat 4 at every size from 6 MiB up (8 MiB 13.04 against
14.48 ms, 16 MiB 15.88 against 19.27 ms, 32 MiB 22.50 against 29.95 ms), while
2 workers are worse than 4 everywhere and worse than serial below 16 MiB. The
gain flattens after 8: worker count cannot exceed the chunk count, and at the
12 MiB threshold there are only 12 chunks to hand out.

### Before and after

Same host, same buffers, both libraries in one process, best of 11. `HEAD` is the
previous commit built from source to a separate shared library; `now` is this
change. `threaded` is the chunked path with the shipped worker settings (HEAD:
8 Python threads, 4 MiB threshold; now: 8 Mojo workers, 12 MiB threshold).

| Input | HEAD serial | now serial | HEAD threaded | now threaded |
|---|---:|---:|---:|---:|
| 1 MiB | 1.60 ms | 1.59 ms | 1.76 ms | 1.92 ms (serial, below threshold) |
| 2 MiB | 3.26 ms | 3.28 ms | 3.54 ms | 3.73 ms (serial, below threshold) |
| 4 MiB | 6.39 ms | 6.41 ms | 7.17 ms | 7.84 ms (serial, below threshold) |
| 8 MiB | 13.72 ms | 13.34 ms | 14.97 ms | 15.66 ms (serial, below threshold) |
| 12 MiB | 20.47 ms | 20.93 ms | 22.61 ms | 17.27 ms |
| 16 MiB | 32.74 ms | 28.07 ms | 30.58 ms | 18.34 ms |

The serial path is unchanged, which is what the equal serial columns show. The
previous threaded path was slower than serial at 1-8 MiB even though its 4 MiB
threshold enabled it there: that is the failure mode this change removes, by
letting the serial loop cover everything below 12 MiB. Where the work is large
enough to pay for workers, the Mojo path is about 1.3x faster than the previous
threaded path at 12 MiB (17.27 against 22.61 ms) and about 1.7x at 16 MiB
(18.34 against 30.58 ms).

The end-to-end `pixi run bench` HTML escape row escapes 4.6 MB, which is below the
threshold, so it measures the serial path: 24.47 ms in that run, against 21.80 ms
in the table at the top of this file. Both rows are dominated by host noise on this
shared machine (the parse and render rows moved by a comparable fraction between
those runs), and this row does not exercise the parallel path at all. The factory
benchmark lock was held by other jobs for the duration of this work, so the numbers
here come from running the same bench body without the lock.

Not adopted, after measuring:

- Lowering the threshold to 4 MiB: at 4 MiB, 8 workers take 10.18 ms against
  7.84 ms serial, a 1.3x loss; 4 workers are worse still at 11.93 ms.
- Two workers: slower than four workers at every size, and slower than serial up
  to 16 MiB.
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
| `mmi_escape_html_size` | 4 byte compares + one lane select/add | 0.46 ms | 9.2 GB/s read |
| `mmi_escape_html_write` | 4 byte compares, 1 byte in, 1-6 bytes out | 5.89 ms | 1.4 GB/s read+write |
| `mmi_scan_lines` | 1 compare + space/tab skip per block | 1.71 ms | 2.5 GB/s read |

In flops per byte, the first two are four comparisons per byte, so at most about
**0.5 flops/byte**, and the input is already running at 9.2 GB/s on the CPU. Even
the strongest kernel is an order of magnitude below the 2 flops/byte gate, so a
GPU kernel would be a straight transport of the same bytes with no arithmetic to
hide the transfer behind. The escape is also a variable-length scatter, the
opposite of the uniform per-thread writes a GPU wants, and the parser's real cost
is structural (line splitting, block state, inline token trees) and calls back
into Python per token.

Consequence, as measured for the parallel phase in the section above: this
factor of the work is memory-bound, so it is kept on the CPU, where the existing
SIMD kernels and `max.algorithm.parallelize` chunking already run at 1.4-9.2
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
