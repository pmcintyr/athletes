"use strict";
/* Static athlete-profile site. Data files are produced by build_site.py; routes use the URL hash
   (#/a/<id>, #/h2h/<id>/<id>, #/l/<year>/<out|in>/<M|W>, #/plan, #/clubs, #/club/<id>,
   #/cantons, #/canton/<XX>, #/region/<id>) so GitHub Pages never needs a server. */

const app = document.getElementById("app");
let META = null;
const cache = new Map();

function getJSON(path) {
  if (cache.has(path)) return cache.get(path);
  const p = fetch(path).then(r => { if (!r.ok) throw new Error(`${r.status} ${path}`); return r.json(); });
  cache.set(path, p);
  p.catch(() => cache.delete(path));
  return p;
}
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const norm = s => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z]+/g, " ").trim();
const slug = s => norm(s).replace(/ /g, "-");
const fmtDate = iso => { if (!iso) return ""; const [y, m, d] = iso.split("-"); return d ? `${d}.${m}.${y}` : iso; };
const fmtInt = n => Number(n).toLocaleString("fr-CH");
const D = i => META.disc[i];            // [indoor, label_de, label_fr, lower_better, wind_event]
const dName = i => D(i)[2];
const lower = i => D(i)[3] === 1;
const sortv = (d, v) => (lower(d) ? v : -v);
const fmtWind = w => (w === null || w === undefined ? "" : (w > 0 ? "+" : "") + w.toFixed(1));
const athleteHref = (id, name) => `#/a/${id}/${slug(name)}`;

function fmtVal(v, d) {
  if (lower(d)) {
    if (v >= 3600) { const h = Math.floor(v / 3600), m = Math.floor((v - h * 3600) / 60); return `${h}:${String(m).padStart(2, "0")}:${String(Math.round(v % 60)).padStart(2, "0")}`; }
    if (v >= 60) { const m = Math.floor(v / 60); return `${m}:${(v - m * 60).toFixed(2).padStart(5, "0")}`; }
    return v.toFixed(2);
  }
  return /kampf|Athletic-Cup/i.test(D(d)[1]) ? String(Math.round(v)) : v.toFixed(2);
}

/* Same rule as parse_mark in alabus_scraper.py: "1:42.55" -> 102.55, "1.42.55" -> 102.55, "10.9m" -> 10.9 */
function markValue(t) {
  const b = String(t ?? "").replace(/[^\d:.,]/g, "").replace(",", ".");
  if (!b) return null;
  if (b.includes(":")) return b.split(":").reduce((a, p) => a * 60 + parseFloat(p), 0);
  const p = b.split(".");
  if (p.length === 3) return +p[0] * 60 + +p[1] + +p[2] / 100;
  const v = parseFloat(b);
  return Number.isFinite(v) ? v : null;
}

/* Shards store meetings, venues and clubs once ("_": [meetings, venues, clubs]); rows point at them by number. */
const decoded = new Map();
async function loadAthlete(id) {
  if (!/^[0-9a-f]{10}$/.test(id)) return null;
  if (decoded.has(id)) return decoded.get(id);
  let shard;
  try { shard = await getJSON(`a/${id.slice(0, 3)}.json`); } catch { return null; }
  const raw = shard[id];
  if (!raw) return null;
  const [M, L, C] = shard._ || [[], [], []];
  const a = {
    ...raw,
    pb: raw.pb.map(([d, mark, wind, date, m, l, hand]) => [d, mark, wind, date, M[m] ?? "", L[l] ?? "", hand]),
    m: raw.m,
    r: raw.r.map(([d, date, mark, wind, place, round, m, l, c, ass]) =>
      [d, date, mark, wind, place, round, M[m] ?? "", L[l] ?? "", C[c] ?? "", ass, markValue(mark)]),
  };
  decoded.set(id, a);
  return a;
}

/* ---------- search ---------- */
function searchBox({ label, placeholder, small, link, exclude }) {
  const box = document.createElement("div");
  box.className = "search" + (small ? " search-small" : "");
  const id = "q" + Math.random().toString(36).slice(2, 8);
  box.innerHTML = `<label for="${id}">${esc(label)}</label>
    <input id="${id}" type="search" autocomplete="off" spellcheck="false" placeholder="${esc(placeholder)}">
    <ul class="hits" aria-live="polite"></ul>`;
  const input = box.querySelector("input"), hits = box.querySelector(".hits");
  let timer, seq = 0;
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, 120); });
  input.addEventListener("keydown", e => {
    if (e.key !== "Enter") return;
    const a = hits.querySelector("a");
    if (a) { e.preventDefault(); location.hash = a.getAttribute("href"); }
  });
  async function run() {
    const toks = norm(input.value).split(" ").filter(Boolean);
    const key = toks.filter(t => t.length >= 2).sort((a, b) => b.length - a.length)[0];
    if (!key) { hits.innerHTML = ""; return; }
    const my = ++seq;
    let entries;
    try { entries = await getJSON(`idx/${key.slice(0, 2)}.json`); } catch { entries = []; }
    if (my !== seq) return;
    const found = entries.filter(e => e[0] !== exclude && toks.every(t => norm(e[1]).split(" ").some(w => w.startsWith(t)))).slice(0, 30);
    hits.innerHTML = found.length
      ? found.map(e => `<li><a href="${link(e)}"><span class="who">${esc(e[1])}</span><span class="meta">${e[2]}${e[3] ? ", " + esc(e[3]) : ""}</span></a></li>`).join("")
      : `<li class="msg">Aucun athlète trouvé pour « ${esc(input.value.trim())} ».</li>`;
  }
  return box;
}

/* ---------- views ---------- */
function home() {
  document.title = META.title;
  const y = META.years[0];
  app.innerHTML = `
    <section class="hero">
      <h1>Profils des athlètes suisses</h1>
      <p class="lede">Records personnels, progression saison par saison et confrontations directes, à partir des résultats publics de Swiss Athletics depuis ${META.first_year}.</p>
      <div id="s"></div>
      <div class="counts">
        <span><b class="num">${fmtInt(META.athletes)}</b>athlètes</span>
        <span><b class="num">${fmtInt(META.results)}</b>résultats</span>
        <span><b class="num">${META.years.length}</b>saisons</span>
      </div>
    </section>
    <h2>Meilleures performances ${y}</h2>
    <div class="quick">
      <a href="#/l/${y}/out/M">Hommes, plein air</a>
      <a href="#/l/${y}/out/W">Femmes, plein air</a>
      <a href="#/l/${y}/in/M">Hommes, en salle</a>
      <a href="#/l/${y}/in/W">Femmes, en salle</a>
    </div>
    ${META.geo ? `<h2>Clubs, cantons et régions</h2>
    <p class="lede small">Tous les athlètes d'un club avec leur saison et le rang du club dans chaque épreuve ; les cantons et les trois régions côte à côte.</p>
    <div class="quick"><a href="#/clubs">Trouver un club</a><a href="#/cantons">Comparer les cantons</a><a href="#/cantons/regions">Comparer les régions</a></div>` : ""}
    ${META.plan ? `<h2>Planifier ma saison</h2>
    <p class="lede small">Partez de vos objectifs : les courses à placer avant et après, là où l'on court à votre niveau, à portée de train. <a href="#/plan">Ouvrir le planificateur</a></p>` : ""}`;
  const s = searchBox({ label: "Rechercher un athlète", placeholder: "Nom ou prénom", link: e => athleteHref(e[0], e[1]) });
  app.querySelector("#s").replaceWith(s);
  s.querySelector("input").focus();
}

function pbLanes(rows) {
  return `<ul class="lanes">${rows.map(([d, mark, wind, date, meeting, loc, hand]) => `
    <li class="lane">
      <span class="ev">${esc(dName(d))}</span>
      <span class="mk">${esc(mark)}${D(d)[4] && wind !== null ? `<small>${fmtWind(wind)}</small>` : ""}${hand ? `<small>manuel</small>` : ""}</span>
      <span class="where">${fmtDate(date)}, ${esc(meeting)}${loc ? `, ${esc(loc)}` : ""}</span>
    </li>`).join("")}</ul>`;
}

