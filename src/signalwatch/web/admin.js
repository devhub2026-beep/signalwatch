const $ = (id) => document.getElementById(id);
let TOKEN = localStorage.getItem("sw_token") || "";

async function api(path, opts = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  if (TOKEN) headers["Authorization"] = "Bearer " + TOKEN;
  const csrfPart = document.cookie.split("; ").find((v) => v.startsWith("sw_csrf="));
  if (csrfPart) headers["X-CSRF-Token"] = decodeURIComponent(csrfPart.slice("sw_csrf=".length));
  const res = await fetch(path, Object.assign({}, opts, { headers }));
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = { detail: text }; }
  if (!res.ok) {
    let detail = data && data.detail;
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg || JSON.stringify(d)).join("；");
    throw new Error(detail || ("HTTP " + res.status));
  }
  return data;
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function t(ms) {
  if (!ms) return "-";
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

async function boot() {
  const h = await api("/api/health");
  $("ver").textContent = "v" + h.version;
  if (!TOKEN) return;
  try {
    const me = await api("/api/auth/me");
    signedIn(me.user);
  } catch (e) {
    TOKEN = "";
    localStorage.removeItem("sw_token");
  }
}

function signedIn(user) {
  $("who").textContent = `${user.username} · ${user.role}`;
  $("login").classList.add("hide");
  $("app").classList.remove("hide");
  $("logout").classList.remove("hide");
  $("mypwd").classList.remove("hide");
  if (user.must_change_password) {
    $("who").textContent += " · 请先修改默认口令";
    document.querySelectorAll(".tab").forEach((el) => {
      if (el.dataset.t !== "me") el.classList.add("hide");
    });
    document.querySelector('.tab[data-t="me"]').click();
    return;
  }
  loadOverview();
}

$("go").onclick = async () => {
  $("lerr").textContent = "";
  try {
    const r = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ username: $("u").value.trim(), password: $("p").value }) });
    TOKEN = r.token;
    localStorage.setItem("sw_token", TOKEN);
    signedIn(r.user);
  } catch (e) { $("lerr").textContent = e.message; }
};
$("p").onkeydown = (e) => { if (e.key === "Enter") $("go").click(); };
$("logout").onclick = async () => {
  try { await api("/api/auth/logout", { method: "POST" }); } catch (e) {}
  TOKEN = ""; localStorage.removeItem("sw_token"); location.reload();
};
$("mypwd").onclick = async () => {
  const old = prompt("现在的口令");
  if (!old) return;
  const next = prompt("新口令（≥6 位）");
  if (!next) return;
  try {
    await api("/api/auth/password", { method: "POST", body: JSON.stringify({ old, new: next }) });
    alert("口令已改，需要重新登录");
    TOKEN = ""; localStorage.removeItem("sw_token"); location.reload();
  } catch (e) { alert(e.message); }
};

const TABS = ["overview", "items", "categories", "jobs", "report", "users", "sources",
              "rules", "settings", "audit", "me"];
const LOADERS = {
  overview: loadOverview, items: loadItems, categories: loadCategories, jobs: loadJobs,
  report: loadReport, users: loadUsers, sources: loadSources, settings: loadSettings, audit: loadAudit,
  rules: loadRules, me: loadMe,
};

document.querySelectorAll(".tab").forEach((el) => {
  el.onclick = () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("on"));
    el.classList.add("on");
    TABS.forEach((k) => $("t-" + k).classList.add("hide"));
    $("t-" + el.dataset.t).classList.remove("hide");
    (LOADERS[el.dataset.t] || (() => {}))();
  };
});

async function loadOverview() {
  const r = await api("/api/report?days=7");
  const sum = r.daily.reduce((a, d) => a + d.items, 0);
  $("kpi").innerHTML = `
    <div><span class="muted">7 天新增</span><b>${sum}</b></div>
    <div><span class="muted">修订条目</span><b>${r.revisions}</b></div>
    <div><span class="muted">在跑源</span><b>${r.sources}</b></div>
    <div><span class="muted">分类</span><b>${r.by_category.map((c) => c.category).join(" / ")}</b></div>`;
  $("daily").innerHTML = `<tr><th>日期</th><th>新增</th><th>修订</th></tr>` +
    r.daily.map((d) => `<tr><td>${d.day}</td><td>${d.items}</td><td>${d.revised}</td></tr>`).join("") +
    `<tr><th>源</th><th>条目</th><th>最近一条</th></tr>` +
    r.by_source.map((s) => `<tr><td>${esc(s.source)}</td><td>${s.items}</td><td>${t(s.latest)}</td></tr>`).join("");
}

