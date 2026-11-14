"use strict";
/* Ghana Fire Calendar: talks to the FastAPI backend in api/main.py */

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const UNIT = { harmonized: "VIIRS-equivalent cell-days", modis: "MODIS cell-days", viirs: "VIIRS cell-days" };
const SERIES_VAR = { harmonized: "--s-harm", modis: "--s-modis", viirs: "--s-viirs" };
const VIIRS_FIRST_FULL_YEAR = 2013;
const MAP_TIP = "Click a region, then a district, or draw an area. Pick a month in the calendar to map its fires.";
const DISTRICT_ZOOM = 9.5;   // zoomed in this close, every district shows; farther out, only the selected region's

const state = {
  area: { region: "Ghana" }, sensor: "harmonized", ref: "", layout: "fire",
  meta: null, data: null, compare: null, national: null, season: null, month: null, request: 0,
  mapMode: "regions",
};
const dailyCache = new Map();
const charts = {};
let map, regionLayer, districtLayer, districtFeatures = [], drawnLayer, hotLayer, animLayer, drawers, lastDrawEnd = 0;
const districtInfo = new Map();   // lower-case district name -> { name, label, region }

/* ---------- small helpers ---------- */
const $ = id => document.getElementById(id);
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const fmt = (n, d = 0) => n == null || Number.isNaN(n) ? "–" :
  Number(n).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d });
const pct = x => x == null ? "–" : `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(Math.round(100 * x))}%`;
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const yy = y => String(y).slice(2);
function fmtDate(iso, withYear = true) {
  if (!iso) return "–";
  const d = new Date(iso + "T00:00:00Z");
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}${withYear ? " " + d.getUTCFullYear() : ""}`;
}
const monthOf = label => label ? MONTHS.indexOf(label.split(" ")[1]) + 1 : 1;  // "1 Jul" -> 7
const seriesColor = () => css(SERIES_VAR[state.sensor]);
const seqColors = () => css("--seq").split(",").map(s => s.trim());
const icon = (name, cls = "i") => `<svg class="${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const setIcon = (el, name) => el.querySelector("use").setAttribute("href", `#i-${name}`);
const kFmt = v => v >= 1000 ? `${v / 1000}k` : v;

/* ---------- charts: one theme (page font, gentle easing), resized with their box ---------- */
let themed = false;
function chart(id) {
  if (!charts[id]) {
    if (!themed) {
      echarts.registerTheme("app", {
        textStyle: { fontFamily: getComputedStyle(document.body).fontFamily },
        animationDuration: 700, animationEasing: "cubicOut",
        animationDurationUpdate: 450, animationEasingUpdate: "cubicInOut",
      });
      themed = true;
    }
    const el = $(id), c = echarts.init(el, "app");
    charts[id] = c;
    new ResizeObserver(() => { if (el.clientWidth) c.resize(); }).observe(el);
  }
  return charts[id];
}
const axis = () => ({
  axisLine: { lineStyle: { color: css("--axis") } }, axisTick: { show: false },
  axisLabel: { color: css("--ink-2"), fontSize: 11 }, splitLine: { lineStyle: { color: css("--grid") } },
});
const tip = () => ({
  backgroundColor: css("--tip"), borderWidth: 0, padding: [10, 14], confine: true, transitionDuration: 0.2,
  textStyle: { color: css("--ink"), fontSize: 13 },
  extraCssText: "border-radius:12px;box-shadow:0 8px 28px rgba(0,0,0,.16);" +
    "backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);line-height:1.5;",
});
const legend = extra => ({ icon: "roundRect", itemWidth: 14, itemHeight: 8, itemGap: 16,
  textStyle: { color: css("--ink-2"), fontSize: 12 }, ...extra });
/* Room above the plot for the legend: one row, or two when it wraps on a narrow chart */
const measure = document.createElement("canvas").getContext("2d");
function legendTop(id, labels) {
  measure.font = `12px ${getComputedStyle(document.body).fontFamily}`;
  const w = labels.reduce((t, l) => t + 14 + 5 + measure.measureText(l).width, 0) + 16 * (labels.length - 1);
  return w > $(id).clientWidth ? 58 : 36;
}

/* Seven colour steps on a rough log scale, rounded to friendly numbers */
function niceBins(max) {
  const nice = v => { const p = 10 ** Math.floor(Math.log10(v)); const m = v / p;
    return (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * p; };
  const edges = [0];
  const top = Math.max(max, 10);
  for (let i = 1; i < 7; i++) {
    let e = nice(top ** (i / 7));
    if (e <= edges[i - 1]) e = edges[i - 1] < 1 ? 1 : edges[i - 1] * 2;
    edges.push(e);
  }
  const seq = seqColors();
  const pieces = [{ value: 0, color: css("--zero"), label: "0" }];
  edges.forEach((lo, i) => {
    const hi = edges[i + 1];
    pieces.push(i === 0 ? { gt: 0, lt: hi, color: seq[0], label: `under ${fmt(hi)}` }
      : hi ? { gte: lo, lt: hi, color: seq[i], label: `${fmt(lo)}–${fmt(hi)}` }
        : { gte: lo, color: seq[i], label: `${fmt(lo)}+` });
  });
  return pieces;
}
const visualMap = (pieces, size = 12) => ({ type: "piecewise", pieces, orient: "horizontal", left: "center", bottom: 0,
  itemWidth: size, itemHeight: size, itemGap: 10, itemSymbol: "roundRect", textStyle: { color: css("--ink-2"), fontSize: 11 } });

/* ---------- API ---------- */
async function getJSON(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}
async function post(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
                                body: JSON.stringify(body) });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch { /* not JSON */ }
    throw new Error(msg);
  }
  return path.endsWith(".csv") ? r.blob() : r.json();
}
function refParam() {
  if (!state.ref) return null;
  const [a, b] = state.meta.reference_presets.recent_15;
  return `${state.sensor === "viirs" ? Math.max(a, VIIRS_FIRST_FULL_YEAR) : a}-${b}`;
}
const areaBody = extra => ({ ...state.area, sensor: state.sensor, ref: refParam(), ...extra });

function showError(msg) { $("bannertext").textContent = msg; $("banner").hidden = false; }
function hideError() { $("banner").hidden = true; }
function setLoading(on) {
  ["sumcard", "calcard", "sensorcard", "seasoncard", "totalscard", "dailycard"].forEach(id => $(id).classList.toggle("loading", on));
}

/* ---------- URL hash (shareable links for regions) ---------- */
function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.get("region")) state.area = { region: p.get("region") };
  if (["harmonized", "modis", "viirs"].includes(p.get("sensor"))) state.sensor = p.get("sensor");
  if (p.get("ref") === "recent") state.ref = "recent";
  if (p.get("layout") === "calendar") state.layout = "calendar";
}
function writeHash() {
  const p = new URLSearchParams();
  if (state.area.region) p.set("region", state.area.region);
  if (state.sensor !== "harmonized") p.set("sensor", state.sensor);
  if (state.ref) p.set("ref", state.ref);
  if (state.layout !== "fire") p.set("layout", state.layout);
  history.replaceState(null, "", p.toString() ? "#" + p : location.pathname);
}

/* ---------- controls ---------- */
function setSeg(id, value) {
  document.querySelectorAll(`#${id} button`).forEach(b => b.setAttribute("aria-pressed", b.dataset.v === value));
}
function syncControls() {
  setSeg("sensor", state.sensor); setSeg("ref", state.ref); setSeg("layout", state.layout);
  $("region").value = state.area.region || "__drawn";
}
function wireSeg(id, key, after) {
  document.querySelectorAll(`#${id} button`).forEach(b => b.addEventListener("click", () => {
    if (state[key] === b.dataset.v) return;
    state[key] = b.dataset.v; setSeg(id, state[key]); writeHash(); after();
  }));
}
function scrollToMap() { $("mapcard").scrollIntoView({ behavior: "smooth", block: "center" }); }

/* ---------- map ---------- */
const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
function applyTileTheme() {
  // OSM has no dark style: dim and invert the tiles in dark mode (data layers are untouched)
  $("map").classList.toggle("dark-tiles", css("--tiles") === "dark");
}
/* Map colour scales for the region modes. Darker / stronger = more burning or bigger drop. */
const DENSITY_BINS = [15, 30, 45, 60];                       // cell-days per 100 km² in a normal season
const CHANGE_BINS = [-0.45, -0.30, -0.15, -0.05, 0.05];       // change since 2003–11
function densityColor(v) {
  const i = DENSITY_BINS.filter(b => v >= b).length;           // 0..4
  return seqColors()[[1, 2, 3, 5, 6][i]];
}
function changeColor(v) {
  if (v > CHANGE_BINS[4]) return css("--increase");
  if (v > CHANGE_BINS[3]) return css("--neutral");
  const i = CHANGE_BINS.slice(0, 4).filter(b => v <= b).length;  // 1..4 = size of the drop
  return seqColors()[[1, 1, 3, 5, 6][i]];
}
function regionValue(name) {
  const c = state.compare;
  return c && c.regions.find(r => r.region.toLowerCase() === name.toLowerCase());
}
const selectedDistrict = () => state.area.region ? districtInfo.get(state.area.region.toLowerCase()) : null;
/* The region the current area belongs to: the region itself, a district's region, or "Ghana" */
function selectedRegion() {
  const d = selectedDistrict();
  return d ? d.region : state.area.region || "";
}
function regionStyle(f) {
  const name = f.properties.name.toLowerCase();
  const sel = !!state.area.region && state.area.region.toLowerCase() === name;
  const parent = !sel && selectedRegion().toLowerCase() === name;      // a district inside it is selected
  const base = { color: sel ? css("--ink") : css("--map-line"), weight: sel ? 2.5 : parent ? 1.6 : 0.8, fillOpacity: 0 };
  if (state.mapMode === "regions") return { ...base, color: sel ? css("--s-harm") : css("--map-line"),
                                            fillColor: css("--s-harm"), fillOpacity: sel ? 0.14 : 0 };
  if (state.mapMode === "play") return { ...base, weight: sel ? 2 : 0.6 };
  const r = regionValue(f.properties.name);
  if (!r) return base;
  const v = state.mapMode === "density" ? r.density : r.long_term_change;
  if (v == null) return { ...base, fillColor: css("--neutral"), fillOpacity: 0.35 };
  return { ...base, fillColor: state.mapMode === "density" ? densityColor(v) : changeColor(v), fillOpacity: 0.78 };
}
function districtStyle(f) {
  const sel = !!state.area.region && state.area.region.toLowerCase() === f.properties.name.toLowerCase();
  return sel ? { color: css("--s-harm"), weight: 2.5, opacity: 1, dashArray: null, fillColor: css("--s-harm"), fillOpacity: 0.18 }
             : { color: css("--ink-2"), weight: 1, opacity: 0.55, dashArray: "3 3", fillOpacity: 0 };
}
/* Districts show for the selected region (to drill down), or all of them when zoomed in close */
function showDistricts() {
  if (!districtLayer) return;
  const region = selectedRegion().toLowerCase(), all = map.getZoom() >= DISTRICT_ZOOM;
  districtLayer.clearLayers();
  if (state.mapMode === "play") return;
  districtLayer.addData(districtFeatures.filter(f => all || f.properties.region.toLowerCase() === region));
}
const drawing = () => drawers && Object.values(drawers).some(d => d.enabled());
const fitPadding = { paddingTopLeft: [16, 64], paddingBottomRight: [16, 16] };

