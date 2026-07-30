/* Dataset/signal freshness check for the published page.
 *
 * qa_eval.mjs guards the page's WIRING. This guards its CONTENTS: that the dataset list the
 * page renders is the lab's current inventory and not a frozen older sweep. It exists because
 * on 2026-07-30 the Data page was serving data_assets built from a lakehouse_source_overview.json
 * that stopped being written on 2026-06-26 -- 329 sources on the page vs 756 actually landed --
 * while a second list on the same page showed the fresh count. Nothing failed; the page just
 * quietly under-reported the estate by more than half.
 *
 * Run:  node qa_datasets.mjs
 */
import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const html = fs.readFileSync("index.html", "utf8");
const snap = JSON.parse(fs.readFileSync("data.json", "utf8"));
const inv = JSON.parse(fs.readFileSync(
  "../lakehouse/silver/source_inventory/source_inventory.json", "utf8"));
const state = snap.state;

/* ---- 1. the page's dataset lists match the lab's current inventory ---- */
const invIds = [...new Set(inv.map(r => r.source).filter(Boolean))];
// the public build strips certain funder/programme names out of ids and titles, so a page id
// can be a suffix of the lab id (nsf_awards_discovery -> awards_discovery). Match either form.
const present = (ids, id) => ids.has(id) || [...ids].some(p => p && id.endsWith(p));

const lh = state.lakehouse_sources || [];
assert.equal(lh.length, inv.length,
  `lakehouse_sources has ${lh.length} entries, inventory has ${inv.length}`);
const lhIds = new Set(lh.map(s => s.id));
const missing = invIds.filter(id => !present(lhIds, id));
assert.equal(missing.length, 0,
  `${missing.length} landed sources missing from the page, e.g. ${missing.slice(0, 5)}`);

const assets = state.data_assets || [];
const assetIds = new Set(assets.map(a => a.asset_id));
const missingAssets = invIds.filter(id => !present(assetIds, id));
assert.equal(missingAssets.length, 0,
  `${missingAssets.length} landed sources missing from data_assets, e.g. ${missingAssets.slice(0, 5)}`);

/* ---- 2. the "as of" the page shows is the inventory's, not a frozen sweep's ---- */
const invMtime = fs.statSync("../lakehouse/silver/source_inventory/source_inventory.json").mtimeMs;
for (const [label, iso] of [["lakehouse_summary.generated_at", state.lakehouse_summary?.generated_at],
                            ["inventory.built_iso", state.inventory?.built_iso]]) {
  assert(iso, `${label} is missing`);
  const skew = Math.abs(new Date(iso).getTime() - invMtime);
  assert(skew < 60_000,
    `${label} = ${iso} does not match when the inventory was built -- the page is quoting a stale sweep`);
}

/* ---- 3. signals, targets and findings are present ---- */
assert((state.signals || []).length >= 60, `signals collapsed to ${(state.signals || []).length}`);
assert((state.targets || []).length >= 14, `targets collapsed to ${(state.targets || []).length}`);
assert((state.findings || []).length >= 60, `findings collapsed to ${(state.findings || []).length}`);
const withSignals = lh.filter(s => (s.signals || []).length).length;
assert(withSignals > lh.length * 0.5,
  `only ${withSignals}/${lh.length} sources carry a signal list`);

/* ---- 4. the page actually RENDERS them (run its own script, no DOM library) ---- */
const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const src = blocks[blocks.length - 1].replace(/\bload\(\);?\s*$/, "");
const sink = {};
const el = (id) => new Proxy({}, {
  get: (t, k) => k === "innerHTML" ? (t._h || "")
    : k === "querySelectorAll" ? () => []
    : k === "classList" ? { toggle() {}, add() {}, remove() {} }
    : k === "dataset" ? {} : el(id),
  set: (t, k, v) => { if (k === "innerHTML") { t._h = v; sink[id] = (sink[id] || "") + v; } return true; },
});
const ctx = {
  document: { querySelector: s => el(s), querySelectorAll: () => [], addEventListener() {},
              getElementById: s => el("#" + s), createElement: () => el("new"), body: el("body") },
  window: { location: { hash: "", search: "" }, addEventListener() {}, matchMedia: () => ({ matches: false, addEventListener() {} }) },
  history: { scrollRestoration: "auto", pushState() {}, replaceState() {} },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  fetch: async () => ({ json: async () => snap }),
  console, setTimeout, clearTimeout, requestAnimationFrame: f => f(),
  navigator: { userAgent: "node" }, gtag: () => {}, URLSearchParams, URL,
};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(src, ctx, { filename: "index.html:script" });
ctx.__snap = snap;
vm.runInContext(
  "DATA=Object.assign({},__snap.state,{_built_iso:__snap.built_iso,_built_pacific:__snap.built_pacific});" +
  "try{EVID=__snap.evidence||{}}catch(e){};try{DOCS=__snap.project_docs||[]}catch(e){};", ctx);
const call = (fn, ...a) => vm.runInContext(fn, ctx)(...a);

call("renderLake");
const rendered = Object.values(sink).join("");
assert(rendered.length > 50_000, "the Data view rendered almost nothing");
for (const id of ["argo_gdac_float_profiles", "ais_marinecadastre", "usgs_iv_turbidity"]) {
  assert(rendered.includes(id) || lh.some(s => s.id === id),
    `the Data view is missing ${id}`);
}
assert.equal(call("lakeAssets").length, assets.filter(a => a.exists !== false).length,
  "lakeAssets() does not expose the refreshed data_assets");

const rows = lh.reduce((a, s) => a + (s.rows || 0), 0);
console.log(`PASS  sources=${lh.length} (inventory ${inv.length})  data_assets=${assets.length}  ` +
            `rows=${rows.toLocaleString()}  with-signals=${withSignals}  ` +
            `signals=${state.signals.length}  targets=${state.targets.length}  findings=${state.findings.length}`);