async function loadUsers() {
  const r = await api("/api/users");
  const roles = ["viewer", "auditor", "analyst", "admin"];
  $("users").innerHTML = `<tr><th>ID</th><th>用户名</th><th>邮箱</th><th>角色</th><th>状态</th><th>最后登录</th><th>操作</th></tr>` +
    r.users.map((u) => `<tr>
      <td>${u.id}</td><td>${esc(u.username)}</td>
      <td><input data-email="${esc(u.username)}" value="${esc(u.email || "")}" placeholder="填了才收摘要" class="email-input"></td>
      <td><select data-role="${esc(u.username)}">
        ${roles.map((x) => `<option value="${x}"${x === u.role ? " selected" : ""}>${x}</option>`).join("")}
      </select></td>
      <td>${u.enabled ? '<span class="pill text-green">启用</span>' : '<span class="pill text-red">停用</span>'}</td>
      <td class="muted">${t(u.last_login_at)}</td>
      <td class="row">
        <button class="ghost" data-email-save="${esc(u.username)}">存邮箱</button>
        <button class="ghost" data-toggle="${esc(u.username)}" data-on="${u.enabled ? 1 : 0}">${u.enabled ? "停用" : "启用"}</button>
        <button class="ghost" data-pwd="${esc(u.username)}">改口令</button>
        <button class="danger" data-del="${esc(u.username)}">删除</button>
      </td></tr>`).join("");

  $("users").querySelectorAll("[data-email-save]").forEach((el) => {
    el.onclick = async () => {
      const name = el.dataset.emailSave;
      const box = $("users").querySelector(`[data-email="${name}"]`);
      try {
        await api(`/api/users/${encodeURIComponent(name)}`, { method: "PATCH", body: JSON.stringify({ email: box.value.trim() }) });
        loadUsers();
      } catch (e) { alert(e.message); }
    };
  });

  $("users").querySelectorAll("[data-role]").forEach((el) => {
    el.onchange = async () => {
      try { await api(`/api/users/${encodeURIComponent(el.dataset.role)}`, { method: "PATCH", body: JSON.stringify({ role: el.value }) }); loadUsers(); }
      catch (e) { alert(e.message); }
    };
  });
  $("users").querySelectorAll("[data-toggle]").forEach((el) => {
    el.onclick = async () => {
      try { await api(`/api/users/${encodeURIComponent(el.dataset.toggle)}`, { method: "PATCH", body: JSON.stringify({ enabled: el.dataset.on !== "1" }) }); loadUsers(); }
      catch (e) { alert(e.message); }
    };
  });
  $("users").querySelectorAll("[data-pwd]").forEach((el) => {
    el.onclick = async () => {
      const pw = prompt(`给 ${el.dataset.pwd} 设新口令（≥6 位）`);
      if (!pw) return;
      try { await api(`/api/users/${encodeURIComponent(el.dataset.pwd)}`, { method: "PATCH", body: JSON.stringify({ password: pw }) }); alert("已改"); }
      catch (e) { alert(e.message); }
    };
  });
  $("users").querySelectorAll("[data-del]").forEach((el) => {
    el.onclick = async () => {
      if (!confirm(`删除 ${el.dataset.del}？`)) return;
      try { await api(`/api/users/${encodeURIComponent(el.dataset.del)}`, { method: "DELETE" }); loadUsers(); }
      catch (e) { alert(e.message); }
    };
  });
}

$("adduser").onclick = async () => {
  $("uerr").textContent = "";
  try {
    await api("/api/users", { method: "POST", body: JSON.stringify({ username: $("nu").value.trim(), password: $("np").value, role: $("nr").value }) });
    $("nu").value = ""; $("np").value = "";
    loadUsers();
  } catch (e) { $("uerr").textContent = e.message; }
};

