"""
Compile src/*.c into build/*.o with gcc, then set build/my.o to my1.o.

build/my.o is the object asm-differ diffs against build/ref.o. Use toggle.py
to swap it between my1.o and my2.o while watch mode is running.
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(ROOT, "src")
BUILD_DIR = os.path.join(ROOT, "build")
NAMES = ["ref", "my1", "my2"]


def main() -> None:
    os.makedirs(BUILD_DIR, exist_ok=True)
    for name in NAMES:
        src = os.path.join(SRC_DIR, f"{name}.c")
        obj = os.path.join(BUILD_DIR, f"{name}.o")
        if not os.path.exists(src):
            sys.exit(f"missing {os.path.relpath(src, ROOT)}, run generate.py first")
        subprocess.run(["gcc", "-O1", "-c", src, "-o", obj], check=True)
        print(f"built {os.path.relpath(obj, ROOT)}")

    # copyfile writes into the existing file, like a compiler would
    shutil.copyfile(
        os.path.join(BUILD_DIR, "my1.o"), os.path.join(BUILD_DIR, "my.o")
    )
    print("build/my.o <- build/my1.o")


if __name__ == "__main__":
    main()