function chartSVG(pts, d) {
  const W = 640, H = 260, L = 58, R = 18, T = 26, B = 30;
  const ys = pts.map(p => p.year), vs = pts.map(p => p.value);
  let x0 = Math.min(...ys), x1 = Math.max(...ys);
  if (x0 === x1) { x0 -= 1; x1 += 1; }
  let v0 = Math.min(...vs), v1 = Math.max(...vs);
  const pad = (v1 - v0) * 0.15 || Math.abs(v1) * 0.03 || 1;
  v0 -= pad; v1 += pad;
  const X = y => L + (W - L - R) * (y - x0) / (x1 - x0);
  const Y = v => (lower(d) ? T + (H - T - B) * (v - v0) / (v1 - v0) : T + (H - T - B) * (v1 - v) / (v1 - v0));
  let g = "";
  const raw = (v1 - v0) / 4, p10 = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / p10;
  const tick = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p10;
  for (let v = Math.ceil(v0 / tick) * tick; v <= v1 + 1e-9; v += tick) {
    const yy = Y(v).toFixed(1);
    g += `<line class="grid" x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}"/><text class="axis" x="${L - 8}" y="${(+yy + 4).toFixed(1)}" text-anchor="end">${fmtVal(v, d)}</text>`;
  }
  const step = x1 - x0 > 12 ? 2 : 1;
  for (let y = Math.ceil(x0); y <= x1; y += step) {
    if (!ys.includes(y) && pts.length > 1 && step === 1) continue;
    g += `<text class="axis" x="${X(y).toFixed(1)}" y="${H - 8}" text-anchor="middle">${y}</text>`;
  }
  const best = pts.reduce((a, p) => (sortv(d, p.value) < sortv(d, a.value) ? p : a), pts[0]);
  const line = pts.map(p => `${X(p.year).toFixed(1)},${Y(p.value).toFixed(1)}`).join(" ");
  const dots = pts.map(p => `<circle class="pt${p === best ? " best" : ""}" cx="${X(p.year).toFixed(1)}" cy="${Y(p.value).toFixed(1)}" r="${p === best ? 6 : 4.5}"><title>${p.year} : ${esc(p.mark)}</title></circle>`).join("");
  const bx = Math.min(Math.max(X(best.year), L + 24), W - R - 24);
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Progression en ${esc(dName(d))}, meilleure performance par saison">
    ${g}<polyline class="line" points="${line}"/>${dots}
    <text class="lbl" x="${bx.toFixed(1)}" y="${(Y(best.value) - 12).toFixed(1)}" text-anchor="middle">${esc(best.mark)}</text></svg>`;
}

function medalList(rows) {
  return `<ul class="medals">${rows.map(([y, d, p, meet, intl, mark]) => `<li><span class="medal p${p}">${p}</span><span><strong class="num">${y}</strong> ${esc(dName(d))}${D(d)[0] ? " (salle)" : ""}, ${esc(meet)}${mark ? ` <span class="num muted">${esc(mark)}</span>` : ""}</span></li>`).join("")}</ul>`;
}

function medalCount(rows) {
  const n = [1, 2, 3].map(p => rows.filter(m => m[2] === p).length);
  return [["or", n[0]], ["argent", n[1]], ["bronze", n[2]]].filter(x => x[1]).map(([w, c]) => `${c} ${w}`).join(", ");
}

function honours(m) {
  const intl = m.filter(x => x[4] === 1), nat = m.filter(x => x[4] !== 1);
  return `<h2>Palmarès</h2>
    ${intl.length ? `<h3>Championnats internationaux <span class="muted">· ${medalCount(intl)}</span></h3>${medalList(intl)}` : ""}
    ${nat.length ? `<h3>Championnats suisses <span class="muted">· ${medalCount(nat)}</span></h3>${medalList(nat)}` : ""}
    <p class="note">Podiums en finale relevés automatiquement d'après le nom des compétitions (championnats suisses de toutes catégories, championnats d'Europe et du monde, Jeux olympiques, Universiades, masters) : à vérifier.</p>`;
}

async function profile(id) {
  const a = await loadAthlete(id);
  if (!a) return notFound("Cet athlète n'est pas (ou plus) dans la base.");
  const [rks, geo, plan, cad] = await Promise.all([optJSON(`rk/${id.slice(0, 3)}.json`), optJSON("geo.json"), optJSON("planner.json"), optJSON("cadres.json")]);
  const rk = rks && rks[id], canton = a.g && a.g[0];
  document.title = `${a.n} – ${META.title}`;
  const out = a.pb.filter(p => D(p[0])[0] === 0), ins = a.pb.filter(p => D(p[0])[0] === 1);
  const sbBy = {};
  a.sb.forEach(([d, year, mark, value]) => (sbBy[d] = sbBy[d] || []).push({ year, mark, value }));
  const progDiscs = Object.keys(sbBy).map(Number).sort((x, y) => sbBy[y].length - sbBy[x].length || x - y);
  const resDiscs = [...new Set(a.r.map(r => r[0]))].sort((x, y) => x - y);
  const seasons = [...new Set(a.r.map(r => r[1].slice(0, 4)))].sort().reverse();
  const pbKey = new Set(a.pb.map(p => `${p[0]}|${p[3]}|${p[1]}`));

  app.innerHTML = `
    <section class="profile-head">
      <h1>${esc(a.n)}</h1>
      ${(a.k || []).length ? `<div class="badges">${a.k.map(([code, group, wcp]) => `<a class="badge" href="#/cadres">${esc((META.cadres || {})[code] || code)}${wcp ? " · World Class Potential" : ""}</a>`).join("")}</div>` : ""}
      <div class="facts">
        <span>${a.s === "W" ? "Née" : "Né"} en <strong class="num">${a.by}</strong></span>
        ${a.c ? `<span>Club ${META.geo ? `<a href="${clubHref(a.c)}"><strong>${esc(a.c)}</strong></a>` : `<strong>${esc(a.c)}</strong>`}</span>` : ""}
        ${canton && geo && geo.cantons[canton] ? `<span>Canton <a href="#/canton/${canton}"><strong>${esc(geo.cantons[canton][0])}</strong></a></span>` : ""}
        ${a.cs.length > 1 ? `<span>Avant : ${a.cs.slice(0, -1).reverse().map(esc).join(", ")}</span>` : ""}
        ${a.pg ? `<span><a href="p/${id}.html">Lien à partager</a></span>` : ""}
        ${META.plan ? `<span><a href="#/plan/${id}">Planifier ma saison</a></span>` : ""}
      </div>
      ${(a.lim || []).length ? `<p class="limit">${esc((META.limit_name || "").replace(/^Limites/, "Limite"))} ${META.limit_year} réalisée : ${a.lim.map(([ev, mark, lim, date, indoor]) =>
        `<strong>${esc(ev)} ${esc(mark)}</strong>${indoor ? " (salle)" : ""} <span class="muted">(limite ${esc(lim)})</span>`).join(", ")}. <a href="#/cadres">Voir les cadres</a></p>` : ""}
      <div id="cmp" class="compare"></div>
    </section>
    <h2>Records personnels</h2>
    ${a.pb.length ? `<div class="cols">
      ${out.length ? `<div><h3>Plein air</h3>${pbLanes(out)}</div>` : ""}
      ${ins.length ? `<div><h3>En salle</h3>${pbLanes(ins)}</div>` : ""}
    </div>` : `<p class="empty">Aucune performance régulière.</p>`}
    ${strongEvent(a, rk, geo)}
    ${limitsBlock(a, plan, cad)}
    ${a.m.length ? honours(a.m) : ""}
    ${rivalsBlock(id, rk)}
    ${meetingsBlock(a)}
    ${progDiscs.length ? `<h2>Progression</h2>
      <div class="controls"><label>Épreuve <select id="pd">${progDiscs.map(d => `<option value="${d}">${esc(dName(d))}${D(d)[0] ? " (salle)" : ""}</option>`).join("")}</select></label></div>
      <div id="chart"></div>` : ""}
    <h2>Tous les résultats</h2>
    <div class="controls">
      <label>Épreuve <select id="fd"><option value="">Toutes</option>${resDiscs.map(d => `<option value="${d}">${esc(dName(d))}${D(d)[0] ? " (salle)" : ""}</option>`).join("")}</select></label>
      <label>Saison <select id="fy"><option value="">Toutes</option>${seasons.map(y => `<option>${y}</option>`).join("")}</select></label>
    </div>
    <div id="res"></div>
    ${META.full_results ? "" : `<p class="note">Seule la meilleure performance de chaque saison et épreuve est disponible pour l'instant.</p>`}`;

  app.querySelector("#cmp").replaceWith(Object.assign(searchBox({
    label: "Comparer avec un autre athlète", placeholder: "Nom ou prénom", small: true, exclude: id,
    link: e => `#/h2h/${id}/${e[0]}`,
  }), { id: "cmp" }));
  app.querySelector("#cmp").classList.add("compare");

  const drawChart = () => {
    const d = +app.querySelector("#pd").value;
    app.querySelector("#chart").innerHTML = chartSVG(sbBy[d].slice().sort((x, y) => x.year - y.year), d);
  };
  if (progDiscs.length) { app.querySelector("#pd").addEventListener("change", drawChart); drawChart(); }

  let shown = 50;
  const drawRes = reset => {
    if (reset !== false) shown = 50;
    const fd = app.querySelector("#fd").value, fy = app.querySelector("#fy").value;
    const all = a.r.filter(r => (fd === "" || r[0] === +fd) && (fy === "" || r[1].startsWith(fy)));
    const rows = all.slice(0, shown);
    const showWind = rows.some(r => D(r[0])[4] && r[3] !== null);
    app.querySelector("#res").innerHTML = rows.length ? `<div class="tbl-wrap"><table>
      <thead><tr><th>Date</th><th>Épreuve</th><th class="r">Perf.</th>${showWind ? `<th class="r">Vent</th>` : ""}<th>Rang</th><th>Compétition</th><th class="hide-s">Lieu</th><th class="hide-s">Club</th></tr></thead>
      <tbody>${rows.map(([d, date, mark, wind, place, round, meet, loc, club, ass]) => `<tr>
        <td class="dt">${fmtDate(date)}</td><td>${esc(dName(d))}${D(d)[0] ? " (salle)" : ""}</td>
        <td class="mk r">${esc(mark)}${pbKey.has(`${d}|${date}|${mark}`) ? `<span class="tag pb">RP</span>` : ""}</td>
        ${showWind ? `<td class="r num">${D(d)[4] ? fmtWind(wind) : ""}${ass ? `<span class="tag w">vent</span>` : ""}</td>` : ""}
        <td class="num">${place ?? ""}${round ? ` ${esc(round)}` : ""}</td><td>${esc(meet)}</td>
        <td class="hide-s">${esc(loc)}</td><td class="hide-s">${esc(club)}</td></tr>`).join("")}</tbody></table></div>
      ${all.length > shown ? `<p><button type="button" class="more">Afficher ${Math.min(100, all.length - shown)} résultats de plus (${all.length - shown} restants)</button></p>` : ""}`
      : `<p class="empty">Aucun résultat pour ce filtre.</p>`;
    const more = app.querySelector("#res .more");
    if (more) more.addEventListener("click", () => { shown += 100; drawRes(false); });
  };
  app.querySelector("#fd").addEventListener("change", () => drawRes());
  app.querySelector("#fy").addEventListener("change", () => drawRes());
  drawRes();
}

async function h2h(idA, idB) {
  const [a, b] = await Promise.all([loadAthlete(idA), loadAthlete(idB)]);
  if (!a || !b) return notFound("Un des deux athlètes n'est pas dans la base.");
  document.title = `${a.n} contre ${b.n} – ${META.title}`;
  const bestBy = ath => {
    const m = new Map();
    ath.r.forEach(r => {
      const k = `${r[0]}|${r[1]}|${r[6]}`, cur = m.get(k);
      if (r[10] !== null && (!cur || sortv(r[0], r[10]) < sortv(r[0], cur[10]))) m.set(k, r);
    });
    return m;
  };
  const ma = bestBy(a), mb = bestBy(b), meets = [];
  ma.forEach((ra, k) => {
    const rb = mb.get(k);
    if (!rb) return;
    const diff = sortv(ra[0], ra[10]) - sortv(ra[0], rb[10]);
    meets.push({ d: ra[0], date: ra[1], meet: ra[6], ra, rb, w: Math.abs(diff) < 1e-9 ? 0 : diff < 0 ? 1 : 2 });
  });
  meets.sort((x, y) => (x.date < y.date ? 1 : -1));
  const wa = meets.filter(m => m.w === 1).length, wb = meets.filter(m => m.w === 2).length, ties = meets.length - wa - wb;
  const byDisc = {};
  meets.forEach(m => { const t = (byDisc[m.d] = byDisc[m.d] || [0, 0]); if (m.w) t[m.w - 1]++; });
  const pbA = new Map(a.pb.map(p => [p[0], p])), common = b.pb.filter(p => pbA.has(p[0])).map(p => p[0]);
  const link = (id, n) => `<a href="${athleteHref(id, n)}">${esc(n)}</a>`;

  app.innerHTML = `
    <section class="versus">
      <div><h1>${link(idA, a.n)}</h1><div class="facts"><span class="num">${a.by}</span><span>${esc(a.c)}</span></div></div>
      <div class="vs">contre</div>
      <div class="b"><h1>${link(idB, b.n)}</h1><div class="facts" style="justify-content:flex-end"><span class="num">${b.by}</span><span>${esc(b.c)}</span></div></div>
    </section>
    ${meets.length ? `
      <div class="score"><b class="${wa > wb ? "win" : ""}">${wa}</b><span class="mid">victoires en ${meets.length} confrontation${meets.length > 1 ? "s" : ""}${ties ? `, ${ties} égalité${ties > 1 ? "s" : ""}` : ""}</span><b class="b ${wb > wa ? "win" : ""}">${wb}</b></div>
      <h2>Par épreuve</h2>
      <div class="tbl-wrap"><table><thead><tr><th>Épreuve</th><th class="r">${esc(a.n)}</th><th class="r">${esc(b.n)}</th></tr></thead>
      <tbody>${Object.entries(byDisc).map(([d, [x, y]]) => `<tr><td>${esc(dName(+d))}${D(+d)[0] ? " (salle)" : ""}</td><td class="r num ${x > y ? "win" : ""}">${x}</td><td class="r num ${y > x ? "win" : ""}">${y}</td></tr>`).join("")}</tbody></table></div>
      <h2>Toutes les confrontations</h2>
      <div class="tbl-wrap"><table><thead><tr><th>Date</th><th>Épreuve</th><th class="r">${esc(a.n)}</th><th class="r">${esc(b.n)}</th><th class="hide-s">Compétition</th></tr></thead>
      <tbody>${meets.map(m => `<tr><td class="dt">${fmtDate(m.date)}</td><td>${esc(dName(m.d))}${D(m.d)[0] ? " (salle)" : ""}</td>
        <td class="mk r ${m.w === 1 ? "win" : ""}">${esc(m.ra[2])}</td><td class="mk r ${m.w === 2 ? "win" : ""}">${esc(m.rb[2])}</td>
        <td class="hide-s">${esc(m.meet)}</td></tr>`).join("")}</tbody></table></div>`
    : `<p class="empty">Pas de confrontation directe dans les résultats${META.full_results ? "" : " disponibles (seules les meilleures performances par saison sont chargées)"}.</p>`}
    ${common.length ? `<h2>Records personnels</h2>
      <div class="tbl-wrap"><table><thead><tr><th>Épreuve</th><th class="r">${esc(a.n)}</th><th class="r">${esc(b.n)}</th></tr></thead>
      <tbody>${common.map(d => {
        const pa = pbA.get(d), pb = b.pb.find(p => p[0] === d);
        const va = a.sb.filter(s => s[0] === d).map(s => s[3]), vb = b.sb.filter(s => s[0] === d).map(s => s[3]);
        const bestA = Math.min(...va.map(v => sortv(d, v))), bestB = Math.min(...vb.map(v => sortv(d, v)));
        return `<tr><td>${esc(dName(d))}${D(d)[0] ? " (salle)" : ""}</td><td class="mk r ${bestA < bestB ? "win" : ""}">${esc(pa[1])}</td><td class="mk r ${bestB < bestA ? "win" : ""}">${esc(pb[1])}</td></tr>`;
      }).join("")}</tbody></table></div>` : ""}
    <p class="note">Une confrontation est une même épreuve le même jour dans la même compétition, toutes séries confondues : la meilleure marque de chacun ce jour-là est comparée.</p>`;
}

