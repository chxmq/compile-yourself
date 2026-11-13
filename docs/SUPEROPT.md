# Superoptimizer results (`toolkit/superopt.py`)

A bounded exhaustive-search superoptimizer for the one-instruction machine
(`mem[a] -= mem[b]; if mem[a] < 0 then pc = c else pc += 3`), in the spirit of Massalin,
*Superoptimizer: A Look at the Smallest Program* (ASPLOS 1987).  Original implementation: a C core
(`toolkit/superopt_c.c`, multithreaded DFS over all instruction sequences) driven by Python.

## 1. Model

- Cells: writable `X, Y, T, U` (`T`, `U` are scratch with an arbitrary initial value, never assumed 0) and the
  reserved read-only `ZERO, ONE, NEG1` (`Z, O, N`), which must still hold their values at every exit.
- An instruction may write only to the spec's writable cells.  Constants appear only as the `b` operand, plus the
  no-op write `NEG1 -= ZERO`, which always branches (the unconditional jump idiom).  Writing a constant cell to
  something else and restoring it later is deliberately excluded.
- Branch targets `c`: any later instruction, the end of the sequence (`F`, fall-through exit) or `J` (jump-out exit).
  The search enumerates forward jumps only (so every candidate terminates); the compiler's own sequences are also
  allowed to jump backwards (the signed-compare idiom shares its subtract code), which the checker simulates with a step cap.  Steps are instructions executed, per path.
- A spec fixes which cells must hold which values at exit (all other writable cells are clobberable) and, for branch
  specs, which exit is taken: e.g. `JEQ0` = leave via `J` iff `X == 0`, via `F` otherwise, `X` preserved at both exits.
- Pooled literal cells (`x = 5` in 2 steps with a cell holding -5) are outside the model: only `ONE`/`NEG1` exist.
  That is why the constant-load rows compare against a unary ONE/NEG1 baseline, not the compiler's real 2 steps.

## 2. Method and what "minimal" means

- Sequences of length 1..K are enumerated exhaustively (with `a == b` instructions never branching, `NEG1 -= ZERO`
  only as a jump).  Straight-line specs are searched with and without internal branches.
  Bounds K: straight-line 6-8 instructions depending on the number of cells, branching 5 (4 when a fourth cell
  `U` is available), listed per spec in `toolkit/superopt_results.json` (`bounds`).
- A candidate must pass the equivalence test on every vector in a table of edge values (0, +-1, +-2, +-3, INT_MIN(+1,+2),
  INT_MAX(-1), powers of two and their neighbours, 100, 12345, random) for all (X, Y) pairs with two scratch
  settings each (about 4000 vectors); survivors are re-checked in Python with an independently written reference over
  all-pairs edge values plus 100,000 random samples (scratch cells random too).
  Documented sequences are additionally run on the real `vm/sqvm` (`tests/test_superopt.sh`).
- **"Minimal" means minimal within the searched bound and cell model above**, with equivalence established by testing
  (edge values + random), not by a symbolic proof.  A passing candidate is a very strong but not a formal guarantee;
  a search that finds nothing proves nothing beyond the bound K.
- "worst steps" is the maximum number of executed instructions over all test vectors, per exit for branch specs
  (`F` = fall-through exit, `J` = jump exit).  The search keeps two winners per length: fewest worst-case steps,
  and fewest summed per-sign-class worst steps (X<0, X==0, X>0).

## 3. Results

`ours` = the sequence tier-2 `v1c.py` emits for that primitive on its common path (encoded in this cell model and verified by the same checker).  "min found" = shortest verified sequence within the stated bounds (see section 2); ">K" = nothing found up to K instructions.

