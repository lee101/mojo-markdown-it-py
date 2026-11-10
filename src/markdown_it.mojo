from max.algorithm import parallelize
from std.time import perf_counter_ns
from std.sys import simd_width_of as simdwidthof


comptime BPtr = Pointer[UInt8, AnyOrigin[mut=True]]
comptime IPtr = Pointer[Int64, AnyOrigin[mut=True]]


def _special_mask[W: Int](value: SIMD[DType.uint8, W]) -> SIMD[DType.bool, W]:
    return (
        value.eq(UInt8(10))
        | value.eq(UInt8(13))
        | value.eq(UInt8(33))
        | value.eq(UInt8(38))
        | value.eq(UInt8(42))
        | value.eq(UInt8(60))
        | value.eq(UInt8(91))
        | value.eq(UInt8(92))
        | value.eq(UInt8(95))
        | value.eq(UInt8(96))
    )


def _escaped_size(src: BPtr, start: Int, end: Int) -> Int:
    comptime W = simdwidthof[DType.float64]()
    comptime BYTE_W = W * 8
    var size = end - start
    var i = start
    while i + BYTE_W <= end:
        var value = src.unsafe_load[width=BYTE_W, alignment=1](i)
        var extra = value.eq(UInt8(38)).select(
            SIMD[DType.uint8, BYTE_W](4), SIMD[DType.uint8, BYTE_W](0)
        )
        extra += (value.eq(UInt8(60)) | value.eq(UInt8(62))).select(
            SIMD[DType.uint8, BYTE_W](3), SIMD[DType.uint8, BYTE_W](0)
        )
        extra += value.eq(UInt8(34)).select(
            SIMD[DType.uint8, BYTE_W](5), SIMD[DType.uint8, BYTE_W](0)
        )
        size += Int(extra.cast[DType.int16]().reduce_add())
        i += BYTE_W
    while i < end:
        var value = src.unsafe_load(i)
        if value == UInt8(38):
            size += 4
        elif value == UInt8(60) or value == UInt8(62):
            size += 3
        elif value == UInt8(34):
            size += 5
        i += 1
    return size


