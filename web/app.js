"use strict";
/* Static athlete-profile site. Data files are produced by build_site.py; routes use the URL hash
   (#/a/<id>, #/h2h/<id>/<id>, #/l/<year>/<out|in>/<M|W>) so GitHub Pages never needs a server. */

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
    </div>`;
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
        ${a.c ? `<span>Club <strong>${esc(a.c)}</strong></span>` : ""}
        ${a.cs.length > 1 ? `<span>Avant : ${a.cs.slice(0, -1).reverse().map(esc).join(", ")}</span>` : ""}
        ${a.pg ? `<span><a href="p/${id}.html">Lien à partager</a></span>` : ""}
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
    ${a.m.length ? honours(a.m) : ""}
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

function notFound(msg) {
  document.title = META ? META.title : document.title;
  app.innerHTML = `<section class="hero"><h1>Introuvable</h1><p class="lede">${esc(msg)}</p><p><a href="#/">Retour à la recherche</a></p></section>`;
}

/* ---------- router ---------- */
async function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  document.querySelectorAll(".top-nav a").forEach(x => x.removeAttribute("aria-current"));
  const nav = document.querySelector(`.top-nav a[href="${parts[0] === "l" ? "#/l" : parts[0] === "cadres" ? "#/cadres" : "#/"}"]`);
  if (nav && (parts[0] === "l" || parts[0] === "cadres" || !parts.length)) nav.setAttribute("aria-current", "page");
  window.scrollTo(0, 0);
  try {
    if (parts[0] === "a" && parts[1]) await profile(parts[1]);
    else if (parts[0] === "h2h" && parts[1] && parts[2]) await h2h(parts[1], parts[2]);
    else if (parts[0] === "l") await lists(parts[1], parts[2], parts[3]);
    else if (parts[0] === "cadres") await cadresPage();
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
    <p>Site indépendant et non officiel, sans lien avec Swiss Athletics. Données : résultats publics de la base Alabus de Swiss Athletics, dès ${META.first_year} ; courses sur route : listes de World Athletics (parcours certifiés). Dernière performance enregistrée le ${fmtDate(META.last_result)}, mise à jour le ${fmtDate(META.generated)}.</p>
    <p>Seule l'année de naissance est publiée, et les athlètes de moins de ${META.min_age} ans ne sont pas affichés.${META.contact ? ` Pour corriger ou retirer un profil : <a href="mailto:${esc(META.contact)}">${esc(META.contact)}</a>.` : ""}</p>`;
  window.addEventListener("hashchange", route);
  route();
}
start();
