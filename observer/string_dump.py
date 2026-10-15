import sys
from pathlib import Path

from config import FTL_CONTINUE_SAVE
from observer.context_reader import read_bytes, extract_ascii_strings


def dump_strings(path: Path):
    data = read_bytes(path)
    strings = extract_ascii_strings(data, min_length=3)

    print(f"[string_dump] File: {path}")
    print(f"[string_dump] Size: {len(data)} bytes")
    print(f"[string_dump] Strings found: {len(strings)}")
    print()

    for index, text in enumerate(strings):
        print(f"{index:04d}: {text}")


def main():
    if len(sys.argv) >= 2:
        path = Path(sys.argv[1])
    else:
        path = FTL_CONTINUE_SAVE

    if not path.exists():
        print(f"[string_dump] File does not exist: {path}")
        return

    dump_strings(path)


if __name__ == "__main__":
    main()