def _escape_range(
    src: BPtr,
    start: Int,
    end: Int,
    dst: BPtr,
    dst_start: Int,
) -> Int:
    comptime W = simdwidthof[DType.float64]()
    var i = start
    var j = dst_start
    while i + W <= end:
        var value = src.unsafe_load[width=W, alignment=1](i)
        var special = (
            value.eq(UInt8(38))
            | value.eq(UInt8(60))
            | value.eq(UInt8(62))
            | value.eq(UInt8(34))
        )
        if not special:
            dst.unsafe_store[alignment=1](j, value)
            i += W
            j += W
            continue
        for lane in range(W):
            var c = value[lane]
            if c == UInt8(38):
                dst.unsafe_store(j, UInt8(38))
                dst.unsafe_store(j + 1, UInt8(97))
                dst.unsafe_store(j + 2, UInt8(109))
                dst.unsafe_store(j + 3, UInt8(112))
                dst.unsafe_store(j + 4, UInt8(59))
                j += 5
            elif c == UInt8(60):
                dst.unsafe_store(j, UInt8(38))
                dst.unsafe_store(j + 1, UInt8(108))
                dst.unsafe_store(j + 2, UInt8(116))
                dst.unsafe_store(j + 3, UInt8(59))
                j += 4
            elif c == UInt8(62):
                dst.unsafe_store(j, UInt8(38))
                dst.unsafe_store(j + 1, UInt8(103))
                dst.unsafe_store(j + 2, UInt8(116))
                dst.unsafe_store(j + 3, UInt8(59))
                j += 4
            elif c == UInt8(34):
                dst.unsafe_store(j, UInt8(38))
                dst.unsafe_store(j + 1, UInt8(113))
                dst.unsafe_store(j + 2, UInt8(117))
                dst.unsafe_store(j + 3, UInt8(111))
                dst.unsafe_store(j + 4, UInt8(116))
                dst.unsafe_store(j + 5, UInt8(59))
                j += 6
            else:
                dst.unsafe_store(j, c)
                j += 1
        i += W
        # While the next block is entirely clean, copy it and check the one after.
        # Mixed input then pays the scalar lane loop once per special block rather
        # than once per special character, and keeps plain runs at vector speed.
        while i + 2 * W <= end:
            var peek = src.unsafe_load[width=W, alignment=1](i)
            var peek_special = (
                peek.eq(UInt8(38))
                | peek.eq(UInt8(60))
                | peek.eq(UInt8(62))
                | peek.eq(UInt8(34))
            )
            if peek_special:
                break
            dst.unsafe_store[alignment=1](j, peek)
            i += W
            j += W
    while i < end:
        var c = src.unsafe_load(i)
        if c == UInt8(38):
            dst.unsafe_store(j, UInt8(38))
            dst.unsafe_store(j + 1, UInt8(97))
            dst.unsafe_store(j + 2, UInt8(109))
            dst.unsafe_store(j + 3, UInt8(112))
            dst.unsafe_store(j + 4, UInt8(59))
            j += 5
        elif c == UInt8(60):
            dst.unsafe_store(j, UInt8(38))
            dst.unsafe_store(j + 1, UInt8(108))
            dst.unsafe_store(j + 2, UInt8(116))
            dst.unsafe_store(j + 3, UInt8(59))
            j += 4
        elif c == UInt8(62):
            dst.unsafe_store(j, UInt8(38))
            dst.unsafe_store(j + 1, UInt8(103))
            dst.unsafe_store(j + 2, UInt8(116))
            dst.unsafe_store(j + 3, UInt8(59))
            j += 4
        elif c == UInt8(34):
            dst.unsafe_store(j, UInt8(38))
            dst.unsafe_store(j + 1, UInt8(113))
            dst.unsafe_store(j + 2, UInt8(117))
            dst.unsafe_store(j + 3, UInt8(111))
            dst.unsafe_store(j + 4, UInt8(116))
            dst.unsafe_store(j + 5, UInt8(59))
            j += 6
        else:
            dst.unsafe_store(j, c)
            j += 1
        i += 1
    return j


@export("mmi_scan_lines")
def mmi_scan_lines(
    src_addr: Int,
    n: Int,
    starts_addr: Int,
    ends_addr: Int,
    first_addr: Int,
    capacity: Int,
) abi("C") -> Int:
    if (
        src_addr == 0
        or starts_addr == 0
        or ends_addr == 0
        or first_addr == 0
        or n < 0
        or capacity < 0
    ):
        return -1
    var src = BPtr(unsafe_from_address=src_addr)
    var starts = IPtr(unsafe_from_address=starts_addr)
    var ends = IPtr(unsafe_from_address=ends_addr)
    var first = IPtr(unsafe_from_address=first_addr)
    var pos = 0
    var count = 0
    while pos < n:
        if count >= capacity:
            return -1
        var line_start = pos
        comptime W = simdwidthof[DType.float64]()
        while pos + W <= n:
            var value = src.unsafe_load[width=W](pos)
            if value.eq(UInt8(10)):
                break
            pos += W
        while pos < n and src.unsafe_load(pos) != UInt8(10):
            pos += 1
        var line_end = pos
        if line_end > line_start and src.unsafe_load(line_end - 1) == UInt8(13):
            line_end -= 1
        var content = line_start
        while content + W <= line_end:
            var value = src.unsafe_load[width=W](content)
            if not (value.eq(UInt8(32)) | value.eq(UInt8(9))).reduce_and():
                break
            content += W
        while content < line_end:
            var c = src.unsafe_load(content)
            if c != UInt8(32) and c != UInt8(9):
                break
            content += 1
        starts.unsafe_store(count, Int64(line_start))
        ends.unsafe_store(count, Int64(line_end))
        first.unsafe_store(count, Int64(content))
        count += 1
        if pos < n:
            pos += 1
    return count


