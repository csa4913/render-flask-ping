async function api(url, opts = {}) {
  const res = await fetch(url, opts);
  const txt = await res.text();
  let data = null;
  try { data = JSON.parse(txt); } catch { data = txt; }
  if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
  return data;
}

function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  Object.entries(attrs).forEach(([k,v]) => {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  });
  children.flat().forEach(c => {
    if (c === null || c === undefined) return;
    n.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  });
  return n;
}

function attachKinds() {
  return [
    { key: "invoice", label: "인보이스" },
    { key: "work", label: "작업확인서" },
    { key: "inspect", label: "검수서" },
    { key: "other", label: "기타 서류" },
  ];
}

async function createRow() {
  const title = document.getElementById("title").value.trim();
  const category = document.getElementById("category").value.trim();
  const note = document.getElementById("note").value.trim();
  if (!title) { alert("제목(필수)을 입력해주세요."); return; }

  await api("/api/rows", {
    method: "POST",
    headers: { "Content-Type":"application/json" },
    body: JSON.stringify({ title, category, note })
  });

  document.getElementById("title").value = "";
  document.getElementById("note").value = "";
  refresh();
}

async function deleteRow(id) {
  if (!confirm("이 행을 삭제할까요? (첨부도 함께 삭제됨)")) return;
  await api(`/api/rows/${id}`, { method: "DELETE" });
  refresh();
}

function countAllFiles(rows) {
  let cnt = 0;
  for (const r of rows) {
    const files = r.files || {};
    for (const k of Object.keys(files)) cnt += (files[k] || []).length;
  }
  return cnt;
}

function setTotalBadge(n) {
  const badge = document.querySelector("#totalBadge .num");
  badge.textContent = String(n);
}

function makeInstantUploadButton(rowId, kind) {
  // 버튼처럼 보이지만 input file을 클릭
  const input = el("input", { type:"file", style:"display:none" });
  const btn = el("button", { class:"btn-ghost mini" }, "업로드");

  btn.addEventListener("click", () => input.click());

  input.addEventListener("change", async () => {
    if (!input.files || input.files.length === 0) return;
    const file = input.files[0];

    const fd = new FormData();
    fd.append("row_id", rowId);
    fd.append("kind", kind);
    fd.append("file", file);

    btn.disabled = true;
    btn.textContent = "업로드중…";
    try {
      await api("/api/upload", { method:"POST", body: fd });
      await refresh();
    } catch (e) {
      alert(e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "업로드";
      input.value = "";
    }
  });

  return el("div", { class:"uploader" }, btn, input);
}

function renderFilesCell(row, kind) {
  const wrap = el("div", { class:"files" });

  // 업로드 버튼(즉시 업로드)
  wrap.appendChild(makeInstantUploadButton(row.id, kind));

  const list = (row.files && row.files[kind]) ? row.files[kind] : [];
  for (const f of list) {
    const link = el("a", { class:"fileLink", href:`/api/download/${f.id}`, target:"_blank" }, f.original_name);
    const del = el("button", { class:"btn-danger mini", onclick: async () => {
      if (!confirm("이 파일을 삭제할까요?")) return;
      try {
        await api(`/api/files/${f.id}`, { method:"DELETE" });
        refresh();
      } catch (e) { alert(e.message); }
    }}, "삭제");

    wrap.appendChild(el("div", { class:"fileRow" }, link, del));
  }

  return wrap;
}

function renderTable(rows) {
  const table = el("table", { class:"grid" });
  const thead = el("thead");
  const trh = el("tr");

  // 첨부 헤더를 4개로 분리
  trh.appendChild(el("th", {}, "생성일"));
  trh.appendChild(el("th", {}, "제목 / 비고"));
  trh.appendChild(el("th", {}, "종류"));
  trh.appendChild(el("th", {}, "인보이스"));
  trh.appendChild(el("th", {}, "작업확인서"));
  trh.appendChild(el("th", {}, "검수서"));
  trh.appendChild(el("th", {}, "기타 서류"));
  trh.appendChild(el("th", {}, "관리"));

  thead.appendChild(trh);
  table.appendChild(thead);

  const tbody = el("tbody");
  for (const r of rows) {
    const tr = el("tr");

    const dt = new Date(r.created_at);
    tr.appendChild(el("td", {}, el("div", { class:"meta" }, dt.toLocaleString())));

    tr.appendChild(el("td", {}, 
      el("div", {}, r.title),
      r.note ? el("div", { class:"meta" }, r.note) : el("div", { class:"meta" }, " ")
    ));

    tr.appendChild(el("td", {}, el("span", { class:"badge" }, r.category)));

    tr.appendChild(el("td", {}, renderFilesCell(r, "invoice")));
    tr.appendChild(el("td", {}, renderFilesCell(r, "work")));
    tr.appendChild(el("td", {}, renderFilesCell(r, "inspect")));
    tr.appendChild(el("td", {}, renderFilesCell(r, "other")));

    tr.appendChild(el("td", {}, el("button", { class:"btn-danger mini", onclick: () => deleteRow(r.id) }, "행 삭제")));
    tbody.appendChild(tr);
  }

  table.appendChild(tbody);
  return table;
}

async function refresh() {
  const mode = document.getElementById("groupMode").value;
  const data = await api(`/api/rows?group=${encodeURIComponent(mode)}`);

  const content = document.getElementById("content");
  content.innerHTML = "";

  if (data.mode === "time") {
    setTotalBadge(countAllFiles(data.rows));
    content.appendChild(renderTable(data.rows));
  } else {
    // 종류별 그룹핑
    const groups = data.groups || {};
    let all = [];
    Object.values(groups).forEach(arr => all = all.concat(arr));
    setTotalBadge(countAllFiles(all));

    const keys = Object.keys(groups);
    if (keys.length === 0) {
      content.appendChild(el("div", { class:"meta" }, "데이터가 없습니다."));
      return;
    }

    for (const k of keys) {
      content.appendChild(el("div", { class:"groupHead" }, `📁 ${k}`));
      content.appendChild(renderTable(groups[k]));
    }
  }
}

window.addEventListener("DOMContentLoaded", refresh);
