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
Large escapes are counted and written in independent 1 MiB chunks by a bounded
worker pool; inputs below 4 MiB stay serial to avoid thread-launch overhead. The
parser splits Unicode lines directly, avoiding an encode/scan/decode round trip,
assembles inline token trees, and keeps plugin-visible objects as ordinary Python
`Token` instances.

## Development

```bash
pixi run build
pixi run test
pixi run bench
```

The benchmark task holds `/tmp/mojo-bench.lock` to prevent overlapping factory
benchmark jobs.