@export("mmi_scan_specials")
def mmi_scan_specials(
    src_addr: Int,
    n: Int,
    positions_addr: Int,
    capacity: Int,
) abi("C") -> Int:
    if src_addr == 0 or positions_addr == 0 or n < 0 or capacity < 0:
        return -1
    var src = BPtr(unsafe_from_address=src_addr)
    var positions = IPtr(unsafe_from_address=positions_addr)
    var count = 0
    comptime W = simdwidthof[DType.float64]()
    var i = 0
    while i + W <= n:
        var value = src.unsafe_load[width=W](i)
        if _special_mask(value):
            for lane in range(W):
                var c = value[lane]
                if (
                    c == UInt8(10)
                    or c == UInt8(13)
                    or c == UInt8(33)
                    or c == UInt8(38)
                    or c == UInt8(42)
                    or c == UInt8(60)
                    or c == UInt8(91)
                    or c == UInt8(92)
                    or c == UInt8(95)
                    or c == UInt8(96)
                ):
                    if count >= capacity:
                        return -1
                    positions.unsafe_store(count, Int64(i + lane))
                    count += 1
        i += W
    while i < n:
        var c = src.unsafe_load(i)
        if (
            c == UInt8(10)
            or c == UInt8(13)
            or c == UInt8(33)
            or c == UInt8(38)
            or c == UInt8(42)
            or c == UInt8(60)
            or c == UInt8(91)
            or c == UInt8(92)
            or c == UInt8(95)
            or c == UInt8(96)
        ):
            if count >= capacity:
                return -1
            positions.unsafe_store(count, Int64(i))
            count += 1
        i += 1
    return count


@export("mmi_escape_html_size")
def mmi_escape_html_size(src_addr: Int, n: Int) abi("C") -> Int:
    if src_addr == 0 or n < 0:
        return -1
    var src = BPtr(unsafe_from_address=src_addr)
    return _escaped_size(src, 0, n)


def _chunk_offsets(
    src: BPtr, n: Int, chunk_size: Int, workers: Int, offsets: IPtr
) -> Int:
    """Sizing pass, then a serial prefix sum. Identical output for any worker count."""
    var chunks = (n + chunk_size - 1) // chunk_size
    offsets.unsafe_store(0, Int64(0))
    if workers > 1 and chunks > 1:
        var active = min(workers, chunks)

        def size_chunk(chunk: Int) {imm}:
            var start = chunk * chunk_size
            var end = min(start + chunk_size, n)
            offsets.unsafe_store(chunk + 1, Int64(_escaped_size(src, start, end)))

        parallelize(size_chunk, chunks, active)
    else:
        for chunk in range(chunks):
            var start = chunk * chunk_size
            var end = min(start + chunk_size, n)
            offsets.unsafe_store(chunk + 1, Int64(_escaped_size(src, start, end)))

    var total = 0
    for chunk in range(chunks):
        total += Int(offsets.unsafe_load(chunk + 1))
        offsets.unsafe_store(chunk + 1, Int64(total))
    return total


def _chunk_write(
    src: BPtr, n: Int, dst: BPtr, chunk_size: Int, workers: Int, offsets: IPtr
) -> Int:
    """Write pass. Each chunk owns a disjoint output range, so order cannot matter."""
    var chunks = (n + chunk_size - 1) // chunk_size
    if workers > 1 and chunks > 1:
        var active = min(workers, chunks)

        def escape_chunk(chunk: Int) {imm}:
            var start = chunk * chunk_size
            var end = min(start + chunk_size, n)
            _ = _escape_range(src, start, end, dst, Int(offsets.unsafe_load(chunk)))

        parallelize(escape_chunk, chunks, active)
    else:
        for chunk in range(chunks):
            var start = chunk * chunk_size
            var end = min(start + chunk_size, n)
            _ = _escape_range(src, start, end, dst, Int(offsets.unsafe_load(chunk)))
    return Int(offsets.unsafe_load(chunks))


