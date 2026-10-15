import hashlib
import sys
from pathlib import Path

from config import FTL_CONTINUE_SAVE


def read_bytes(path: Path) -> bytes:
    with open(path, "rb") as file:
        return file.read()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hexdump(data: bytes, start: int = 0, length: int = 128):
    end = min(start + length, len(data))

    for offset in range(start, end, 16):
        chunk = data[offset:offset + 16]

        hex_part = " ".join(f"{byte:02X}" for byte in chunk)
        ascii_part = "".join(chr(byte) if 32 <= byte <= 126 else "." for byte in chunk)

        print(f"{offset:08X}  {hex_part:<48}  {ascii_part}")


def extract_ascii_strings(data: bytes, min_length: int = 4):
    strings = []
    current = []

    for byte in data:
        if 32 <= byte <= 126:
            current.append(chr(byte))
        else:
            if len(current) >= min_length:
                strings.append("".join(current))
            current = []

    if len(current) >= min_length:
        strings.append("".join(current))

    return strings


def analyze_save(path: Path):
    if not path.exists():
        print(f"[reader] No existe el archivo: {path}")
        return

    data = read_bytes(path)

    print("[reader] Archivo analizado")
    print(f"[reader] Ruta: {path}")
    print(f"[reader] Tamaño: {len(data)} bytes")
    print(f"[reader] SHA256: {sha256(data)}")
    print()

    print("[reader] Primeros 128 bytes:")
    hexdump(data, 0, 128)
    print()

    print("[reader] Texto legible encontrado:")
    strings = extract_ascii_strings(data)

    if not strings:
        print("[reader] No se encontraron cadenas de texto legibles.")
    else:
        for text in strings[:100]:
            print(f"  {text}")

        if len(strings) > 100:
            print(f"[reader] Se omitieron {len(strings) - 100} cadenas adicionales.")


def main():
    if len(sys.argv) >= 2:
        path = Path(sys.argv[1])
    else:
        path = FTL_CONTINUE_SAVE

    analyze_save(path)


if __name__ == "__main__":
    main()