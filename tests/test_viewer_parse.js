// Tests the trace-parsing code embedded in tools/viewer.html against traces made by vm/sqvm.
// usage: node tests/test_viewer_parse.js   (needs build/demo/demo.trace; run scripts/make_demo_trace.sh first)
const fs = require("fs"), path = require("path"), assert = require("assert");
const root = path.join(__dirname, "..");
const html = fs.readFileSync(path.join(root, "tools/viewer.html"), "utf8");
const src = html.split("<script>")[1].split("</script>")[0];
const m = { exports: {} };
new Function("module", "document", src)(m, undefined);
const { parseTrace, parseProgram } = m.exports;

const tr = fs.readFileSync(path.join(root, "build/demo/demo.trace"));
const ab = tr.buffer.slice(tr.byteOffset, tr.byteOffset + tr.byteLength);
const T = parseTrace(ab);
const prog = parseProgram(fs.readFileSync(path.join(root, "build/demo/demo.txt"), "utf8"));
assert.strictEqual(T.codeWords, prog.length, "code words = program length");
const FULL = parseInt(fs.readFileSync(path.join(root, "build/demo/demo.steps"), "utf8"), 10);
assert.strictEqual(T.steps, FULL, "step count matches VM_STATS");
assert.strictEqual(T.addr[0], 0, "first instruction at pc 0");
assert.strictEqual(T.addr[T.kind.findIndex((k, i) => k === 0 && i > 0)], prog[2], "boot jump goes to entry");
// independent replay: apply writes onto the program and re-run the machine; must reproduce the same pc sequence
const mem = new Int32Array(1 << 20); mem.set(prog);
let pc = 0, i = 0, n = 0, writes = 0;
while (i < T.kind.length) {
  assert.strictEqual(T.kind[i], 0); assert.strictEqual(T.addr[i], pc, `pc mismatch at step ${n}`);
  i++; n++;
  const a = mem[pc], b = mem[pc + 1], c = mem[pc + 2];
  const OUTA = (1 << 20) - 1;
  const r = ((a === OUTA ? 0 : mem[a]) - (b === OUTA ? 0 : mem[b])) | 0;
  if (a !== OUTA) mem[a] = r;
  if (i < T.kind.length && T.kind[i] === 1) {
    assert.strictEqual(T.addr[i], a); assert.strictEqual(T.val[i], r); assert.strictEqual(T.step[i], n); i++; writes++;
  } else assert(!(a < prog.length) || a === OUTA, "write to code cell without record");
  pc = r < 0 ? c : pc + 3;
}
assert(writes > 20, "demo should patch code words (pointer access)");
// truncated trace tolerated
const cut = parseTrace(ab.slice(0, ab.byteLength - 4 * 1 - 0));
assert(cut.steps <= T.steps);
assert.throws(() => parseTrace(new ArrayBuffer(16)), /bad magic/);
console.log(`viewer parse OK: ${T.steps} steps, ${writes} code writes, ${T.codeWords} words`);
