# Performance of generated code (step counts)

The hidden tests run compiled programs under a limit of 10^8 machine steps, and the slowest corpus program
(`tests/corpus/loop_1e6.v1`) used 70M. This document records where the steps went, what was changed, and the
numbers before and after. "Before" is the compiler at commit `3489509`; "after" is the current `tier2/v1c.py`
(and `tier3/tier3.v1`, which emits byte-identical output).

All numbers below were measured with `vm/sqvm` (`VM_STATS=1`); the "profile" numbers use the new
`vm/sqvm --profile FILE` (per-instruction hit counts) and `tools/v1prof.py` (hit counts folded onto source lines, using the
statement map that `tier2/v1c.py` writes when the environment variable `V1C_MAP=file` is set; it does not change the output).

    tools/v1prof.py tests/corpus/loop_1e6.v1          # steps per source line, top 25
    V1C=other_compiler.py tools/v1prof.py prog.v1     # profile with another compiler (e.g. an old version)

## 1. Where the steps went (before)

Per-source-line profile of the heaviest corpus programs (old compiler; runtime-routine steps are attributed to the last
statement, which is why `arrays_big` blames its last line):

| program | total | hot lines |
|---|---:|---|
| loop_1e6 | 70.0M | `t = t * 31 + i;` 41%, `s = s + i * 3;` 30%, `i = i + 1;` 17%, `while (i < 1000000)` 11% |
| arrays_big | 43.1M | the general-multiply routine 80% (100,000 `big[i] * (i - 50000)` at about 340 steps each), the loops 20% |
| gcd | 25.0M | `if (a > b) { a = a - b; } else { b = b - a; }` 76%, `while (a != b)` 24% |
| loop_calls_3e5 | 19.5M | the call statement 60%, the callee `return a * 7 + b;` 40% (65 steps per call all together) |
| ackermann | 7.1M | `return ack(m - 1, ack(m, n - 1));` 89% (push/pop of the frame) |
| fib_rec | 4.9M | `return fib(n - 1) + fib(n - 2);` 85% |

The same facts as a micro-benchmark (`construct` repeated 1000 times in a loop, loop overhead subtracted):

| construct (cost of one execution, loop overhead removed) | before | after |
|---|---:|---:|
| `empty loop iteration` | 20 | 5 |
| `x = y;` | 4 | 4 |
| `x = 5;` | 4 | 2 |
| `x = x + 1;` | 11 | 1 |
| `x = x - 1;` | 9 | 1 |
| `x = x + y;` | 11 | 3 |
| `x = y + z;` | 11 | 5 |
| `x = y + 5;` | 11 | 5 |
| `x = y - z;` | 9 | 5 |
| `x = 5 - y;` | 9 | 3 |
| `x = y + z + 1;` | 12 | 10 |
| `g = g + 1;  (global)` | 9 | 1 |
| `g = g + h;` | 11 | 3 |
| `x = g + h;` | 11 | 5 |
| `x = y * 3;` | 14 | 6 |
| `x = y * 10;` | 20 | 12 |
| `x = y * 31;` | 26 | 18 |
| `x = y * z; (small)` | 44 | 32 |
| `x = i * i; (i<1000)` | 336.4 | 78.4 |
| `x = a[3];` | 13 | 2 |
| `x = a[y];` | 13 | 7 |
| `a[3] = x;` | 17 | 2 |
| `a[y] = x;` | 17 | 11 |
| `a[y] = 1;` | 17 | 11 |
| `a[y] = a[y] + 1;` | 27 | 19 |
| `x = a[y] + b[z];` | 25 | 19 |
| `if (x < y) {}` | 8 | 8 |
| `if (x < 100) {}` | 8 | 3 |
| `if (x >= 5) {}` | 9 | 4 |
| `if (x == y) {}` | 6 | 6 |
| `if (x == 5) {}` | 8 | 6 |
| `if (x) {}` | 3 | 3 |
| `if (x > 0) {}` | 3 | 3 |
| `x = (y < z);` | 14 | 14 |
| `f1(y);  (stmt)` | 20 | 13 |
| `x = f1(y);` | 24 | 17 |
| `x = f2(y, z);` | 35 | 22 |
| `x = f2(y + 1, z * 2);` | 49 | 24 |
| `x = fr(0); (self-rec, 0 depth)` | 32 | 14 |
| `x = fr(5); (5 rec calls)` | 417 | 320 |
| `putc(x);` | 3 | 3 |