| primitive | spec | ours (instr) | ours worst steps | min found (instr) | worst steps of found | verdict | sequence found |
|---|---|---:|---:|---:|---:|---|---|
| CLEAR | X := 0 | 1 | 1 | 1 | 1 | equal | `X X` |
| MOV | X := Y (Y kept, T clobbered) | 4 | 4 | 4 | 4 | equal | `X X;T T;T Y;X T` |
| NEG | X := -X in place (T, U clobbered) | 6 | 6 | 6 | 6 | equal | `X T;U U;U X;X X;T U;X T` |
| NEGTO | X := -Y (Y kept) | 2 | 2 | 2 | 2 | equal | `X X;X Y` |
| ADD | X += Y (T clobbered) | 3 | 3 | 3 | 3 | equal | `T T;T Y;X T` |
| SUB | X -= Y | 1 | 1 | 1 | 1 | equal | `X Y` |
| DBL | X += X (T clobbered) | 3 | 3 | 3 | 3 | equal | `T T;T X;X T` |
| ADD3 | U := X + Y (X, Y kept, T clobbered) | 5 | 5 | 5 | 5 | equal | `T T;T X;T Y;U U;U T` |
| SUB3 | U := X - Y (X, Y kept, T clobbered) | 5 | 5 | 5 | 5 | equal | `T T;T X;U U;U Y;U T` |
| CONST-3 | X := -3 using only ONE/NEG1 (T clobbered) | 4 | 4 | 4 | 4 | equal | `X X;X O;X O;X O` |
| CONST-2 | X := -2 using only ONE/NEG1 (T clobbered) | 3 | 3 | 3 | 3 | equal | `X X;X O;X O` |
| CONST-1 | X := -1 using only ONE/NEG1 (T clobbered) | 2 | 2 | 2 | 2 | equal | `X X;X O` |
| CONST+0 | X := 0 using only ONE/NEG1 (T clobbered) | 1 | 1 | 1 | 1 | equal | `X X` |
| CONST+1 | X := 1 using only ONE/NEG1 (T clobbered) | 2 | 2 | 2 | 2 | equal | `X X;X N` |
| CONST+2 | X := 2 using only ONE/NEG1 (T clobbered) | 3 | 3 | 3 | 3 | equal | `X X;X N;X N` |
| CONST+3 | X := 3 using only ONE/NEG1 (T clobbered) | 4 | 4 | 4 | 4 | equal | `X X;X N;X N;X N` |
| CONST+4 | X := 4 using only ONE/NEG1 (T clobbered) | 5 | 5 | 5 | 5 | equal | `X X;X N;X N;X N;X N` |
| CONST+5 | X := 5 using only ONE/NEG1 (T clobbered) | 6 | 6 | 6 | 6 | equal | `X X;X N;X N;X N;X N;X N` |
| CONST+6 | X := 6 using only ONE/NEG1 (T clobbered) | 7 | 7 | 7 | 7 | equal | `X X;X N;X N;X N;X N;X N;X N` |
| CONST+7 | X := 7 using only ONE/NEG1 (T clobbered) | 8 | 8 | 8 | 8 | equal | `X X;X N;X N;X N;X N;X N;X N;X N` |
| CONST+8 | X := 8 using only ONE/NEG1 (T clobbered) | 9 | 9 | 8 | 8 | **shorter** | `X X;X N;X N;X N;X N;T T;T X;X T` |
| JMP | unconditional jump out | 1 | 1 | 1 | 1 | equal | `N Z>J` |
| JLT0 | jump if X < 0 (X kept, T clobbered) | 1 | F1/J1 | 1 | F1/J1 | equal | `X Z>J` |
| JGE0 | jump if X >= 0 | 2 | F1/J2 | 2 | F1/J2 | equal | `X Z>2;N Z>J` |
| JGT0 | jump if X > 0 | 3 | F3/J3 | 3 | F3/J3 | equal | `X Z>3;T T;T X>J` |
| JLE0 | jump if X <= 0 | 4 | F3/J4 | 4 | F3/J3 | equal | `T T;T X>3;N Z>J;X Z>J` |
| JEQ0 | jump if X == 0 | 4 | F3/J4 | 3 | F3/J3 | **shorter** | `T T;T X>3;T O>J` |
| JNE0 | jump if X != 0 | 3 | F3/J3 | 3 | F3/J3 | equal | `X Z>J;T T;T X>J` |
| JEQ_XY | jump if X == Y (X, Y kept; T clobbered; U also free in 4-cell run) | 9 | F8/J9 | >6/5 | - | none found (straight <= 6, branching <= 5 instr) | - |
| JNE_XY | jump if X != Y | 8 | F8/J8 | >6/5 | - | none found (straight <= 6, branching <= 5 instr) | - |
| JLT_XY | jump if X < Y, true signed compare (no overflow) | 11 | F9/J8 | >6/5 | - | none found (straight <= 6, branching <= 5 instr) | - |
| JGE_XY | jump if X >= Y, true signed compare | 10 | F8/J9 | >6/5 | - | none found (straight <= 6, branching <= 5 instr) | - |

