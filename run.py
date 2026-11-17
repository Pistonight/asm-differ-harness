"""
Run asm-differ/diff.py on build/my.o against build/ref.o.

Extra arguments are passed to diff.py, for example:
    python run.py --no-pager
    python run.py -w -y
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
FUNC = "bench_fn"


def main() -> None:
    cmd = [
        sys.executable,
        os.path.join("asm-differ", "diff.py"),
        "-o",
        "-f",
        os.path.join("build", "my.o"),
        "--ref-file",
        os.path.join("build", "ref.o"),
        *sys.argv[1:],
        FUNC,
    ]
    # diff.py looks for diff_settings.py in the current directory first
    os.chdir(ROOT)
    if os.name == "posix":
        # replace this process, so diff.py runs exactly as if invoked directly
        # (same pid, signals and job control)
        os.execv(sys.executable, cmd)
        # safety
        return # pyright: ignore[reportUnreachable]
    if sys.platform == "win32":
        # Let diff.py find the less.exe that sits next to this script, ahead of
        # any other less on PATH (e.g. Git for Windows' MSYS less)
        os.environ["PATH"] = ROOT + os.pathsep + os.environ.get("PATH", "")
    # Windows has no real exec, so run it as a child. Ctrl-C reaches diff.py
    # through the console, so ignore it here and keep waiting for diff.py
    # to finish cleaning up.
    proc = subprocess.Popen(cmd)
    while True:
        try:
            sys.exit(proc.wait())
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