The big costs, in order of how much they mattered in the corpus:

1. **Every statement went through a temp and a copy.** `x = x + 1` was `ADD` into a temp (7 steps) and then a `mov` (4): 11 steps for what
   the machine does in one (`x -= -1`). Every assignment, return value, call result and call argument paid the extra `mov`.
2. **General multiplication was ~330 steps** (always 32 iterations, about 10 steps each).
3. **Constants:** `x = 5` cost 4, `x + 5` cost 7-11, `i < 1000000` cost 8, `x * 31` 26 steps.
4. **Arrays:** read 9 + the mov, write 12 + computing the pointer; a constant index (`a[3]`) cost the same.
5. **Calls:** return 5 steps (indirect jump by patching), result copied twice, self-calls 11 steps per saved cell to push and 7 to pop,
   and every local and parameter was saved whether or not it was used again.
6. **Global scalars as left operands** were always copied to a temp (4 steps) "in case a call in the right operand changes them".

## 2. What was changed (all in `tier2/v1c.py` and mirrored in `tier3/tier3.v1`)

Exact sequences are in `docs/ABI.md`, "Final layout (v1c codegen)". In the order they were implemented:

| # | change | typical gain |
|---|---|---|
| 1 | **Destination-directed code.** `x = e;`, `return e;` and non-self call arguments tell the last operation of the expression where to put its value (`want`, `wantp`, `pend`, `isfinal`), so the temp and the `mov` vanish. A call statement no longer copies `RV`. | `x = a + b` 11 -> 5 (7 -> 5 with the cheaper add below), call results and returns -4 |
| 2 | **In-place `+`/`-` with pooled immediates:** `x = x + c` is one `SUB x, lit(-c)`; `x = c - y` is 3 steps; `x = c` is 2 steps (`CLR`, `SUB lit(-c)`) instead of 4. | `i = i + 1` 11 -> 1 |
| 3 | **Global scalar snapshots only when a call follows in the statement** (`cz`, a per-token flag computed after lexing). Call arguments are copied to temps only for the same reason, or (self-calls) when a parameter cell would be clobbered. | `g = g + 1` 9 -> 1 |
| 4 | **Constant multiply:** the first two Horner bits are loaded as 2 or 3 subtractions of `-x` (no `mov`); for a negative constant the result is built negated (no final negation). | `x * 3` 14 -> 6, `x * 31` 26 -> 18 |
| 5 | **New general multiply routine:** sign fix, then an unrolled MSB-first bit test per bit (`MB -= 2^j`, add it back when negative), entered at the highest possible bit through a short dispatch chain; `MB < 8` is a repeated addition. About 5.5 steps per bit instead of 10 and no leading-zero iterations. Negative multipliers are negated first. | `i * i` (i < 1000) 336 -> 78; `arrays_big` 43.1M -> 15.8M |
| 6 | **Arrays hold negated elements.** A read is `res res; res <patched>` (no `T` round trip): 7 steps; a write subtracts the value directly (no `U`): 11 steps. A literal in-bounds index is a direct cell: read 2, write 2. The pointer of `a[i] = e` is kept negated in `pt` so the patches read it directly. | `a[3] = x` 17 -> 2, `x = a[y]` 13 -> 7 |
| 7 | **Calls:** each function starts with its own return instruction `NEG1 ZERO c` and callers patch its `c` word (so the old `RA` cell and `jmpi` are gone): enter 3 steps, return 2 instead of 5. Push stores the negated cell (no `U`): 9 steps per cell instead of 11; pop reads straight into the cell: 5 instead of 7. | -3 per call, -4 per saved cell |
| 8 | **Liveness for self-call saves:** only parameters and locals whose name occurs in the rest of the function (from the start of the current statement, or of the outermost enclosing loop) are pushed. | fewer cells per recursive call |
| 9 | **Compare with a literal** (`< <= > >=` with `0 < c < INT_MAX`, `== !=` with `c != 0`): subtract `c` in place, test the sign, add it back. **Compare with a temp:** the difference is computed in the temp (1 step instead of 5). | `x < 100` 8 -> 3, `x >= 5` 9 -> 4, `x == 5` 8 -> 6 |
| 10 | **Generic add in 5 steps** (`T = -(l+r); res = -T`) instead of 7. | `x = y + z` 7 -> 5 |

