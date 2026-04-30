#!/usr/bin/env python3
import argparse
import csv
import json
from pathlib import Path

STD_COLS = [
    "action", "duplicate_status", "source_absolute_path", "source_path_b64",
    "destination_path", "organization_strategy", "preserved_context",
    "media_category", "suspected_origin", "year", "size_bytes", "hash"
]

def load_plan(path_str):
    path = Path(path_str).expanduser().resolve()
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))

    if not rows:
        return []

    missing = set(STD_COLS) - set(rows[0].keys())
    if missing:
        raise SystemExit(f"ERROR: {path} missing columns: {sorted(missing)}")

    out = []
    for i, row in enumerate(rows, 1):
        d = {k: row.get(k, "") for k in STD_COLS}
        d["_id"] = f"{path.name}:{i}"
        d["plan_source"] = path.name
        d["_selected"] = True

        try:
            d["_size_int"] = int(d.get("size_bytes") or 0)
        except Exception:
            d["_size_int"] = 0

        dest = Path(d["destination_path"])
        d["destination_filename"] = dest.name
        d["destination_parent"] = str(dest.parent)
        parent_parts = Path(d["destination_parent"]).parts
        d["destination_group"] = str(Path(*parent_parts[-3:])) if len(parent_parts) >= 3 else d["destination_parent"]

        out.append(d)

    return out

