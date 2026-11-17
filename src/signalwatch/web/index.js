const $ = (id) => document.getElementById(id);
let state = { src: "", q: "", items: [] };
const fmt = (ms) => {
  if (!ms) return "—";
  const d = new Date(ms), now = Date.now();
  const diff = (now - ms) / 1000;
  const p = (n) => String(n).padStart(2, "0");
  const t = `${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff/60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff/3600)} 小时前 · ${t}`;
  return t;
};
const esc = (s) => (s || "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const safeHttpUrl = (value) => {
  try {
    const u = new URL(value || "", location.origin);
    return (u.protocol === "http:" || u.protocol === "https:") ? u.href : "";
  } catch (e) { return ""; }
};
const cookieValue = (name) => {
  const prefix = name + "=";
  const part = document.cookie.split("; ").find((v) => v.startsWith(prefix));
  return part ? decodeURIComponent(part.slice(prefix.length)) : "";
};
async function authedFetch(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  const csrf = cookieValue("sw_csrf");
  if (csrf) headers["X-CSRF-Token"] = csrf;
  return fetch(path, Object.assign({}, opts, { headers }));
}

async function loadMeta(){
  try{
    const h = await (await fetch("/api/health")).json();
    $("ver").textContent = "v" + h.version;
  }catch(e){}
  try{
    const r = await fetch("/api/sources"); const j = await r.json();
    const bad = j.sources.filter(s => s.status !== "ok");
    $("degraded").style.display = bad.length ? "block" : "none";
    if (bad.length) $("degraded").textContent =
      "源异常：" + bad.map(s => `${s.name}（连续失败 ${s.fail_count} 次：${s.last_error||"?"}）`).join("；");
    const chips = j.sources.map(s => `<button class="src-chip ${state.src===s.name?'on':''}" data-src="${s.name}">${s.name}${s.status!=="ok"?" !":""}</button>`).join("");
    $("chips").innerHTML = `<button class="src-chip ${state.src===''?'on':''}" data-src="">全部</button>` + chips;
    bindChips();
    const last = j.runs[0];
    $("last-run").textContent = last ? `上次 ${last.source} · ${fmt(last.started_at)} · ${last.ok?"成功":"失败"}` : "还没跑过";
  }catch(e){}
}
async function loadItems(){
  const u = new URL("/api/items", location.origin);
  u.searchParams.set("limit", "100");
  if (state.src) u.searchParams.set("source", state.src);
  if (state.q) u.searchParams.set("q", state.q);
  const r = await fetch(u); const j = await r.json();
  state.items = j.items;
  $("list").innerHTML = j.items.length ? j.items.map(it => `
    <div class="item ${it.version>1?'rev':''}">
      <div class="top">
        <span class="src">${esc(it.source)}</span>
        <span>${fmt(it.published_at)}</span>
        ${it.version>1?`<span class="rev-tag">已修订 v${it.version}</span>`:""}
        <span class="spacer flex1"></span>
        <span>${esc(it.kind)}</span>
      </div>
      <div class="title">
        ${it.sec_code?`<span class="sec">${esc(it.sec_code)} ${esc(it.sec_name||"")}</span>`:""}
        ${safeHttpUrl(it.url)?`<a href="${esc(safeHttpUrl(it.url))}" target="_blank" rel="noopener noreferrer nofollow">${esc(it.title)}</a>`:esc(it.title)}
      </div>
      ${it.summary?`<div class="sum">${esc(it.summary.slice(0,160))}</div>`:""}
      ${it.body_chars?`<button class="more" data-text="${it.id}">看正文（${it.body_chars} 字）</button>`:""}
      <button class="mark read ${it.read?'on':''}" data-id="${it.id}" title="标记已读">${it.read?'✓已读':'未读'}</button>
      <button class="mark star ${it.star?'on':''}" data-id="${it.id}" title="收藏">${it.star?'★':'☆'}</button>
      <div class="body" id="body-${it.id}"></div>
    </div>`).join("") : `<div class="empty">没有匹配的条目。</div>`;
  document.querySelectorAll("button.more").forEach(b => {
    b.onclick = async () => {
      const box = document.getElementById("body-" + b.dataset.text);
      if (box.dataset.open === "1") { box.dataset.open = "0"; box.textContent = ""; return; }
      box.dataset.open = "1";
      box.textContent = "正在取…";
      try {
        const r = await authedFetch(`/api/items/${b.dataset.text}/text`);
        const j = await r.json();
        if (r.status === 401) { box.textContent = "请先到管理台登录，再看正文。"; return; }
        if (!r.ok) { box.textContent = j.detail || ("取正文失败：HTTP " + r.status); return; }
        box.textContent = j.text || "（这条没抠到正文）";
      } catch (e) { box.textContent = "取正文失败：" + e; }
    };
  });
  document.querySelectorAll("button.read").forEach(b => {
    b.onclick = async () => {
      const r = await authedFetch(`/api/items/${b.dataset.id}/read${b.classList.contains("on")?"?read=false":""}`, {method:"POST"});
      if (r.status === 401) { location.href = "/admin"; return; }
      if (r.ok) { b.classList.toggle("on"); b.textContent = b.classList.contains("on") ? "✓已读" : "未读"; }
    };
  });
  document.querySelectorAll("button.star").forEach(b => {
    b.onclick = async () => {
      const r = await authedFetch(`/api/items/${b.dataset.id}/star`, {method:"POST"});
      if (r.status === 401) { location.href = "/admin"; return; }
      if (r.ok) { b.classList.toggle("on"); b.textContent = b.classList.contains("on") ? "★" : "☆"; }
    };
  });
}
async function loadStats(){
  try{ const j = await (await fetch("/api/stats")).json();
    $("n-items").textContent = j.items; $("n-today").textContent = j.today; $("n-rev").textContent = j.revised;
    $("status").textContent = "运行中"; $("status").className = "badge ok";
  }catch(e){ $("status").textContent = "服务未响应"; $("status").className = "badge bad"; }
}
function bindChips(){
  document.querySelectorAll(".src-chip").forEach(b => b.onclick = () => {
    state.src = b.dataset.src; document.querySelectorAll(".src-chip").forEach(x=>x.classList.remove("on"));
    b.classList.add("on"); loadItems();
  });
}
let timer;
$("q").oninput = (e) => { clearTimeout(timer); timer = setTimeout(()=>{ state.q = e.target.value.trim(); loadItems(); }, 220); };
$("refresh").onclick = () => { loadMeta(); loadItems(); loadStats(); };
$("crawl").onclick = async () => {
  $("crawl").textContent = "抓取中…";
  try {
    const r = await authedFetch("/api/crawl", {method:"POST"});
    if (r.status === 401) { location.href = "/admin"; return; }
    if (!r.ok) {
      const j = await r.json();
      $("crawl").textContent = j.detail || "抓取失败";
      setTimeout(() => { $("crawl").textContent = "立刻抓一轮"; }, 2500);
      return;
    }
  } catch (e) {
    $("crawl").textContent = "抓取失败";
    setTimeout(() => { $("crawl").textContent = "立刻抓一轮"; }, 2500);
    return;
  }
  setTimeout(()=>{ $("crawl").textContent = "立刻抓一轮"; loadMeta(); loadItems(); loadStats(); }, 4000);
};
(async function init(){
  loadMeta(); loadItems(); loadStats();
  setInterval(()=>{ loadMeta(); loadItems(); loadStats(); }, 60000);

// 服务端推计数：有新条目立刻刷，不用等那 60 秒
let lastTotal = null;
function live(){
  try{
    const es = new EventSource("/api/events/stats");
    es.onmessage = (ev) => {
      let s; try { s = JSON.parse(ev.data); } catch(e){ return; }
      if (lastTotal !== null && s.items > lastTotal) { loadItems(); loadStats(); }
      lastTotal = s.items;
    };
    es.onerror = () => { es.close(); setTimeout(live, 5000); };
  }catch(e){}
}
live();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(()=>{});
})();