Ideas that were looked at and not done: eager patching of array stores (the patch addresses are not known before the value's code is
emitted in a single pass); loop inversion (needs a jump-if-true form of every compare); compare of two variables in place (saves
about 2 of 8 steps but needs a restore on both paths); a cheaper push/pop (a store through a pointer needs 3 patched words and a
clear; anything shorter needs a frame pointer, i.e. a different calling convention).

## 3. Corpus results (steps, valid programs; `tests/run_corpus_compiled.sh -v`)

Largest programs, per stage (each column adds to the previous one):

| program | before | + 1,2,3 | + 4,5 | + 6 | after (all) |
|---|---:|---:|---:|---:|---:|
| loop_1e6 | 70006940 | 48004693 | 48004693 | 48004226 | 37003841 |
| arrays_big | 43093613 | 40491474 | 17400979 | 16600459 | 15800091 |
| gcd | 25008506 | 17006203 | 17006203 | 17005812 | 17005575 |
| loop_calls_3e5 | 19503623 | 14702526 | 14702526 | 14702263 | 11102140 |
| ackermann | 7087423 | 6364626 | 6364626 | 6364315 | 5249367 |
| fib_rec | 4949190 | 4942983 | 4942983 | 4941692 | 3852626 |
| bignum_fact | 1335585 | 677660 | 669999 | 652678 | 456782 |
| sieve | 237496 | 179123 | 179123 | 149992 | 111126 |
| fib_wrap | 236147 | 159087 | 159087 | 144140 | 134343 |
| big_evaluator | 209415 | 169505 | 169249 | 159688 | 129223 |
| sort_input | 155755 | 128986 | 128986 | 112091 | 102571 |
| depth_1000 | 97395 | 92693 | 92693 | 92514 | 75427 |

All programs (steps, and code size in words; the pool is included):

| program | steps before | steps after | after/before | words before | words after |
|---|---:|---:|---:|---:|---:|
| loop_1e6 | 70006940 | 37003841 | 52.9% | 1336 | 762 |
| arrays_big | 43093613 | 15800091 | 36.7% | 1896 | 1784 |
| gcd | 25008506 | 17005575 | 68.0% | 1603 | 978 |
| loop_calls_3e5 | 19503623 | 11102140 | 56.9% | 1302 | 743 |
| ackermann | 7087423 | 5249367 | 74.1% | 2452 | 1603 |
| fib_rec | 4949190 | 3852626 | 77.8% | 1693 | 1076 |
| bignum_fact | 1335585 | 456782 | 34.2% | 1100 | 1374 |
| sieve | 237496 | 111126 | 46.8% | 1425 | 792 |
| fib_wrap | 236147 | 134343 | 56.9% | 1207 | 681 |
| big_evaluator | 209415 | 129223 | 61.7% | 41888 | 30144 |
| sort_input | 155755 | 102571 | 65.9% | 1766 | 1050 |
| depth_1000 | 97395 | 75427 | 77.4% | 1433 | 874 |
| depth_999_calls | 96216 | 74808 | 77.8% | 1433 | 877 |
| lcg | 92425 | 51861 | 56.1% | 1568 | 1022 |
| mutual_style | 91122 | 69173 | 75.9% | 2620 | 2476 |
| wrap_arith | 36797 | 20651 | 56.1% | 4069 | 3131 |
| int_min | 27936 | 16271 | 58.2% | 2345 | 2235 |
| rw_histogram | 25996 | 17979 | 69.2% | 1632 | 980 |
| signed_compare | 21921 | 15985 | 72.9% | 3838 | 2866 |
| precedence | 21620 | 15045 | 69.6% | 3548 | 2438 |
| state_machine | 20268 | 10428 | 51.5% | 2315 | 1102 |
| rw_calc_realistic | 17641 | 9581 | 54.3% | 2285 | 2093 |
| long_expr | 15485 | 8151 | 52.6% | 27864 | 16629 |
| minmax_chain | 14705 | 9071 | 61.7% | 2338 | 1537 |
| params8 | 13077 | 8647 | 66.1% | 3775 | 2560 |
| globals_arrays | 12589 | 7848 | 62.3% | 3234 | 2610 |
| eval_order | 9981 | 6789 | 68.0% | 3097 | 3069 |
| max_functions | 9559 | 7167 | 75.0% | 18277 | 12723 |
| truthiness | 8216 | 5928 | 72.2% | 2839 | 1961 |
| shadowing | 7853 | 5371 | 68.4% | 1867 | 1058 |
| stmt_forms | 7853 | 5226 | 66.5% | 2618 | 2316 |
| nested_control | 7756 | 5096 | 65.7% | 1657 | 1783 |
| many_locals | 7730 | 4824 | 62.4% | 3002 | 2379 |
| no_return | 6346 | 4421 | 69.7% | 1755 | 1020 |
| names | 5877 | 3949 | 67.2% | 1781 | 1016 |
| hanoi | 4990 | 3561 | 71.4% | 1161 | 819 |
| array_param_style | 4591 | 2830 | 61.6% | 1820 | 1037 |
| getc_eof | 4344 | 2906 | 66.9% | 1264 | 758 |
| comments_lex | 4309 | 2912 | 67.6% | 1271 | 706 |
| getc_echo | 2580 | 1485 | 57.6% | 1188 | 670 |
| deep_nesting | 2242 | 1304 | 58.2% | 1509 | 850 |
| call_stmt | 2227 | 1403 | 63.0% | 1515 | 870 |
| reverse_input | 1783 | 948 | 53.2% | 1254 | 712 |
| getc_empty | 1317 | 832 | 63.2% | 1097 | 639 |
| spec_squares | 662 | 388 | 58.6% | 602 | 1153 |
| spec_fact | 311 | 230 | 74.0% | 662 | 1255 |
| putc_wrap | 212 | 183 | 86.3% | 667 | 588 |
| hello | 29 | 26 | 89.7% | 98 | 92 |

