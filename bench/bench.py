from __future__ import annotations

import math
import os
import platform
import sys
import time


sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "python"),
)

from markdown_it import MarkdownIt as UpstreamMarkdownIt  # noqa: E402
from markdown_it.common.utils import escapeHtml as upstream_escape_html  # noqa: E402

from mojo_markdown_it import MarkdownIt  # noqa: E402
from mojo_markdown_it._lib import escape_html  # noqa: E402


def best_time(function, repeat=5):
    best = math.inf
    for _ in range(repeat):
        start = time.perf_counter()
        function()
        best = min(best, time.perf_counter() - start)
    return best


def cpu_name():
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as stream:
            for line in stream:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def main():
    ours = MarkdownIt("commonmark")
    upstream = UpstreamMarkdownIt("commonmark")
    escape_source = ('5 < 6 & "quoted" > 0; café. ' * 160_000)
    plain_source = (
        "A plain paragraph has enough words to exercise UTF-8 line scanning without markup.\n\n"
        * 12_000
    )
    mixed_unit = """## Parser benchmark

A paragraph with **strong**, *emphasis*, `code`, &amp; an [example](/url).

> A block quote with two lines.
> The second line has <span>HTML</span>.

- first item
- second item

```python
print("hello <world>")
```

"""
    mixed_source = mixed_unit * 2_000
    cases = [
        (
            "HTML escape",
            f"{len(escape_source.encode()) / 1e6:.1f} MB",
            lambda: escape_html(escape_source),
            lambda: upstream_escape_html(escape_source),
        ),
        (
            "parse plain blocks",
            f"{len(plain_source.encode()) / 1e6:.1f} MB",
            lambda: ours.parse(plain_source),
            lambda: upstream.parse(plain_source),
        ),
        (
            "render plain blocks",
            f"{len(plain_source.encode()) / 1e6:.1f} MB",
            lambda: ours.render(plain_source),
            lambda: upstream.render(plain_source),
        ),
        (
            "parse mixed CommonMark",
            f"{len(mixed_source.encode()) / 1e6:.1f} MB",
            lambda: ours.parse(mixed_source),
            lambda: upstream.parse(mixed_source),
        ),
        (
            "render mixed CommonMark",
            f"{len(mixed_source.encode()) / 1e6:.1f} MB",
            lambda: ours.render(mixed_source),
            lambda: upstream.render(mixed_source),
        ),
    ]

    print(f"Machine: {cpu_name()} ({platform.system()} {platform.machine()})")
    print()
    print("| Operation | Input | Mojo port | markdown-it-py | Relative |")
    print("|---|---:|---:|---:|---:|")
    for name, size, mojo_fn, upstream_fn in cases:
        mojo_result = mojo_fn()
        upstream_result = upstream_fn()
        if name.startswith(("HTML", "render")):
            assert mojo_result == upstream_result
        mojo_seconds = best_time(mojo_fn)
        upstream_seconds = best_time(upstream_fn)
        ratio = upstream_seconds / mojo_seconds
        label = "faster" if ratio >= 1 else "slower"
        print(
            f"| {name} | {size} | {mojo_seconds * 1e3:.2f} ms | "
            f"{upstream_seconds * 1e3:.2f} ms | {ratio:.2f}x {label} |"
        )


if __name__ == "__main__":
    main()