async function lists(year, io, sex) {
  year = year || String(META.years[0]); io = io === "in" ? "in" : "out"; sex = sex === "W" ? "W" : "M";
  document.title = `Meilleures performances ${year} – ${META.title}`;
  let data;
  try { data = await getJSON(`lists/${year}.json`); } catch { return notFound("Pas de liste pour cette saison."); }
  const discs = Object.keys(data[sex] || {}).map(Number).filter(d => D(d)[0] === (io === "in" ? 1 : 0)).sort((x, y) => x - y);
  const href = (y, i, s) => `#/l/${y}/${i}/${s}`;
  app.innerHTML = `
    <section class="profile-head">
      <h1>Meilleures performances ${esc(year)}</h1>
      <p class="lede">Les ${META.list_n} meilleurs de chaque épreuve, une performance par athlète, chronométrage électronique et vent régulier.</p>
    </section>
    <div class="controls" style="margin-top:20px">
      <label>Saison <select id="ly">${META.years.map(y => `<option${String(y) === year ? " selected" : ""}>${y}</option>`).join("")}</select></label>
      <span class="seg"><a href="${href(year, "out", sex)}" aria-current="${io === "out"}">Plein air</a><a href="${href(year, "in", sex)}" aria-current="${io === "in"}">En salle</a></span>
      <span class="seg"><a href="${href(year, io, "M")}" aria-current="${sex === "M"}">Hommes</a><a href="${href(year, io, "W")}" aria-current="${sex === "W"}">Femmes</a></span>
    </div>
    ${discs.length ? discs.map(d => {
      const rows = data[sex][d], wind = D(d)[4];
      return `<section class="list-sec"><h3>${esc(dName(d))}</h3><div class="tbl-wrap"><table>
        <thead><tr><th class="r">Rg</th><th class="r">Perf.</th>${wind ? `<th class="r">Vent</th>` : ""}<th>Athlète</th><th class="r">Année</th><th class="hide-s">Club</th><th class="hide-s">Date</th></tr></thead>
        <tbody>${rows.map(([rk, mark, w, id, n, by, club, date]) => `<tr><td class="r num">${rk}</td><td class="mk r">${esc(mark)}</td>${wind ? `<td class="r num">${fmtWind(w)}</td>` : ""}
          <td><a href="${athleteHref(id, n)}">${esc(n)}</a></td><td class="r num">${by}</td><td class="hide-s">${esc(club)}</td><td class="dt hide-s">${fmtDate(date)}</td></tr>`).join("")}</tbody>
      </table></div></section>`;
    }).join("") : `<p class="empty">Aucune performance pour ce choix.</p>`}
    <p class="note">Liste non officielle. Les athlètes de moins de ${META.min_age} ans en ${new Date().getFullYear()} n'y figurent pas.</p>`;
  app.querySelector("#ly").addEventListener("change", e => { location.hash = href(e.target.value, io, sex); });
}

async function cadresPage() {
  document.title = `Cadres nationaux – ${META.title}`;
  let c;
  try { c = await getJSON("cadres.json"); } catch { return notFound("Les cadres ne sont pas disponibles."); }
  const who = (id, n, by) => `<a href="${athleteHref(id, n)}">${esc(n)}</a> <span class="muted num">${by}</span>`;
  const sec = k => {
    const groups = [...new Set(k.members.map(m => m[4]))];
    return `<section class="list-sec"><h2>${esc(k.name)}</h2>
      <p class="note">${esc(k.desc || "")}. Liste officielle de Swiss Athletics du ${fmtDate(k.date)} (<a href="${esc(k.source)}" rel="noopener">PDF</a>) : ${k.listed} athlètes.
      ${k.minors ? `${k.minors} ont moins de ${META.min_age} ans et ne sont pas affichés ici.` : ""}
      ${k.missing ? `${k.missing} sans profil trouvé dans les résultats.` : ""}${k.marker ? ` ★ = ${esc(k.marker)}.` : ""}</p>
      <div class="cols">${groups.map(g => `<div><h3>${esc(g)}</h3><ul class="plain">${k.members.filter(m => m[4] === g).map(([id, n, by, club, grp, wcp]) =>
        `<li>${who(id, n, by)}${wcp ? " ★" : ""}${club ? ` <span class="muted">· ${esc(club)}</span>` : ""}</li>`).join("")}</ul></div>`).join("")}</div></section>`;
  };
  const L = c.limits;
  const inCadre = codes => codes.map(x => `<span class="tag pb">${esc(x === "SS" ? "Swiss Starters" : x === "SSF" ? "SS Future" : x)}</span>`).join("");
  app.innerHTML = `
    <section class="profile-head">
      <h1>Cadres nationaux</h1>
      <p class="lede">Les cadres officiels de Swiss Athletics${L ? `, et les athlètes suisses qui ont réalisé une limite Swiss Starters en ${L.year}` : ""}.</p>
    </section>
    ${c.cadres.map(sec).join("")}
    ${L ? `<section class="list-sec"><h2>${esc(L.name)} ${L.year}</h2>
      <p class="note">Limites à réaliser en ${L.year} pour le cadre ${esc(L.for)} (<a href="${esc(L.source)}" rel="noopener">PDF Swiss Athletics</a>). Ici : athlètes de nationalité suisse, meilleure performance ${L.year} avec vent régulier et chronométrage électronique, salle comprise. Atteindre une limite ne garantit pas la sélection.</p>
      <div class="tbl-wrap"><table><thead><tr><th>Épreuve</th><th>Athlète</th><th class="r">Perf.</th><th class="r">Limite</th><th class="hide-s">Date</th><th class="hide-s">Cadre</th></tr></thead>
      <tbody>${L.achieved.length ? L.achieved.map(([id, n, by, club, sex, i, ev, mark, lim, date, indoor, codes]) =>
        `<tr><td>${esc(ev)}${indoor ? " (salle)" : ""}</td><td>${who(id, n, by)}</td><td class="mk r">${esc(mark)}</td><td class="r num">${esc(lim)}</td><td class="dt hide-s">${fmtDate(date)}</td><td class="hide-s">${inCadre(codes)}</td></tr>`).join("")
        : `<tr><td colspan="6" class="empty">Personne pour l'instant.</td></tr>`}</tbody></table></div>
      <h3>Les limites</h3>
      <div class="tbl-wrap"><table><thead><tr><th>Épreuve</th><th class="r">Hommes</th><th class="r">Femmes</th></tr></thead>
      <tbody>${L.events.map(([ev, m, w, checked]) => `<tr><td>${esc(ev)}${checked ? "" : ' <span class="muted">(pas dans les données)</span>'}</td><td class="r num">${esc(m)}</td><td class="r num">${esc(w)}</td></tr>`).join("")}</tbody></table></div>
    </section>` : ""}`;
}

/* ---------- clubs, cantons and regions ----------
   Data from build_site.py: geo.json (cantons, regions), clubs.json, club/<id>.json, canton/<XX>.json, region/<id>.json and
   rk/<xyz>.json (ranks of each active athlete). "Active" = an adult with a result in the last two seasons. */
const clubHref = name => `#/club/${slug(name)}`;
const optJSON = path => getJSON(path).catch(() => null);
const ordinal = n => (n === 1 ? "1er" : `${n}e`);
const seasonsLabel = g => (g.years[0] === g.years[1] ? `saison ${g.years[0]}` : `plein air ${g.years[0]}, salle ${g.years[1]}`);
const evLabel = d => `${dName(d)}${D(d)[0] ? " (salle)" : ""}`;
const unitOf = d => (lower(d) ? " s" : /kampf|Athletic-Cup/i.test(D(d)[1]) ? " pts" : " m");
/* How far `value` is from `target` in event d: <= 0 means reached. */
const gapTo = (d, value, target) => sortv(d, value) - sortv(d, target);
const fmtGap = (d, gap) => `${lower(d) && gap >= 60 ? fmtVal(gap, d) : gap.toFixed(lower(d) || !/kampf/i.test(D(d)[1]) ? 2 : 0)}${lower(d) && gap >= 60 ? "" : unitOf(d)}`;

/* Is an athlete born in `by` allowed in a competition reserved for `cat` ("U20", "M" = masters) held in `year`?
   null when the birth year is not known. */
function eligible(cat, by, year) {
  if (!cat) return true;
  if (!by) return null;
  const age = year - by;
  return cat === "M" ? age >= 30 : age < +cat.slice(1);
}

function deltaCell(d, now, prev) {
  const a = markValue(now), b = markValue(prev);
  if (a === null || b === null) return "";
  const gain = sortv(d, b) - sortv(d, a);
  if (Math.abs(gain) < 1e-9) return `<span class="muted">=</span>`;
  return `<span class="${gain > 0 ? "win" : "down"}">${gain > 0 ? "▲" : "▼"} ${fmtGap(d, Math.abs(gain))}</span>`;
}

async function clubsPage() {
  document.title = `Clubs – ${META.title}`;
  const [clubs, geo] = await Promise.all([optJSON("clubs.json"), optJSON("geo.json")]);
  if (!clubs || !geo) return notFound("Les pages des clubs ne sont pas encore disponibles.");
  app.innerHTML = `
    <section class="profile-head">
      <h1>Clubs</h1>
      <p class="lede">Chaque club avec ses athlètes, leurs meilleures performances de la saison, leur progression et le rang du club dans chaque épreuve.</p>
    </section>
    <div class="controls" style="margin-top:20px">
      <label>Nom du club <input id="cq" type="search" class="field" autocomplete="off" placeholder="Stade, LC, CA…"></label>
      <label>Canton <select id="cc"><option value="">Tous</option>${Object.entries(geo.cantons).sort((x, y) => x[1][0].localeCompare(y[1][0], "fr")).map(([k, v]) => `<option value="${k}">${esc(v[0])}</option>`).join("")}<option value="?">Non situé</option></select></label>
    </div>
    <div id="cl"></div>
    <p class="note">${fmtInt(clubs.length)} clubs comptent au moins un athlète majeur avec un résultat depuis ${Math.min(...geo.years) - 1}. Le canton est déduit de la localité du club${geo.unplaced[0] ? ` ; ${geo.unplaced[0]} club${geo.unplaced[0] > 1 ? "s n'ont" : " n'a"} pas pu être situé${geo.unplaced[0] > 1 ? "s" : ""}` : ""}.</p>`;
  const draw = () => {
    const q = norm(app.querySelector("#cq").value), c = app.querySelector("#cc").value;
    const rows = clubs.filter(x => (!q || norm(x[1]).includes(q)) && (!c || (c === "?" ? !x[2] : x[2] === c)));
    app.querySelector("#cl").innerHTML = rows.length ? `<div class="tbl-wrap"><table><thead><tr><th>Club</th><th>Canton</th><th class="r">Athlètes</th></tr></thead>
      <tbody>${rows.slice(0, 400).map(([id, n, k, na]) => `<tr><td><a href="#/club/${id}">${esc(n)}</a></td><td>${k ? `<a href="#/canton/${k}">${esc(geo.cantons[k] ? geo.cantons[k][0] : k)}</a>` : `<span class="muted">non situé</span>`}</td><td class="r num">${na}</td></tr>`).join("")}</tbody></table></div>
      ${rows.length > 400 ? `<p class="note">${rows.length - 400} autres clubs : précisez la recherche.</p>` : ""}` : `<p class="empty">Aucun club pour ce filtre.</p>`;
  };
  app.querySelector("#cq").addEventListener("input", draw);
  app.querySelector("#cc").addEventListener("change", draw);
  draw();
}

