"""
Swap build/my.o between my1.o and my2.o, to simulate a recompile while
asm-differ is in watch mode.
"""

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
BUILD_DIR = os.path.join(ROOT, "build")


def read(name: str) -> bytes:
    with open(os.path.join(BUILD_DIR, name), "rb") as f:
        return f.read()


def main() -> None:
    try:
        current = read("my.o")
        my1 = read("my1.o")
    except FileNotFoundError as e:
        sys.exit(f"{e.filename} not found, run build.py first")
    new = "my2.o" if current == my1 else "my1.o"
    # copyfile writes into the existing file, like a compiler would
    shutil.copyfile(os.path.join(BUILD_DIR, new), os.path.join(BUILD_DIR, "my.o"))
    print(f"build/my.o <- build/{new}")


if __name__ == "__main__":
    main()
