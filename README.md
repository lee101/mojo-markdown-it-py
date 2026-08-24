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

Measured on 2026-08-24 on an Intel Xeon E5-2697 v4 at 2.30 GHz, Linux x86-64,
using the pinned Mojo nightly and `markdown-it-py` 4.2.0. Times are the best of
five warm runs from `pixi run bench`; rendered-output equality is asserted before
timing.

| Operation | Input | Mojo port | markdown-it-py | Relative |
|---|---:|---:|---:|---:|
| HTML escape | 4.6 MB | 20.77 ms | 39.05 ms | 1.88x faster |
| parse plain blocks | 1.0 MB | 175.57 ms | 376.49 ms | 2.14x faster |
| render plain blocks | 1.0 MB | 208.05 ms | 404.90 ms | 1.95x faster |
| parse mixed CommonMark | 0.5 MB | 635.81 ms | 863.31 ms | 1.36x faster |
| render mixed CommonMark | 0.5 MB | 641.12 ms | 809.29 ms | 1.26x faster |

The port was faster in all five cases on this run. The host is shared
with unrelated production workloads, so absolute times vary even though the
machine-wide benchmark lock prevents overlapping factory benchmark jobs.

GPU acceleration is intentionally omitted. These kernels scan bytes, compare
characters, and perform variable-length copies; they do no floating-point work and
have too little arithmetic intensity to recover device-transfer and launch costs.
Large CPU escaping uses a bounded worker pool above its parallelism threshold.

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