## 4. Sequences that beat or differ from the compiler's

| primitive | found instr | found sequence | steps (X<0 / X=0 / X>0) | worst | ours | ours steps (X<0/X=0/X>0) | ours worst |
|---|---:|---|---|---:|---|---|---:|
| CONST+8 | 8 | `X X;X N;X N;X N;X N;T T;T X;X T` | 8/8/8 | 8 | `X X;X N;X N;X N;X N;X N;X N;X N;X N` | 9/9/9 | 9 |
| JLE0 | 4 | `T T;T X>3;N Z>J;X Z>J` | 3/3/3 | 3 | `X Z>J;T T;T X>4;N Z>J` | 1/4/3 | 4 |
| JLE0 | 5 | `X O>2;N Z>4;X N;N Z>J;X N>J` | 3/3/3 | 3 | `X Z>J;T T;T X>4;N Z>J` | 1/4/3 | 4 |
| JEQ0 | 3 | `T T;T X>3;T O>J` | 3/3/2 | 3 | `X Z>4;T T;T X>4;N Z>J` | 1/4/3 | 4 |
| JEQ0 | 5 | `X O>3;X N;N Z>5;X N>5;N Z>J` | 3/3/3 | 3 | `X Z>4;T T;T X>4;N Z>J` | 1/4/3 | 4 |

## 5. Reading the numbers

- **Equal rows** confirm the hand-written v1c sequences are already optimal in this model: CLR, MOV (4), in-place ADD (3),
  DBL (3), SUB (1), NEGTO (2), in-place NEG (6), three-address ADD/SUB (5 each), JMP, `BLT0`, and the sign branches
  `>=0` (2), `>0` (3), `!=0` (3) are all minimal within the bounds.  The search also reproduces the hand-found
  costs exactly for ONE/NEG1 constants (|k|+1 for small k).
- **Nothing found** for the two-variable equality and true signed-compare branches (9, 8, 11 and 10 instructions in v1c):
  no equivalent exists with up to 5 instructions (6 straight-line, 4 with a spare cell `U`).  The exhaustive search
  cannot say how far below the compiler's 8-11 the true minimum is; it only rules out <= 5 (<= 4 with `U`).
- **Strictly shorter found:** `JEQ0` (3 instructions, `T=0; T-=X; if T<0 skip; T-=ONE; if T<0 jump`; the trick is that
  `-X-1 < 0` exactly when `X >= 0`, after `-X >= 0` has excluded `X > 0` and `INT_MIN`) vs `jz()`'s 4, and the same
  trick for `JLE0` (4 instructions with worst 3 steps vs 4).  `CONST+8` in 8 instructions uses doubling (`X=4; T=-X; X-=T`).
  The constant load is moot for the compiler, which loads constants from pool cells in 2 steps.
- **Why the compiler was NOT changed.**  The `JEQ0`/`JLE0` sequences cut the worst case (3 vs 4 steps) but make the
  `X < 0` path slower (3 vs 1 step), and the compiler's idioms are tuned for the common sign.  Per-class step counts
  (`X<0 / X=0 / X>0`) are in the table above: ours 1/4/3, found 3/3/2 (sum 8 vs 8, no sequence beats the sum).
  As a one-off experiment (not committed) `jz()` in a scratch copy of `v1c.py` was replaced with the 3-instruction form and the
  whole corpus re-run on `vm/sqvm`: total steps over `tests/corpus` went from 92.6M to 94.6M, with most programs a
  little faster but `gcd` (`while (a != b)`, where `a - b` is mostly positive) 12% slower (17.0M -> 19.0M).  Outputs
  of that experiment were not diffed and no other test was run, since it was discarded.  No change was made to
  `tier2/v1c.py`, `tier3/tier3.v1` or the generated output.