async function loadSources() {
  const r = await api("/api/sources");
  $("sources").innerHTML = `<tr><th>源</th><th>状态</th><th>间隔(秒)</th><th>上次运行</th><th>失败数</th><th>操作</th></tr>` +
    r.sources.map((s) => `<tr>
      <td>${esc(s.name)}</td>
      <td>${s.paused ? '<span class="pill text-warn">已暂停</span>' : '<span class="pill text-green">运行中</span>'}</td>
      <td><input type="number" min="60" value="${s.interval_seconds}" data-iv="${esc(s.name)}" / class="w90"></td>
      <td class="muted">${t(s.last_run_at)}</td>
      <td>${s.fail_count || 0}</td>
      <td class="row">
        <button class="ghost" data-save="${esc(s.name)}">保存间隔</button>
        <button class="ghost" data-pause="${esc(s.name)}" data-p="${s.paused ? 1 : 0}">${s.paused ? "恢复" : "暂停"}</button>
      </td></tr>`).join("");
  $("sources").querySelectorAll("[data-save]").forEach((el) => {
    el.onclick = async () => {
      const iv = document.querySelector(`[data-iv="${CSS.escape(el.dataset.save)}"]`).value;
      try { await api(`/api/sources/${encodeURIComponent(el.dataset.save)}`, { method: "PATCH", body: JSON.stringify({ interval_seconds: Number(iv) }) }); loadSources(); }
      catch (e) { alert(e.message); }
    };
  });
  $("sources").querySelectorAll("[data-pause]").forEach((el) => {
    el.onclick = async () => {
      try { await api(`/api/sources/${encodeURIComponent(el.dataset.pause)}`, { method: "PATCH", body: JSON.stringify({ enabled: el.dataset.p !== "1" }) }); loadSources(); }
      catch (e) { alert(e.message); }
    };
  });
}

async function loadRules() {
  const r = await api("/api/admin/rules");
  $("rules").querySelector("tbody").innerHTML = r.rules.map((x) => `<tr>
    <td class="muted">${x.id}</td><td><b>${esc(x.keyword)}</b></td>
    <td>${esc(x.category || "不限")}</td><td>${esc(x.source || "不限")}</td>
    <td>${x.enabled ? '<span class="ok">启用</span>' : '<span class="err">已停用</span>'}</td>
    <td class="row">
      <button class="ghost" data-rt="${x.enabled ? "off" : "on"}" data-id="${x.id}">${x.enabled ? "停用" : "启用"}</button>
      <button class="danger" data-rd="${x.id}">删除</button>
    </td></tr>`).join("") || '<tr><td colspan="6" class="muted">还没有订阅词</td></tr>';
  $("rules").querySelectorAll("[data-rt]").forEach((b) => {
    b.onclick = async () => {
      await api("/api/admin/rules/" + b.dataset.id, { method: "PATCH", body: JSON.stringify({ enabled: b.dataset.rt === "on" }) });
      loadRules();
    };
  });
  $("rules").querySelectorAll("[data-rd]").forEach((b) => {
    b.onclick = async () => {
      if (!confirm("删除这个订阅词？")) return;
      await api("/api/admin/rules/" + b.dataset.rd, { method: "DELETE" });
      loadRules();
    };
  });
}

async function loadMe() {
  const me = await api("/api/auth/me");
  const u = me.user || me;
  $("me_name").textContent = u.username || "—";
  $("me_role").textContent = ({ admin: "管理员", analyst: "分析员", auditor: "审计员", viewer: "只读" })[u.role] || u.role || "—";
  $("me_last").textContent = u.last_login_at ? t(u.last_login_at) : "—";
  try {
    const r = await api("/api/admin/rules");
    $("me_rules").textContent = r.rules.length + " 条";
  } catch (e) {
    $("me_rules").textContent = "—";
  }
}

$("r_add").onclick = async () => {
  $("r_err").textContent = "";
  try {
    await api("/api/admin/rules", {
      method: "POST",
      body: JSON.stringify({ keyword: $("r_kw").value.trim(), category: $("r_cat").value.trim(),
                             source: $("r_src").value.trim() }),
    });
    $("r_kw").value = $("r_cat").value = $("r_src").value = "";
    loadRules();
  } catch (e) {
    $("r_err").textContent = e.message;
  }
};

$("me_save").onclick = async () => {
  $("me_err").textContent = "";
  try {
    await api("/api/auth/password", { method: "POST",
      body: JSON.stringify({ old: $("me_old").value, new: $("me_new").value }) });
    $("me_err").textContent = "";
    alert("口令已改，请用新口令重新登录");
    localStorage.removeItem("sw_token");
    location.reload();
  } catch (e) {
    $("me_err").textContent = e.message;
  }
};