@export("mmi_escape_html_offsets")
def mmi_escape_html_offsets(
    src_addr: Int,
    n: Int,
    offsets_addr: Int,
    chunk_size: Int,
    offsets_capacity: Int,
    workers: Int,
) abi("C") -> Int:
    if (
        src_addr == 0
        or offsets_addr == 0
        or n < 0
        or chunk_size <= 0
        or workers < 0
    ):
        return -1
    var chunks = (n + chunk_size - 1) // chunk_size
    if offsets_capacity < chunks + 1:
        return -1
    var src = BPtr(unsafe_from_address=src_addr)
    var offsets = IPtr(unsafe_from_address=offsets_addr)
    return _chunk_offsets(src, n, chunk_size, workers, offsets)


@export("mmi_escape_html_chunks")
def mmi_escape_html_chunks(
    src_addr: Int,
    n: Int,
    dst_addr: Int,
    offsets_addr: Int,
    chunk_size: Int,
    offsets_capacity: Int,
    dst_capacity: Int,
    workers: Int,
) abi("C") -> Int:
    if (
        src_addr == 0
        or dst_addr == 0
        or offsets_addr == 0
        or n < 0
        or chunk_size <= 0
        or dst_capacity < 0
        or workers < 0
    ):
        return -1
    var chunks = (n + chunk_size - 1) // chunk_size
    if offsets_capacity < chunks + 1:
        return -1
    var src = BPtr(unsafe_from_address=src_addr)
    var dst = BPtr(unsafe_from_address=dst_addr)
    var offsets = IPtr(unsafe_from_address=offsets_addr)
    if Int(offsets.unsafe_load(0)) != 0:
        return -1
    var previous = 0
    for chunk in range(chunks):
        var current = Int(offsets.unsafe_load(chunk + 1))
        if current < previous or current > dst_capacity:
            return -1
        previous = current

    return _chunk_write(src, n, dst, chunk_size, workers, offsets)


@export("mmi_escape_html_write")
def mmi_escape_html_write(
    src_addr: Int, n: Int, dst_addr: Int, dst_capacity: Int
) abi("C") -> Int:
    if src_addr == 0 or dst_addr == 0 or n < 0 or dst_capacity < n:
        return -1
    var src = BPtr(unsafe_from_address=src_addr)
    var dst = BPtr(unsafe_from_address=dst_addr)
    comptime W = simdwidthof[DType.float64]()
    var i = 0
    var j = 0
    while i + W <= n:
        var value = src.unsafe_load[width=W, alignment=1](i)
        var special = (
            value.eq(UInt8(38))
            | value.eq(UInt8(60))
            | value.eq(UInt8(62))
            | value.eq(UInt8(34))
        )
        if not special:
            dst.unsafe_store[alignment=1](j, value)
            i += W
            j += W
            continue
        for lane in range(W):
            var c = value[lane]
            if c == UInt8(38):
                dst.unsafe_store(j, UInt8(38))
                dst.unsafe_store(j + 1, UInt8(97))
                dst.unsafe_store(j + 2, UInt8(109))
                dst.unsafe_store(j + 3, UInt8(112))
                dst.unsafe_store(j + 4, UInt8(59))
                j += 5
            elif c == UInt8(60):
                dst.unsafe_store(j, UInt8(38))
                dst.unsafe_store(j + 1, UInt8(108))
                dst.unsafe_store(j + 2, UInt8(116))
                dst.unsafe_store(j + 3, UInt8(59))
                j += 4
            elif c == UInt8(62):
                dst.unsafe_store(j, UInt8(38))
                dst.unsafe_store(j + 1, UInt8(103))
                dst.unsafe_store(j + 2, UInt8(116))
                dst.unsafe_store(j + 3, UInt8(59))
                j += 4
            elif c == UInt8(34):
                dst.unsafe_store(j, UInt8(38))
                dst.unsafe_store(j + 1, UInt8(113))
                dst.unsafe_store(j + 2, UInt8(117))
                dst.unsafe_store(j + 3, UInt8(111))
                dst.unsafe_store(j + 4, UInt8(116))
                dst.unsafe_store(j + 5, UInt8(59))
                j += 6
            else:
                dst.unsafe_store(j, c)
                j += 1
        i += W
    while i < n:
        var c = src.unsafe_load(i)
        if c == UInt8(38):
            dst.unsafe_store(j, UInt8(38))
            dst.unsafe_store(j + 1, UInt8(97))
            dst.unsafe_store(j + 2, UInt8(109))
            dst.unsafe_store(j + 3, UInt8(112))
            dst.unsafe_store(j + 4, UInt8(59))
            j += 5
        elif c == UInt8(60):
            dst.unsafe_store(j, UInt8(38))
            dst.unsafe_store(j + 1, UInt8(108))
            dst.unsafe_store(j + 2, UInt8(116))
            dst.unsafe_store(j + 3, UInt8(59))
            j += 4
        elif c == UInt8(62):
            dst.unsafe_store(j, UInt8(38))
            dst.unsafe_store(j + 1, UInt8(103))
            dst.unsafe_store(j + 2, UInt8(116))
            dst.unsafe_store(j + 3, UInt8(59))
            j += 4
        elif c == UInt8(34):
            dst.unsafe_store(j, UInt8(38))
            dst.unsafe_store(j + 1, UInt8(113))
            dst.unsafe_store(j + 2, UInt8(117))
            dst.unsafe_store(j + 3, UInt8(111))
            dst.unsafe_store(j + 4, UInt8(116))
            dst.unsafe_store(j + 5, UInt8(59))
            j += 6
        else:
            dst.unsafe_store(j, c)
            j += 1
        i += 1
    return j


