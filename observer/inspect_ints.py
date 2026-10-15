import struct
import sys
from pathlib import Path


def read_bytes(path: Path) -> bytes:
    with open(path, "rb") as file:
        return file.read()


def print_int32_table(data: bytes, start: int = 0, end: int = 512):
    end = min(end, len(data))

    print(f"[ints] Mostrando int32 little-endian desde {start} hasta {end}")
    print()

    for offset in range(start, end, 4):
        if offset + 4 > len(data):
            break

        value = struct.unpack_from("<i", data, offset)[0]
        raw = data[offset:offset + 4]
        raw_hex = " ".join(f"{byte:02X}" for byte in raw)

        ascii_part = "".join(chr(byte) if 32 <= byte <= 126 else "." for byte in raw)

        print(f"{offset:08X} | {raw_hex:<11} | {value:>8} | {ascii_part}")


def main():
    if len(sys.argv) < 2:
        print("Uso:")
        print("  python3 -m observer.inspect_ints archivo.sav")
        print()
        print("Ejemplo:")
        print("  python3 -m observer.inspect_ints data/snapshots/archivo.sav")
        return

    path = Path(sys.argv[1])
    data = read_bytes(path)

    print(f"[ints] Archivo: {path}")
    print(f"[ints] Tamaño: {len(data)} bytes")
    print()

    print_int32_table(data, 0, 512)


if __name__ == "__main__":
    main()