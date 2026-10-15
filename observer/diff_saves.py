import struct
import sys
from pathlib import Path


def read_bytes(path: Path) -> bytes:
    with open(path, "rb") as file:
        return file.read()


def show_byte_differences(data_a: bytes, data_b: bytes, limit: int = 200):
    max_len = max(len(data_a), len(data_b))
    differences = []

    for offset in range(max_len):
        byte_a = data_a[offset] if offset < len(data_a) else None
        byte_b = data_b[offset] if offset < len(data_b) else None

        if byte_a != byte_b:
            differences.append((offset, byte_a, byte_b))

    print(f"[diff] Diferencias byte por byte: {len(differences)}")
    print()

    for offset, byte_a, byte_b in differences[:limit]:
        a_text = "--" if byte_a is None else f"{byte_a:02X}"
        b_text = "--" if byte_b is None else f"{byte_b:02X}"
        print(f"Offset {offset:08X}: {a_text} -> {b_text}")

    if len(differences) > limit:
        print()
        print(f"[diff] Solo se mostraron las primeras {limit} diferencias.")

    return differences


def show_int32_candidates(data_a: bytes, data_b: bytes, expected_delta=None):
    min_len = min(len(data_a), len(data_b))
    candidates = []

    for offset in range(0, min_len - 4):
        value_a = struct.unpack_from("<i", data_a, offset)[0]
        value_b = struct.unpack_from("<i", data_b, offset)[0]

        if value_a == value_b:
            continue

        delta = value_b - value_a

        if expected_delta is not None and delta != expected_delta:
            continue

        if -100000 <= value_a <= 100000 and -100000 <= value_b <= 100000:
            candidates.append((offset, value_a, value_b, delta))

    print()
    print("[diff] Candidatos int32 little-endian:")

    if expected_delta is not None:
        print(f"[diff] Filtrando por cambio esperado: {expected_delta}")

    if not candidates:
        print("[diff] No se encontraron candidatos.")
        return

    for offset, value_a, value_b, delta in candidates[:100]:
        print(
            f"Offset {offset:08X}: "
            f"{value_a} -> {value_b} "
            f"(delta {delta:+})"
        )

    if len(candidates) > 100:
        print()
        print(f"[diff] Se omitieron {len(candidates) - 100} candidatos adicionales.")


def main():
    if len(sys.argv) < 3:
        print("Uso:")
        print("  python3 -m observer.diff_saves archivo_a.sav archivo_b.sav")
        print("  python3 -m observer.diff_saves archivo_a.sav archivo_b.sav delta_esperado")
        print()
        print("Ejemplo:")
        print("  python3 -m observer.diff_saves antes.sav despues.sav -1")
        return

    path_a = Path(sys.argv[1])
    path_b = Path(sys.argv[2])

    expected_delta = None

    if len(sys.argv) >= 4:
        expected_delta = int(sys.argv[3])

    data_a = read_bytes(path_a)
    data_b = read_bytes(path_b)

    print("[diff] Comparando saves")
    print(f"[diff] A: {path_a}")
    print(f"[diff] B: {path_b}")
    print(f"[diff] Tamaño A: {len(data_a)} bytes")
    print(f"[diff] Tamaño B: {len(data_b)} bytes")
    print()

    show_byte_differences(data_a, data_b)
    show_int32_candidates(data_a, data_b, expected_delta)


if __name__ == "__main__":
    main()