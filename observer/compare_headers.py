import struct
import sys
from pathlib import Path


def read_bytes(path: Path) -> bytes:
    with open(path, "rb") as file:
        return file.read()


def read_int32_safe(data: bytes, offset: int):
    if offset + 4 > len(data):
        return None

    return struct.unpack_from("<i", data, offset)[0]


def compare_region(data_a: bytes, data_b: bytes, start: int, end: int):
    print(f"[compare] Region 0x{start:08X} to 0x{end:08X}")
    print()

    for offset in range(start, end):
        value_a = read_int32_safe(data_a, offset)
        value_b = read_int32_safe(data_b, offset)

        if value_a is None or value_b is None:
            continue

        if value_a == value_b:
            continue

        if -100000 <= value_a <= 100000 and -100000 <= value_b <= 100000:
            delta = value_b - value_a
            print(
                f"0x{offset:08X} | "
                f"{value_a:>8} -> {value_b:<8} "
                f"delta {delta:+}"
            )


def main():
    if len(sys.argv) < 3:
        print("Usage:")
        print("  python3 -m observer.compare_headers active.sav after.sav")
        return

    path_a = Path(sys.argv[1])
    path_b = Path(sys.argv[2])

    data_a = read_bytes(path_a)
    data_b = read_bytes(path_b)

    print("[compare] Comparing files")
    print(f"[compare] A: {path_a}")
    print(f"[compare] B: {path_b}")
    print(f"[compare] Size A: {len(data_a)} bytes")
    print(f"[compare] Size B: {len(data_b)} bytes")
    print()

    compare_region(data_a, data_b, 0x00, 0x200)


if __name__ == "__main__":
    main()