Programs added by this work (no 'before' number was measured): cmp_literals, cmp_temps, eq_literals, mul_consts, mul_signs, rec_live.

The slowest program is now `loop_1e6` with 37.0M steps (was 70.0M); the sum over the 48 old corpus programs went down by 47% (172.5M to 91.4M).

Fuzz programs (`tests/fuzz/gen_v1.py`, seeds 1-200, sizes 2-30): total 287.1M steps with the old compiler, 187.2M with the new one (65.2%),
all 200 outputs identical between the old and the new compiler (and both match the interpreter in `scripts/difftest.sh`).

## 4. Tier 3 (the compiler written in v1)

`tier3/tier3.v1` was updated with every change above, and still emits byte-identical output (`scripts/tier3_diff.sh`, `scripts/tier3_fuzz.sh`,
`scripts/bootstrap.sh`). Its own compiled form is smaller and faster: the self-compile (C1 on `tier3.v1`) went from 107M to about 54M steps.
The source grew from 30.3 KB to about 41 KB (limit 60,000 bytes), 75 functions (limit 200). Data memory: the three trie arrays (`tch`,
`tsib`, `tchild`) are dead after lexing, so they are reused for the `cz` flags and the array table (see `tier3/NOTES.md`);
without that the data would no longer fit below address 2^19 (v1c's "array too large" rule).

## 5. How the equivalence was checked

For every change: `tests/run_corpus_compiled.sh`, `scripts/difftest.sh` (compiled code vs the Python interpreter `ref/v1interp.py`),
`scripts/tier3_diff.sh`, `scripts/tier3_fuzz.sh` (tier3 output vs v1c output, byte for byte), `scripts/bootstrap.sh` (C2 == C3, v1c(tier3) == C2)
and finally `make test`. New corpus tests that target the new code: `mul_signs` (300 products of edge values through the general
routine, including INT_MIN), `mul_consts` (literal multipliers in the three code shapes), `cmp_literals` and `eq_literals` (literal compares at
the boundaries, INT_MAX, INT_MIN), `cmp_temps` (compares of temps, including `==` and chains), `rec_live` (self-calls whose locals are live,
dead, loop-carried or pending in an expression). Their expected outputs were computed independently (Python wrap-around arithmetic or the old compiler) and all of them were
also confirmed with `ref/v1interp.py`.

Final runs on the committed tree: `scripts/difftest.sh` on 2000 programs with fresh seeds (10000-11999): 0 failures;
`scripts/tier3_fuzz.sh` on 600 more (seeds 20000-20599): 0 failures; 1,100 further difftest/tier3_fuzz programs during development
(seeds 1-300, 1000-1299, 2000-2299, 3000-3299, 4000-4299, 5000-5399); `scripts/bootstrap.sh` (C1 == C2 == C3, v1c(tier3.v1) == C2);
`make test`; `scripts/ddc.sh` (MATCH with the regenerated `tier3/tier3.selfcompiled.txt`).
Not run: the official VM binaries (`scripts/official_check.sh`) on the new output.