HTML_TEMPLATE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Organization Plan Reviewer v6</title>
<style>
body { font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; margin: 18px; color: #222; }
h1 { margin-bottom: .2rem; }
h2 { margin-top: 1.2rem; }
.small { color: #666; font-size: .9rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 10px; }
.panel { border: 1px solid #ddd; border-radius: 12px; padding: 12px; background: #fafafa; margin-bottom: 12px; }
input, select, button { box-sizing: border-box; padding: 7px; font-size: .9rem; }
input[type=text], select { width: 100%; }
button { cursor: pointer; border: 1px solid #bbb; border-radius: 8px; background: #f3f3f3; }
button:hover { background: #e9e9e9; }
button.primary { background: #1f2937; color: white; }
button.danger { background: #fee2e2; color: #7f1d1d; border-color: #fecaca; }
.statbar { display: flex; flex-wrap: wrap; gap: 10px; margin: 10px 0; }
.card { padding: 10px; border: 1px solid #ddd; border-radius: 10px; background: white; min-width: 160px; }
.tabs { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.tab { padding: 8px 12px; border: 1px solid #ddd; border-radius: 999px; background: white; cursor: pointer; }
.tab.active { background: #1f2937; color: white; }
.group-table-wrap { max-height: 280px; overflow: auto; border: 1px solid #ddd; border-radius: 10px; background: white; }
.main-table-wrap { max-height: 62vh; overflow: auto; border: 1px solid #ddd; border-radius: 10px; }
table { border-collapse: collapse; width: 100%; font-size: 12px; }
th, td { border: 1px solid #e5e5e5; padding: 4px; vertical-align: top; white-space: nowrap; }
th { position: sticky; top: 0; background: #eee; z-index: 2; cursor: pointer; }
td.path { max-width: 540px; overflow: hidden; text-overflow: ellipsis; }
tr.off { opacity: .45; background: #fff2f2; }
.group-actions { display: flex; gap: 6px; }
.group-actions button { width: auto; padding: 4px 8px; }
</style>
</head>
<body>
<h1>Organization Plan Reviewer v6</h1>
<p class="small">Review multiple organization plans, select/unselect by groups, fine-tune rows, then export <code>organization_plan_final.tsv</code>. This page does not modify files.</p>

<div class="panel">
<h2>Filters</h2>
<div class="grid">
<label>Global search<br><input id="q" type="text" placeholder="Search paths, context, hash..."></label>
<label>Plan source<br><select id="plan"></select></label>
<label>Strategy<br><select id="strategy"></select></label>
<label>Category<br><select id="category"></select></label>
<label>Origin<br><select id="origin"></select></label>
<label>Year<br><select id="year"></select></label>
<label>Context contains<br><input id="context" type="text" placeholder="e.g. Chloe"></label>
<label>Destination group contains<br><input id="destgroup" type="text" placeholder="folder text"></label>
</div>
</div>

<div class="statbar">
<div class="card">Loaded<br><b id="loaded"></b></div>
<div class="card">Visible<br><b id="visible"></b></div>
<div class="card">Selected total<br><b id="selected"></b></div>
<div class="card">Selected visible<br><b id="selected_visible"></b></div>
<div class="card">Selected size<br><b id="selsize"></b></div>
</div>

<div class="panel">
<h2>Bulk actions</h2>
<div class="grid">
<button onclick="selectVisible(true)">Select visible rows</button>
<button class="danger" onclick="selectVisible(false)">Unselect visible rows</button>
<button onclick="selectAll(true)">Select all loaded rows</button>
<button class="danger" onclick="selectAll(false)">Unselect all loaded rows</button>
<button onclick="invertVisible()">Invert visible selection</button>
<button class="primary" onclick="exportSelected()">Export selected TSV</button>
</div>
</div>

<div class="panel">
<h2>Group selection</h2>
<p class="small">Use tabs to select/remove entire groups: plan, strategy, context, folder, category, origin, or year.</p>
<div class="tabs" id="tabs"></div>
<div class="group-table-wrap"><table id="group_table"></table></div>
</div>

<h2>Rows</h2>
<div class="main-table-wrap"><table id="tbl"></table></div>

<script>
const rows = __DATA_JSON__;
const stdCols = __STD_COLS_JSON__;
const rowCols = ["selected","plan_source","organization_strategy","preserved_context","destination_group","media_category","suspected_origin","year","size_bytes","duplicate_status","source_absolute_path","destination_path"];
const groupModes = [["plan_source","Plan"],["organization_strategy","Strategy"],["preserved_context","Context"],["destination_group","Destination folder"],["media_category","Category"],["suspected_origin","Origin"],["year","Year"]];
let filtered = rows.slice();
let sortKey = null;
let asc = true;
let groupMode = "destination_group";

function human(n) {
  let x = Number(n || 0);
  const u = ["B","KB","MB","GB","TB","PB"];
  for (let i=0; i<u.length; i++) {
    if (x < 1024) return x.toFixed(2) + " " + u[i];
    x /= 1024;
  }
  return x.toFixed(2) + " EB";
}
function uniq(k) { return [...new Set(rows.map(r => r[k] || ""))].filter(x => x).sort(); }
function fill(id, vals) {
  let e = document.getElementById(id);
  e.innerHTML = '<option value="">All</option>';
  vals.forEach(v => {
    let o = document.createElement("option");
    o.value = v;
    o.textContent = v;
    e.appendChild(o);
  });
}
function init() {
  fill("plan", uniq("plan_source"));
  fill("strategy", uniq("organization_strategy"));
  fill("category", uniq("media_category"));
  fill("origin", uniq("suspected_origin"));
  fill("year", uniq("year"));
  ["q","plan","strategy","category","origin","year","context","destgroup"].forEach(id => {
    document.getElementById(id).oninput = render;
    document.getElementById(id).onchange = render;
  });
  const tabs = document.getElementById("tabs");
  groupModes.forEach(([key,label]) => {
    const b = document.createElement("button");
    b.className = "tab" + (key === groupMode ? " active" : "");
    b.textContent = label;
    b.onclick = () => {
      groupMode = key;
      document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
      b.classList.add("active");
      renderGroups();
    };
    tabs.appendChild(b);
  });
}
function pass(r) {
  const q = document.getElementById("q").value.toLowerCase().trim();
  const p = document.getElementById("plan").value;
  const s = document.getElementById("strategy").value;
  const c = document.getElementById("category").value;
  const o = document.getElementById("origin").value;
  const y = document.getElementById("year").value;
  const ctx = document.getElementById("context").value.toLowerCase().trim();
  const dg = document.getElementById("destgroup").value.toLowerCase().trim();

  if (p && r.plan_source !== p) return false;
  if (s && r.organization_strategy !== s) return false;
  if (c && r.media_category !== c) return false;
  if (o && r.suspected_origin !== o) return false;
  if (y && r.year !== y) return false;
  if (ctx && !(r.preserved_context || "").toLowerCase().includes(ctx)) return false;
  if (dg && !(r.destination_group || "").toLowerCase().includes(dg)) return false;

  if (q) {
    const blob = [r.plan_source,r.organization_strategy,r.preserved_context,r.destination_group,r.media_category,r.suspected_origin,r.year,r.source_absolute_path,r.destination_path,r.hash].join(" ").toLowerCase();
    if (!blob.includes(q)) return false;
  }
  return true;
}
function val(r,k) { if (k === "selected") return r._selected ? "yes" : "no"; return r[k] ?? ""; }
function render() {
  filtered = rows.filter(pass);
  if (sortKey) {
    filtered.sort((a,b) => {
      let x = val(a,sortKey), y = val(b,sortKey);
      if (sortKey === "size_bytes") { x = Number(x || 0); y = Number(y || 0); }
      return (x < y ? -1 : x > y ? 1 : 0) * (asc ? 1 : -1);
    });
  }
  renderRows();
  renderGroups();
  stats();
}
function renderRows() {
  const t = document.getElementById("tbl");
  t.innerHTML = "";
  const head = document.createElement("tr");
  rowCols.forEach(k => {
    const th = document.createElement("th");
    th.textContent = k;
    th.onclick = () => {
      if (sortKey === k) asc = !asc;
      else { sortKey = k; asc = true; }
      render();
    };
    head.appendChild(th);
  });
  t.appendChild(head);
  filtered.forEach(r => {
    const tr = document.createElement("tr");
    if (!r._selected) tr.className = "off";
    rowCols.forEach(k => {
      const td = document.createElement("td");
      if (k === "selected") {
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = r._selected;
        cb.onchange = () => { r._selected = cb.checked; render(); };
        td.appendChild(cb);
      } else {
        td.textContent = val(r,k);
        if (k.includes("path")) td.className = "path";
      }
      tr.appendChild(td);
    });
    t.appendChild(tr);
  });
}
function groupRows() {
  const m = new Map();
  filtered.forEach(r => {
    const key = r[groupMode] || "(empty)";
    if (!m.has(key)) m.set(key, []);
    m.get(key).push(r);
  });
  return [...m.entries()].map(([key,items]) => {
    const selected = items.filter(r => r._selected).length;
    const size = items.reduce((a,r) => a + Number(r.size_bytes || 0), 0);
    const selectedSize = items.filter(r => r._selected).reduce((a,r) => a + Number(r.size_bytes || 0), 0);
    return {key,items,count:items.length,selected,size,selectedSize};
  }).sort((a,b) => b.count - a.count || String(a.key).localeCompare(String(b.key)));
}
function renderGroups() {
  const t = document.getElementById("group_table");
  t.innerHTML = "";
  const head = document.createElement("tr");
  ["group","files","selected","size","selected_size","actions"].forEach(k => {
    const th = document.createElement("th");
    th.textContent = k;
    head.appendChild(th);
  });
  t.appendChild(head);
  groupRows().forEach(g => {
    const tr = document.createElement("tr");
    [g.key,g.count,g.selected,human(g.size),human(g.selectedSize)].forEach(v => {
      const td = document.createElement("td");
      td.textContent = v;
      tr.appendChild(td);
    });
    const td = document.createElement("td");
    td.className = "group-actions";
    const b1 = document.createElement("button");
    b1.textContent = "Select group";
    b1.onclick = () => { g.items.forEach(r => r._selected = true); render(); };
    const b2 = document.createElement("button");
    b2.textContent = "Unselect group";
    b2.className = "danger";
    b2.onclick = () => { g.items.forEach(r => r._selected = false); render(); };
    td.appendChild(b1);
    td.appendChild(b2);
    tr.appendChild(td);
    t.appendChild(tr);
  });
}
function stats() {
  const selected = rows.filter(r => r._selected);
  const selectedVisible = filtered.filter(r => r._selected);
  document.getElementById("loaded").textContent = rows.length;
  document.getElementById("visible").textContent = filtered.length;
  document.getElementById("selected").textContent = selected.length;
  document.getElementById("selected_visible").textContent = selectedVisible.length;
  document.getElementById("selsize").textContent = human(selected.reduce((a,r) => a + Number(r.size_bytes || 0), 0));
}
function selectVisible(v) { filtered.forEach(r => r._selected = v); render(); }
function selectAll(v) { rows.forEach(r => r._selected = v); render(); }
function invertVisible() { filtered.forEach(r => r._selected = !r._selected); render(); }
function esc(v) { return String(v ?? "").replace(/\\t/g," ").replace(/\\r/g,"\\\\r").replace(/\\n/g,"\\\\n"); }
function exportSelected() {
  const selected = rows.filter(r => r._selected);
  if (!selected.length) { alert("No rows selected."); return; }
  const lines = [stdCols.join("\\t")];
  selected.forEach(r => lines.push(stdCols.map(c => esc(r[c])).join("\\t")));
  const blob = new Blob([lines.join("\\n") + "\\n"], {type:"text/tab-separated-values"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "organization_plan_final.tsv";
  a.click();
}
init();
render();
</script>
</body>
</html>
"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", nargs="+", required=True)
    ap.add_argument("--out-html", required=True)
    args = ap.parse_args()

    rows = []
    for p in args.plans:
        rows.extend(load_plan(p))
    if not rows:
        raise SystemExit("ERROR: no rows loaded")

    out = Path(args.out_html).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    html = HTML_TEMPLATE.replace("__DATA_JSON__", json.dumps(rows, ensure_ascii=False))
    html = html.replace("__STD_COLS_JSON__", json.dumps(STD_COLS, ensure_ascii=False))
    out.write_text(html, encoding="utf-8")
    print(f"HTML reviewer written: {out}")

if __name__ == "__main__":
    main()
