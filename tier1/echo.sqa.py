"""ECHO: copy stdin to stdout until EOF.  Build:  python3 toolkit/sqasm.py tier1/echo.sqa.py -o tier1/echo.txt

Per byte (6 steps):
    T = -IN            (T = -byte, or +1 at EOF)
    U = -T             (U = byte, or -1 at EOF)   -- branch to `eof` if U < 0
    OUT <- T           (prints 0 - T = byte)
    jump back
NUL bytes (0) are fine: U = 0 is not negative.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "toolkit"))
from sqasm import Asm


def build():
    a = Asm()
    a.label("loop")
    a.clr("T")
    a.ins("T", "IN")             # T = -byte  (EOF: T = +1)
    a.clr("U")
    a.ins("U", "T", "eof")       # U = byte (EOF: -1), branch at EOF
    a.ins("OUT", "T")            # print low 8 bits of 0 - T = byte
    a.jmp("loop")
    a.label("eof")
    a.halt()
    return a