@export("mmi_escape_html")
def mmi_escape_html(
    src_addr: Int, n: Int, dst_addr: Int, dst_capacity: Int
) abi("C") -> Int:
    if src_addr == 0 or dst_addr == 0 or n < 0 or dst_capacity < n:
        return -1
    var src = BPtr(unsafe_from_address=src_addr)
    var dst = BPtr(unsafe_from_address=dst_addr)
    var needed = _escaped_size(src, 0, n)
    if needed > dst_capacity:
        return -1
    return _escape_range(src, 0, n, dst, 0)


@export("mmi_escape_html_chunks_timed")
def mmi_escape_html_chunks_timed(
    src_addr: Int,
    n: Int,
    dst_addr: Int,
    offsets_addr: Int,
    chunk_size: Int,
    offsets_capacity: Int,
    dst_capacity: Int,
    workers: Int,
    reps: Int,
) abi("C") -> Int:
    """Benchmark-only entry point: best-of-`reps` microseconds for one chunked escape.

    `mmi_escape_html_offsets` is measured through `_chunk_offsets`, and
    `mmi_escape_html_chunks` through `_chunk_write`, so the timed work is the same
    code path the library ships. The caller still supplies both buffers.
    """
    if (
        src_addr == 0
        or dst_addr == 0
        or offsets_addr == 0
        or n < 0
        or chunk_size <= 0
        or offsets_capacity <= 0
        or dst_capacity < 0
        or workers < 0
        or reps <= 0
    ):
        return -1
    var chunks = (n + chunk_size - 1) // chunk_size
    if offsets_capacity < chunks + 1:
        return -1
    var src = BPtr(unsafe_from_address=src_addr)
    var dst = BPtr(unsafe_from_address=dst_addr)
    var offsets = IPtr(unsafe_from_address=offsets_addr)
    var total = _chunk_offsets(src, n, chunk_size, workers, offsets)
    if total > dst_capacity:
        return -1
    var best: Int = 1 << 62
    for rep in range(reps):
        var t0 = perf_counter_ns()
        _ = _chunk_offsets(src, n, chunk_size, workers, offsets)
        _ = _chunk_write(src, n, dst, chunk_size, workers, offsets)
        var t1 = perf_counter_ns()
        if t1 - t0 < best:
            best = t1 - t0
    return Int(best // 1000)
