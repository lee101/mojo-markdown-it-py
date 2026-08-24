from std.algorithm import map
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
    var size = end - start
    var i = start
    while i + W <= end:
        var value = src.unsafe_load[width=W](i)
        size += 4 * Int(value.eq(UInt8(38)).cast[DType.int64]().reduce_add())
        size += 3 * Int(
            (
                value.eq(UInt8(60)).cast[DType.int64]()
                + value.eq(UInt8(62)).cast[DType.int64]()
            ).reduce_add()
        )
        size += 5 * Int(value.eq(UInt8(34)).cast[DType.int64]().reduce_add())
        i += W
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
        var value = src.unsafe_load[width=W](i)
        var special = (
            value.eq(UInt8(38))
            | value.eq(UInt8(60))
            | value.eq(UInt8(62))
            | value.eq(UInt8(34))
        )
        if not special:
            dst.unsafe_store(j, value)
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


@export("mmi_escape_html_offsets")
def mmi_escape_html_offsets(
    src_addr: Int,
    n: Int,
    offsets_addr: Int,
    chunk_size: Int,
    offsets_capacity: Int,
) abi("C") -> Int:
    if (
        src_addr == 0
        or offsets_addr == 0
        or n < 0
        or chunk_size <= 0
    ):
        return -1
    var chunks = (n + chunk_size - 1) // chunk_size
    if offsets_capacity < chunks + 1:
        return -1
    var src = BPtr(unsafe_from_address=src_addr)
    var offsets = IPtr(unsafe_from_address=offsets_addr)
    offsets.unsafe_store(0, Int64(0))

    @__parameter
    def count_chunk(chunk: Int):
        var start = chunk * chunk_size
        var end = min(start + chunk_size, n)
        offsets.unsafe_store(chunk + 1, Int64(_escaped_size(src, start, end)))

    map[count_chunk](chunks)
    var total = 0
    for chunk in range(chunks):
        total += Int(offsets.unsafe_load(chunk + 1))
        offsets.unsafe_store(chunk + 1, Int64(total))
    return total


@export("mmi_escape_html_chunks")
def mmi_escape_html_chunks(
    src_addr: Int,
    n: Int,
    dst_addr: Int,
    offsets_addr: Int,
    chunk_size: Int,
    offsets_capacity: Int,
    dst_capacity: Int,
) abi("C") -> Int:
    if (
        src_addr == 0
        or dst_addr == 0
        or offsets_addr == 0
        or n < 0
        or chunk_size <= 0
        or dst_capacity < 0
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

    @__parameter
    def escape_chunk(chunk: Int):
        var start = chunk * chunk_size
        var end = min(start + chunk_size, n)
        _ = _escape_range(
            src,
            start,
            end,
            dst,
            Int(offsets.unsafe_load(chunk)),
        )

    map[escape_chunk](chunks)
    return Int(offsets.unsafe_load(chunks))


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
