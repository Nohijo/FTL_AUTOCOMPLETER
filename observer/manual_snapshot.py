import shutil
import sys
from datetime import datetime
from pathlib import Path

from config import FTL_CONTINUE_SAVE, SNAPSHOT_DIR


def make_snapshot(label: str):
    if not FTL_CONTINUE_SAVE.exists():
        print("[snapshot] No existe continue.sav.")
        print("[snapshot] Abre FTL, inicia/carga una partida y vuelve a intentar.")
        return

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    clean_label = label.strip().replace(" ", "_")

    output_path = SNAPSHOT_DIR / f"{timestamp}_{clean_label}.sav"

    shutil.copy2(FTL_CONTINUE_SAVE, output_path)

    print(f"[snapshot] Snapshot creado:")
    print(f"[snapshot] {output_path}")


def main():
    if len(sys.argv) < 2:
        print("Uso:")
        print("  python3 -m observer.manual_snapshot etiqueta")
        print()
        print("Ejemplo:")
        print("  python3 -m observer.manual_snapshot antes_salto")
        return

    label = sys.argv[1]
    make_snapshot(label)


if __name__ == "__main__":
    main()