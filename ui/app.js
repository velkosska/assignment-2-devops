const { CATALOG_URL, LOANS_URL } = window.CONFIG;
const REQUEST_TIMEOUT_MS = 5000;

class ApiError extends Error {}

async function call(service, baseUrl, path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let response;
  try {
    response = await fetch(baseUrl + path, {
      method: options.method || "GET",
      headers: options.body ? { "Content-Type": "application/json" } : {},
      body: options.body ? JSON.stringify(options.body) : undefined,
      signal: controller.signal,
    });
  } catch {
    throw new ApiError(`${service} service unavailable. Try again shortly.`);
  } finally {
    clearTimeout(timer);
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(data.error || `${service} service answered ${response.status}.`);
  }
  return data;
}

const catalog = (path, options) => call("Catalog", CATALOG_URL, path, options);
const loans = (path, options) => call("Loans", LOANS_URL, path, options);

// Resolves to { ok, data } or { ok: false, error } so one failing service does not blank the page.
const settle = (promise) =>
  promise.then((data) => ({ ok: true, data }), (error) => ({ ok: false, error: error.message }));

const $ = (id) => document.getElementById(id);

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function showError(message) {
  const box = $("error");
  if (!box) return;
  box.textContent = message || "";
  box.hidden = !message;
}

function showNotices(results) {
  const messages = results.filter((r) => !r.ok).map((r) => r.error);
  $("notices").innerHTML = [...new Set(messages)]
    .map((m) => `<p class="notice">${esc(m)}</p>`)
    .join("");
}

function toolNames(toolsResult) {
  const names = new Map();
  if (toolsResult.ok) toolsResult.data.forEach((tool) => names.set(tool.id, tool.name));
  return (id) => names.get(id) || `Tool #${id}`;
}

function onSubmit(form, handler) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fields = Object.fromEntries(new FormData(form));
    try {
      await handler(fields);
      form.reset();
      showError("");
    } catch (error) {
      showError(error.message);
    }
    await render();
  });
}

function onAction(container, handler) {
  container.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-id]");
    if (!button) return;
    button.disabled = true;
    try {
      await handler(Number(button.dataset.id));
      showError("");
    } catch (error) {
      showError(error.message);
    }
    await render();
  });
}

const day = (iso) => esc((iso || "").slice(0, 10));

