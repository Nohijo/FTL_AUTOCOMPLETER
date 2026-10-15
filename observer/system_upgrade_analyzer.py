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


def analyze_upgrade(before_path: Path, after_path: Path, expected_scrap_delta: int | None = None):
    before = read_bytes(before_path)
    after = read_bytes(after_path)

    print("[upgrade_analyzer] Comparing snapshots")
    print(f"[upgrade_analyzer] Before: {before_path}")
    print(f"[upgrade_analyzer] After:  {after_path}")
    print(f"[upgrade_analyzer] Size before: {len(before)} bytes")
    print(f"[upgrade_analyzer] Size after:  {len(after)} bytes")
    print()

    max_len = max(len(before), len(after))
    byte_diffs = []

    for offset in range(max_len):
        old = before[offset] if offset < len(before) else None
        new = after[offset] if offset < len(after) else None

        if old != new:
            byte_diffs.append((offset, old, new))

    print(f"[upgrade_analyzer] Byte differences: {len(byte_diffs)}")
    print()

    for offset, old, new in byte_diffs:
        old_text = "--" if old is None else f"{old:02X}"
        new_text = "--" if new is None else f"{new:02X}"
        delta = None if old is None or new is None else new - old

        if delta is None:
            print(f"0x{offset:08X}: {old_text} -> {new_text}")
        else:
            print(f"0x{offset:08X}: {old_text} -> {new_text}  byte_delta {delta:+}")

    print()
    print("[upgrade_analyzer] Possible +1 level candidates:")

    for offset, old, new in byte_diffs:
        if old is not None and new is not None and new - old == 1:
            print(f"  0x{offset:08X}: {old} -> {new}")

    if expected_scrap_delta is not None:
        print()
        print(f"[upgrade_analyzer] Possible int32 scrap candidates delta {expected_scrap_delta:+}:")

        min_len = min(len(before), len(after))

        for offset in range(0, min_len - 4):
            old_value = read_int32_safe(before, offset)
            new_value = read_int32_safe(after, offset)

            if old_value is None or new_value is None:
                continue

            if new_value - old_value == expected_scrap_delta:
                if -1000 <= old_value <= 5000 and -1000 <= new_value <= 5000:
                    print(f"  0x{offset:08X}: {old_value} -> {new_value}")


def main():
    if len(sys.argv) < 3:
        print("Usage:")
        print("  python3 -m observer.system_upgrade_analyzer before.sav after.sav [expected_scrap_delta]")
        print()
        print("Example:")
        print("  python3 -m observer.system_upgrade_analyzer before.sav after.sav -20")
        return

    before_path = Path(sys.argv[1])
    after_path = Path(sys.argv[2])

    expected_scrap_delta = None

    if len(sys.argv) >= 4:
        expected_scrap_delta = int(sys.argv[3])

    analyze_upgrade(before_path, after_path, expected_scrap_delta)


if __name__ == "__main__":
    main()