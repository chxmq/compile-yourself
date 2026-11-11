"""REVERSE: read all of stdin into buf (store through a pointer cell by self-modifying code),
then print it backwards.  Works for any bytes (no sentinel), any length up to the free memory.
Build:  python3 toolkit/sqasm.py tier1/reverse.sqa.py -o tier1/reverse.txt
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "toolkit"))
from sqasm import Asm


def build():
    a = Asm()
    a.cell("buf0", "buf")        # constant: address of buf
    a.bss("p"); a.bss("c")
    a.mov("p", "buf0")           # p = &buf[0]
    a.label("rd")
    a.getc("c")
    a.blt0("c", "wr")            # EOF
    a.store("p", "c")            # buf[p] = c
    a.inc("p")
    a.jmp("rd")
    a.label("wr")
    a.beq("p", "buf0", "end")    # nothing left
    a.dec("p")
    a.load("c", "p")
    a.putc("c")
    a.jmp("wr")
    a.label("end")
    a.halt()
    a.bss("buf", 1100)           # last, so it is not stored in the file
    return a
