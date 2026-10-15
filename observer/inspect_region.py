import struct
import sys
from pathlib import Path


def read_bytes(path: Path) -> bytes:
    with open(path, "rb") as file:
        return file.read()


def inspect_region(data: bytes, start: int, end: int):
    print(f"[region] Inspeccionando desde 0x{start:08X} hasta 0x{end:08X}")
    print()

    for offset in range(start, min(end, len(data) - 4 + 1)):
        raw = data[offset:offset + 4]
        value = struct.unpack_from("<i", data, offset)[0]
        raw_hex = " ".join(f"{byte:02X}" for byte in raw)

        if -1000 <= value <= 100000:
            print(f"0x{offset:08X} | {raw_hex:<11} | {value}")


def main():
    if len(sys.argv) < 4:
        print("Uso:")
        print("  python3 -m observer.inspect_region archivo.sav inicio_hex fin_hex")
        print()
        print("Ejemplo:")
        print("  python3 -m observer.inspect_region data/snapshots/save.sav 0xD0 0xF0")
        return

    path = Path(sys.argv[1])
    start = int(sys.argv[2], 16)
    end = int(sys.argv[3], 16)

    data = read_bytes(path)

    print(f"[region] Archivo: {path}")
    print(f"[region] Tamaño: {len(data)} bytes")
    print()

    inspect_region(data, start, end)


if __name__ == "__main__":
    main()