async function clubPage(cid) {
  const [c, geo] = await Promise.all([optJSON(`club/${cid}.json`), optJSON("geo.json")]);
  if (!c || !geo) return notFound("Ce club n'a pas d'athlète majeur actif dans la base.");
  document.title = `${c.n} – ${META.title}`;
  const canton = c.g && c.g[0], years = geo.years;
  const discs = [...new Set(c.a.flatMap(a => a[4].map(e => e[0])))].sort((x, y) => x - y);
  app.innerHTML = `
    <section class="profile-head">
      <h1>${esc(c.n)}</h1>
      <div class="facts">
        ${c.g && c.g[1] ? `<span>${esc(c.g[1])}</span>` : ""}
        ${canton ? `<span>Canton <a href="#/canton/${canton}"><strong>${esc(geo.cantons[canton] ? geo.cantons[canton][0] : canton)}</strong></a></span>` : ""}
        <span><strong class="num">${c.a.length}</strong> athlète${c.a.length > 1 ? "s" : ""} actif${c.a.length > 1 ? "s" : ""}</span>
      </div>
    </section>
    <div class="controls" style="margin-top:20px">
      <span class="seg" role="group" aria-label="Sexe"><a href="#" data-sex="" aria-current="true">Tous</a><a href="#" data-sex="M">Hommes</a><a href="#" data-sex="W">Femmes</a></span>
      <label>Épreuve <select id="ce"><option value="">Toutes</option>${discs.map(d => `<option value="${d}">${esc(evLabel(d))}</option>`).join("")}</select></label>
    </div>
    <h2>Le club dans chaque épreuve</h2>
    <div id="cev"></div>
    <h2>Athlètes</h2>
    <div id="cat"></div>
    <p class="note">Athlètes majeurs avec un résultat depuis ${Math.min(...years) - 1}, rattachés à leur dernier club connu ; les moins de ${META.min_age} ans ne sont pas affichés. Rangs et meilleures performances : ${seasonsLabel(geo)}, chronométrage électronique et vent régulier. Le rang du club est celui de son meilleur athlète parmi les clubs classés dans l'épreuve.</p>`;
  let sex = "";
  const draw = () => {
    const fd = app.querySelector("#ce").value;
    const ev = c.ev.filter(e => (!sex || e[1] === sex) && (fd === "" || e[0] === +fd));
    app.querySelector("#cev").innerHTML = ev.length ? `<div class="tbl-wrap"><table>
      <thead><tr><th>Épreuve</th><th class="r">Rang du club</th><th class="r">Meilleure perf.</th><th>Athlète</th><th class="r hide-s">Athlètes classés</th></tr></thead>
      <tbody>${ev.map(([d, s, rk, n, mark, pid, name, na]) => `<tr><td>${esc(evLabel(d))} <span class="muted">${s === "W" ? "F" : "H"}</span></td>
        <td class="r num"><strong class="${rk <= 3 ? "win" : ""}">${ordinal(rk)}</strong> <span class="muted">sur ${n}</span></td><td class="mk r">${esc(mark)}</td>
        <td><a href="${athleteHref(pid, name)}">${esc(name)}</a></td><td class="r num hide-s">${na}</td></tr>`).join("")}</tbody></table></div>`
      : `<p class="empty small">Aucun classement pour ce filtre.</p>`;
    const rows = [];
    c.a.filter(a => !sex || a[3] === sex).forEach(([pid, name, by, s, evs]) => {
      const list = evs.filter(e => (fd === "" || e[0] === +fd) && (e[1] || e[2] || fd !== ""));
      (fd === "" ? list.slice(0, 4) : list).forEach((e, i) => rows.push([i ? null : [pid, name, by], e]));
    });
    app.querySelector("#cat").innerHTML = rows.length ? `<div class="tbl-wrap"><table>
      <thead><tr><th>Athlète</th><th>Épreuve</th><th class="r">${years[0] === years[1] ? years[0] : "Saison"}</th><th class="r hide-s">Saison d'avant</th><th>Progression</th><th class="r hide-s">Record</th><th class="r">Rang suisse</th></tr></thead>
      <tbody>${rows.map(([who, [d, sb, prev, pb, nat]]) => `<tr${who ? ' class="first"' : ""}><td>${who ? `<a href="${athleteHref(who[0], who[1])}">${esc(who[1])}</a> <span class="muted num">${who[2]}</span>` : ""}</td>
        <td>${esc(evLabel(d))}</td><td class="mk r">${esc(sb || "")}${sb && sb === pb ? `<span class="tag pb">RP</span>` : ""}</td><td class="r num hide-s">${esc(prev || "")}</td>
        <td class="num">${deltaCell(d, sb, prev)}</td><td class="r num hide-s">${esc(pb || "")}</td><td class="r num">${nat ?? ""}</td></tr>`).join("")}</tbody></table></div>
      ${fd === "" ? `<p class="note">Quatre épreuves au plus par athlète, les mieux classées d'abord : choisissez une épreuve pour voir tout le monde.</p>` : ""}`
      : `<p class="empty small">Aucun athlète pour ce filtre.</p>`;
  };
  app.querySelector(".seg").addEventListener("click", e => {
    if (e.target.dataset.sex === undefined) return;
    e.preventDefault(); sex = e.target.dataset.sex;
    app.querySelectorAll(".seg a").forEach(x => x.setAttribute("aria-current", String(x.dataset.sex === sex)));
    draw();
  });
  app.querySelector("#ce").addEventListener("change", draw);
  draw();
}

