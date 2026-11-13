# Design log (one page)

## The machine and the goal
One instruction `a b c`: `mem[a] -= mem[b]; if mem[a] < 0 goto c`. No indexed addressing, no call, no stack, no
shift, no AND. Everything else (add, move, compare, multiply, arrays, calls, I/O of decimal numbers) has to be
built from subtract-and-branch. Plan: tier 1 hand-built programs, tier 2 a compiler for the small language v1
written in Python (`tier2/v1c.py`), tier 3 the same compiler written in v1 (`tier3/tier3.v1`) and run on the
machine itself.

## What was tried, what worked, what was learned
- **Exact VM clone first (`vm/sqvm.c`).** Every later claim depends on the VM being right, so edge cases (fetch of
  a, b, c before the write; `OUT` as `a` stores nothing; `IN` as `a` is an error; wraparound; the 10^8 step cap)
  were written down and tested by hand (`tests/test_vm.py`, 29 cases). A trace mode was added early because
  self-modifying code is very hard to debug without seeing which words get written.
- **Idioms as macros before a compiler (`toolkit/sqasm.py`).** Writing ECHO, REVERSE, BIGMUL and HANOI with an
  assembler showed which sequences recur (move = 4 steps, add = 3, signed compare, indirect load/store). The
  compiler then emits exactly those sequences, so tier 1 de-risked tier 2.
- **Signed compare is not one subtraction.** `a - b` overflows when signs differ (`2000000000 < -2000000000`).
  Fix: test the signs first, subtract only when they agree. `INT_MIN` also breaks "negate then test", so
  zero tests look at the sign of the value itself.
- **Pointer access by self-modification** (the only way to index memory): overwrite the operand word of a later
  instruction with the address, then execute it. Each use site owns private patch words, so sites never interfere,
  and the path is branch-free. Cost: 8 steps per load, 12 per store at first; later 7 and 11 (negated array storage), 2 for a literal index (docs/PERF.md).
- **Static frames instead of a stack for every call.** Each function's parameters, locals, temporaries and return
  address are fixed cells, so almost every operand is a plain direct address (cheap, and no stack-pointer
  arithmetic). Only self-recursive call sites save and restore the frame (below).
- **Output without division.** Numbers are printed by subtracting descending powers of ten. Multiplication is inline
  shift-and-add for literal factors, a 32-iteration routine otherwise.
- **Tier 3 as a two-pass port.** v1 has no strings and no way to buffer unbounded output, so `tier3.v1` runs the
  code generator twice: pass 1 only counts code size and records label addresses, pass 2 prints. Names are interned
  in a trie (no string compare exists in v1). It reproduces v1c byte for byte, which is what makes the bootstrap
  and the DDC check possible (any optimisation added to one compiler must be mirrored in the other).
- **Testing strategy that paid off:** a strict reference interpreter (`ref/v1interp.py`, rejects what v1 forbids,
  can randomise uninitialised locals), a 131-case corpus including invalid programs, a fuzz program generator,
  and differential comparison v1c vs interpreter, then tier3 vs v1c. The bootstrap fixed point
  C1 = C2 = C3 is the final integration test.
- **Things that were deliberate trade-offs, not bugs:** `fail()` in tier3.v1 stops the VM by storing out of range
  (v1 has no exit); the compiler has hard limits (60,000 source bytes, 200 functions, 24-character names).
- **Not done / not claimed:** no optimiser beyond constant multiply and a few peepholes; no proof of correctness
  beyond testing and the soundness argument below.

## Static-frame soundness argument
Claim: giving every function one static copy of its frame is correct if only self-recursive call sites save the
frame.

1. v1 requires a function to be **defined before it is used**, and a function's own name is visible inside its body.
   Therefore a call from `f` can only target a function defined earlier, or `f` itself. The call graph is a DAG plus
   direct self-loops: there is no mutual recursion and no longer cycle (calling a later function is a compile error).
2. Suppose `f` calls `g`, `g != f`. `g` was defined before `f`, so `g` cannot (transitively) call `f`, and hence
   no activation of `g` can be live while another activation of `g` is started, except by `g` calling itself.
   So the frames of different functions never alias, and a non-recursive function has at most one live
   activation.
3. If `f` calls itself, a second activation overwrites `f`'s frame cells. The call site pushes every live frame
   cell of the caller (parameters, locals, return address, and the temporaries currently in use) onto a software
   stack before the call and pops them after it returns. Arguments are evaluated into caller temporaries first and
   copied into the parameter cells afterwards, so `f(f(x))` and `f(n, f(n))` do not see half-written parameters.
   After the pop the caller's frame equals what it was before the call; the callee's return value travels through
   the global cell `RV`, which is not part of the frame.
4. Globals are shared by design and are not saved. An operand that is a global scalar and could be changed by a
   call is snapshotted into a temporary before the call, to preserve left-to-right evaluation.
5. Induction on call depth gives that each activation sees its own values. Tested by the Ackermann, fib, and
   depth-999/1000 corpus programs and fuzzing; not formally verified.
- **Superoptimizer check of the idioms (`toolkit/superopt.py`, `docs/SUPEROPT.md`).** A bounded exhaustive search (C core,
  Massalin 1987 style) over all 1..K instruction sequences confirmed that the hand-picked CLR/MOV/ADD/NEG/DBL/compare
  idioms are minimal within the searched bounds, and found only one shorter branch idiom (`x == 0` in 3 instead of 4
  instructions). It makes the `x < 0` path slower, and gcd got 12% slower in an experiment, so the compilers were not changed.

## Prior art used for ideas
Sources consulted for ideas (no code was copied; the design above was built and tested here):
- howerj/subleq and jserv/muxleq: small one-instruction VMs and how they are made self-hosting.
- Oleg Mazonka, "Higher Subleq": layering a higher-level language on one instruction; self-modifying pointer access.
- jvorob/subleq-bootstrap: bootstrapping a compiler chain on a one-instruction machine.
- hackaday.io/project/25583: a SUBLEQ build log (idioms, I/O conventions).
- David A. Wheeler, *Fully Countering Trusting Trust through Diverse Double-Compiling* (dissertation): the procedure
  implemented in `scripts/ddc.sh`.
- Ken Thompson, "Reflections on Trusting Trust": the motivation for the DDC check. A self-hosted compiler binary can
  carry behaviour that is in no source file; recompiling the source with an independent compiler (v1c) and comparing
  exposes that.
- Henry Massalin, "Superoptimizer: A Look at the Smallest Program" (ASPLOS 1987): inspiration for `toolkit/superopt.py`.
