import time
import shutil
from datetime import datetime
from pathlib import Path

from config import FTL_SAVE_DIR, FTL_CONTINUE_SAVE, SNAPSHOT_DIR


WATCHED_EXTENSIONS = {".sav", ".ini"}


def get_file_state(path: Path):
    if not path.exists():
        return None

    stat = path.stat()

    return {
        "modified_time": stat.st_mtime,
        "size": stat.st_size,
    }


def scan_save_folder():
    files = {}

    if not FTL_SAVE_DIR.exists():
        return files

    for path in FTL_SAVE_DIR.iterdir():
        if path.is_file() and path.suffix in WATCHED_EXTENSIONS:
            files[path] = get_file_state(path)

    return files


def copy_continue_snapshot():
    if not FTL_CONTINUE_SAVE.exists():
        return

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output_path = SNAPSHOT_DIR / f"continue_{timestamp}.sav"

    shutil.copy2(FTL_CONTINUE_SAVE, output_path)

    print(f"[watcher] Snapshot creado: {output_path}")


def print_folder_status(files):
    print("[watcher] Archivos detectados:")

    if not files:
        print("[watcher] No se encontraron archivos .sav o .ini.")
        return

    for path, state in files.items():
        print(f"  - {path.name} | tamaño: {state['size']} bytes")


def watch_save_folder():
    print("[watcher] Observando carpeta de guardados de FTL")
    print(f"[watcher] Ruta: {FTL_SAVE_DIR}")
    print()

    previous_files = scan_save_folder()
    print_folder_status(previous_files)

    if not FTL_CONTINUE_SAVE.exists():
        print()
        print("[watcher] continue.sav todavía no existe.")
        print("[watcher] Abre FTL, inicia una partida y guarda/sal al menú si hace falta.")

    try:
        while True:
            current_files = scan_save_folder()

            previous_paths = set(previous_files.keys())
            current_paths = set(current_files.keys())

            created = current_paths - previous_paths
            deleted = previous_paths - current_paths
            common = previous_paths & current_paths

            for path in created:
                print()
                print(f"[watcher] Archivo creado: {path.name}")

                if path == FTL_CONTINUE_SAVE:
                    copy_continue_snapshot()

            for path in deleted:
                print()
                print(f"[watcher] Archivo eliminado: {path.name}")

            for path in common:
                if current_files[path] != previous_files[path]:
                    print()
                    print(f"[watcher] Archivo modificado: {path.name}")
                    print(f"[watcher] Tamaño: {current_files[path]['size']} bytes")

                    if path == FTL_CONTINUE_SAVE:
                        copy_continue_snapshot()

            previous_files = current_files
            time.sleep(0.5)

    except KeyboardInterrupt:
        print()
        print("[watcher] Observación detenida por el usuario.")


if __name__ == "__main__":
    watch_save_folder()