function initMap(regions, districts) {
  map = L.map("map", { preferCanvas: true, zoomSnap: 0.25, zoomControl: false });
  L.tileLayer(TILE_URL, { maxZoom: 14,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' }).addTo(map);
  applyTileTheme();
  const border = L.geoJSON(regions.border, { interactive: false,
    style: () => ({ color: css("--ink-2"), weight: 1.5, fill: false }) }).addTo(map);
  regionLayer = L.geoJSON(regions.regions, {
    style: regionStyle,
    onEachFeature: (f, layer) => {
      layer.bindTooltip(f.properties.name, { sticky: true, direction: "top", offset: [0, -10] });
      layer.on("click", () => {
        // A rectangle drag ends with a click on the region underneath: ignore it
        if (drawing() || Date.now() - lastDrawEnd < 500) return;
        selectRegion(f.properties.name);
      });
      layer.on("mouseover", () => { if (!drawing()) layer.setStyle({ weight: 2.5, color: css("--ink-2") }); });
      layer.on("mouseout", () => regionLayer.resetStyle(layer));
    },
  }).addTo(map);
  districtFeatures = districts.features;
  districtLayer = L.geoJSON(null, {
    style: districtStyle,
    onEachFeature: (f, layer) => {
      layer.bindTooltip(`${esc(f.properties.label)}<br><span style="opacity:.65">${esc(f.properties.region)}</span>`,
                        { sticky: true, direction: "top", offset: [0, -10] });
      layer.on("click", e => {
        L.DomEvent.stopPropagation(e);   // don't also select the region underneath
        if (drawing() || Date.now() - lastDrawEnd < 500) return;
        selectRegion(f.properties.name);
      });
      layer.on("mouseover", () => { if (!drawing()) layer.setStyle({ weight: 2, color: css("--ink-2"), dashArray: null }); });
      layer.on("mouseout", () => districtLayer.resetStyle(layer));
    },
  }).addTo(map);
  map.on("zoomend", showDistricts);
  drawnLayer = new L.FeatureGroup().addTo(map);
  animLayer = L.layerGroup().addTo(map);
  hotLayer = L.layerGroup().addTo(map);

  const shape = { color: css("--s-harm"), weight: 2.5, fillOpacity: 0.12 };
  drawers = {
    drawrect: new L.Draw.Rectangle(map, { showArea: false, shapeOptions: shape }),
    drawpoly: new L.Draw.Polygon(map, { allowIntersection: false, showArea: false, shapeOptions: shape }),
  };
  map.on("draw:drawstart", syncTools);
  map.on("draw:drawstop", () => { lastDrawEnd = Date.now(); syncTools(); });
  map.on(L.Draw.Event.CREATED, e => {
    lastDrawEnd = Date.now();
    drawnLayer.clearLayers(); drawnLayer.addLayer(e.layer);
    state.area = { geometry: e.layer.toGeoJSON().geometry };
    $("drawnopt").disabled = false;
    $("cleardraw").hidden = false;
    syncControls(); regionLayer.setStyle(regionStyle); showDistricts(); writeHash(); refresh();
  });
  map.fitBounds(border.getBounds(), fitPadding);
  new ResizeObserver(() => map.invalidateSize()).observe($("map"));
}
function syncTools() {
  Object.entries(drawers).forEach(([id, d]) => $(id).setAttribute("aria-pressed", String(d.enabled())));
}
function toggleDraw(id) {
  const was = drawers[id].enabled();
  Object.values(drawers).forEach(d => d.disable());
  if (!was) drawers[id].enable();
  syncTools();
}
function selectRegion(name) {
  drawnLayer.clearLayers();
  $("drawnopt").disabled = true;
  $("cleardraw").hidden = true;
  state.area = { region: name };
  syncControls(); regionLayer.setStyle(regionStyle); showDistricts(); writeHash(); refresh();
}
function zoomToArea() {
  if (!state.data) return;
  const [w, s, e, n] = state.data.area.bounds;
  map.fitBounds([[s, w], [n, e]], { ...fitPadding, maxZoom: 10 });
}
function setMapNote() { $("mapnote").textContent = MAP_TIP; }
function clearMonth() {
  state.month = null;
  hotLayer.clearLayers();
  setMapNote();
  if (state.data) renderCalendar();
}

async function loadHotspots() {
  if (!state.month) return;
  const { year, month } = state.month;
  hotLayer.clearLayers();
  $("mapnote").textContent = `Loading fires for ${MONTHS[month - 1]} ${year}…`;
  try {
    const h = await post("/api/hotspots?limit=3000", { ...state.area, year, month });
    const colors = { MODIS: css("--s-modis"), VIIRS: css("--s-viirs") };
    h.hotspots.forEach(p => {
      L.circleMarker([p.lat, p.lon], {
        radius: Math.min(3 + Math.sqrt(p.frp || 0) / 3, 11), color: css("--surface"), weight: 1,
        fillColor: colors[p.sensor], fillOpacity: 0.75,
      }).bindPopup(`<b>${esc(p.time_utc)} UTC</b><br>${esc(p.sensor)} (${esc(p.satellite)})<br>` +
                   `Fire radiative power: ${fmt(p.frp, 1)} MW<br>Confidence: ${esc(p.conf_class)}`)
        .addTo(hotLayer);
    });
    const shown = h.shown < h.total ? `${fmt(h.shown)} most intense of ${fmt(h.total)}` : fmt(h.total);
    $("mapnote").innerHTML = `<b>${MONTHS[month - 1]} ${year}</b><span>${shown} hotspots</span>` +
      `<span class="dot" style="background:${colors.MODIS}"></span>MODIS` +
      `<span class="dot" style="background:${colors.VIIRS}"></span>VIIRS` +
      `<span class="muted">· larger dot = more intense</span>` +
      `<button class="link-btn" id="clearfires" type="button">Clear</button>`;
  } catch (e) {
    $("mapnote").textContent = `Could not load fires: ${e.message}`;
  }
}

/* ---------- data refresh ---------- */
async function refresh() {
  const token = ++state.request;
  hideError(); setLoading(true);
  try {
    const d = await post("/api/calendar", areaBody());
    if (token !== state.request) return;         // a newer request has started
    state.data = d;
    if (!d.fire_years.some(f => f.label === state.season)) state.season = defaultSeason(d);
    state.month = null;
    hotLayer.clearLayers();
    if (state.mapMode !== "play") setMapNote();
    regionLayer.setStyle(regionStyle);
    zoomToArea();
    renderAll();
    loadCompare();   // independent of the area; cached per record and baseline
    loadForecast();  // harmonized record only, loads on its own
    await renderDaily();
  } catch (e) {
    if (token === state.request) showError(e.message);
  } finally {
    if (token === state.request) setLoading(false);
  }
}
/* Show the season in progress once it has got going (10% of a normal season burned by now);
   before that, the last complete season is more useful. */
function defaultSeason(d) {
  const fys = d.fire_years;
  if (!fys.length) return null;
  const cur = fys[fys.length - 1];
  const started = cur.complete || (d.fire_year_normal && cur.normal_to_date >= 0.1 * d.fire_year_normal);
  const done = fys.filter(f => f.complete);
  return started || !done.length ? cur.label : done[done.length - 1].label;
}

function renderAll() {
  if (!state.data) return;
  renderSummary(); renderCalendar(); renderSensors(); renderSeasonSelect(); renderSeason(); renderTotals();
  renderSeasonTable(); renderCompare();
  if (fcState.data) renderForecast();
}

/* ---------- area summary ---------- */
const kpiHTML = rows => rows.map(([k, v, s, cls]) =>
  `<div class="kpi ${cls || ""}"><div class="kpi-k">${esc(k)}</div><div class="kpi-v">${v}</div><div class="kpi-s">${esc(s || "")}</div></div>`).join("");

function renderSummary() {
  const d = state.data, s = d.season, r = d.recent;
  $("areaname").textContent = d.area.name;
  const dist = selectedDistrict();
  $("areameta").textContent = `${dist ? dist.region + " · " : ""}${fmt(d.area.km2)} km² · normals ${d.reference_years[0]}–${d.reference_years[1]} · data to ${fmtDate(d.data.last)}`;
  const status = {
    "above normal": ["▲", "Above normal", "status-above"], "below normal": ["▼", "Below normal", ""],
    "normal": ["●", "Normal", ""], "quiet season": ["○", "Quiet", ""],
  }[r.status] || ["●", r.status, ""];
  const done = d.fire_years.filter(f => f.complete && f.vs_normal != null);
  const last = done[done.length - 1];
  const tiles = d.low_activity ? [
    ["Activity", "Very little fire", `About ${fmt(d.fire_year_normal, 1)} cell-days in a normal season`],
    ["Whole record", fmt(d.monthly.reduce((t, m) => t + m.value, 0)), UNIT[d.sensor]],
  ] : s ? [
    ["Fire season", `${s.start} – ${s.end}`, "80% of the year's burning"],
    ["Peak", s.peak, `Half of burning ${s.core_start} – ${s.core_end}`],
    ["Last 30 days", `<span class="icon" aria-hidden="true">${status[0]}</span>${status[1]}`,
     `${fmt(r.value)} vs ${fmt(r.normal)} normal`, status[2]],
    ["Last full season", last ? pct(last.vs_normal) : "–", last ? `${last.label} vs normal` : ""],
  ] : [["Activity", "None", "No fire detected in this area"]];
  $("tiles").innerHTML = kpiHTML(tiles);
  // Drop the sentences the tiles already show
  const repeats = s && !d.low_activity ? /^(In .+, the fire season usually|Last \d+ days|Last full season)/ : null;
  $("summary").innerHTML = d.summary.filter(t => !repeats || !repeats.test(t)).map(t => `<li>${esc(t)}</li>`).join("");
}

/* ---------- calendar heatmap ---------- */
function rowKey(year, month, start) {
  if (state.layout === "calendar" || start === 1) return { row: String(year), col: month - 1 };
  const fy = month >= start ? year : year - 1;
  return { row: `${fy}/${String(fy + 1).slice(-2)}`, col: (month - start + 12) % 12 };
}
function calendarStart() { return state.layout === "fire" && state.data.season ? monthOf(state.data.season.fire_year_start) : 1; }

function renderCalendar() {
  const d = state.data, start = calendarStart();
  const normals = Object.fromEntries(d.normals.map(n => [n.month, n]));
  const cols = Array.from({ length: 12 }, (_, i) => MONTHS[(start - 1 + i) % 12]);
  const rows = [], cells = [];
  d.monthly.forEach(m => {
    const k = rowKey(m.year, m.month, start);
    if (!rows.includes(k.row)) rows.push(k.row);
    cells.push({ ...m, ...k });
  });
  const selected = state.layout === "fire" ? state.season : null;
  const pieces = niceBins(Math.max(...d.monthly.map(m => m.value)));
  const data = cells.map(c => {
    const st = { borderColor: css("--surface"), borderWidth: 2, borderRadius: 4 };
    if (c.flag === "above") Object.assign(st, { borderColor: css("--serious"), borderWidth: 3 });
    if (c.flag === "below") Object.assign(st, { borderColor: css("--ink-2"), borderWidth: 2, borderType: "dashed" });
    if (state.month && state.month.year === c.year && state.month.month === c.month)
      Object.assign(st, { borderColor: css("--ink"), borderWidth: 3, borderType: "solid" });
    if (c.confidence === "lower") st.opacity = 0.55;
    return { value: [c.col, rows.indexOf(c.row), c.value], itemStyle: st, m: c };
  });
  const flagText = { above: "▲ Unusually high (above the usual range)", below: "▼ Unusually low (below the usual range)" };
  chart("cal").setOption({
    tooltip: { ...tip(), formatter: p => {
      const m = p.data.m, n = normals[m.month];
      return `<b>${MONTHS[m.month - 1]} ${m.year}</b><br>${fmt(m.value)} ${UNIT[d.sensor]}` +
        `<br>Normal ${fmt(n.mean)} · usual range ${fmt(n.p10)}–${fmt(n.p90)}` +
        (m.flag ? `<br>${flagText[m.flag]}` : "") +
        (m.partial ? "<br><i>Partial month</i>" : "") +
        (m.confidence === "lower" ? "<br><i>Lower confidence: only Terra was flying</i>" : "") +
        "<br><span style='opacity:.65'>Click to map its fires</span>";
    } },
    grid: { left: 64, right: 8, top: 4, bottom: 52 },
    xAxis: { type: "category", data: cols, position: "top", ...axis(), splitLine: { show: false }, axisLine: { show: false } },
    yAxis: { type: "category", data: rows, inverse: true, ...axis(), splitLine: { show: false }, axisLine: { show: false },
             axisLabel: { color: css("--ink-2"), fontSize: 11, formatter: v => v === selected ? `{sel|${v}}` : v,
                          rich: { sel: { color: css("--ink"), fontWeight: "bold", fontSize: 11 } } } },
    visualMap: visualMap(pieces, 13),
    series: [{ type: "heatmap", data, emphasis: { itemStyle: { borderColor: css("--ink"), borderWidth: 2 } } }],
  }, true);

  $("callede").textContent = (state.layout === "fire" && start !== 1
    ? `Burning each month. One row per fire season, ${MONTHS[start - 1]} to ${MONTHS[(start + 10) % 12]}.`
    : "Burning each month. One row per calendar year.") + ` Units: ${UNIT[d.sensor]}.`;
  $("calkey").innerHTML =
    `<span><i class="sw above"></i>Unusually high</span>` +
    `<span><i class="sw below"></i>Unusually low</span>` +
    (d.sensor === "harmonized" ? `<span><i class="sw lower" style="background:${seqColors()[3]}"></i>Lower confidence (Terra only, 2000–02)</span>` : "") +
    `<span>Usual range = middle 80% of ${d.reference_years[0]}–${d.reference_years[1]}</span>`;

  // table view
  const grid = Object.fromEntries(rows.map(r => [r, Array(12).fill(null)]));
  cells.forEach(c => { grid[c.row][c.col] = c; });
  const mark = c => c ? `${fmt(c.value)}${c.flag === "above" ? " ▲" : c.flag === "below" ? " ▼" : ""}` : "";
  $("caltable").innerHTML = `<thead><tr><th>${state.layout === "fire" && start !== 1 ? "Season" : "Year"}</th>` +
    cols.map(c => `<th>${c}</th>`).join("") + "</tr></thead><tbody>" +
    rows.map(r => `<tr><td>${r}</td>${grid[r].map(c => `<td>${mark(c)}</td>`).join("")}</tr>`).join("") + "</tbody>";
}

function onCalendarClick(p) {
  if (!p.data || !p.data.m) return;
  if (state.mapMode === "play") setMapMode("regions");   // month fires replace the playback
  const m = p.data.m;
  state.month = { year: m.year, month: m.month };
  const k = rowKey(m.year, m.month, state.data.season ? monthOf(state.data.season.fire_year_start) : 1);
  const fy = state.layout === "fire" ? k.row : seasonLabelFor(m.year, m.month);
  if (fy) state.season = fy;
  renderCalendar(); renderSeasonSelect(); renderSeason(); renderTotals(); renderDaily(); loadHotspots();
  toast(`${MONTHS[m.month - 1]} ${m.year} fires are on the map`, { label: "View", fn: scrollToMap });
}
function seasonLabelFor(year, month) {
  const f = state.data.fire_years.find(f => {
    const d = `${year}-${String(month).padStart(2, "0")}-15`;
    return d >= f.from && d <= f.to;
  });
  return f ? f.label : null;
}

/* ---------- season vs normal ---------- */
function renderSeasonSelect() {
  const fys = state.data.fire_years.slice().reverse();
  $("season").innerHTML = fys.map(f =>
    `<option value="${f.label}">${f.label}${f.complete ? "" : " (so far)"}</option>`).join("");
  $("season").value = state.season;
}
function selectedSeason() { return state.data.fire_years.find(f => f.label === state.season); }
function openSeason(label) {
  state.season = label; state.month = null; hotLayer.clearLayers();
  if (state.mapMode !== "play") setMapNote();
  renderCalendar(); renderSeasonSelect(); renderSeason(); renderTotals(); renderDaily();
}

function renderSeason() {
  const d = state.data, fy = selectedSeason();
  if (!fy) { chart("seasonchart").clear(); $("seasonnormtable").innerHTML = ""; return; }
  const start = d.season ? monthOf(d.season.fire_year_start) : 1;
  const order = Array.from({ length: 12 }, (_, i) => (start - 1 + i) % 12 + 1);
  const normals = Object.fromEntries(d.normals.map(n => [n.month, n]));
  const byKey = Object.fromEntries(d.monthly.map(m => [`${m.year}-${m.month}`, m]));
  const y0 = fy.start_year;
  const vals = order.map(m => byKey[`${m >= start ? y0 : y0 + 1}-${m}`] || null);
  const color = seriesColor();
  chart("seasonchart").setOption({
    tooltip: { trigger: "axis", ...tip(), axisPointer: { type: "line", lineStyle: { color: css("--axis") } }, formatter: ps => {
      const i = ps[0].dataIndex, m = order[i], n = normals[m], v = vals[i];
      return `<b>${MONTHS[m - 1]}</b><br>${fy.label}: ${v ? fmt(v.value) : "no data yet"}` +
        `${v && v.flag ? (v.flag === "above" ? " ▲ unusually high" : " ▼ unusually low") : ""}` +
        `<br>Normal: ${fmt(n.mean)}<br>Usual range: ${fmt(n.p10)}–${fmt(n.p90)}`;
    } },
    legend: legend({ top: 0, left: 0, data: [fy.label, "Normal", "Usual range"] }),
    grid: { left: 44, right: 12, top: legendTop("seasonchart", [fy.label, "Normal", "Usual range"]), bottom: 24 },
    xAxis: { type: "category", data: order.map(m => MONTHS[m - 1]), boundaryGap: false, ...axis(), splitLine: { show: false } },
    yAxis: { type: "value", ...axis(), axisLine: { show: false }, axisLabel: { color: css("--ink-2"), fontSize: 11, formatter: kFmt } },
    series: [
      { name: "_base", type: "line", stack: "band", data: order.map(m => normals[m].p10),
        symbol: "none", lineStyle: { opacity: 0 }, silent: true },
      { name: "Usual range", type: "line", stack: "band", data: order.map(m => normals[m].p90 - normals[m].p10),
        symbol: "none", lineStyle: { opacity: 0 }, areaStyle: { color: css("--band") }, itemStyle: { color: css("--band") },
        silent: true },
      { name: "Normal", type: "line", data: order.map(m => normals[m].mean), symbol: "none",
        lineStyle: { color: css("--normal"), width: 2, type: [6, 4] }, itemStyle: { color: css("--normal") } },
      { name: fy.label, type: "line", z: 5, showSymbol: true, symbolSize: 8,
        lineStyle: { color, width: 2.5 }, itemStyle: { color, borderColor: css("--surface"), borderWidth: 2 },
        data: vals.map(v => v == null ? null : {
          value: v.value,
          symbol: v.flag ? "triangle" : "circle", symbolSize: v.flag ? 13 : 8, symbolRotate: v.flag === "below" ? 180 : 0,
          itemStyle: v.flag === "above" ? { color: css("--serious"), borderColor: css("--surface"), borderWidth: 2 } : undefined,
        }) },
    ],
  }, true);
  $("seasonlede").textContent = fy.complete
    ? `${fy.label}: ${pct(fy.vs_normal)} vs normal · ${fmtDate(fy.start, false)} – ${fmtDate(fy.end, false)}, peak ${fmtDate(fy.peak, false)}`
    : `${fy.label} so far: ${pct(fy.vs_normal_to_date)} vs normal to date`;
  $("seasonnormtable").innerHTML = `<thead><tr><th>Month</th><th>${esc(fy.label)}</th><th>Normal</th><th>Usual range</th></tr></thead><tbody>` +
    order.map((m, i) => { const n = normals[m], v = vals[i];
      return `<tr><td>${MONTHS[m - 1]}</td><td>${v ? fmt(v.value) + (v.flag === "above" ? " ▲" : v.flag === "below" ? " ▼" : "") : "–"}</td>` +
        `<td>${fmt(n.mean)}</td><td>${fmt(n.p10)}–${fmt(n.p90)}</td></tr>`; }).join("") + "</tbody>";
}

/* ---------- season totals ---------- */
const totalsSeasons = d => d.fire_years.filter(f => f.complete || f === d.fire_years[d.fire_years.length - 1]);
function renderTotals() {
  const d = state.data, color = seriesColor();
  const fys = totalsSeasons(d);
  chart("totals").setOption({
    tooltip: { ...tip(), formatter: p => {
      const f = fys[p.dataIndex];
      return `<b>${f.label}${f.complete ? "" : " (so far)"}</b><br>${fmt(f.total)} ${UNIT[d.sensor]}` +
        (f.vs_normal != null ? `<br>${pct(f.vs_normal)} vs normal` : "") +
        (f.vs_normal_to_date != null ? `<br>${pct(f.vs_normal_to_date)} vs normal to date` : "") +
        (f.peak ? `<br>Peak around ${fmtDate(f.peak)}` : "") +
        "<br><span style='opacity:.65'>Click to open</span>";
    } },
    grid: { left: 44, right: 52, top: 16, bottom: 24 },
    xAxis: { type: "category", data: fys.map(f => f.label), ...axis(), splitLine: { show: false },
             axisLabel: { color: css("--ink-2"), fontSize: 11, formatter: v => "’" + v.slice(2, 4) } },
    yAxis: { type: "value", ...axis(), axisLine: { show: false }, axisLabel: { color: css("--ink-2"), fontSize: 11, formatter: kFmt } },
    series: [{
      type: "bar", barWidth: "62%", cursor: "pointer",
      data: fys.map(f => ({ value: Math.round(f.total), itemStyle: {
        color, opacity: f.complete ? 1 : 0.45, borderRadius: [4, 4, 0, 0],
        borderColor: f.label === state.season ? css("--ink") : "transparent", borderWidth: 2 } })),
      markLine: d.fire_year_normal == null ? undefined : {
        symbol: "none", silent: true, lineStyle: { color: css("--normal"), type: [6, 4], width: 2 },
        label: { formatter: "Normal", color: css("--ink-2"), position: "end", fontSize: 11 },
        data: [{ yAxis: d.fire_year_normal }] },
    }],
  }, true);
}
function renderSeasonTable() {
  const d = state.data;
  $("seasontable").innerHTML = "<thead><tr><th>Season</th><th>Total</th><th>vs normal</th><th>Start</th><th>Peak</th><th>End</th></tr></thead><tbody>" +
    d.fire_years.slice().reverse().map(f => `<tr><td>${f.label}${f.complete ? "" : " (so far)"}</td>` +
      `<td>${fmt(f.total)}</td><td>${pct(f.complete ? f.vs_normal : f.vs_normal_to_date)}</td>` +
      `<td>${fmtDate(f.start, false)}</td><td>${fmtDate(f.peak, false)}</td><td>${fmtDate(f.end, false)}</td></tr>`).join("") + "</tbody>";
}

/* ---------- daily calendar for the selected season ---------- */
async function dailyFor(year) {
  const key = JSON.stringify([state.area, state.sensor, refParam(), year]);
  if (!dailyCache.has(key)) dailyCache.set(key, post("/api/daily", areaBody({ year })).catch(e => {
    dailyCache.delete(key); throw e;
  }));
  return dailyCache.get(key);
}
async function renderDaily() {
  const fy = selectedSeason();
  if (!fy) return;
  const token = state.request, label = fy.label;
  const last = state.data.data.last;
  const years = [...new Set([fy.from.slice(0, 4), fy.to.slice(0, 4)])].map(Number)
    .filter(y => `${y}-01-01` <= last);
  let days = [];
  try {
    for (const y of years) days = days.concat((await dailyFor(y)).days);
  } catch (e) { showError(e.message); return; }
  if (token !== state.request || label !== state.season) return;   // the area or season changed meanwhile
  days = days.filter(x => x.date >= fy.from && x.date <= fy.to);
  chart("daily").setOption({
    tooltip: { ...tip(), formatter: p => `<b>${fmtDate(p.data.d.date)}</b><br>${fmt(p.data.d.value, 1)} ${UNIT[state.sensor]}` +
                                         `<br>Normal for this day: ${fmt(p.data.d.normal, 1)}` },
    visualMap: visualMap(niceBins(Math.max(0, ...days.map(x => x.value))), 11),
    calendar: { range: [fy.from, fy.to], top: 22, left: 30, right: 8, bottom: 40, cellSize: ["auto", 15],
                itemStyle: { color: css("--zero"), borderColor: css("--surface"), borderWidth: 2 },
                splitLine: { lineStyle: { color: css("--axis"), width: 1 } },
                dayLabel: { color: css("--ink-2"), firstDay: 1, fontSize: 10, nameMap: ["S", "M", "T", "W", "T", "F", "S"] },
                monthLabel: { color: css("--ink-2"), fontSize: 11 }, yearLabel: { show: false } },
    series: [{ type: "heatmap", coordinateSystem: "calendar",
               data: days.map(x => ({ value: [x.date, x.value], d: x })) }],
  }, true);
  $("dailylede").textContent = `${fy.label}${fy.complete ? "" : " so far"} · one square per day. Single days are noisy; read the weeks.`;
  $("dailytable").innerHTML = "<thead><tr><th>Date</th><th>Burning</th><th>Normal for the day</th></tr></thead><tbody>" +
    days.map(x => `<tr><td>${fmtDate(x.date)}</td><td>${fmt(x.value, 1)}</td><td>${fmt(x.normal, 1)}</td></tr>`).join("") + "</tbody>";
}

/* ---------- regions compared ---------- */
const compareCache = new Map();
let compareRequest = 0;
const compareKey = () => `${state.sensor}|${refParam() || ""}`;
const shortName = r => r.replace(/ Region$/, "");

async function loadCompare() {
  const key = compareKey(), token = ++compareRequest;
  if (state.compare && state.compare.key === key) { renderCompare(); return; }
  $("regioncard").classList.add("loading");
  try {
    if (!compareCache.has(key)) {
      const q = new URLSearchParams({ sensor: state.sensor });
      if (refParam()) q.set("ref", refParam());
      compareCache.set(key, getJSON(`/api/compare?${q}`).catch(e => { compareCache.delete(key); throw e; }));
    }
    const c = await compareCache.get(key);
    if (token !== compareRequest) return;
    state.compare = { ...c, key };
    renderCompare();
    regionLayer.setStyle(regionStyle);   // density / change map modes use these numbers
    renderMapLegend();
  } catch (e) {
    if (token === compareRequest) showError(`Region comparison: ${e.message}`);
  } finally {
    if (token === compareRequest) $("regioncard").classList.remove("loading");
  }
}

/* "29 Nov" -> a date in a nominal July–June fire year, so seasons line up on one axis */
function seasonTime(label) {
  if (!label) return null;
  const [d, mon] = label.split(" ");
  const m = MONTHS.indexOf(mon);
  return Date.UTC(m >= 6 ? 2001 : 2002, m, Number(d));
}
/* Opening a region from the charts or table below the map: confirm it, offer a jump back up */
function pickRegion(name) {
  selectRegion(name);
  toast(`Showing ${shortName(name)}`, { label: "View", fn: scrollToMap });
}

function renderCompare() {
  const c = state.compare;
  if (!c || c.key !== compareKey()) return;
  const color = seriesColor(), sel = selectedRegion().toLowerCase();   // a district highlights its region
  const isSel = r => r.region.toLowerCase() === sel;
  const dim = r => isSel(r) || !sel || sel === "ghana" ? 1 : 0.5;
  const selLabel = { color: css("--ink-2"), fontSize: 11, rich: { sel: { color: css("--ink"), fontWeight: "bold", fontSize: 11 } },
                     formatter: v => v.toLowerCase() === shortName(sel) ? `{sel|${v}}` : v };

  // When each region burns: ordered by usual peak, earliest at the top
  const bySeason = c.regions.filter(r => r.peak).sort((a, b) => seasonTime(a.peak) - seasonTime(b.peak));
  const seasons = chart("regionseasons");
  seasons.off("click");
  seasons.on("click", p => pickRegion(bySeason[p.dataIndex].region));
  seasons.setOption({
    tooltip: { ...tip(), formatter: p => {
      const r = bySeason[p.dataIndex];
      return `<b>${esc(r.region)}</b><br>Fire season ${r.start} – ${r.end}<br>Usual peak ${r.peak}` +
             `<br>${fmt(100 * r.share, 1)}% of Ghana's burning<br><span style='opacity:.65'>Click to open</span>`;
    } },
    legend: legend({ top: 0, left: 0, data: ["Fire season (80% of burning)", "Usual peak"] }),
    grid: { left: 92, right: 12, top: legendTop("regionseasons", ["Fire season (80% of burning)", "Usual peak"]), bottom: 24 },
    xAxis: { type: "time", min: Date.UTC(2001, 8, 1), max: Date.UTC(2002, 5, 1), ...axis(),
             axisLabel: { color: css("--ink-2"), fontSize: 11, formatter: v => MONTHS[new Date(v).getUTCMonth()] }, splitNumber: 9 },
    yAxis: { type: "category", data: bySeason.map(r => shortName(r.region)), inverse: true, ...axis(),
             splitLine: { show: false }, axisLabel: selLabel },
    series: [
      { name: "Fire season (80% of burning)", type: "custom", itemStyle: { color }, cursor: "pointer",
        encode: { x: [1, 2], y: 0 },
        data: bySeason.map((r, i) => [i, seasonTime(r.start), seasonTime(r.end)]),
        renderItem: (params, api) => {
          const s = api.coord([api.value(1), api.value(0)]), e = api.coord([api.value(2), api.value(0)]);
          const h = api.size([0, 1])[1] * 0.5, r = bySeason[params.dataIndex];
          return { type: "rect", shape: { x: s[0], y: s[1] - h / 2, width: Math.max(e[0] - s[0], 3), height: h, r: 4 },
                   style: { fill: color, opacity: dim(r), stroke: isSel(r) ? css("--ink") : null, lineWidth: 2 } };
        } },
      { name: "Usual peak", type: "scatter", symbol: "diamond", symbolSize: 11, z: 5,
        itemStyle: { color: css("--ink"), borderColor: css("--surface"), borderWidth: 1.5 },
        data: bySeason.map((r, i) => [seasonTime(r.peak), i]) },
    ],
  }, true);

  // Change in burning (harmonized only: neither raw sensor spans both periods)
  const change = chart("change");
  change.off("click");
  const hasChange = c.change_periods && c.regions.some(r => r.long_term_change != null);
  const [[a0, a1], [b0, b1]] = c.change_periods || [[0, 0], [0, 0]];
  $("changetitle").textContent = hasChange ? `Change, ${b0}–${yy(b1)} vs ${a0}–${yy(a1)}` : "Change in burning";
  if (!hasChange) {
    change.clear();
    change.setOption({ title: { text: "Long-term change needs the harmonized record:\nneither sensor alone covers 2003–2025.",
                                left: "center", top: "middle", textStyle: { color: css("--ink-2"), fontSize: 13, fontWeight: "normal", lineHeight: 20 } } });
  } else {
    const byChange = c.regions.slice().sort((x, y) => x.long_term_change - y.long_term_change);
    change.on("click", p => pickRegion(byChange[p.dataIndex].region));
    change.setOption({
      tooltip: { ...tip(), formatter: p => {
        const r = byChange[p.dataIndex];
        return `<b>${esc(r.region)}</b><br>${pct(r.long_term_change)} (${fmt(r.early_mean)} → ${fmt(r.late_mean)} a year)` +
               `<br>${fmt(100 * r.share_of_change, 1)}% of Ghana's total drop<br><span style='opacity:.65'>Click to open</span>`;
      } },
      grid: { left: 92, right: 20, top: 34, bottom: 24 },
      xAxis: { type: "value", ...axis(), axisLabel: { color: css("--ink-2"), fontSize: 11, formatter: v => `${Math.round(v * 100)}%` } },
      yAxis: { type: "category", data: byChange.map(r => shortName(r.region)), inverse: true, ...axis(),
               splitLine: { show: false }, axisLine: { show: false }, axisLabel: selLabel },
      series: [{ type: "bar", barWidth: "60%", cursor: "pointer",
                 data: byChange.map(r => ({ value: r.long_term_change, itemStyle: {
                   color, borderRadius: r.long_term_change < 0 ? [4, 0, 0, 4] : [0, 4, 4, 0], opacity: dim(r),
                   borderColor: isSel(r) ? css("--ink") : "transparent", borderWidth: 2 } })),
                 markLine: { symbol: "none", silent: true, lineStyle: { color: css("--axis"), type: "solid" },
                             label: { show: false }, data: [{ xAxis: 0 }] } }],
    }, true);
  }

  // Lede: the two headline facts
  const first = bySeason[0], last = bySeason[bySeason.length - 1];
  let lede = first && last ? `The season moves south as the dry season goes on: ${shortName(first.region)} peaks around ${first.peak}, ` +
    `${shortName(last.region)} around ${last.peak}.` : "How Ghana's 16 regions differ.";
  if (hasChange) {
    const top = c.regions.slice().sort((x, y) => y.share_of_change - x.share_of_change);
    let acc = 0; const names = [];
    for (const r of top) { names.push(shortName(r.region)); acc += r.share_of_change; if (acc >= 0.6) break; }
    lede += ` ${names.slice(0, -1).join(", ")} and ${names[names.length - 1]} make up ${Math.round(100 * acc)}% of the national drop.`;
  }
  $("comparelede").textContent = lede;

  // Table
  $("comparetable").innerHTML = "<thead><tr><th>Region</th><th>Share of Ghana's burning</th><th>Fire season</th><th>Peak</th>" +
    `<th>Change ${hasChange ? `${b0}–${yy(b1)} vs ${a0}–${yy(a1)}` : ""}</th>` +
    "<th>Share of national drop</th><th>Last season vs normal</th></tr></thead><tbody>" +
    c.regions.map(r => `<tr data-region="${esc(r.region)}" class="${isSel(r) ? "sel" : ""}" tabindex="0">` +
      `<td>${esc(shortName(r.region))}</td><td>${fmt(100 * r.share, 1)}%</td>` +
      `<td>${r.start ? `${r.start} – ${r.end}` : "–"}</td><td>${r.peak || "–"}</td>` +
      `<td>${pct(r.long_term_change)}</td><td>${r.share_of_change == null ? "–" : fmt(100 * r.share_of_change, 1) + "%"}</td>` +
      `<td>${pct(r.last_season_vs_normal)}${r.last_season ? ` (${r.last_season})` : ""}</td></tr>`).join("") + "</tbody>";
  $("comparetable").querySelectorAll("tbody tr").forEach(tr => {
    tr.addEventListener("click", () => pickRegion(tr.dataset.region));
    tr.addEventListener("keydown", e => { if (e.key === "Enter") pickRegion(tr.dataset.region); });
  });
}

/* ---------- headline: national findings (always harmonized, all years) ---------- */
async function loadHeadline() {
  try {
    const [g, c] = await Promise.all([
      post("/api/calendar", { region: "Ghana", sensor: "harmonized" }),
      compareCache.get("harmonized|") || (() => { const p = getJSON("/api/compare"); compareCache.set("harmonized|", p); return p; })(),
    ]);
    state.national = g;
    renderHeadline(g, c);
    fillAnimSeasons();
  } catch (e) {
    $("headline").innerHTML = `<div class="stat"><div class="stat-k">Headline figures unavailable</div><div class="stat-l">${esc(e.message)}</div></div>`;
  }
}
function renderHeadline(g, c) {
  const done = g.fire_years.filter(f => f.complete && f.vs_normal != null);
  const last = done[done.length - 1];
  const lowest = done.reduce((a, b) => (b.vs_normal < a.vs_normal ? b : a), done[0]);
  const byPeak = c.regions.filter(r => r.peak).sort((a, b) => seasonTime(a.peak) - seasonTime(b.peak));
  const first = byPeak[0], end = byPeak[byPeak.length - 1];
  const ch = g.long_term_change;
  const cards = [
    ["#seasons", "Last full season", pct(last.vs_normal),
     last === lowest ? `${last.label}, the lowest on record` : `${last.label} vs a normal season`],
    ["#regions", "Fire moves south", `<span class="nw">${first.peak}</span> → <span class="nw">${end.peak}</span>`,
     `${shortName(first.region)} peaks first, ${shortName(end.region)} last`],
    ["#regions", "Long-term change", pct(ch.change), `Burning in ${ch.to[0]}–${yy(ch.to[1])} vs ${ch.from[0]}–${yy(ch.from[1])}`],
    ["#sensors", "One record", `${g.data.first.slice(0, 4)}–${g.data.last.slice(0, 4)}`,
     `MODIS + VIIRS joined with ${fmt(100 * state.meta.harmonization.annual_error, 1)}% yearly error`],
  ];
  $("headline").innerHTML = cards.map(([href, k, v, l], i) =>
    `<a class="stat" href="${href}" style="--n:${i}"><div class="stat-k">${esc(k)}</div><div class="stat-v">${v}</div>` +
    `<div class="stat-l">${esc(l)}</div>${icon("chevron", "i sm go")}</a>`).join("");
}

/* ---------- one record from two sensors ---------- */
function renderSensors() {
  const d = state.data;
  const rows = d.yearly.filter(y => !y.partial);
  const years = rows.map(y => y.year);
  const overlap = rows.filter(y => y.year >= 2013 && y.viirs != null);
  const ratio = overlap.reduce((t, y) => t + y.viirs, 0) / Math.max(1, overlap.reduce((t, y) => t + y.modis, 0));
  const h = state.meta.harmonization, f = Object.values(h.factors_by_month);
  document.querySelectorAll(".gridsize").forEach(el => { el.textContent = h.grid_deg; });
  $("facts").innerHTML = [
    [`${fmt(ratio, 1)}×`, `VIIRS vs MODIS in ${d.area.name}`],
    [`${fmt(Math.min(...f), 1)}–${fmt(Math.max(...f), 1)}×`, "Monthly correction factor"],
    [`${fmt(100 * h.annual_error, 1)}%`, "Yearly error on held-out years"],
  ].map(([v, l]) => `<div class="fact"><div class="fact-v">${v}</div><div class="fact-l">${esc(l)}</div></div>`).join("");
  const line = (name, key, color, width, dash, z) => ({
    name, type: "line", z, symbol: "circle", symbolSize: 7, showSymbol: false,
    lineStyle: { color, width, type: dash ? [6, 4] : "solid" },
    itemStyle: { color, borderColor: css("--surface"), borderWidth: 2 },
    data: rows.map(y => y[key] == null ? null : Math.round(y[key])),
    endLabel: { show: key !== "viirs", formatter: name, color: css("--ink-2"), fontSize: 11 },
  });
  const viirsStart = years.indexOf(2012), terraEnd = years.indexOf(2002);
  chart("yearchart").setOption({
    tooltip: { trigger: "axis", ...tip(), valueFormatter: v => v == null ? "–" : fmt(v),
               axisPointer: { type: "line", lineStyle: { color: css("--axis") } } },
    legend: legend({ top: 0, left: 0 }),
    grid: { left: 48, right: 96, top: legendTop("yearchart", ["MODIS raw", "Harmonized", "VIIRS raw"]), bottom: 24 },
    xAxis: { type: "category", data: years, boundaryGap: false, ...axis(), splitLine: { show: false } },
    yAxis: { type: "value", ...axis(), axisLine: { show: false }, axisLabel: { color: css("--ink-2"), fontSize: 11, formatter: kFmt } },
    series: [
      { ...line("MODIS raw", "modis", css("--s-modis"), 2, false, 2),
        markArea: terraEnd < 0 ? undefined : { silent: true, itemStyle: { color: css("--band") },
          label: { color: css("--ink-2"), fontSize: 11, position: "insideTop" },
          data: [[{ name: "Terra only", xAxis: String(years[0]) }, { xAxis: String(years[terraEnd]) }]] },
        markLine: viirsStart < 0 ? undefined : { symbol: "none", silent: true,
          lineStyle: { color: css("--axis"), type: "solid" },
          label: { formatter: "VIIRS starts", color: css("--ink-2"), fontSize: 11, position: "insideEndTop" },
          data: [{ xAxis: "2012" }] } },
      line("Harmonized", "harmonized", css("--s-harm"), 3, false, 3),
      line("VIIRS raw", "viirs", css("--s-viirs"), 2, true, 4),
    ],
  }, true);
  $("yeartable").innerHTML = "<thead><tr><th>Year</th><th>MODIS raw</th><th>VIIRS raw</th><th>Harmonized</th></tr></thead><tbody>" +
    rows.slice().reverse().map(y => `<tr><td>${y.year}</td><td>${fmt(y.modis)}</td><td>${fmt(y.viirs)}</td><td>${fmt(y.harmonized)}</td></tr>`).join("") +
    "</tbody>";
}

/* ---------- map modes: regions, burning density, change, season playback ---------- */
function setMapMode(mode) {
  state.mapMode = mode;
  setSeg("mapmode", mode);
  $("player").hidden = mode !== "play";
  $("mapnote").hidden = mode === "play";
  if (mode !== "play") { stopPlay(); animLayer.clearLayers(); anim.rects = []; }
  else { hotLayer.clearLayers(); state.month = null; setMapNote(); if (state.data) renderCalendar(); startAnimation(); }
  regionLayer.setStyle(regionStyle);
  showDistricts();
  renderMapLegend();
}
function renderMapLegend() {
  const el = $("maplegend");
  const box = c => `<i style="background:${c}"></i>`;
  const seq = seqColors();
  if (state.mapMode === "density") {
    el.innerHTML = "<b>Burning in a normal season</b>cell-days per 100 km²<br>" +
      [[seq[1], "under 15"], [seq[2], "15–30"], [seq[3], "30–45"], [seq[5], "45–60"], [seq[6], "60+"]]
        .map(([c, l]) => `${box(c)}${l}`).join("<br>");
  } else if (state.mapMode === "change") {
    const ch = state.compare && state.compare.change_periods;
    el.innerHTML = ch ? `<b>Change in burning</b>${ch[1][0]}–${yy(ch[1][1])} vs ${ch[0][0]}–${yy(ch[0][1])}<br>` +
      [[seq[6], "−45% or more"], [seq[5], "−30 to −45%"], [seq[3], "−15 to −30%"], [seq[1], "−5 to −15%"],
       [css("--neutral"), "about the same"], [css("--increase"), "more than +5%"]].map(([c, l]) => `${box(c)}${l}`).join("<br>")
      : "<b>Change in burning</b>Needs the harmonized record.";
  } else if (state.mapMode === "play" && anim.data) {
    el.innerHTML = `<b>Burning this week</b><span class="ramp">Less<span>${animBins().map((_, i) =>
      `<i style="background:${seq[i + 1]}"></i>`).join("")}</span>More</span>`;
  } else { el.hidden = true; return; }
  el.hidden = false;
}

const anim = { data: null, key: null, week: 0, timer: null, rects: [], byWeek: [] };
const animBins = () => [0.03, 0.08, 0.18, 0.35, 0.6, 0.85].map(f => f * anim.data.max);
function fillAnimSeasons() {
  const fys = (state.national ? state.national.fire_years : []).slice().reverse();
  $("animseason").innerHTML = `<option value="normal">Normal season</option>` +
    fys.map(f => `<option value="${f.start_year}">${f.label}${f.complete ? "" : " (so far)"}</option>`).join("");
}
async function startAnimation() {
  const season = $("animseason").value || "normal";
  if (anim.key !== season) {
    $("weeklabel").textContent = "Loading the season…";
    $("playbtn").disabled = true;
    try {
      anim.data = await getJSON(`/api/animation?season=${encodeURIComponent(season)}`);
    } catch (e) { $("weeklabel").textContent = `Could not load: ${e.message}`; return; }
    finally { $("playbtn").disabled = false; }
    anim.key = season;
    anim.byWeek = Array.from({ length: 52 }, () => []);
    anim.data.values.forEach(([w, i, v]) => anim.byWeek[w].push([i, v]));
    anim.week = anim.data.play_from;
  }
  if (state.mapMode !== "play") return;
  const c = anim.data.cell_deg;
  animLayer.clearLayers();
  anim.rects = anim.data.cells.map(([s, w]) =>
    L.rectangle([[s, w], [s + c, w + c]], { stroke: false, fillOpacity: 0, interactive: false }).addTo(animLayer));
  $("weekslider").min = anim.data.play_from; $("weekslider").max = anim.data.play_to;
  renderSpark(); renderMapLegend(); drawWeek(anim.week);
}
function drawWeek(w) {
  if (!anim.data || !anim.rects.length) return;
  anim.week = w;
  const seq = seqColors(), bins = animBins();
  anim.rects.forEach(r => r.setStyle({ fillOpacity: 0 }));
  anim.byWeek[w].forEach(([i, v]) => {
    if (v < bins[0]) return;
    const k = bins.filter(b => v >= b).length;           // 1..6
    anim.rects[i].setStyle({ fillColor: seq[k], fillOpacity: 0.82 });
  });
  const s = $("weekslider"), lo = anim.data.play_from, hi = anim.data.play_to;
  s.value = w;
  s.style.setProperty("--p", `${hi > lo ? (100 * (w - lo)) / (hi - lo) : 0}%`);
  $("weeklabel").innerHTML = `<b>Week of ${esc(anim.data.weeks[w])}</b> <span>· ${fmt(anim.data.national[w])} cell-days across Ghana</span>`;
  chart("spark").setOption({ series: [{ markLine: { data: [{ xAxis: anim.data.weeks[w] }] } }] });
}
function renderSpark() {
  const a = anim.data, range = [];
  for (let w = a.play_from; w <= a.play_to; w++) range.push(w);
  const spark = chart("spark");
  spark.getZr().off("click");
  spark.getZr().on("click", e => {
    const [x] = spark.convertFromPixel({ gridIndex: 0 }, [e.offsetX, e.offsetY]);
    if (x != null) { stopPlay(); drawWeek(range[Math.max(0, Math.min(range.length - 1, Math.round(x)))]); }
  });
  spark.setOption({
    grid: { left: 2, right: 2, top: 4, bottom: 16 },
    tooltip: { trigger: "axis", ...tip(), valueFormatter: v => fmt(v) },
    xAxis: { type: "category", data: range.map(w => a.weeks[w]), boundaryGap: false, ...axis(), axisLine: { show: false },
             axisLabel: { color: css("--ink-2"), fontSize: 10, interval: Math.ceil(range.length / 8) } },
    yAxis: { type: "value", show: false },
    series: [{ type: "line", data: range.map(w => a.national[w]), symbol: "none", name: "Across Ghana", smooth: 0.3,
               lineStyle: { color: css("--s-harm"), width: 2 }, areaStyle: { color: css("--band") },
               markLine: { symbol: "none", silent: true, label: { show: false }, animation: false,
                           lineStyle: { color: css("--ink"), width: 1.5, type: "solid" }, data: [] } }],
  }, true);
}
function setPlayIcon(name, label) {
  setIcon($("playbtn"), name);
  $("playbtn").setAttribute("aria-label", label);
}
function stopPlay() {
  clearInterval(anim.timer); anim.timer = null;
  setPlayIcon("play", "Play");
}
function togglePlay() {
  if (anim.timer) { stopPlay(); return; }
  if (!anim.data) return;
  if (anim.week >= anim.data.play_to) drawWeek(anim.data.play_from);
  setPlayIcon("pause", "Pause");
  anim.timer = setInterval(() => {
    if (anim.week >= anim.data.play_to) { stopPlay(); setPlayIcon("replay", "Replay"); return; }
    drawWeek(anim.week + 1);
  }, 380);
}

/* ---------- outlook: forecast for a period in the next 12 months (harmonized record) ---------- */
const fcState = { data: null, start: null, end: null, request: 0 };
const ymLabel = ym => { const [y, m] = ym.split("-"); return `${MONTHS[+m - 1]} ${y}`; };
function addMonths(ym, n) {
  const [y, m] = ym.split("-").map(Number), t = y * 12 + m - 1 + n;
  return `${Math.floor(t / 12)}-${String(t % 12 + 1).padStart(2, "0")}`;
}
/* First month that can be forecast: the month after the last complete month of data */
function forecastFirstMonth() {
  const last = new Date(state.meta.data.last + "T00:00:00Z");
  const next = new Date(Date.UTC(last.getUTCFullYear(), last.getUTCMonth(), last.getUTCDate() + 1));
  const ym = `${last.getUTCFullYear()}-${String(last.getUTCMonth() + 1).padStart(2, "0")}`;
  return next.getUTCDate() === 1 ? addMonths(ym, 1) : ym;
}

async function loadForecast() {
  const token = ++fcState.request;
  if (!fcState.start) { fcState.start = forecastFirstMonth(); fcState.end = addMonths(fcState.start, 5); }
  ["fccard", "fcpastcard"].forEach(id => $(id).classList.add("loading"));
  try {
    const d = await post("/api/forecast", { ...state.area, ref: refParam(), start: fcState.start, end: fcState.end });
    if (token !== fcState.request) return;
    fcState.data = d;
    renderForecast();
  } catch (e) {
    if (token === fcState.request) showError(`Outlook: ${e.message}`);
  } finally {
    if (token === fcState.request) ["fccard", "fcpastcard"].forEach(id => $(id).classList.remove("loading"));
  }
}

function setForecastPeriod(start, end) {
  fcState.start = start;
  fcState.end = end < start ? start : end;
  loadForecast();
}

function renderForecastControls(d) {
  const months = d.months.map(m => m.month);
  const opts = sel => months.map(m => `<option value="${m}"${m === sel ? " selected" : ""}>${ymLabel(m)}</option>`).join("");
  $("fcfrom").innerHTML = opts(d.period.start);
  $("fcto").innerHTML = opts(d.period.end);
  document.querySelectorAll("#fcquick button").forEach(b => b.setAttribute("aria-pressed",
    String(d.period.start === d.window.first && d.period.months === Number(b.dataset.v))));
}

function renderForecast() {
  const d = fcState.data;
  if (!d) return;
  const t = d.total, tr = d.trend.recent;
  renderForecastControls(d);
  $("fclede").textContent = `Expected burning in ${d.area.name}, ${d.period.label}. Based on its fires since 2001, data to ${fmtDate(d.data_last)}.`;
  const arrow = tr ? ({ downward: "▼", upward: "▲" }[tr.direction] || "●") : "●";
  const trendWord = tr ? (tr.clear ? (tr.direction === "downward" ? "Falling" : "Rising") : "No clear trend") : "–";
  $("fctiles").innerHTML = kpiHTML([
    ["Expected", fmt(t.forecast), t.low == null ? UNIT.harmonized : `Likely ${fmt(t.low)} – ${fmt(t.high)}`],
    ["vs normal", pct(t.vs_normal), `Normal ${fmt(t.normal)} (${d.reference_years[0]}–${d.reference_years[1]})`],
    ["vs a year earlier", pct(t.vs_last_year), `${fmt(t.last_year)} in the same months`],
    ["Trend", tr ? `<span class="icon" aria-hidden="true">${arrow}</span>${trendWord}` : "–",
     tr ? `${pct(tr.per_year)} a season, last ${tr.seasons} seasons` : "Not enough seasons"],
  ]);
  // The tiles carry the numbers; keep only warnings and the accuracy note
  $("fcsummary").innerHTML = d.summary.filter(s => /^(There is very little fire|Tested on past years)/.test(s))
    .map(s => `<li>${esc(s)}</li>`).join("");
  renderForecastChart(d);
  renderForecastPast(d);
}

function renderForecastChart(d) {
  const harm = css("--s-harm"), hist = d.history, fut = d.months, nH = hist.length;
  const labels = [...hist.map(h => h.label), ...fut.map(m => m.label)];
  const blank = n => Array(n).fill(null);
  const observed = [...hist.map(h => h.value), ...blank(fut.length)];
  // The forecast line starts from the last observed month so the two lines join
  const forecast = [...blank(nH - 1), hist[nH - 1].value, ...fut.map(m => m.forecast)];
  const low = [...blank(nH), ...fut.map(m => m.low)];
  const band = [...blank(nH), ...fut.map(m => m.high == null ? null : m.high - m.low)];
  const normal = [...hist.map(h => h.normal), ...fut.map(m => m.normal)];
  const sel = fut.filter(m => m.in_period);
  chart("fcchart").setOption({
    // Line icons (not squares): Observed and Forecast share a colour and differ only by dash
    legend: legend({ top: 0, left: 0, icon: null, itemWidth: 24, itemHeight: 10, data: ["Observed", "Forecast", "Likely range", "Normal"] }),
    tooltip: { ...tip(), trigger: "axis", axisPointer: { type: "line", lineStyle: { color: css("--axis") } },
      formatter: ps => {
        const i = ps[0].dataIndex;
        if (i < nH) {
          const h = hist[i];
          return `<b>${h.label}</b><br>Observed: ${fmt(h.value)}<br>Normal: ${fmt(h.normal)}`;
        }
        const m = fut[i - nH];
        return `<b>${m.label}</b>${m.in_period ? " (in your period)" : ""}<br>Forecast: ${fmt(m.forecast)}` +
          (m.low != null ? `<br>Likely range: ${fmt(m.low)} – ${fmt(m.high)}` : "") +
          `<br>Normal: ${fmt(m.normal)}` +
          (m.last_year != null ? `<br>A year earlier: ${fmt(m.last_year)}` : "") +
          (m.observed_so_far != null ? `<br>Observed so far this month: ${fmt(m.observed_so_far)}` : "");
      } },
    grid: { left: 44, right: 12, top: legendTop("fcchart", ["Observed", "Forecast", "Likely range", "Normal"]), bottom: 24 },
    xAxis: { type: "category", data: labels, boundaryGap: false, ...axis(), splitLine: { show: false },
             axisLabel: { color: css("--ink-2"), fontSize: 11, hideOverlap: true,
                          formatter: v => v.startsWith("Jan") ? `Jan ’${v.slice(-2)}` : v.slice(0, 3) } },
    yAxis: { type: "value", min: 0, ...axis(), axisLine: { show: false }, axisLabel: { color: css("--ink-2"), fontSize: 11, formatter: kFmt } },
    series: [
      { name: "Likely range base", type: "line", data: low, stack: "band", symbol: "none", silent: true,
        lineStyle: { opacity: 0 } },
      { name: "Likely range", type: "line", data: band, stack: "band", symbol: "none", silent: true,
        lineStyle: { opacity: 0 }, areaStyle: { color: harm, opacity: 0.2 }, itemStyle: { color: harm, opacity: 0.35 } },
      { name: "Normal", type: "line", data: normal, symbol: "none", silent: true,
        lineStyle: { color: css("--normal"), width: 2, type: [4, 4] }, itemStyle: { color: css("--normal") } },
      { name: "Observed", type: "line", data: observed, symbol: "circle", symbolSize: 8, showSymbol: false,
        lineStyle: { color: harm, width: 2 }, itemStyle: { color: harm } },
      { name: "Forecast", type: "line", data: forecast, symbol: "circle", symbolSize: 8, showSymbol: false,
        lineStyle: { color: harm, width: 2, type: [6, 4] }, itemStyle: { color: harm },
        markArea: sel.length ? { silent: true, itemStyle: { color: css("--wash") },
          label: { show: true, position: "insideTop", color: css("--ink-2"), fontSize: 11, formatter: "Your period" },
          data: [[{ xAxis: sel[0].label }, { xAxis: sel[sel.length - 1].label }]] } : undefined },
    ],
  }, true);
  $("fctable").innerHTML = "<thead><tr><th>Month</th><th>Observed</th><th>Forecast</th><th>Likely range</th><th>Normal</th><th>A year earlier</th></tr></thead><tbody>" +
    hist.map(h => `<tr><td>${h.label}</td><td>${fmt(h.value)}</td><td>–</td><td>–</td><td>${fmt(h.normal)}</td><td>–</td></tr>`).join("") +
    fut.map(m => `<tr><td>${m.label}${m.in_period ? " (your period)" : ""}</td>` +
      `<td>${m.observed_so_far != null ? fmt(m.observed_so_far) + " so far" : "–"}</td>` +
      `<td>${fmt(m.forecast)}</td><td>${m.low == null ? "–" : `${fmt(m.low)} – ${fmt(m.high)}`}</td>` +
      `<td>${fmt(m.normal)}</td><td>${fmt(m.last_year)}</td></tr>`).join("") + "</tbody>";
}

function renderForecastPast(d) {
  const past = d.past, harm = css("--s-harm"), ink = css("--ink");
  const yr = iso => iso.slice(0, 4);
  const cat = p => yr(p.from) === yr(p.to) ? yr(p.from) : `${yr(p.from)}/${yr(p.to).slice(2)}`;
  const inside = past.filter(p => p.inside).length;
  const made = past.length ? MONTHS[new Date(past[0].made + "T00:00:00Z").getUTCMonth()] : "";
  $("fcpastlede").textContent = past.length
    ? `The same forecast made at the end of ${made} in earlier years, against what happened. ${inside} of ${past.length} landed in the likely range.`
    : "Not enough history yet to test this forecast.";
  chart("fcpast").setOption({
    legend: legend({ top: 0, left: 0, data: ["Likely range", "Forecast", "What happened"] }),
    tooltip: { ...tip(), trigger: "axis", axisPointer: { type: "shadow", shadowStyle: { color: css("--wash") } },
      formatter: ps => {
        const p = past[ps[0].dataIndex];
        return `<b>${esc(p.label)}</b><br>Forecast: ${fmt(p.forecast)} (likely ${fmt(p.low)} – ${fmt(p.high)})` +
          `<br>What happened: ${fmt(p.actual)}<br>${p.inside ? "Inside the range" : "Outside the range"}`;
      } },
    grid: { left: 44, right: 12, top: legendTop("fcpast", ["Likely range", "Forecast", "What happened"]), bottom: 24 },
    xAxis: { type: "category", data: past.map(cat), ...axis(), splitLine: { show: false } },
    yAxis: { type: "value", min: 0, ...axis(), axisLine: { show: false }, axisLabel: { color: css("--ink-2"), fontSize: 11, formatter: kFmt } },
    series: [
      { name: "base", type: "bar", stack: "r", data: past.map(p => p.low), itemStyle: { color: "transparent" },
        silent: true, barWidth: "46%" },
      { name: "Likely range", type: "bar", stack: "r", data: past.map(p => p.high - p.low),
        itemStyle: { color: harm, opacity: 0.3, borderRadius: 4 } },
      { name: "Forecast", type: "scatter", data: past.map(p => p.forecast), symbol: "rect", symbolSize: [22, 3],
        itemStyle: { color: harm, opacity: 1 }, z: 3 },
      { name: "What happened", type: "scatter", data: past.map(p => p.actual), symbol: "circle", symbolSize: 10,
        itemStyle: { color: ink, borderColor: css("--surface"), borderWidth: 2 }, z: 4 },
    ],
  }, true);
  $("fcpasttable").innerHTML = "<thead><tr><th>Period</th><th>Forecast</th><th>Likely range</th><th>What happened</th><th>Inside?</th></tr></thead><tbody>" +
    past.slice().reverse().map(p => `<tr><td>${esc(p.label)}</td><td>${fmt(p.forecast)}</td>` +
      `<td>${fmt(p.low)} – ${fmt(p.high)}</td><td>${fmt(p.actual)}</td><td>${p.inside ? "Yes" : "No"}</td></tr>`).join("") + "</tbody>";
}

/* ---------- small UI helpers ---------- */
function toast(msg, action) {
  const t = $("toast"), a = $("toastaction");
  $("toasttext").textContent = msg;
  a.hidden = !action;
  a.onclick = action ? () => { t.classList.remove("show"); action.fn(); } : null;
  if (action) a.textContent = action.label;
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), action ? 4500 : 2200);
}
function saveChart(id) {
  const c = charts[id];
  if (!c) return;
  const a = document.createElement("a");
  a.href = c.getDataURL({ pixelRatio: 2, backgroundColor: css("--surface") });
  const area = state.data ? state.data.area.name.toLowerCase().replace(/[^a-z0-9]+/g, "_") : "ghana";
  a.download = `ghana_fire_calendar_${id}_${area}.png`;
  a.click();
  toast("Image saved");
}
function toggleTable(btn) {
  const viz = btn.closest(".card").querySelector(".viz");
  const on = viz.classList.toggle("as-table");
  btn.setAttribute("aria-pressed", String(on));
  btn.title = on ? "Show as chart" : "Show as table";
  btn.setAttribute("aria-label", btn.title);
}
function watchSections() {
  const links = [...document.querySelectorAll(".sections a")];
  const byId = Object.fromEntries(links.map(a => [a.getAttribute("href").slice(1), a]));
  const io = new IntersectionObserver(entries => entries.forEach(e => {
    if (e.isIntersecting) { links.forEach(a => a.classList.remove("active")); byId[e.target.id]?.classList.add("active"); }
  }), { rootMargin: "-35% 0px -60% 0px" });
  Object.keys(byId).forEach(id => $(id) && io.observe($(id)));
}
/* Cards fade up as they scroll into view (CSS skips this under reduced motion) */
function watchReveal() {
  const els = document.querySelectorAll(".reveal");
  if (!("IntersectionObserver" in window)) { els.forEach(e => e.classList.add("in")); return; }
  const io = new IntersectionObserver(entries => entries.forEach(e => {
    if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
  }), { rootMargin: "0px 0px -6% 0px", threshold: 0.04 });
  els.forEach(e => io.observe(e));
}