async function loadAudit() {
  const r = await api("/api/audit?limit=100");
  $("audit").innerHTML = `<tr><th>时间</th><th>人</th><th>动作</th><th>对象</th><th>说明</th></tr>` +
    r.rows.map((a) => `<tr><td class="muted">${t(a.at)}</td><td>${esc(a.username)}</td>
      <td>${esc(a.action)}</td><td>${esc(a.target)}</td><td class="muted">${esc(a.detail)}</td></tr>`).join("");
}

$("aexp").onclick = async () => {
  try {
    const res = await fetch("/api/admin/audit.csv", { headers: { Authorization: "Bearer " + (localStorage.getItem("sw_token") || "") } });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "audit.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (e) { $("aerr").textContent = e.message; }
};

$("aprune").onclick = async () => {
  if (!confirm("删掉 90 天前的审计记录？删了不能恢复")) return;
  try {
    const r = await api("/api/admin/audit/prune", { method: "POST", body: JSON.stringify({ days: 90 }) });
    $("aerr").textContent = `删了 ${r.removed} 行，剩 ${r.left} 行`;
    loadAudit();
  } catch (e) { $("aerr").textContent = e.message; }
};

let ITEM_PAGE = 1, ITEM_PAGES = 1;

async function loadItems(page = ITEM_PAGE) {
  ITEM_PAGE = page;
  $("ierr").textContent = "";
  const q = $("iq").value.trim();
  const r = await api(`/api/admin/items?q=${encodeURIComponent(q)}&page=${page}&size=50`);
  ITEM_PAGES = r.pages;
  $("ipage").textContent = `第 ${r.page} / ${r.pages} 页 · 共 ${r.total} 条`;
  $("items").innerHTML = `<tr><th>时间</th><th>来源</th><th>状态</th><th>标题</th><th>操作</th></tr>` +
    r.items.map((it) => `<tr data-id="${it.id}"><td class="muted">${t(it.published_at)}</td><td>${esc(it.source)}</td>
      <td>${it.state === "offline" ? "下架" : "在架"}${it.pinned ? " · 置顶" : ""}</td>
      <td>${esc(it.title)}</td>
      <td class="row">
        <button class="ghost" data-pin="${it.id}">${it.pinned ? "取消置顶" : "置顶"}</button>
        <button class="ghost" data-hist="${it.id}">改动历史</button>
        ${it.sec_code ? `<button class="ghost" data-tl="${encodeURIComponent(it.sec_code)}">时间线</button>` : ""}
      </td></tr>`).join("");
  $("items").querySelectorAll("tr[data-id]").forEach((tr) => {
    tr.onclick = (ev) => { if (!ev.target.closest("button")) tr.classList.toggle("sel"); };
  });
  $("items").querySelectorAll("[data-pin]").forEach((el) => {
    el.onclick = async () => {
      try { await api(`/api/admin/items/${el.dataset.pin}/pin`, { method: "POST" }); loadItems(); }
      catch (e) { $("ierr").textContent = e.message; }
    };
  });
  $("items").querySelectorAll("[data-hist]").forEach((el) => {
    el.onclick = async () => {
      try {
        const h = await api(`/api/admin/items/${el.dataset.hist}/history`);
        const lines = (h.history || []).map((x) => `v${x.version} · ${t(x.at)}`).join("\n") || "没有历史版本";
        const diff = (h.diff || []).join("\n") || "相邻版本没有差异";
        alert(`改动历史\n${lines}\n\n差异\n${diff}`);
      } catch (e) { $("ierr").textContent = e.message; }
    };
  });
  $("items").querySelectorAll("[data-tl]").forEach((el) => {
    el.onclick = async () => {
      try {
        const code = decodeURIComponent(el.dataset.tl);
        const r2 = await api(`/api/admin/timeline?code=${encodeURIComponent(code)}&limit=60`);
        const rows = (r2.items || r2.rows || []);
        alert(`${code} · ${rows.length} 条\n\n` + rows.map((x) => `${t(x.published_at)}  ${x.title}`).join("\n").slice(0, 3000));
      } catch (e) { $("ierr").textContent = e.message; }
    };
  });
}

function picked() {
  return [...document.querySelectorAll("#items tr.sel")].map((tr) => Number(tr.dataset.id)).filter(Boolean);
}

async function itemState(state) {
  const ids = picked();
  if (!ids.length) { $("ierr").textContent = "先点选几行"; return; }
  try {
    await api("/api/admin/items/state", { method: "POST", body: JSON.stringify({ ids, state }) });
    loadItems();
  } catch (e) { $("ierr").textContent = e.message; }
}

$("isearch").onclick = () => loadItems(1);
$("iprev").onclick = () => loadItems(Math.max(1, ITEM_PAGE - 1));
$("inext").onclick = () => loadItems(Math.min(ITEM_PAGES, ITEM_PAGE + 1));
$("ion").onclick = () => itemState("online");
$("ioff").onclick = () => itemState("offline");
$("idel").onclick = async () => {
  const ids = [...document.querySelectorAll("#items tr.sel")].map((tr) => Number(tr.dataset.id)).filter(Boolean);
  if (!ids.length) { $("ierr").textContent = "先点选几行"; return; }
  if (!confirm(`删掉这 ${ids.length} 条？`)) return;
  try { await api("/api/admin/items/delete", { method: "POST", body: JSON.stringify({ ids }) }); loadItems(); }
  catch (e) { $("ierr").textContent = e.message; }
};

async function loadCategories() {
  const r = await api("/api/admin/categories");
  $("cats").innerHTML = `<tr><th>分类</th><th>条目</th><th>状态</th><th>操作</th></tr>` +
    r.categories.map((c) => `<tr><td><b>${esc(c.name)}</b></td><td>${c.items ?? 0}</td>
      <td class="muted">${c.enabled ? "启用" : "停用"}</td>
      <td><button class="ghost" data-cid="${c.id}" data-en="${c.enabled ? 1 : 0}">${c.enabled ? "停用" : "启用"}</button></td></tr>`).join("");
  $("cats").querySelectorAll("[data-cid]").forEach((el) => {
    el.onclick = async () => {
      try {
        await api(`/api/admin/categories/${el.dataset.cid}`, { method: "PATCH", body: JSON.stringify({ enabled: el.dataset.en !== "1" }) });
        loadCategories();
      } catch (e) { $("cerr").textContent = e.message; }
    };
  });
}

$("addcat").onclick = async () => {
  const name = $("nc").value.trim();
  if (!name) return;
  try { await api("/api/admin/categories", { method: "POST", body: JSON.stringify({ name }) }); $("nc").value = ""; loadCategories(); }
  catch (e) { $("cerr").textContent = e.message; }
};

async function loadJobs() {
  const r = await api("/api/admin/jobs");
  const text = { queued: "排队", running: "运行中", paused: "已暂停", done: "已完成", failed: "失败", cancelled: "已取消" };
  $("jcounts").textContent = `排队 ${r.counts.queued || 0} · 运行 ${r.counts.running || 0} · 暂停 ${r.counts.paused || 0} · 完成 ${r.counts.done || 0} · 失败 ${r.counts.failed || 0} · 取消 ${r.counts.cancelled || 0}`;
  $("jobs").innerHTML = `<tr><th>#</th><th>类型</th><th>对象</th><th>状态</th><th>发起人</th><th>结果</th><th>操作</th></tr>` +
    r.jobs.map((j) => `<tr><td>${j.id}</td><td>${esc(j.kind)}</td><td>${j.target || "全部"}</td>
      <td>${text[j.status] || esc(j.status)}</td><td>${esc(j.created_by)}</td>
      <td class="muted">${esc((j.result || j.error || "").slice(0, 120))}</td>
      <td>${j.status === "failed" ? `<button class="ghost" data-act="retry" data-id="${j.id}">重试</button>` : ""}
          ${j.status === "running" ? `<button class="ghost" data-act="pause" data-id="${j.id}">暂停</button>` : ""}
          ${j.status === "paused" ? `<button class="ghost" data-act="resume" data-id="${j.id}">继续</button>` : ""}
          ${["queued", "running", "paused"].includes(j.status) ? `<button class="ghost" data-act="cancel" data-id="${j.id}">取消</button>` : ""}</td></tr>`).join("");
  $("jobs").querySelectorAll("[data-act]").forEach((el) => {
    el.onclick = async () => {
      try { await api(`/api/admin/jobs/${el.dataset.id}/${el.dataset.act}`, { method: "POST" }); }
      catch (e) { $("jerr").textContent = e.message; }
      loadJobs();
    };
  });
}

async function enqueueJob(kind, target) {
  try { await api("/api/admin/jobs", { method: "POST", body: JSON.stringify({ kind, target, priority: 5 }) }); loadJobs(); }
  catch (e) { $("jerr").textContent = e.message; }
}

$("jall").onclick = () => enqueueJob("all", "");
$("jrefresh").onclick = () => loadJobs();
document.querySelectorAll(".jone").forEach((el) => { el.onclick = () => enqueueJob("source", el.dataset.s); });

async function loadReport() {
  const days = Number($("rdays").value);
  const r = await api(`/api/report?days=${days}`);
  $("rsource").innerHTML = `<tr><th>来源</th><th>条目</th><th>最近一条</th></tr>` +
    r.by_source.map((s) => `<tr><td>${esc(s.source)}</td><td>${s.items}</td><td class="muted">${t(s.latest)}</td></tr>`).join("");
  $("rtop").innerHTML = `<tr><th>代码</th><th>公司</th><th>条数</th></tr>` +
    (r.top_companies || []).map((c) => `<tr><td>${esc(c.sec_code || "")}</td><td>${esc(c.sec_name || "")}</td><td>${c.items}</td></tr>`).join("");
}

$("rload").onclick = () => loadReport();
$("rdays").onchange = () => loadReport();

const SET_ROWS = [["notify_toast", "新条目录屏提示"], ["notify_sound", "提示音"], ["notify_rules_only", "只提醒命中订阅词的条目"], ["notify.alert", "源连续失败时发邮件告警"], ["enrich.auto", "新条目自动抠正文（PDF 文字层）"]];

async function loadSettings() {
  const r = await api("/api/admin/settings");
  $("setform").innerHTML = SET_ROWS.map(([k, label]) => `
    <label class="chk"><input type="checkbox" data-k="${k}" ${r.values[k] === "1" ? "checked" : ""}/> ${label}</label>`).join("");
  loadNotify();
}

async function loadNotify() {
  const n = await api("/api/admin/notify");
  const last = n.last && n.last.at
    ? `上次发送 ${t(n.last.at)} · ${n.last.ok ? "成功" : "失败：" + esc(n.last.error || "")}`
    : "还没发过";
  $("ntfstate").innerHTML = (n.ready ? '<span class="text-green">已配置</span>' : '<span class="muted">未配置</span>')
    + ` · 收件 ${esc(n.to || "-")} · ${last}`;
  $("n_host").value = n.host || "";
  $("n_port").value = n.port || "";
  $("n_user").value = n.user || "";
  $("n_to").value = n.to || "";
  $("n_pwd").placeholder = n.has_password ? "已保存，留空不改" : "授权码（不是登录密码）";
  $("n_enabled").checked = String(n.enabled) === "1";
}

async function saveNotify(sendTest) {
  const body = {
    host: $("n_host").value.trim(), port: $("n_port").value.trim(),
    user: $("n_user").value.trim(), to: $("n_to").value.trim(),
    enabled: $("n_enabled").checked, send_test: sendTest,
  };
  const pwd = $("n_pwd").value;
  if (pwd) body.password = pwd;
  $("ntfstate").textContent = sendTest ? "正在发测试信…" : "正在保存…";
  try {
    const r = await api("/api/admin/notify", { method: "POST", body: JSON.stringify(body) });
    $("n_pwd").value = "";
    if (r.error) { $("ntfstate").innerHTML = `<span class="text-red">${esc(r.error)}</span>`; }
    else if (r.sent_to) { $("ntfstate").innerHTML = `<span class="text-green">测试信已发到 ${esc(r.sent_to)}</span>`; }
    await loadNotify();
  } catch (e) { $("ntfstate").innerHTML = `<span class="text-red">${esc(e.message)}</span>`; }
}

$("nsave").onclick = () => saveNotify(false);
$("ntest").onclick = () => saveNotify(true);

$("savset").onclick = async () => {
  const values = {};
  $("setform").querySelectorAll("input[data-k]").forEach((el) => { values[el.dataset.k] = el.checked ? "1" : "0"; });
  try { await api("/api/admin/settings", { method: "PATCH", body: JSON.stringify({ values }) }); $("serr").textContent = "已保存"; }
  catch (e) { $("serr").textContent = e.message; }
};

boot();
