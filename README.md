# asm-differ harness

Scratch project for manually testing asm-differ (including on Windows).
Assumes x86_64, with `gcc` and `objdump` on PATH.

```sh
git clone <asm-differ repo> asm-differ   # or check out the branch to test
python generate.py      # src/ref.c, src/my1.c, src/my2.c
python build.py         # build/*.o, build/my.o = my1.o
python run.py -w -y     # diff my.o vs ref.o in watch mode (extra args go to diff.py)
python toggle.py        # in another terminal: swap my.o between my1.o and my2.o
```