/* ---------- export ---------- */
async function exportCsv() {
  const btn = $("export");
  btn.disabled = true;
  try {
    const blob = await post("/api/export.csv", areaBody());
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${state.data.area.name.toLowerCase().replace(/[^a-z0-9]+/g, "_")}_${state.sensor}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    toast("CSV downloaded");
  } catch (e) { showError(e.message); }
  finally { btn.disabled = false; }
}

/* ---------- start ---------- */
function wireUI() {
  $("region").addEventListener("change", e => { if (e.target.value !== "__drawn") selectRegion(e.target.value); });
  wireSeg("sensor", "sensor", refresh);
  wireSeg("ref", "ref", refresh);
  wireSeg("layout", "layout", () => { if (state.data) renderCalendar(); });
  $("season").addEventListener("change", e => openSeason(e.target.value));
  $("export").addEventListener("click", exportCsv);
  $("fcfrom").addEventListener("change", e => setForecastPeriod(e.target.value, fcState.end));
  $("fcto").addEventListener("change", e =>
    setForecastPeriod(e.target.value < fcState.start ? e.target.value : fcState.start, e.target.value));
  document.querySelectorAll("#fcquick button").forEach(b => b.addEventListener("click", () => {
    const first = fcState.data ? fcState.data.window.first : forecastFirstMonth();
    setForecastPeriod(first, addMonths(first, Number(b.dataset.v) - 1));
  }));
  document.querySelectorAll("#mapmode button").forEach(b =>
    b.addEventListener("click", () => { if (state.mapMode !== b.dataset.v) setMapMode(b.dataset.v); }));
  $("zoomin").addEventListener("click", () => map.zoomIn());
  $("zoomout").addEventListener("click", () => map.zoomOut());
  $("fitarea").addEventListener("click", zoomToArea);
  $("drawrect").addEventListener("click", () => toggleDraw("drawrect"));
  $("drawpoly").addEventListener("click", () => toggleDraw("drawpoly"));
  $("cleardraw").addEventListener("click", () => selectRegion("Ghana"));
  $("mapnote").addEventListener("click", e => { if (e.target.closest("#clearfires")) clearMonth(); });
  $("playbtn").addEventListener("click", togglePlay);
  $("weekslider").addEventListener("input", e => { stopPlay(); drawWeek(Number(e.target.value)); });
  $("animseason").addEventListener("change", () => { stopPlay(); anim.key = null; startAnimation(); });
  $("copylink").addEventListener("click", () => navigator.clipboard.writeText(location.href)
    .then(() => toast("Link copied")).catch(() => toast(location.href)));
  $("bannerclose").addEventListener("click", hideError);
  document.querySelectorAll("[data-save]").forEach(b => b.addEventListener("click", () => saveChart(b.dataset.save)));
  document.querySelectorAll("[data-table]").forEach(b => b.addEventListener("click", () => toggleTable(b)));
  chart("cal").on("click", onCalendarClick);
  chart("totals").on("click", p => openSeason(totalsSeasons(state.data)[p.dataIndex].label));
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    applyTileTheme(); regionLayer.setStyle(regionStyle); districtLayer.setStyle(districtStyle); renderAll(); renderDaily(); renderMapLegend();
    if (state.mapMode === "play" && anim.data) { renderSpark(); drawWeek(anim.week); }
  });
}