const pages = {
  async home() {
    const [tools, open] = await Promise.all([settle(catalog("/tools")), settle(loans("/loans"))]);
    showNotices([tools, open]);
    if (open.ok) {
      $("out").textContent = open.data.length;
      $("overdue").textContent = open.data.filter((loan) => loan.overdue).length;
    } else {
      $("out").textContent = $("overdue").textContent = "?";
    }
    if (tools.ok && open.ok) {
      const outIds = new Set(open.data.map((loan) => loan.tool_id));
      $("free").textContent = tools.data.filter((t) => !t.retired && !outIds.has(t.id)).length;
    } else {
      $("free").textContent = "?";
    }
  },

  async tools() {
    const [tools, open] = await Promise.all([settle(catalog("/tools")), settle(loans("/loans"))]);
    showNotices([tools, open]);
    if (!tools.ok) {
      $("tools").innerHTML = `<p class="empty">The tool list is unavailable right now.</p>`;
      return;
    }
    if (tools.data.length === 0) {
      $("tools").innerHTML = `<p class="empty">No tools yet. Add the first one above.</p>`;
      return;
    }
    const outIds = new Set(open.ok ? open.data.map((loan) => loan.tool_id) : []);
    const rows = tools.data.map((tool) => {
      let status = `<span class="badge">Available</span>`;
      if (tool.retired) status = `<span class="badge retired">Retired</span>`;
      else if (outIds.has(tool.id)) status = `<span class="badge overdue">Out</span>`;
      else if (!open.ok) status = `<span class="badge retired">Unknown</span>`;
      const canRetire = !tool.retired && !outIds.has(tool.id);
      return `<tr>
        <td>${esc(tool.name)}</td>
        <td>${esc(tool.category)}</td>
        <td>${status}</td>
        <td>${canRetire ? `<button class="quiet" data-id="${tool.id}">Retire</button>` : ""}</td>
      </tr>`;
    });
    $("tools").innerHTML = `<table>
      <tr><th>Name</th><th>Category</th><th>Status</th><th></th></tr>
      ${rows.join("")}
    </table>`;
  },

  async loans() {
    const [tools, open, members] = await Promise.all([
      settle(catalog("/tools")),
      settle(loans("/loans")),
      settle(loans("/members")),
    ]);
    showNotices([tools, open, members]);
    const toolName = toolNames(tools);

    if (!open.ok) {
      $("open-loans").innerHTML = `<p class="empty">Open loans are unavailable right now.</p>`;
    } else if (open.data.length === 0) {
      $("open-loans").innerHTML = `<p class="empty">Nothing is out right now.</p>`;
    } else {
      const rows = open.data.map((loan) => `<tr ${loan.overdue ? 'class="overdue"' : ""}>
        <td>${esc(toolName(loan.tool_id))}</td>
        <td>${esc(loan.member_name)}</td>
        <td>${day(loan.borrowed_at)}</td>
        <td>${day(loan.due_at)} ${loan.overdue ? '<span class="badge overdue">Overdue</span>' : ""}</td>
        <td><button data-id="${loan.id}">Return</button></td>
      </tr>`);
      $("open-loans").innerHTML = `<table>
        <tr><th>Tool</th><th>Member</th><th>Borrowed</th><th>Due</th><th></th></tr>
        ${rows.join("")}
      </table>`;
    }

    if (!tools.ok || !open.ok || !members.ok) {
      $("borrow").innerHTML = `<p class="empty">Borrowing needs both services. Try again shortly.</p>`;
      return;
    }
    const outIds = new Set(open.data.map((loan) => loan.tool_id));
    const available = tools.data.filter((t) => !t.retired && !outIds.has(t.id));
    if (members.data.length === 0) {
      $("borrow").innerHTML = `<p class="empty">Add a member above before lending a tool.</p>`;
    } else if (available.length === 0) {
      $("borrow").innerHTML = `<p class="empty">No tool is free to borrow. Add one on the Tools page, or return one that is out.</p>`;
    } else {
      const option = (item) => `<option value="${item.id}">${esc(item.name)}</option>`;
      $("borrow").innerHTML = `<form id="borrow-form">
        <label>Member <select name="member_id" required>${members.data.map(option).join("")}</select></label>
        <label>Available tool <select name="tool_id" required>${available.map(option).join("")}</select></label>
        <button type="submit">Borrow for 7 days</button>
      </form>`;
      onSubmit($("borrow-form"), (fields) =>
        loans("/loans", { method: "POST", body: { tool_id: Number(fields.tool_id), member_id: Number(fields.member_id) } }),
      );
    }
  },

  async history() {
    const [tools, history] = await Promise.all([settle(catalog("/tools")), settle(loans("/loans/history"))]);
    showNotices([tools, history]);
    const toolName = toolNames(tools);
    if (!history.ok) {
      $("history").innerHTML = `<p class="empty">History is unavailable right now.</p>`;
      return;
    }
    if (history.data.length === 0) {
      $("history").innerHTML = `<p class="empty">No returned loans yet. They show up here after you press Return.</p>`;
      return;
    }
    const rows = history.data.map((loan) => `<tr>
      <td>${esc(toolName(loan.tool_id))}</td>
      <td>${esc(loan.member_name)}</td>
      <td>${day(loan.borrowed_at)}</td>
      <td>${day(loan.due_at)}</td>
      <td>${day(loan.returned_at)}</td>
      <td><button data-id="${loan.id}">Repeat</button></td>
    </tr>`);
    $("history").innerHTML = `<table>
      <tr><th>Tool</th><th>Member</th><th>Borrowed</th><th>Due</th><th>Returned</th><th></th></tr>
      ${rows.join("")}
    </table>`;
  },
};

const page = document.body.dataset.page;
const render = () => pages[page]();

if (page === "tools") {
  onSubmit($("add-tool"), (fields) => catalog("/tools", { method: "POST", body: fields }));
  onAction($("tools"), (id) => catalog(`/tools/${id}/retire`, { method: "POST" }));
}
if (page === "loans") {
  onSubmit($("add-member"), (fields) => loans("/members", { method: "POST", body: fields }));
  onAction($("open-loans"), (id) => loans(`/loans/${id}/return`, { method: "POST" }));
}
if (page === "history") {
  onAction($("history"), async (id) => {
    await loans(`/loans/${id}/repeat`, { method: "POST" });
    window.location.href = "loans.html";
  });
}

render();
