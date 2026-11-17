"""
Generate random C sources for the harness: src/ref.c, src/my1.c and src/my2.c.

Each defines the same function with a long run of stores to a volatile global.
About 30% of the stored constants are random per file, so the objects differ
in scattered places, which gives asm-differ something to show.
"""

import argparse
import os
import random

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(ROOT, "src")
FUNC = "bench_fn"
NAMES = ["ref", "my1", "my2"]


def gen_source(seed: int, lines: int) -> str:
    rng = random.Random(seed)
    body = "\n".join(
        (
            f"    sink = {rng.randrange(1 << 16)};"
            if rng.random() < 0.3
            else f"    sink = {i * 7};"
        )
        for i in range(lines)
    )
    return f"volatile int sink;\nvoid {FUNC}(void) {{\n{body}\n}}\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lines", type=int, default=200, help="statements per function (default 200)"
    )
    parser.add_argument(
        "--seed", type=int, default=0, help="base seed; file i uses seed+i (default 0)"
    )
    args = parser.parse_args()

    os.makedirs(SRC_DIR, exist_ok=True)
    for i, name in enumerate(NAMES):
        path = os.path.join(SRC_DIR, f"{name}.c")
        with open(path, "w", newline="\n") as f:
            f.write(gen_source(args.seed + i, args.lines))
        print(f"generated {os.path.relpath(path, ROOT)}")


if __name__ == "__main__":
    main()
