// Smoke test of the viewer UI logic with a stub DOM (no browser needed): loads the demo trace, scrubs, steps, jumps to writes.
const fs = require("fs"), path = require("path"), assert = require("assert");
const root = path.join(__dirname, "..");
const src = fs.readFileSync(path.join(root, "tools/viewer.html"), "utf8").split("<script>")[1].split("</script>")[0];
const els = {};
const ctx = new Proxy({}, { get: (t, k) => (k in t ? t[k] : () => {}), set: (t, k, v) => (t[k] = v, true) });
const mk = id => els[id] || (els[id] = { id, value: "0", textContent: "", innerHTML: "", clientWidth: 600, width: 10, height: 10,
  getContext: () => ctx, addEventListener() {}, getBoundingClientRect: () => ({ left: 0, top: 0, width: 10, height: 10 }) });
const document = { getElementById: mk };
mk("fade").value = "200"; mk("speed").value = "20";
const win = { addEventListener() {} };
new Function("document", "window", "requestAnimationFrame", "module", src)(document, win, () => {}, undefined);
const FULL = parseInt(fs.readFileSync(path.join(root, "build/demo/demo.steps"), "utf8"), 10);
const tr = fs.readFileSync(path.join(root, "build/demo/demo.trace")), pr = fs.readFileSync(path.join(root, "build/demo/demo.txt"));
const ab = b => b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength);
(async () => {
  await els.pfile.onchange({ target: { files: [{ text: async () => pr.toString() }] } });
  await els.tfile.onchange({ target: { files: [{ arrayBuffer: async () => ab(tr) }] } });
  assert.strictEqual(els.msg.textContent, "");
  assert.strictEqual(els.scrub.max, FULL);
  els.scrub.value = 300; // scrub via the handler registered on the slider
  els.nextw.onclick(); const s1 = els.status.textContent;
  assert(new RegExp("step \\d+ / " + FULL).test(s1), s1);
  els.fwd.onclick(); els.back.onclick(); els.prevw.onclick();
  const s2 = els.status.textContent; assert(new RegExp("step \\d+ / " + FULL).test(s2));
  assert(els.dis.innerHTML.includes("class=\"cur\""), "current instruction row shown");
  els.nextw.onclick(); els.nextw.onclick();
  assert(els.dis.innerHTML.length > 100 && els.log.innerHTML.includes(":="), "log shows a write");
  console.log("viewer UI smoke OK:", els.status.textContent);
})().catch(e => { console.error(e); process.exit(1); });