async function init() {
  watchReveal();
  watchSections();
  // Charts draw text on a canvas, so wait (briefly) for the web font before the first one
  const fonts = Promise.race([document.fonts ? document.fonts.ready : null, new Promise(r => setTimeout(r, 1500))]);
  try {
    const [meta, regions, districts] = await Promise.all([getJSON("/api/meta"), getJSON("/api/regions"),
                                                          getJSON("/api/districts"), fonts]);
    state.meta = meta;
    $("span").textContent = `${meta.data.first.slice(0, 4)} to ${fmtDate(meta.data.last)}`;
    meta.districts.forEach(d => districtInfo.set(d.name.toLowerCase(), d));
    // Picker: Ghana, then each region followed by its districts
    const opt = (v, label) => `<option value="${esc(v)}">${esc(label)}</option>`;
    $("region").innerHTML = opt("Ghana", "Ghana") + meta.regions.filter(r => r !== "Ghana").map(r =>
      `<optgroup label="${esc(r)}">${opt(r, `All of ${r}`)}` +
      meta.districts.filter(d => d.region === r).map(d => opt(d.name, d.label)).join("") + "</optgroup>").join("") +
      `<option value="__drawn" id="drawnopt" disabled>Drawn area</option>`;
    readHash();
    const want = (state.area.region || "").toLowerCase();
    state.area.region = meta.regions.find(r => r.toLowerCase() === want) ||
      (districtInfo.get(want) || {}).name || "Ghana";
    initMap(regions, districts);
    showDistricts();
    syncControls();
    setMapNote();
    wireUI();
    loadHeadline();
    await refresh();
  } catch (e) {
    showError(`Could not start: ${e.message}`);
  }
}
init();