async function cantonsPage(mode) {
  document.title = `Cantons et régions – ${META.title}`;
  const geo = await optJSON("geo.json");
  if (!geo) return notFound("La comparaison des cantons n'est pas encore disponible.");
  const byRegion = mode === "regions";
  const COLS = [["Clubs", 2], ["Athlètes actifs", 3], ["Places dans le top 10 suisse", 4], ["Places sur le podium", 5], ["Meilleures performances suisses", 6], ["Podiums aux championnats suisses", 7]];
  const rows = byRegion
    ? geo.regions.map(r => [r.id, r.name, r.cantons.join(", "), ...COLS.map(([, i]) => r.cantons.reduce((n, c) => n + (geo.cantons[c] ? geo.cantons[c][i] : 0), 0))])
    : Object.entries(geo.cantons).map(([k, v]) => [k, v[0], (geo.regions.find(r => r.id === v[1]) || {}).short || "", ...COLS.map(([, i]) => v[i])]);
  let sortCol = 4, dir = -1;
  app.innerHTML = `
    <section class="profile-head">
      <h1>Cantons et régions</h1>
      <p class="lede">Où se trouvent les athlètes et les performances : chaque canton et chaque région d'après le club des athlètes, ${seasonsLabel(geo)}.</p>
    </section>
    <div class="controls" style="margin-top:20px">
      <span class="seg"><a href="#/cantons" aria-current="${!byRegion}">Cantons</a><a href="#/cantons/regions" aria-current="${byRegion}">Régions</a></span>
    </div>
    <div id="gt"></div>
    <p class="note">Un athlète compte pour le canton de son dernier club. Athlètes actifs : majeurs avec un résultat depuis ${Math.min(...geo.years) - 1}. Top 10, podium et meilleure performance suisse : rang dans la liste suisse de la saison, par épreuve et par sexe, dans les épreuves comptant au moins ${geo.min_field} athlètes classés (une place par athlète et par épreuve, salle et plein air séparés). Podiums aux championnats suisses : toutes catégories, saison ${Math.max(...geo.years)}. Les trois régions sont celles des championnats régionaux de Swiss Athletics.${geo.unplaced[1] ? ` ${fmtInt(geo.unplaced[1])} athlètes de ${geo.unplaced[0]} club${geo.unplaced[0] > 1 ? "s" : ""} non situé${geo.unplaced[0] > 1 ? "s" : ""} ne sont comptés nulle part.` : ""} Les athlètes de moins de ${META.min_age} ans ne sont pas comptés.</p>`;
  const draw = () => {
    const sorted = rows.slice().sort((x, y) => (typeof x[sortCol] === "number" ? (x[sortCol] - y[sortCol]) * dir : String(x[sortCol]).localeCompare(String(y[sortCol]), "fr") * dir));
    const max = COLS.map((_, i) => Math.max(1, ...rows.map(r => r[3 + i])));
    const th = (label, i, num) => `<th class="${num ? "r" : ""}" aria-sort="${sortCol === i ? (dir < 0 ? "descending" : "ascending") : "none"}"><button type="button" class="sort" data-col="${i}">${label}${sortCol === i ? (dir < 0 ? " ▼" : " ▲") : ""}</button></th>`;
    app.querySelector("#gt").innerHTML = `<div class="tbl-wrap"><table class="geo">
      <thead><tr>${th(byRegion ? "Région" : "Canton", 1)}${th(byRegion ? "Cantons" : "Région", 2)}${COLS.map(([l], i) => th(l, 3 + i, true)).join("")}</tr></thead>
      <tbody>${sorted.map(r => `<tr><td><a href="#/${byRegion ? "region" : "canton"}/${r[0]}"><strong>${esc(r[1])}</strong></a></td><td class="muted">${esc(r[2])}</td>
        ${COLS.map((_, i) => `<td class="r num bar" style="--w:${(100 * r[3 + i] / max[i]).toFixed(0)}%"><span>${fmtInt(r[3 + i])}</span></td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  };
  app.querySelector("#gt").addEventListener("click", e => {
    const b = e.target.closest(".sort");
    if (!b) return;
    const col = +b.dataset.col;
    if (col === sortCol) dir = -dir; else { sortCol = col; dir = col <= 2 ? 1 : -1; }
    draw();
  });
  draw();
}

async function unitPage(kind, key) {
  const geo = await optJSON("geo.json");
  const region = kind === "region" && geo ? geo.regions.find(r => r.id === key) : null;
  const canton = kind === "canton" && geo ? geo.cantons[key] : null;
  const data = (region || canton) ? await optJSON(`${kind}/${key}.json`) : null;
  if (!data) return notFound("Ce canton ou cette région n'existe pas dans la base.");
  const name = region ? region.name : canton[0];
  document.title = `${name} – ${META.title}`;
  const cantons = region ? region.cantons : [key];
  const sum = i => cantons.reduce((n, c) => n + (geo.cantons[c] ? geo.cantons[c][i] : 0), 0);
  const reg = canton ? geo.regions.find(r => r.id === canton[1]) : null;
  let sex = "M", io = 0;
  app.innerHTML = `
    <section class="profile-head">
      <h1>${esc(name)}</h1>
      <div class="facts">
        ${reg ? `<span>Région <a href="#/region/${reg.id}"><strong>${esc(reg.name)}</strong></a></span>` : ""}
        ${region ? `<span>${region.cantons.map(c => `<a href="#/canton/${c}">${esc(geo.cantons[c] ? geo.cantons[c][0] : c)}</a>`).join(", ")}</span>` : ""}
        <span><a href="#/cantons${region ? "/regions" : ""}">Comparer ${region ? "les régions" : "les cantons"}</a></span>
      </div>
      <div class="counts">
        <span><b class="num">${fmtInt(sum(2))}</b>clubs</span><span><b class="num">${fmtInt(sum(3))}</b>athlètes actifs</span>
        <span><b class="num">${fmtInt(sum(4))}</b>places dans le top 10 suisse</span><span><b class="num">${fmtInt(sum(7))}</b>podiums aux championnats suisses</span>
      </div>
    </section>
    <h2>Les meilleurs ${region ? "de la région" : "du canton"}</h2>
    <div class="controls">
      <span class="seg" data-g="io"><a href="#" data-v="0" aria-current="true">Plein air</a><a href="#" data-v="1">En salle</a></span>
      <span class="seg" data-g="sex"><a href="#" data-v="M" aria-current="true">Hommes</a><a href="#" data-v="W">Femmes</a></span>
    </div>
    <div id="ub"></div>
    <h2>Clubs</h2>
    ${data.clubs.length ? `<div class="tbl-wrap"><table><thead><tr><th>Club</th><th class="r">Athlètes actifs</th></tr></thead>
      <tbody>${data.clubs.map(([id, n, na]) => `<tr><td><a href="#/club/${id}">${esc(n)}</a></td><td class="r num">${na}</td></tr>`).join("")}</tbody></table></div>` : `<p class="empty small">Aucun club situé ici.</p>`}
    <p class="note">${seasonsLabel(geo)[0].toUpperCase() + seasonsLabel(geo).slice(1)} : les cinq meilleurs par épreuve parmi les athlètes majeurs des clubs ${region ? "de la région" : "du canton"}, avec leur rang dans la liste suisse. Épreuves comptant au moins ${geo.min_field} athlètes classés en Suisse. Liste non officielle, d'après les résultats publics de Swiss Athletics.</p>`;
  const draw = () => {
    const list = (data.best[sex] || []).filter(([d]) => D(d)[0] === io);
    app.querySelector("#ub").innerHTML = list.length ? `<div class="cols">${list.map(([d, rows]) => `<section class="list-sec"><h3>${esc(dName(d))}</h3><div class="tbl-wrap"><table>
      <thead><tr><th class="r">Rg</th><th class="r">Perf.</th><th>Athlète</th><th class="r">CH</th></tr></thead>
      <tbody>${rows.map(([rk, mark, pid, n, by, club, nat]) => `<tr><td class="r num">${rk}</td><td class="mk r">${esc(mark)}</td>
        <td><a href="${athleteHref(pid, n)}">${esc(n)}</a> <span class="muted num">${by}</span><br><a class="sub" href="${clubHref(club)}">${esc(club)}</a></td><td class="r num">${nat}</td></tr>`).join("")}</tbody>
      </table></div></section>`).join("")}</div>` : `<p class="empty small">Aucune performance pour ce choix.</p>`;
  };
  app.querySelectorAll(".seg[data-g]").forEach(seg => seg.addEventListener("click", e => {
    if (e.target.dataset.v === undefined) return;
    e.preventDefault();
    if (seg.dataset.g === "io") io = +e.target.dataset.v; else sex = e.target.dataset.v;
    seg.querySelectorAll("a").forEach(x => x.setAttribute("aria-current", String(x === e.target)));
    draw();
  }));
  draw();
}

/* ---------- profile: strongest event, limits, rivals, meetings that worked ---------- */
function strongEvent(a, rk, geo) {
  const row = rk && rk.r && (rk.r.find(r => r[3] >= 10) || rk.r[0]);
  if (!row) return "";
  const [d, season, nat, natN, can, canN, club, clubN] = row;
  const legal = a.r.filter(r => r[0] === d && !r[9] && r[10] !== null);
  const inSeason = legal.filter(r => r[1].startsWith(String(season))).map(r => r[10]).sort((x, y) => sortv(d, x) - sortv(d, y));
  const pb = a.pb.find(p => p[0] === d), sbs = a.sb.filter(s => s[0] === d).sort((x, y) => x[1] - y[1]);
  const cur = sbs.find(s => s[1] === season), prev = sbs.find(s => s[1] === season - 1);
  const bestSeason = sbs.reduce((b, s) => (!b || sortv(d, s[3]) < sortv(d, b[3]) ? s : b), null);
  const top3 = inSeason.slice(0, 3), avg = top3.length ? top3.reduce((n, v) => n + v, 0) / top3.length : null;
  const canton = a.g && a.g[0], cName = canton && geo && geo.cantons[canton] ? geo.cantons[canton][0] : canton;
  const stat = (big, small) => `<span><b class="num">${big}</b>${small}</span>`;
  return `<h2>Point fort : ${esc(evLabel(d))}</h2>
    <div class="strong">
      <div class="counts">
        ${stat(`${ordinal(nat)}`, `en Suisse en ${season}<br><span class="muted">sur ${fmtInt(natN)} classé${natN > 1 ? "s" : ""}</span>`)}
        ${can ? stat(ordinal(can), `${cName ? `<a href="#/canton/${canton}">${esc(cName)}</a>` : "dans le canton"}<br><span class="muted">sur ${canN}</span>`) : ""}
        ${club ? stat(ordinal(club), `<a href="${clubHref(a.c)}">dans le club</a><br><span class="muted">sur ${clubN}</span>`) : ""}
        ${cur ? stat(esc(cur[2]), `meilleure perf. ${season}${pb && pb[1] === cur[2] ? `<br><span class="win">record personnel</span>` : ""}`) : ""}
      </div>
      <ul class="plain facts-list">
        ${pb ? `<li>Record personnel <strong class="num">${esc(pb[1])}</strong> <span class="muted">le ${fmtDate(pb[3])}, ${esc(pb[4])}</span></li>` : ""}
        ${cur && prev ? `<li>Par rapport à ${season - 1} (<span class="num">${esc(prev[2])}</span>) : ${deltaCell(d, cur[2], prev[2])}</li>` : ""}
        ${inSeason.length ? `<li><strong class="num">${inSeason.length}</strong> performance${inSeason.length > 1 ? "s" : ""} en ${season}${top3.length === 3 ? `, moyenne des trois meilleures <strong class="num">${fmtVal(avg, d)}</strong>` : ""}</li>` : ""}
        ${bestSeason && sbs.length > 1 ? `<li>${sbs.length} saisons dans l'épreuve, la meilleure en <strong class="num">${bestSeason[1]}</strong> (<span class="num">${esc(bestSeason[2])}</span>)</li>` : ""}
      </ul>
    </div>`;
}

function rivalsBlock(id, rk) {
  if (!rk || !rk.v || !rk.v[1].length) return "";
  const [d, list] = rk.v, me = rk.r.find(r => r[0] === d);
  return `<h2>Adversaires directs</h2>
    <p class="note">Les athlètes classés juste avant et juste après en ${esc(evLabel(d))}${me ? ` dans la liste suisse ${me[1]}` : ""}.</p>
    <div class="tbl-wrap"><table><thead><tr><th class="r">Rg</th><th>Athlète</th><th class="r">Perf.</th><th></th></tr></thead>
    <tbody>${list.map(([pid, n, mark, r]) => `<tr><td class="r num">${r}</td><td><a href="${athleteHref(pid, n)}">${esc(n)}</a></td><td class="mk r">${esc(mark)}</td>
      <td><a href="#/h2h/${id}/${pid}">Confrontations</a></td></tr>`).join("")}</tbody></table></div>`;
}

/* Standards and limits against the athlete's own marks: Swiss championships, Swiss Starters, international standards. */
function limitsBlock(a, plan, cad) {
  const sets = [];
  ((plan && plan.championships) || []).forEach(c => {
    if (!c.standards || eligible(c.age || "", a.by, +c.start.slice(0, 4)) === false) return;
    sets.push({ name: c.name + (c.standards.kind === "limit" ? "" : ` ${c.start.slice(0, 4)}`), indoor: c.indoor, from: c.standards.window[0], to: c.standards.window[1],
      rows: c.standards.events.map(e => [e.labels[a.s] || [], e[a.s]]) });
  });
  const L = cad && cad.limits;
  if (L && a.nat === "SUI") sets.push({ name: `${L.name.replace(/^Limites /, "")} (${L.for})`, indoor: null, from: `${L.year}-01-01`, to: `${L.year}-12-31`, rows: L.events.map(e => [e[a.s === "W" ? 5 : 4] || [], e[a.s === "W" ? 2 : 1]]) });
  const out = [];
  sets.forEach(set => set.rows.forEach(([labels, text]) => {
    const target = markValue(text);
    if (target === null) return;
    let best = null;  // nearest of the athlete's personal bests in this event (indoor marks for an indoor championship)
    a.pb.forEach(p => {
      if (!labels.includes(D(p[0])[1]) || (set.indoor !== null && D(p[0])[0] !== set.indoor)) return;
      const v = markValue(p[1]);
      if (v !== null && (!best || sortv(p[0], v) < sortv(best.d, best.v))) best = { d: p[0], v, mark: p[1], date: p[3] };
    });
    if (!best) return;
    const win = a.r.filter(r => labels.includes(D(r[0])[1]) && (set.indoor === null || D(r[0])[0] === set.indoor) && !r[9] && r[10] !== null && r[1] >= set.from && r[1] <= set.to)
      .reduce((b, r) => (!b || sortv(r[0], r[10]) < sortv(r[0], b[10]) ? r : b), null);
    const gap = gapTo(best.d, win ? win[10] : best.v, target);
    out.push({ set, d: best.d, text, best, win, gap, rel: gap / Math.abs(target), done: !!win && gap <= 0 });
  }));
  if (!out.length) return "";
  // per event: what is reached, then the next two steps
  const shown = [];
  [...new Set(out.map(o => D(o.d)[1]))].forEach(label => {
    const rows = out.filter(o => D(o.d)[1] === label).sort((x, y) => x.rel - y.rel);
    if (rows[0].rel > 0.12) return;  // nothing within reach in this event
    shown.push(...rows.filter(o => o.gap <= 0), ...rows.filter(o => o.gap > 0).slice(0, 2));
  });
  if (!shown.length) return "";
  return `<h2>Où j'en suis des limites</h2>
    <div class="tbl-wrap"><table><thead><tr><th>Épreuve</th><th>Limite</th><th class="r">À réaliser</th><th class="r">Ma référence</th><th>Écart</th></tr></thead>
    <tbody>${shown.map(o => `<tr><td>${esc(evLabel(o.d))}</td><td>${esc(o.set.name)}</td><td class="mk r">${esc(o.text)}</td>
      <td class="r num">${o.win ? `${esc(o.win[2])} <span class="muted">${fmtDate(o.win[1])}</span>` : `${esc(o.best.mark)} <span class="muted">record, ${o.best.date.slice(0, 4)}</span>`}</td>
      <td>${o.done ? `<span class="win">réalisée</span>` : o.gap <= 0 ? `<span class="muted">record au niveau, à refaire dans la période</span>` : `à <strong class="num">${fmtGap(o.d, o.gap)}</strong>`}</td></tr>`).join("")}</tbody></table></div>
    <p class="note">Ma référence : la meilleure performance régulière dans la période de qualification, sinon le record personnel. Limites des championnats suisses : celles de la dernière édition publiée, à titre indicatif ; minima internationaux repris des documents des fédérations. Atteindre une limite ne garantit ni l'inscription ni la sélection. Les Talent Cards de Swiss Olympic ne figurent pas ici : elles sont attribuées sur une évaluation (PISTE), pas sur une limite chiffrée. <a href="#/plan">Planifier ma saison</a></p>`;
}

/* Meetings where the athlete set personal bests: same meeting from year to year = same name without numbers, same venue. */
function meetingsBlock(a) {
  const best = new Map(), series = new Map(), sb = new Map();
  a.r.slice().reverse().forEach(r => {
    const [d, date, , , , , meet, loc, , ass, v] = r;
    if (v === null || ass || !meet) return;
    const key = `${norm(meet.replace(/\d+/g, " "))}|${loc}`;
    const s = series.get(key) || { name: meet, loc, n: 0, pb: 0, sb: 0, last: "" };
    s.n++; if (date >= s.last) { s.last = date; s.name = meet; }
    series.set(key, s);
    if (best.has(d) && sortv(d, v) < sortv(d, best.get(d))) s.pb++;
    if (!best.has(d) || sortv(d, v) < sortv(d, best.get(d))) best.set(d, v);
    const k = `${d}|${date.slice(0, 4)}`, cur = sb.get(k);
    if (!cur || sortv(d, v) < sortv(d, cur.v)) sb.set(k, { v, key });
  });
  sb.forEach(x => { series.get(x.key).sb++; });
  const top = [...series.values()].filter(s => s.pb >= 1 && s.n >= 2).sort((x, y) => y.pb - x.pb || y.sb - x.sb || x.n - y.n).slice(0, 5);
  if (!top.length) return "";
  return `<h2>Les compétitions qui me réussissent</h2>
    <div class="tbl-wrap"><table><thead><tr><th>Compétition</th><th class="r">Records personnels</th><th class="r">Meilleures perf. de saison</th><th class="r hide-s">Départs</th><th class="hide-s">Dernière fois</th></tr></thead>
    <tbody>${top.map(s => `<tr><td>${esc(s.name.replace(/\s*\((WRC|WCR)\)/, ""))}${s.loc ? ` <span class="muted">· ${esc(s.loc)}</span>` : ""}</td><td class="r num"><strong>${s.pb}</strong></td>
      <td class="r num">${s.sb}</td><td class="r num hide-s">${s.n}</td><td class="dt hide-s">${fmtDate(s.last)}</td></tr>`).join("")}</tbody></table></div>
    <p class="note">Records battus sur place, toutes épreuves confondues (la première performance dans une épreuve ne compte pas comme record) ; une même compétition d'une année à l'autre est reconnue à son nom et à son lieu.</p>`;
}

/* ---------- season planner (#/plan) ----------
   Data: planner.json (championships, standards, departure cities) and plan/<d>.json (marks run at each meeting
   over the last seasons, from build_site.py). Everything is computed in the browser; choices stay in localStorage. */
const DAY = 86400000;
const WD = ["di", "lu", "ma", "me", "je", "ve", "sa"];
const pDate = iso => { const [y, m, d] = iso.split("-").map(Number); return new Date(Date.UTC(y, m - 1, d)); };
const toISO = t => new Date(t).toISOString().slice(0, 10);
const addDays = (iso, n) => toISO(pDate(iso).getTime() + n * DAY);
const fmtDay = iso => `${WD[pDate(iso).getUTCDay()]} ${fmtDate(iso)}`;
const fmtSpan = (a, b) => (b && b !== a ? `${fmtDate(a).slice(0, a.slice(0, 7) === b.slice(0, 7) ? 3 : 6)} au ${fmtDate(b)}` : fmtDate(a));
const fmtKm = km => `${(km < 100 ? Math.round(km / 5) * 5 : Math.round(km / 10) * 10).toLocaleString("fr-CH")} km`;
const fmtHours = h => { const q = Math.round(h * 4) / 4, m = Math.round((q % 1) * 60); return `${Math.floor(q)} h${m ? " " + m : ""}`; };

function saved(key, val) {
  try {
    if (val === undefined) return JSON.parse(localStorage.getItem(key));
    localStorage.setItem(key, JSON.stringify(val));
  } catch { /* private window: the planner works without remembering */ }
  return null;
}

/* The same weekday in another year: 16.05.2026 (a Saturday) becomes Saturday 15.05.2027. */
function projectDate(iso, year) {
  const d = pDate(iso), same = Date.UTC(year, d.getUTCMonth(), d.getUTCDate());
  let t = d.getTime() + (year - d.getUTCFullYear()) * 364 * DAY;
  while (t - same > 3 * DAY) t -= 7 * DAY;
  while (same - t > 3 * DAY) t += 7 * DAY;
  return toISO(t);
}

/* Door-to-door estimates from the straight-line distance: no timetable and no fares, orders of magnitude to sort and
   filter with. Races in Switzerland: train. Races abroad: train and, from 350 km, plane; the faster one counts. */
function travel(home, v) {
  if (!home || v.length < 3) return null;
  const rad = x => x * Math.PI / 180, [, la1, lo1] = home, [, la2, lo2] = v;
  const a = Math.sin(rad(la2 - la1) / 2) ** 2 + Math.cos(rad(la1)) * Math.cos(rad(la2)) * Math.sin(rad(lo2 - lo1) / 2) ** 2;
  const air = 6371 * 2 * Math.asin(Math.sqrt(a)), km = air * 1.25, r5 = x => Math.max(5, Math.round(x / 5) * 5);
  if (km < 8) return { km, h: 0.5, chf: 0, modes: [] };
  const abroad = v[3] && v[3] !== "SUI";
  const modes = [{ k: "train", name: "Train", h: 1 + km / 95, chf: r5(km * 0.4) }];  // 1 h to and from stations, about 20 ct/km
  // plane: about 4 h around the flight (airport, check-in, transfer), 750 km/h in the air, 120 CHF + 12 ct/km return
  if (abroad && air >= 350) modes.push({ k: "plane", name: "Avion", h: 4 + air / 750, chf: r5(120 + air * 0.12) });
  const best = modes.reduce((x, y) => (y.h < x.h ? y : x));
  return { km, h: best.h, chf: best.chf, mode: best.k, modes };
}

function weekendFit(iso, t) {
  if (!t) return { k: "unk", txt: "trajet à estimer" };
  const wd = pDate(iso).getUTCDay(), we = wd === 0 || wd === 6;
  if (t.h <= 3) return { k: "ok", txt: we ? "aller-retour dans la journée" : "aller-retour dans la journée, en semaine" };
  if (wd === 6 && t.h <= 9) return { k: "ok", txt: t.h <= 5 ? "faisable en un week-end" : "week-end, départ le vendredi soir" };
  if (wd === 0 && t.h <= 5) return { k: "ok", txt: "week-end, retour le dimanche soir" };
  if (wd === 5 && t.h <= 6) return { k: "mid", txt: "vendredi : une demi-journée de congé" };
  return { k: "no", txt: wd === 6 ? "trop loin pour un week-end" : wd === 0 ? "retour le lundi" : "congé nécessaire" };
}

const PHASES = [
  { k: "build", name: "Montée en puissance", from: -49, to: -22, hint: "7 à 3 semaines avant : une ou deux courses pour retrouver le rythme de compétition." },
  { k: "sharp", name: "Affûtage", from: -21, to: -6, hint: "3 semaines à 6 jours avant : la dernière course rapide avant l'objectif." },
  { k: "after", name: "Après l'objectif", from: 4, to: 24, end: true, hint: "Dans les 3 semaines qui suivent : une ou deux courses pour profiter de la forme." },
];

const KIND = ["", "championnat suisse", "championnat régional", "championnat cantonal"];

async function planPage(athId) {
  document.title = `Planifier ma saison – ${META.title}`;
  if (!META.plan || !META.plan.disc.length) return notFound("Le planificateur n'est pas encore disponible.");
  let CFG;
  try { CFG = await getJSON("planner.json"); } catch { return notFound("Le planificateur n'est pas encore disponible."); }
  const [GEO, townList] = await Promise.all([optJSON("geo.json"), optJSON("towns.json")]);
  // departure points: every Swiss place of the build, else the short list of planner.json
  const TOWNS = new Map();
  [...(townList || []), ...CFG.homes].forEach(t => TOWNS.set(norm(t[0]), t));
  const regionOf = c => { const r = GEO && GEO.regions.find(x => x.cantons.includes(c)); return r ? r.id : null; };
  const events = new Map();  // Alabus label -> { out: d, in: d }
  META.plan.disc.forEach(d => { const e = events.get(D(d)[1]) || {}; e[D(d)[0] ? "in" : "out"] = d; events.set(D(d)[1], e); });
  const anyD = label => { const e = events.get(label) || {}; return e.out ?? e.in; };
  const today = toISO(Date.now());
  const champs = CFG.championships.filter(c => (c.end || c.start) >= today);
  const st = Object.assign({ ath: null, athName: "", sex: "M", by: "", ev: "", level: "", target: "", home: CFG.homes[0][0], maxh: 0, weekend: false,
    objs: champs.filter(c => c.default).map(c => c.id), sobj: [], custom: [], picks: [], auto: true }, saved("plan.v2") || {});
  if (!events.has(st.ev)) st.ev = events.has("1500 m") ? "1500 m" : events.keys().next().value;
  st.custom = st.custom.filter(c => c.date >= today);
  st.sobj = st.sobj.filter(c => c.start >= today);

  let ath = null;
  const pbOf = label => {  // best personal best of the loaded athlete in this event, indoor or outdoor
    if (!ath) return "";
    const rows = ath.pb.filter(p => D(p[0])[1] === label).map(p => [markValue(p[1]), p[1], p[0]]).filter(x => x[0] !== null);
    rows.sort((x, y) => sortv(x[2], x[0]) - sortv(y[2], y[0]));
    return rows.length ? rows[0][1] : "";
  };
  if (athId && athId !== st.ath) {
    ath = await loadAthlete(athId);
    if (ath) {
      const first = ath.pb.map(p => D(p[0])[1]).find(l => events.has(l));
      Object.assign(st, { ath: athId, athName: ath.n, sex: ath.s === "W" ? "W" : "M", by: ath.by, ev: first || st.ev, target: "", sobj: [], picks: [], auto: true });
      st.level = pbOf(st.ev);
      // leave from the town of the athlete's club, or from the capital of its canton
      const g = ath.g, cap = g && GEO && GEO.capitals[g[0]];
      if (g && g[1] && g[2] !== null) { TOWNS.set(norm(g[1]), [g[1], g[2], g[3], g[0]]); st.home = g[1]; }
      else if (cap) { TOWNS.set(norm(cap[0]), [cap[0], cap[1], cap[2], g[0]]); st.home = cap[0]; }
    }
  } else if (st.ath) ath = await loadAthlete(st.ath);
  const homeOf = () => TOWNS.get(norm(st.home)) || null;

  const evOpts = [...events.keys()].map(l => `<option value="${esc(l)}"${l === st.ev ? " selected" : ""}>${esc(dName(anyD(l)))}</option>`).join("");
  app.innerHTML = `
    <section class="profile-head">
      <h1>Planifier ma saison</h1>
      <p class="lede">Choisissez vos objectifs : le planificateur propose les courses à placer avant et après, là où l'on court à votre niveau, et accessibles depuis chez vous.</p>
    </section>
    <form id="pf" class="plan-form" onsubmit="return false">
      <div id="pwho"></div>
      <div class="controls">
        <label>Épreuve <select name="ev">${evOpts}</select></label>
        <span class="seg" role="group" aria-label="Sexe"><a href="#" data-sex="M">Hommes</a><a href="#" data-sex="W">Femmes</a></span>
        <label>Mon niveau actuel <input name="level" type="text" inputmode="decimal" size="8" placeholder="3:45.00" value="${esc(st.level)}"></label>
        <label>Mon objectif <input name="target" type="text" inputmode="decimal" size="8" placeholder="3:40.00" value="${esc(st.target)}"></label>
        <label>Année de naissance <input name="by" type="text" inputmode="numeric" size="5" maxlength="4" placeholder="2000" value="${esc(st.by)}"></label>
      </div>
      <div class="controls">
        <label>Départ de <input name="home" type="text" list="ptowns" size="18" autocomplete="off" value="${esc(st.home)}"></label>
        <datalist id="ptowns">${[...TOWNS.values()].map(t => `<option value="${esc(t[0])}">`).join("")}</datalist>
        <label>Trajet aller au plus <select name="maxh">${[[0, "Sans limite"], [2, "2 h"], [3, "3 h"], [4, "4 h"], [6, "6 h"], [9, "9 h"]].map(([v, t]) => `<option value="${v}"${+st.maxh === v ? " selected" : ""}>${t}</option>`).join("")}</select></label>
        <label class="check"><input name="weekend" type="checkbox"${st.weekend ? " checked" : ""}> Faisable en un week-end, sans congé</label>
      </div>
      <p class="note" id="phome"></p>
      <fieldset class="objs"><legend>Mes objectifs</legend><div id="pobj"></div>
        <div class="controls add-obj">
          <label>Autre objectif <input name="cname" type="text" size="18" placeholder="Nom de la compétition"></label>
          <label>Date <input name="cdate" type="date" min="${today}"></label>
          <label class="check"><input name="cin" type="checkbox"> En salle</label>
          <button type="button" class="more" id="cadd">Ajouter</button>
        </div>
      </fieldset>
    </form>
    <div id="pout" aria-live="polite"></div>`;
  const form = app.querySelector("#pf"), out = app.querySelector("#pout");

  const who = () => {
    const box = app.querySelector("#pwho");
    if (st.ath && st.athName) {
      box.innerHTML = `<p class="plan-who">Plan pour <a href="${athleteHref(st.ath, st.athName)}"><strong>${esc(st.athName)}</strong></a> <button type="button" class="link" id="pclear">changer</button></p>`;
      box.querySelector("#pclear").addEventListener("click", () => { st.ath = null; st.athName = ""; ath = null; saved("plan.v2", st); who(); });
    } else {
      box.innerHTML = "";
      box.append(searchBox({ label: "Partir de mon profil (facultatif)", placeholder: "Nom ou prénom", small: true, link: e => `#/plan/${e[0]}` }));
    }
  };
  const byYear = () => (/^(19|20)\d\d$/.test(String(st.by)) ? +st.by : null);
  const refMark = () => markValue(st.level) ?? markValue(st.target);
  const objectives = () => [...champs.filter(c => st.objs.includes(c.id)), ...st.sobj,
    ...st.custom.map((c, i) => ({ id: `c${i}`, name: c.name, start: c.date, end: c.date, indoor: c.indoor ? 1 : 0, custom: true }))]
    .sort((a, b) => (a.start < b.start ? -1 : 1));

  /* How realistic a championship of planner.json is for this athlete: its standard against the level entered. */
  function reach(c, ev) {
    const S = c.standards, d = c.indoor && ev.in !== undefined ? ev.in : (ev.out ?? ev.in), lv = refMark();
    const e = S && S.events.find(x => (x.labels[st.sex] || []).includes(st.ev));
    if (!e || lv === null || d === undefined) return { e, rel: null };
    const target = markValue(e[st.sex]), gap = gapTo(d, lv, target);
    return { e, d, gap, rel: gap / Math.abs(target), word: S.kind === "limit" ? "limite" : "minima" };
  }

  /* Objectives read from the results themselves: championships of the athlete's canton and region, Swiss championships
     by age group, and the strongest meetings where the athlete's level fits in the field. */
  function suggestions(docs, ev, home) {
    const list = [], lv = refMark(), by = byYear();
    ["out", "in"].forEach(k => {
      const doc = docs[k], d = ev[k];
      if (!doc || d === undefined) return;
      const meetings = [];
      (doc[st.sex] || []).forEach(([name, vi, kind, editions, cat]) => {
        const last = editions[editions.length - 1];
        if (+last[0].slice(0, 4) < META.plan.to - 1 || last[1].length < 3) return;
        let date = last[0] >= today ? last[0] : projectDate(last[0], +today.slice(0, 4));
        if (date < today) date = projectDate(last[0], +today.slice(0, 4) + 1);
        if (eligible(cat, by, +date.slice(0, 4)) !== true) return;  // too old, too young, or age unknown for a reserved event
        const v = doc.v[vi], vals = last[1], worst = vals[vals.length - 1], best = vals[0];
        const inField = lv === null || sortv(d, lv) <= sortv(d, worst) + Math.abs(worst) * 0.02;
        const o = { id: `s|${k}|${name}|${v[0]}`, name, start: date, end: date, place: v[0], indoor: k === "in" ? 1 : 0, real: date === last[0],
          kind, cat, n: vals.length, best, worst, med: vals[Math.floor((vals.length - 1) / 2)], d, series: true };
        if (kind === 1) {
          if (champs.some(c => c.scope === "nat" && !!c.indoor === (k === "in") && Math.abs(pDate(c.start) - pDate(date)) < 21 * DAY)) return;  // already listed with its date
          if (inField) list.push(o);
        } else if (kind === 3) {
          if (home && v[4] && v[4] === home[3] && inField) list.push(o);
        } else if (kind === 2) {
          if (home && v[4] && regionOf(v[4]) === regionOf(home[3]) && inField) list.push(o);
        } else if (lv !== null && vals.length >= 6 && sortv(d, lv) <= sortv(d, worst) && sortv(d, lv) >= sortv(d, best) && !cat) {
          meetings.push(o);
        }
      });
      // "high-level meetings within reach": the strongest fields the athlete would still be part of
      meetings.sort((x, y) => sortv(d, x.med) - sortv(d, y.med));
      list.push(...meetings.slice(0, 2).map(o => ({ ...o, top: true })));
    });
    return list.sort((a, b) => (a.start < b.start ? -1 : 1));
  }

  function drawObjs(docs, ev, home) {
    const by = byYear(), near = [], far = [];
    let hiddenByAge = 0;
    champs.forEach(c => {
      const ok = eligible(c.age || "", by, +c.start.slice(0, 4));
      if (ok === false) return;
      if (ok === null) { hiddenByAge++; return; }
      const r = reach(c, ev);
      (c.scope === "intl" && !(r.rel !== null && r.rel <= 0.03) && !st.objs.includes(c.id) ? far : near).push([c, r]);
    });
    const sugg = suggestions(docs, ev, home);
    if (st.auto) {  // first visit, or a new athlete: start from objectives within reach
      st.auto = false;
      if (refMark() !== null) {
        st.objs = near.filter(([c, r]) => c.scope === "nat" && !c.age && (r.rel === null || r.rel <= 0.03)).map(([c]) => c.id);
        if (!st.objs.length) st.sobj = [sugg.find(o => !o.indoor), sugg.find(o => o.indoor)].filter(Boolean);
      }
    }
    const tag = (c, r) => (r.rel === null ? "" : r.gap <= 0 ? ` <span class="tag pb">${r.word} à votre niveau</span>` : ` <span class="tag w">${r.word} ${esc(r.e[st.sex])}, à ${fmtGap(r.d, r.gap)}</span>`);
    const row = ([c, r]) => `<li><label class="check"><input type="checkbox" data-obj="${esc(c.id)}"${st.objs.includes(c.id) ? " checked" : ""}>
      <span><strong>${esc(c.name)}</strong>${c.age && !c.name.includes(c.age) ? ` ${esc(c.age)}` : ""} <span class="muted">· ${fmtSpan(c.start, c.end)}, ${esc(c.place)}</span>${tag(c, r)}</span></label></li>`;
    const picked = id => st.sobj.some(o => o.id === id);
    const srow = o => `<li><label class="check"><input type="checkbox" data-sobj="${esc(o.id)}"${picked(o.id) ? " checked" : ""}>
      <span><strong>${esc(o.name)}</strong> <span class="muted">· ${fmtDay(o.start)}${o.real ? "" : " (date habituelle)"}, ${esc(o.place || "lieu inconnu")}${o.indoor ? ", en salle" : ""}</span>
      <span class="tag ${o.top ? "pb" : "w"}">${o.top ? "meeting relevé à votre portée" : KIND[o.kind]}</span>
      <span class="muted">· ${o.n} résultats, de ${fmtVal(o.best, o.d)} à ${fmtVal(o.worst, o.d)}</span></span></label></li>`;
    const extra = st.sobj.filter(o => !sugg.some(x => x.id === o.id));
    app.querySelector("#pobj").innerHTML = `
      <ul class="plain">${near.map(row).join("")}${[...sugg, ...extra].map(srow).join("")}
      ${st.custom.map((c, i) => `<li><span class="check"><span><strong>${esc(c.name)}</strong> <span class="muted">· ${fmtDate(c.date)}${c.indoor ? ", en salle" : ""}</span>
        <button type="button" class="link" data-del="${i}">retirer</button></span></span></li>`).join("")}</ul>
      ${far.length ? `<details class="std"><summary>Championnats internationaux (${far.length})${refMark() !== null ? ", minima encore loin de votre niveau" : ""}</summary><ul class="plain">${far.map(row).join("")}</ul></details>` : ""}
      <p class="note">${refMark() === null ? "Indiquez votre niveau pour voir les objectifs à votre portée : championnats de votre canton et de votre région, championnats suisses, meetings relevés. " : ""}${by ? "" : `Indiquez votre année de naissance pour voir les championnats par catégorie d'âge (U20, U23, masters)${hiddenByAge ? ` : ${hiddenByAge} ${hiddenByAge > 1 ? "sont masqués" : "est masqué"}` : ""}. `}${home ? "" : "Lieu de départ inconnu : choisissez une localité suisse dans la liste."}</p>`;
    app.querySelector("#phome").innerHTML = home
      ? `Trajets calculés depuis <strong>${esc(home[0])}</strong>${home[3] && GEO && GEO.cantons[home[3]] ? ` (${esc(GEO.cantons[home[3]][0])})` : ""}${ath && ath.g && norm(ath.g[1] || (GEO && GEO.capitals[ath.g[0]] || [""])[0]) === norm(home[0]) ? `, la localité de votre club` : ""} : changez de lieu de départ et tout se recalcule.`
      : "";
    return sugg;
  }

  /* candidate races for one objective, grouped by phase */
  function candidates(obj, doc, d, home) {
    const lv = markValue(st.level), tg = markValue(st.target), by = byYear();
    const ref = tg ?? lv, a = ref === null ? null : sortv(d, ref), b = sortv(d, lv ?? ref);
    const lo = a === null ? null : Math.min(a, b) - Math.abs(ref) * 0.015, hi = a === null ? null : Math.max(a, b) + Math.abs(ref) * 0.015;
    const res = PHASES.map(() => []);
    (doc[st.sex] || []).forEach(([name, vi, champ, editions, cat]) => {
      const lastEd = editions[editions.length - 1], lastYear = +lastEd[0].slice(0, 4);
      if (lastYear < META.plan.to - 1) return;  // not held for two seasons
      const all = editions.flatMap(e => e[1]);
      const zone = a === null ? 0 : all.filter(v => sortv(d, v) >= lo && sortv(d, v) <= hi).length / editions.length;
      const hit = tg === null ? 0 : all.filter(v => sortv(d, v) <= sortv(d, tg)).length / editions.length;
      const v = doc.v[vi], t = travel(home, v);
      PHASES.forEach((ph, pi) => {
        const base = ph.end ? (obj.end || obj.start) : obj.start, from = addDays(base, ph.from), to = addDays(base, ph.to);
        const seen = new Set();
        editions.filter(e => e[0].slice(0, 4) === String(lastYear)).forEach(e => {
          [from.slice(0, 4), to.slice(0, 4)].forEach(y => {
            const real = e[0].slice(0, 4) === y, date = real ? e[0] : projectDate(e[0], +y);
            if (date < from || date > to || date < today || seen.has(date)) return;
            if (eligible(cat, by, +y) === false) return;  // reserved for an age group the athlete is not in
            seen.add(date);
            const vals = e[1], med = vals[Math.floor((vals.length - 1) / 2)];
            res[pi].push({ key: `${name}|${v[0]}|${date}`, name, venue: v, champ, cat, date, real, last: e[0], n: vals.length, best: vals[0], med,
              eds: editions.length, zone, hit, t, fit: weekendFit(date, t), score: a === null ? all.length / editions.length : zone + hit * 0.5 });
          });
        });
      });
    });
    return res.map(list => list.filter(c => (!+st.maxh || (c.t && c.t.h <= +st.maxh)) && (!st.weekend || c.fit.k === "ok"))
      .sort((x, y) => y.score - x.score || (x.t ? x.t.h : 99) - (y.t ? y.t.h : 99)));
  }

  const picked = key => st.picks.some(p => p.key === key);
  function raceCard(c, d, home, obj) {
    const place = c.venue[0] || "lieu inconnu";
    const maps = c.t ? `https://www.google.com/maps/dir/?api=1&origin=${encodeURIComponent(home[0] + ", Suisse")}&destination=${c.venue[1]},${c.venue[2]}&travelmode=transit` : "";
    const o = CFG.omio || {}, ids = o.ids || {}, from = ids[home ? home[0] : ""], to = ids[c.venue[0]];
    const q = [from && to ? `departure_fk=${encodeURIComponent(from)}&arrival_fk=${encodeURIComponent(to)}` : "", o.params || ""].filter(Boolean).join("&");
    const omio = o.site && c.t && c.t.km >= 8 ? o.site + (q ? "?" + q : "") : "";
    const lv = markValue(st.level), tg = markValue(st.target);
    return `<li class="race">
      <div class="race-top"><span class="race-name">${esc(c.name)}${c.champ ? `<span class="tag w">${KIND[c.champ]}</span>` : ""}${c.cat ? `<span class="tag w">réservé ${c.cat === "M" ? "masters" : esc(c.cat)}</span>` : ""}</span>
        <span class="race-date num">${fmtDay(c.date)}</span></div>
      <p class="race-meta">${esc(place)} · ${c.real ? "date de l'édition " + c.date.slice(0, 4) : `date habituelle, à confirmer (édition ${c.last.slice(0, 4)} : ${fmtDay(c.last)})`}</p>
      <p class="race-meta">Édition ${c.last.slice(0, 4)} : ${c.n} résultat${c.n > 1 ? "s" : ""}, meilleur <strong class="num">${fmtVal(c.best, d)}</strong>${c.n > 2 ? `, médiane <span class="num">${fmtVal(c.med, d)}</span>` : ""}.
        ${lv !== null || tg !== null ? `${c.zone ? `<strong>${(Math.round(c.zone * 10) / 10).toLocaleString("fr-CH")}</strong> par édition dans votre zone` : "Personne dans votre zone"}${tg !== null ? `, ${c.hit ? (Math.round(c.hit * 10) / 10).toLocaleString("fr-CH") : "aucun"} à ${esc(st.target)} ou mieux` : ""} <span class="muted">(${c.eds} édition${c.eds > 1 ? "s" : ""})</span>.` : ""}</p>
      <p class="race-meta"><span class="fit ${c.fit.k}">${esc(c.fit.txt)}</span>${c.t ? (c.t.chf ? ` <strong class="num">${fmtKm(c.t.km)}</strong> de ${esc(home[0])}` : ` sur place, à ${esc(home[0])}`) : ""}</p>
      ${c.t && c.t.modes.length ? `<p class="race-meta modes">${c.t.modes.map(m => `<span${m.k === c.t.mode && c.t.modes.length > 1 ? ' class="best"' : ""}>${m.name} : environ ${fmtHours(m.h)} de porte à porte, environ <strong class="num">${m.chf} CHF</strong> aller-retour</span>`).join("")}</p>` : ""}
      <p class="race-act"><button type="button" class="link" data-pick="${esc(c.key)}" data-obj="${esc(obj.id)}" data-km="${c.t && c.t.chf ? Math.round(c.t.km) : ""}" data-chf="${c.t ? c.t.chf : ""}" data-mode="${c.t && c.t.mode ? c.t.mode : ""}">${picked(c.key) ? "Retirer de ma saison" : "Ajouter à ma saison"}</button>
        ${maps ? `<a href="${maps}" target="_blank" rel="noopener">Itinéraire</a>` : ""}${omio ? `<a href="${esc(omio)}" target="_blank" rel="noopener">Billets sur Omio</a>` : ""}</p>
    </li>`;
  }

  function standardRow(c, label, d) {
    const S = c.standards, e = S && S.events.find(x => (x.labels[st.sex] || []).includes(label));
    if (!e) return null;
    const lv = markValue(st.level), val = markValue(e[st.sex]);
    let gap = "";
    if (lv !== null && val !== null) {
      const diff = gapTo(d, lv, val);
      gap = diff <= 0 ? `<span class="win">à votre niveau</span>` : `à ${fmtGap(d, diff)}`;
    }
    const alt = e.out ? ` <span class="muted">ou ${esc(e.out[st.sex === "M" ? 1 : 2])} en plein air${e.out[0] !== e.name ? ` (${esc(e.out[0])})` : ""}</span>` : "";
    return `<tr><td>${esc(c.name)}${c.age && !c.name.includes(c.age) ? ` ${esc(c.age)}` : ""} <span class="muted">· ${esc(c.place)}</span></td><td class="dt">${fmtSpan(c.start, c.end)}</td>
      <td class="mk r">${esc(e[st.sex])}</td><td>${alt}${gap ? `${alt ? " · " : ""}${gap}` : ""}</td>
      <td class="hide-s dt">${fmtDate(S.window[0])} au ${fmtDate(S.window[1])}</td></tr>`;
  }

  function timeline(objs, picks) {
    if (!objs.length) return "";
    const t0 = pDate(addDays(objs[0].start, -56)).getTime(), t1 = pDate(addDays(objs[objs.length - 1].end || objs[objs.length - 1].start, 31)).getTime();
    const W = 940, L = 8, X = iso => L + (W - 2 * L) * (pDate(iso).getTime() - t0) / (t1 - t0);
    let g = "";
    for (let m = new Date(Date.UTC(new Date(t0).getUTCFullYear(), new Date(t0).getUTCMonth() + 1, 1)); m.getTime() < t1; m.setUTCMonth(m.getUTCMonth() + 1)) {
      const x = X(toISO(m.getTime())).toFixed(1);
      g += `<line class="grid" x1="${x}" x2="${x}" y1="18" y2="74"/><text class="axis" x="${+x + 4}" y="12">${m.toLocaleDateString("fr-CH", { month: "short", timeZone: "UTC" })} ${String(m.getUTCFullYear()).slice(2)}</text>`;
    }
    objs.forEach(o => {
      PHASES.forEach(ph => {
        const base = ph.end ? (o.end || o.start) : o.start, x = X(addDays(base, ph.from)), w = X(addDays(base, ph.to)) - x;
        g += `<rect class="ph ${ph.k}" x="${x.toFixed(1)}" y="30" width="${w.toFixed(1)}" height="32" rx="3"><title>${ph.name}</title></rect>`;
      });
    });
    objs.forEach(o => {
      const x = X(o.start), w = Math.max(5, X(o.end || o.start) - x + 5);
      g += `<rect class="goal" x="${x.toFixed(1)}" y="24" width="${w.toFixed(1)}" height="44" rx="2"><title>${esc(o.name)}, ${fmtSpan(o.start, o.end)}</title></rect>`;
    });
    picks.forEach(p => { if (pDate(p.date).getTime() > t0 && pDate(p.date).getTime() < t1) g += `<circle class="pick" cx="${X(p.date).toFixed(1)}" cy="46" r="6"><title>${esc(p.name)}, ${fmtDay(p.date)}</title></circle>`; });
    return `<svg class="chart tl" viewBox="0 0 ${W} 82" role="img" aria-label="Calendrier de la saison : objectifs, phases et courses choisies">${g}</svg>
      <p class="note tl-key"><span class="key goal"></span>objectif <span class="key build"></span>montée en puissance <span class="key sharp"></span>affûtage <span class="key after"></span>après l'objectif <span class="key pick"></span>course choisie</p>`;
  }

  let seq = 0, lastSugg = [];
  const open = new Set();
  async function draw() {
    const my = ++seq, home = homeOf(), ev = events.get(st.ev) || {};
    form.querySelectorAll("[data-sex]").forEach(x => x.setAttribute("aria-current", String(x.dataset.sex === st.sex)));
    const docs = {};
    await Promise.all(["out", "in"].map(async k => { if (ev[k] !== undefined) { try { docs[k] = await getJSON(`plan/${ev[k]}.json`); } catch { /* shown as missing below */ } } }));
    if (my !== seq) return;
    lastSugg = drawObjs(docs, ev, home);
    const objs = objectives();
    st.picks = st.picks.filter(p => p.date >= today && objs.some(o => o.id === p.obj));
    saved("plan.v2", st);
    const dAny = anyD(st.ev), evName = dName(dAny), by = byYear();
    const rows = champs.filter(c => eligible(c.age || "", by, +c.start.slice(0, 4)) !== false).map(c => standardRow(c, st.ev, c.indoor && ev.in !== undefined ? ev.in : dAny)).filter(Boolean);
    const season = [...objs.map(o => ({ date: o.start, goal: o })), ...st.picks.map(p => ({ date: p.date, pick: p }))].sort((x, y) => (x.date < y.date ? -1 : 1));
    out.innerHTML = `
      ${objs.length ? `<h2>Ma saison</h2>${timeline(objs, st.picks)}
        <ul class="season">${season.map(x => x.goal
          ? `<li class="goal"><span class="num">${fmtSpan(x.goal.start, x.goal.end)}</span><span><strong>${esc(x.goal.name)}</strong>${x.goal.place ? ` <span class="muted">· ${esc(x.goal.place)}${x.goal.series && !x.goal.real ? ", date à confirmer" : ""}</span>` : ""}</span></li>`
          : `<li><span class="num">${fmtDay(x.pick.date)}</span><span>${esc(x.pick.name)} <span class="muted">· ${esc(x.pick.venue)}${x.pick.real ? "" : ", date à confirmer"}${x.pick.km ? ` · ${fmtKm(x.pick.km)} de ${esc(x.pick.home)}, environ ${x.pick.chf} CHF aller-retour${x.pick.mode === "plane" ? " en avion" : x.pick.mode === "train" ? " en train" : ""}` : ""}</span> <button type="button" class="link" data-pick="${esc(x.pick.key)}">retirer</button></span></li>`).join("")}</ul>
        ${st.picks.some(p => p.chf) ? `<p class="note">Déplacements des courses choisies : environ <strong class="num">${st.picks.reduce((n, p) => n + (p.chf || 0), 0).toLocaleString("fr-CH")} CHF</strong> aller-retour au total, ${fmtKm(st.picks.reduce((n, p) => n + (p.km || 0), 0) * 2)} parcourus (estimation).</p>` : ""}
        ${st.picks.length ? "" : `<p class="note">Ajoutez des courses depuis les propositions ci-dessous : elles apparaissent ici, dans l'ordre de la saison.</p>`}` : `<p class="empty">Cochez au moins un objectif pour voir les courses proposées.</p>`}
      ${objs.map(o => {
        const k = o.indoor ? "in" : "out", d = ev[k], doc = docs[k];
        const head = `<section class="list-sec obj"><h2>${esc(o.name)} <span class="muted">· ${fmtSpan(o.start, o.end)}${o.place ? `, ${esc(o.place)}` : ""}</span></h2>`;
        if (d === undefined || !doc) return `${head}<p class="empty">Pas de résultats ${o.indoor ? "en salle" : "en plein air"} pour ${esc(evName)} : aucune proposition pour cet objectif.</p></section>`;
        const lists = candidates(o, doc, d, home), std = o.standards ? o.standards.events.find(x => (x.labels[st.sex] || []).includes(st.ev)) : null;
        return `${head}
          ${std ? `<p class="limit">${o.standards.kind === "limit" ? "Limite" : "Minima"} ${esc(evName)} : <strong class="num">${esc(std[st.sex])}</strong>, à réaliser du ${fmtDate(o.standards.window[0])} au ${fmtDate(o.standards.window[1])}. <a href="#pmin">Voir les limites</a></p>` : ""}
          ${PHASES.map((ph, pi) => {
            const base = ph.end ? (o.end || o.start) : o.start, id = `${o.id}|${ph.k}`, list = lists[pi], n = open.has(id) ? list.length : 4;
            return `<h3>${ph.name} <span class="muted">· ${fmtDate(addDays(base, ph.from)).slice(0, 6)} au ${fmtDate(addDays(base, ph.to))}</span></h3>
              <p class="note">${ph.hint}</p>
              ${list.length ? `<ul class="races">${list.slice(0, n).map(c => raceCard(c, d, home, o)).join("")}</ul>
                ${list.length > n ? `<p><button type="button" class="more" data-open="${esc(id)}">Afficher les ${list.length - n} autres</button></p>` : ""}`
                : `<p class="empty small">Aucune course connue dans cette période avec ces critères.</p>`}`;
          }).join("")}</section>`;
      }).join("")}
      ${rows.length ? `<section class="list-sec" id="pmin"><h2>Limites et minima ${esc(evName)}, ${st.sex === "W" ? "femmes" : "hommes"}</h2>
        <div class="tbl-wrap"><table><thead><tr><th>Championnat</th><th>Dates</th><th class="r">À réaliser</th><th></th><th class="hide-s">Période de qualification</th></tr></thead><tbody>${rows.join("")}</tbody></table></div>
        ${champs.filter(c => c.standards).map(c => `<details class="std"><summary>${esc(c.name)} : toutes les épreuves</summary>
          <p class="note">${esc(c.standards.note || "")} <a href="${esc(c.standards.source)}" rel="noopener">Source</a></p>
          <div class="tbl-wrap"><table><thead><tr><th>Épreuve</th><th class="r">Hommes</th><th class="r">Femmes</th>${c.standards.events.some(e => e.out) ? `<th class="hide-s">Équivalent en plein air</th>` : ""}</tr></thead>
          <tbody>${c.standards.events.map(e => `<tr><td>${esc(e.name)}</td><td class="r num">${esc(e.M)}</td><td class="r num">${esc(e.W)}</td>${c.standards.events.some(x => x.out) ? `<td class="hide-s muted">${e.out ? `${esc(e.out[0])} : ${esc(e.out[1])} / ${esc(e.out[2])}` : ""}</td>` : ""}</tr>`).join("")}</tbody></table></div></details>`).join("")}
      </section>` : ""}
      <p class="note">Les propositions viennent des résultats des saisons ${META.plan.from} à ${META.plan.to} enregistrés par Swiss Athletics : ce sont les compétitions où des athlètes licenciés en Suisse ont couru, en Suisse comme à l'étranger. Les championnats cantonaux et régionaux sont reconnus à leur nom et proposés pour le canton et la région du lieu de départ ; une compétition réservée à une catégorie d'âge (U16 à U23, masters) n'est proposée qu'aux athlètes de cet âge. « Votre zone » compte les résultats entre votre niveau et votre objectif (à 1,5 % près). Les dates à venir sont reprises du jour habituel de chaque compétition : vérifiez-les auprès de l'organisateur avant de réserver. Distances, durées et prix sont des ordres de grandeur calculés d'après la distance : ni des horaires, ni des tarifs. En Suisse, le trajet est compté en train ; à l'étranger, en train et, dès 350 km, en avion, et le plus rapide des deux sert au filtre « week-end ». Train : environ 20 centimes par kilomètre. Avion : environ 4 h autour du vol, 120 CHF plus 12 centimes par kilomètre.</p>`;
  }

  form.addEventListener("input", e => {
    const t = e.target;
    if (t.dataset.obj) st.objs = t.checked ? [...new Set([...st.objs, t.dataset.obj])] : st.objs.filter(x => x !== t.dataset.obj);
    else if (t.dataset.sobj) {
      const o = lastSugg.find(x => x.id === t.dataset.sobj);
      st.sobj = t.checked && o ? [...st.sobj, o] : st.sobj.filter(x => x.id !== t.dataset.sobj);
    } else if (t.name === "weekend") st.weekend = t.checked;
    else if (t.name === "home") { if (!TOWNS.has(norm(t.value))) return; st.home = TOWNS.get(norm(t.value))[0]; }
    else if (["ev", "level", "target", "maxh", "by"].includes(t.name)) {
      st[t.name] = t.value.trim();
      if (t.name === "ev") { st.target = form.target.value = ""; st.level = form.level.value = pbOf(st.ev); st.sobj = []; }
    } else return;
    draw();
  });
  form.addEventListener("click", e => {
    const t = e.target;
    if (t.dataset.sex) { e.preventDefault(); st.sex = t.dataset.sex; st.sobj = []; draw(); }
    else if (t.id === "cadd") {
      const name = form.cname.value.trim(), date = form.cdate.value;
      if (!name || !date || date < today) { form.cname.focus(); return; }
      st.custom.push({ name, date, indoor: form.cin.checked });
      form.cname.value = form.cdate.value = ""; form.cin.checked = false;
      draw();
    } else if (t.dataset.del !== undefined) { st.custom.splice(+t.dataset.del, 1); st.picks = []; draw(); }
  });
  out.addEventListener("click", e => {
    const t = e.target;
    if (t.dataset.open) { open.add(t.dataset.open); draw(); }
    else if (t.dataset.pick) {
      const key = t.dataset.pick, [name, venue, date] = key.split("|");
      if (picked(key)) st.picks = st.picks.filter(p => p.key !== key);
      else st.picks.push({ key, name, venue, date, obj: t.dataset.obj, real: +date.slice(0, 4) <= META.plan.to, km: +t.dataset.km || 0, chf: +t.dataset.chf || 0, mode: t.dataset.mode || "", home: st.home });
      draw();
    }
  });
  who(); await draw();
}

function notFound(msg) {
  document.title = META ? META.title : document.title;
  app.innerHTML = `<section class="hero"><h1>Introuvable</h1><p class="lede">${esc(msg)}</p><p><a href="#/">Retour à la recherche</a></p></section>`;
}

/* ---------- router ---------- */
async function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  document.querySelectorAll(".top-nav a").forEach(x => x.removeAttribute("aria-current"));
  const sec = { club: "clubs", canton: "cantons", region: "cantons" }[parts[0]] || parts[0];
  const section = ["l", "cadres", "plan", "clubs", "cantons"].includes(sec) ? `#/${sec}` : "#/";
  const nav = document.querySelector(`.top-nav a[href="${section}"]`);
  if (nav && (section !== "#/" || !parts.length)) nav.setAttribute("aria-current", "page");
  window.scrollTo(0, 0);
  try {
    if (parts[0] === "a" && parts[1]) await profile(parts[1]);
    else if (parts[0] === "h2h" && parts[1] && parts[2]) await h2h(parts[1], parts[2]);
    else if (parts[0] === "l") await lists(parts[1], parts[2], parts[3]);
    else if (parts[0] === "cadres") await cadresPage();
    else if (parts[0] === "plan") await planPage(parts[1]);
    else if (parts[0] === "clubs") await clubsPage();
    else if (parts[0] === "club" && parts[1]) await clubPage(parts[1]);
    else if (parts[0] === "cantons") await cantonsPage(parts[1]);
    else if ((parts[0] === "canton" || parts[0] === "region") && parts[1]) await unitPage(parts[0], parts[1]);
    else home();
  } catch (err) {
    console.error(err);
    app.innerHTML = `<p class="empty">Impossible de charger ces données (${esc(err.message)}). Rechargez la page.</p>`;
  }
  if (parts.length) app.focus({ preventScroll: true });
}

async function start() {
  try { META = await getJSON("meta.json"); } catch (err) {
    app.innerHTML = `<p class="empty">meta.json introuvable : lancez build_site.py puis servez le dossier généré.</p>`;
    return;
  }
  document.getElementById("foot").innerHTML = `
    <p>Site indépendant et non officiel, sans lien avec Swiss Athletics, fait pour compléter ses listes officielles et non les remplacer. Source de toutes les performances : les résultats publics de la base <a href="https://alabus.swiss-athletics.ch/satweb/faces/bestlist.xhtml" rel="noopener">Alabus de Swiss Athletics</a>, dès ${META.first_year} ; courses sur route : listes de World Athletics (parcours certifiés). Dernière performance enregistrée le ${fmtDate(META.last_result)}, mise à jour le ${fmtDate(META.generated)}.</p>
    <p>Seule l'année de naissance est publiée, et les athlètes de moins de ${META.min_age} ans ne sont pas affichés.${META.contact ? ` Pour corriger ou retirer un profil : <a href="mailto:${esc(META.contact)}">${esc(META.contact)}</a>.` : ""}</p>`;
  window.addEventListener("hashchange", route);
  route();
}
start();
