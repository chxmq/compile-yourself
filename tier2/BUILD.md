# Tier 2: the v1 compiler (`v1c.py`)

Needs only Python 3 (standard library). There is nothing to build.

    python3 tier2/v1c.py < prog.v1 > prog.txt        # works from any directory
    vm/sqvm prog.txt < input.txt                     # our VM (make -C vm); same format as the official ./vm

- Reads the v1 source on stdin and writes whitespace-separated decimal words (one per line) to stdout.
- An invalid program gets a message on stderr and exit code 1.
- Compile time is about 0.1 s for a 20 KB source.

The code generation scheme is documented in `docs/ABI.md`, section "Final layout (v1c codegen)".
`tier3/tier3.v1` must reproduce this compiler's output byte for byte.

## Tests

    tests/run_corpus_compiled.sh        # tests/corpus/*.v1 compiled, run on sqvm, compared to .out; invalid/ must be rejected
    scripts/difftest.sh N [seed0]       # N random programs (tests/fuzz/gen_v1.py): compiled+sqvm vs ref/v1interp.py
    python3 tests/tier1/run_v1progs.py  # BIGMUL / HANOI (tier1/*.v1 compiled with v1c)

## Notes on speed

Typical step costs: variable move 4, add/sub 3 to 7, compare-and-branch 3 to 9, array read 9 plus the index,
array write 12 plus the index, non-recursive call about 12 plus 4 per argument, return 5.
A self-recursive call additionally pays 11 steps per live cell to push and 7 to pop.
Multiplication by a literal is inline (3 steps per bit). A general `*` costs about 330 steps, or 30 + 3*b when one operand b is in 0..23.

## Verification status (at the time of writing)

- Corpus: 131 checks pass (the valid programs match their `.out`, and every invalid program is rejected). Max 70M steps (`loop_1e6.v1`, a 1M-iteration loop with 2 multiplies per iteration).
- Fuzz: 1200 generated programs (seeds 1-100 and 101-1200) match the interpreter exactly; max 22M steps, max compile time 1 s.
- BIGMUL: 319 cases (0*x, 40x40 digits of 9, random), max 0.67M steps. HANOI: n=1..8 exact, max 78K steps.
- Not covered: the official VM was never run on the compiled output (Docker was unavailable).
