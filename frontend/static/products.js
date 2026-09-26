const $ = (selector, root = document) => root.querySelector(selector);
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[char]));
const state = {tokens: null, user: null, refresh: null, page: 1, query: "", category: "", status: "", sort: "newest", product: null, authMode: "login", render: 0, serverDate: document.body.dataset.serverDate};
const view = $("#view");
let searchTimer, previewTimer, previewSequence = 0, confirmAction;

function date(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en", {day:"numeric", month:"short", year:"numeric", timeZone:"UTC"}).format(new Date(`${value}T00:00:00Z`));
}
function nextDay(value) {
  const day = new Date(`${value}T00:00:00Z`);
  day.setUTCDate(day.getUTCDate() + 1);
  return day.toISOString().slice(0, 10);
}
function badge(status) {
  const types = {"Active":["active", "✓"], "Near Expiry":["near", "◷"], "Expired":["expired", "!"], "Extended Warranty":["extended", "+"], "Not Started":["scheduled", "◷"], "No Warranty":["none", "—"]};
  const [kind, icon] = types[status] || types["No Warranty"];
  return `<span class="status status-${kind}"><span aria-hidden="true">${icon}</span>${escape(status)}</span>`;
}
function notify(message) {
  $("#notice").textContent = message;
  $("#notice").hidden = !message;
}
function errorText(error) {
  const details = error.details ? Object.entries(error.details).map(([key, value]) => `${key.replaceAll("_", " ")}: ${Array.isArray(value) ? value.join(" ") : JSON.stringify(value)}`).join("\n") : "";
  return error.message + (details ? "\n" + details : "");
}
function formError(form, error) {
  const box = $(".form-error", form);
  box.textContent = errorText(error);
  box.hidden = false;
  box.tabIndex = -1;
  box.focus();
}
async function request(path, options = {}, retry = true) {
  const headers = {"Accept":"application/json", ...options.headers};
  if (options.body) headers["Content-Type"] = "application/json";
  if (state.tokens && !headers.Authorization) headers.Authorization = `Bearer ${state.tokens.access_token}`;
  let response;
  try { response = await fetch(path, {...options, headers, credentials:"omit"}); }
  catch { throw new Error("We couldn't connect. Check your connection and try again. Your form entries are still here."); }
  if (response.status === 401 && state.tokens && retry && !path.startsWith("/api/auth/")) {
    if (!state.refresh) {
      state.refresh = fetch("/api/auth/refresh", {method:"POST", credentials:"omit", headers:{Authorization:`Bearer ${state.tokens.refresh_token}`}})
        .then(async (result) => { if (!result.ok) throw new Error("Your session has ended. Sign in to continue."); state.tokens = await result.json(); })
        .finally(() => { state.refresh = null; });
    }
    try { await state.refresh; return await request(path, options, false); }
    catch (error) { if (error.message.includes("session has ended")) { state.tokens = null; setAccount(); openAuth(); } throw error; }
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error?.message || `Request failed (${response.status}). Please try again.`);
    error.details = payload.error?.details;
    error.status = response.status;
    throw error;
  }
  return payload;
}
function navigate(path, message = "") {
  history.pushState({}, "", path);
  notify(message);
  renderRoute();
  $("#main").focus();
  window.scrollTo({top:0, behavior:"instant"});
}
function openAuth() {
  if (!$("#auth-dialog").open) $("#auth-dialog").showModal();
}
function setAccount() {
  $("#account-name").textContent = state.user?.full_name || "Your account";
  $("#sign-in").hidden = Boolean(state.tokens);
  $("#sign-out").hidden = !state.tokens;
}
function authMode() {
  const register = state.authMode === "register";
  $("#auth-title").textContent = register ? "Create your account" : "Sign in to your account";
  $("#auth-submit").textContent = register ? "Create account" : "Sign in";
  $("#full-name-field").hidden = !register;
  const form = $("#auth-form");
  form.elements.full_name.required = register;
  form.elements.password.minLength = register ? 12 : 1;
  form.elements.password.autocomplete = register ? "new-password" : "current-password";
  $("#auth-mode").textContent = register ? "Sign in" : "Create an account";
  $("#auth-prompt").textContent = register ? "Already have an account?" : "New to AssureX?";
  $(".form-error", form).hidden = true;
}
$("#auth-mode").addEventListener("click", () => { state.authMode = state.authMode === "login" ? "register" : "login"; authMode(); });
$("#sign-in").addEventListener("click", openAuth);
$("#auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget, button = $("button[type=submit]", form);
  const input = Object.fromEntries(new FormData(form));
  button.disabled = true;
  try {
    if (state.authMode === "register") {
      if (new TextEncoder().encode(input.password).length > 72) throw new Error("Password must be at most 72 UTF-8 bytes.");
      await request("/api/auth/register", {method:"POST", body:JSON.stringify(input)});
    }
    state.tokens = await request("/api/auth/login", {method:"POST", body:JSON.stringify({email:input.email, password:input.password})});
    state.user = state.tokens.user;
    form.elements.password.value = "";
    $("#auth-dialog").close();
    setAccount();
    // Reauthentication keeps unsaved product/warranty form contents intact.
    if (!$("#product-form") && !$("#warranty-form")) await renderRoute();
    notify(state.authMode === "register" ? "Account created. You're ready to register your first product." : "Signed in successfully.");
  } catch (error) { formError(form, error); }
  finally { button.disabled = false; }
});
$("#sign-out").addEventListener("click", async () => {
  try {
    try { await request("/api/auth/logout", {method:"POST"}); }
    catch (error) {
      if (error.status !== 401) throw error;
      try { await request("/api/auth/logout", {method:"POST", headers:{Authorization:`Bearer ${state.tokens.refresh_token}`}}); }
      catch (refreshError) { if (refreshError.status !== 401) throw refreshError; }
    }
    state.tokens = state.user = state.product = null;
    state.page = 1;
    for (const dialog of document.querySelectorAll("dialog[open]")) dialog.close();
    setAccount(); navigate("/products", "You have been signed out.");
  } catch (error) { notify(error.message); }
});

async function renderRoute({quiet = false} = {}) {
  const sequence = ++state.render;
  if (!state.tokens) {
    view.innerHTML = `<div class="empty-state"><div class="empty-icon" aria-hidden="true">▦</div><p class="eyebrow">PRODUCTS & WARRANTIES</p><h1>Your products, protected.</h1><p>Register the things you rely on. Keep your warranty details close, and know where your cover stands.</p><button class="button" type="button" data-action="login">Sign in to get started <span aria-hidden="true">→</span></button></div>`;
    return;
  }
  if (!["customer", "admin"].includes(state.user.role)) {
    view.innerHTML = `<div class="empty-state"><h1>Customer workspace</h1><p>Product management is available to customer and administrator accounts. Sign in with the appropriate account to continue.</p></div>`;
    return;
  }
  const path = location.pathname;
  if (!quiet) view.innerHTML = '<div class="loading" role="status">Loading your products…</div>';
  try {
    if (path === "/products/new") { renderProductForm(); return; }
    const match = path.match(/^\/products\/(\d+)(\/edit)?$/);
    if (match) {
      const {product} = await request(`/api/products/${match[1]}`);
      if (sequence !== state.render) return;
      state.product = product; state.serverDate = product.server_date;
      if (match[2]) renderProductForm(product); else renderDetails(product);
    } else {
      const params = new URLSearchParams({page:state.page, per_page:10, sort:state.sort});
      if (state.query) params.set("q", state.query);
      if (state.category) params.set("category", state.category);
      if (state.status) params.set("warranty_status", state.status);
      const result = await request(`/api/products?${params}`);
      if (sequence !== state.render) return;
      state.serverDate = result.server_date;
      const focus = document.activeElement?.name;
      const selection = document.activeElement?.selectionStart;
      renderList(result);
      if (quiet && focus) {
        const field = $(`[name="${focus}"]`, view);
        field?.focus();
        if (field?.type === "search" && selection !== null) field.setSelectionRange(selection, selection);
      }
    }
  } catch (error) {
    if (sequence !== state.render) return;
    view.innerHTML = `<div class="empty-state" role="alert"><h2>We couldn't load this page.</h2><p>${escape(error.message)}</p><button class="button secondary" data-action="retry">Try again</button> <a class="button secondary" href="/products" data-nav>Back to products</a></div>`;
  }
}
function renderList(data) {
  const counts = data.summary.by_status;
  const active = (counts.Active || 0) + (counts["Extended Warranty"] || 0) + (counts["Near Expiry"] || 0);
  const stat = (label, count, hint, icon) => `<div class="stat"><span class="label">${label}<span class="stat-icon" aria-hidden="true">${icon}</span></span><strong>${count}</strong><small>${hint}</small></div>`;
  const selected = (a, b) => a === b ? "selected" : "";
  const pages = Math.max(1, Math.ceil(data.total / data.per_page));
  const rows = data.items.map((product) => `<tr><td><a class="product-cell" href="/products/${product.id}" data-nav><span class="product-icon" aria-hidden="true">▣</span><span><strong>${escape(product.name)}</strong><span class="subline">${escape(product.brand)} · ${escape(product.model_number)}</span></span></a></td><td class="serial">${escape(product.serial_number)}</td><td>${date(product.purchase_date)}<span class="subline">${escape(product.product_age)} old</span></td><td>${date(product.warranty_expiry)}<span class="subline">${escape(product.warranty_remaining)}</span></td><td>${badge(product.warranty_status)}</td><td><a class="row-link" href="/products/${product.id}" data-nav aria-label="View ${escape(product.name)}">↗</a></td></tr>`).join("");
  view.innerHTML = `<div class="page-heading"><div><p class="eyebrow">YOUR PRODUCT PORTFOLIO</p><h1>Products & warranties</h1><p>A clear view of what you own. Confidence in what's covered.</p></div><a class="button" href="/products/new" data-nav><span aria-hidden="true">＋</span> Register product</a></div>
    <div class="stats">${stat("Registered products", data.summary.total, "All in one place", "▦")}${stat("Currently covered", active, "Original & extended warranties", "✓")}${stat("Near expiry", counts["Near Expiry"] || 0, `Within ${data.near_expiry_days} days`, "◷")}${stat("Expired warranties", counts.Expired || 0, "Review your protection", "↗")}</div>
    <section class="panel" aria-labelledby="list-title"><div class="panel-heading"><div><h2 id="list-title">${state.user.role === "admin" ? "All registered products" : "Your products"} <span class="count-pill">${data.total}</span></h2><p>Warranty information updates automatically as time passes.</p></div></div>
    <form class="filters" id="filters"><label>Search products<input type="search" name="q" placeholder="Name, brand, model or serial number" value="${escape(state.query)}" maxlength="200"></label><label>Category<select name="category"><option value="">All categories</option>${data.categories.map((category) => `<option ${selected(category, state.category)}>${escape(category)}</option>`).join("")}</select></label><label>Warranty status<select name="status"><option value="">All statuses</option>${["Active","Near Expiry","Expired","Extended Warranty","Not Started","No Warranty"].map((status) => `<option ${selected(status,state.status)}>${status}</option>`).join("")}</select></label><label>Sort by<select name="sort">${[["newest","Newest first"],["oldest","Oldest first"],["name","Product name"],["purchase_date","Purchase date"],["expiry_date","Warranty expiry"]].map(([value,label]) => `<option value="${value}" ${selected(value,state.sort)}>${label}</option>`).join("")}</select></label></form>
    ${rows ? `<div class="table-wrap"><table><thead><tr><th scope="col">Product</th><th scope="col">Serial number</th><th scope="col">Purchased</th><th scope="col">Warranty</th><th scope="col">Status</th><th scope="col"><span class="fine-print">Details</span></th></tr></thead><tbody>${rows}</tbody></table></div>` : `<div class="empty-state"><div class="empty-icon" aria-hidden="true">▦</div><h2>${data.summary.total ? "No matching products" : "Your protection starts here"}</h2><p>${data.summary.total ? "Try another search or clear your filters." : "Register your first product and we'll calculate its warranty dates for you."}</p>${data.summary.total ? '<button class="button secondary" data-action="clear-filters">Clear filters</button>' : '<a class="button" href="/products/new" data-nav>Register your first product</a>'}</div>`}
    <div class="pagination"><span>${data.total ? `${(data.page-1)*data.per_page+1}–${Math.min(data.page*data.per_page,data.total)} of ${data.total} products` : "0 products"}</span><div><button class="button small secondary" data-action="previous" ${data.page <= 1 ? "disabled" : ""}>Previous</button><span>${data.page} / ${pages}</span><button class="button small secondary" data-action="next" ${data.page >= pages ? "disabled" : ""}>Next</button></div></div></section>
    <div class="help-strip"><span class="help-icon" aria-hidden="true">ⓘ</span><div><strong>Stay a step ahead of expiry</strong><p>Warranties within ${data.near_expiry_days} days of expiry are marked “Near Expiry”. Open a product to review your cover or add an extension.</p></div></div>`;
  const filter = $("#filters");
  function applyFilters() {
    state.query = filter.elements.q.value; state.category = filter.elements.category.value;
    state.status = filter.elements.status.value; state.sort = filter.elements.sort.value; state.page = 1;
    renderRoute({quiet:true});
  }
  filter.addEventListener("submit", (event) => {event.preventDefault(); clearTimeout(searchTimer); applyFilters();});
  filter.addEventListener("change", (event) => {if(event.target.tagName === "SELECT") {clearTimeout(searchTimer); applyFilters();}});
  filter.elements.q.addEventListener("input", () => {clearTimeout(searchTimer); searchTimer = setTimeout(applyFilters, 400);});
}
function field(label, name, value = "", attributes = "", wide = false, hint = "") {
  return `<label class="${wide ? "wide" : ""}">${label}<input name="${name}" value="${escape(value)}" ${attributes}>${hint ? `<span class="field-hint">${hint}</span>` : ""}</label>`;
}
function durationFields(durationName, unitName, value = 12, unit = "months") {
  return `${field("Warranty duration", durationName, value, 'type="number" min="1" max="1200" step="1" required data-preview')}<label>Duration unit<select name="${unitName}" data-preview><option value="months" ${unit === "months" ? "selected" : ""}>Months</option><option value="years" ${unit === "years" ? "selected" : ""}>Years</option></select></label>`;
}
function renderProductForm(product = null) {
  const edit = Boolean(product);
  view.innerHTML = `<div class="form-layout"><a href="${edit ? `/products/${product.id}` : "/products"}" class="back-link" data-nav>← ${edit ? "Back to product" : "All products"}</a><div class="form-intro"><p class="eyebrow">${edit ? "PRODUCT INFORMATION" : "A LITTLE MORE PEACE OF MIND"}</p><h1>${edit ? "Edit product" : "Register a product"}</h1><p>${edit ? "Keep your product information up to date. Warranty records are managed separately." : "Add your product and purchase details. We'll take care of the warranty dates."}</p></div>
    <form id="product-form" class="panel"><section class="form-section" aria-labelledby="product-section"><div class="section-heading"><span class="step-number">1</span><h2 id="product-section">Product details</h2></div><div class="fields">
    ${field("Product name", "name", product?.name, 'required maxlength="200" placeholder="e.g. Living room television"', true)}
    ${field("Brand", "brand", product?.brand, 'required maxlength="100" placeholder="e.g. Samsung"')}
    ${field("Category", "category", product?.category, 'required maxlength="100" placeholder="e.g. Electronics" list="categories"')}<datalist id="categories"><option>Electronics</option><option>Home appliances</option><option>Computers</option><option>Mobile devices</option><option>Furniture</option><option>Other</option></datalist>
    ${field("Model", "model_number", product?.model_number, 'required maxlength="100" placeholder="Manufacturer model number"')}
    ${field("Serial number", "serial_number", product?.serial_number, 'required maxlength="150" placeholder="Unique product serial"', false, "Usually printed on the product or packaging.")}</div></section>
    <section class="form-section" aria-labelledby="purchase-section"><div class="section-heading"><span class="step-number">2</span><h2 id="purchase-section">Purchase details</h2></div><div class="fields">
    ${field("Purchase date", "purchase_date", product?.purchase_date, `type="date" max="${state.serverDate}" required data-preview`)}
    ${field("Purchase price", "purchase_price", product?.purchase_price, 'type="number" min="0" max="9999999999.99" step="0.01" required placeholder="0.00"')}
    ${field("Retailer", "retailer", product?.retailer, 'required maxlength="200" placeholder="Where did you buy it?"', true)}</div></section>
    ${!edit ? `<section class="form-section" aria-labelledby="warranty-section"><div class="section-heading"><span class="step-number">3</span><h2 id="warranty-section">Original warranty</h2></div><div class="fields">${durationFields("warranty_duration","warranty_duration_unit")}${field("Provider (optional)","warranty_provider","",'maxlength="200" placeholder="Defaults to the product brand"')}${field("Start date (optional)","warranty_start_date","",'type="date" data-preview', false, "Defaults to your purchase date.")}<label class="wide">Coverage (optional)<textarea name="coverage" maxlength="10000" placeholder="What's included in the warranty?"></textarea></label><label class="wide">Exclusions (optional)<textarea name="exclusions" placeholder="One exclusion per line"></textarea></label><label class="wide">Service-center conditions (optional)<textarea name="service_center_conditions" maxlength="10000" placeholder="Authorized service centers or other requirements"></textarea></label></div><div class="calculation" role="status"><span>Calculated warranty expiry</span><strong id="expiry-preview">Enter a purchase date and duration</strong></div></section>` : ""}
    <div class="form-section"><div class="form-error" role="alert" hidden></div><p class="fine-print">${edit ? "Products already used in claims retain their original purchase and identity details." : "Expiry is calculated from the start date and duration. All required fields must be completed."}</p></div><div class="form-actions"><a class="button secondary" href="${edit ? `/products/${product.id}` : "/products"}" data-nav>Cancel</a><button class="button" type="submit">${edit ? "Save changes" : "Register product"}</button></div></form></div>`;
  const form = $("#product-form");
  form.addEventListener("input", (event) => { if (event.target.matches("[data-preview]")) schedulePreview(form); });
  form.addEventListener("change", (event) => { if (event.target.matches("[data-preview]")) schedulePreview(form); });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("button[type=submit]", form); button.disabled = true;
    const data = Object.fromEntries(new FormData(form));
    if (!edit) {
      data.warranty_duration = Number(data.warranty_duration);
      data.exclusions = data.exclusions.split("\n").map((value) => value.trim()).filter(Boolean);
      if (!data.warranty_provider.trim()) delete data.warranty_provider;
      if (!data.warranty_start_date) delete data.warranty_start_date;
    }
    try {
      const result = await request(edit ? `/api/products/${product.id}` : "/api/products", {method:edit ? "PUT" : "POST", body:JSON.stringify(data)});
      navigate(`/products/${result.product.id}`, result.message);
    } catch (error) { formError(form, error); }
    finally { button.disabled = false; }
  });
}
function schedulePreview(form) {
  clearTimeout(previewTimer);
  const duration = form.elements.duration || form.elements.warranty_duration;
  const unit = form.elements.duration_unit || form.elements.warranty_duration_unit;
  duration.max = unit.value === "years" ? "100" : "1200";
  previewTimer = setTimeout(() => previewExpiry(form), 250);
}
async function previewExpiry(form) {
  const sequence = ++previewSequence;
  const output = $("#expiry-preview", form); if (!output) return;
  const data = new FormData(form);
  const start_date = data.get("start_date") || data.get("warranty_start_date") || data.get("purchase_date");
  const duration = Number(data.get("duration") || data.get("warranty_duration"));
  const duration_unit = data.get("duration_unit") || data.get("warranty_duration_unit");
  if (!start_date || !Number.isInteger(duration) || duration <= 0) {output.textContent = "Enter a valid date and duration"; return;}
  output.textContent = "Calculating…";
  try {
    const result = await request("/api/warranties/preview", {method:"POST", body:JSON.stringify({start_date,duration,duration_unit})});
    if (sequence === previewSequence && output.isConnected) output.textContent = date(result.expiry_date);
  } catch (error) {if (sequence === previewSequence && output.isConnected) output.textContent = error.message;}
}
function detail(label, value, wide = false) {
  return `<div class="${wide ? "wide" : ""}"><dt>${label}</dt><dd class="coverage-text">${escape(value || "Not specified")}</dd></div>`;
}
function coverageText(warranty) {
  if (warranty.coverage?.trim()) return warranty.coverage;
  const readable = (value) => {
    if (typeof value === "boolean") return value ? "Included" : "Excluded";
    if (Array.isArray(value)) return value.map(readable).join(", ");
    if (value && typeof value === "object") return Object.entries(value).map(([key, item]) => `${key.replaceAll("_", " ")}: ${readable(item)}`).join("; ");
    return String(value ?? "");
  };
  return Object.entries(warranty.coverage_conditions || {}).filter(([key, value]) => key !== "description" && value != null && value !== "")
    .map(([key, value]) => `${key.replaceAll("_", " ")}: ${readable(value)}`).join("\n") || "Not specified";
}
function renderDetails(product) {
  const current = product.current_warranty;
  const history = product.warranties.map((w) => `<article class="warranty-card"><header><div><h3>${w.is_extended ? "Extended warranty" : "Original warranty"}</h3><span class="subline">${escape(w.provider)} · ${w.duration} ${w.duration_unit}</span></div>${badge(w.warranty_status)}</header><dl class="data-grid">${detail("Start date", date(w.start_date))}${detail("Expiry date", date(w.expiry_date))}${detail("Coverage", coverageText(w), true)}${detail("Exclusions", (w.exclusions || []).join("\n"), true)}${detail("Service-center conditions", w.service_center_conditions, true)}</dl><div class="actions"><button class="text-button" data-action="edit-warranty" data-id="${w.id}">Edit warranty</button><button class="text-button danger" data-action="delete-warranty" data-id="${w.id}">Delete warranty</button></div></article>`).join("");
  view.innerHTML = `<a class="back-link" href="/products" data-nav>← All products</a><div class="page-heading detail-page"><div class="detail-heading"><span class="product-icon" aria-hidden="true">▣</span><div><p class="eyebrow">PRODUCT DETAILS</p><h1>${escape(product.name)}</h1><p>${escape(product.brand)} · ${escape(product.model_number)} · ${escape(product.category)}</p></div></div><div class="detail-actions"><a class="button secondary" href="/products/${product.id}/edit" data-nav>Edit product</a><button class="button" data-action="add-warranty">＋ ${product.warranties.length ? "Extend warranty" : "Add warranty"}</button></div></div>
    <div class="detail-grid"><div><section class="panel"><div class="panel-heading"><h2>Product information</h2></div><div class="panel-body"><dl class="data-grid">${detail("Product name",product.name)}${detail("Brand",product.brand)}${detail("Category",product.category)}${detail("Model",product.model_number)}${detail("Serial number",product.serial_number)}${detail("Purchase date",date(product.purchase_date))}${detail("Purchase price",product.purchase_price)}${detail("Retailer",product.retailer)}${detail("Product age",product.product_age)}${detail("Original warranty duration",product.warranty_duration ? `${product.warranty_duration} ${product.warranty_duration_unit}` : "No original warranty")}</dl></div></section>
    <section class="panel"><div class="panel-heading"><div><h2>Warranty history <span class="count-pill">${product.warranties.length}</span></h2><p>Original protection and every additional period of cover.</p></div></div>${history || '<div class="panel-body"><p class="muted">No warranty is registered for this product yet.</p><button class="button secondary" data-action="add-warranty">Add original warranty</button></div>'}</section>
    <div class="danger-zone"><span>Products used in claims are retained for your records.</span><button class="text-button danger" data-action="delete-product">Delete product</button></div></div>
    <div><section class="panel"><div class="coverage-hero">${badge(product.warranty_status)}<h2>${escape(product.warranty_remaining)}</h2><p>${current ? `Warranty expiry · ${date(current.expiry_date)}` : "Add a warranty to see your protection."}</p>${current ? `<progress max="100" value="${current.progress_percent}" aria-label="Current position in the displayed warranty period">${current.progress_percent}%</progress><div class="progress-caption"><span>${date(current.start_date)}</span><span>${date(current.expiry_date)}</span></div>` : ""}</div><div class="panel-body"><dl class="data-grid">${detail("Provider",current?.provider)}${detail("As of (UTC)",date(product.server_date))}</dl></div></section>
    <section class="panel"><div class="panel-heading"><h2>Your warranty timeline</h2></div><div class="panel-body"><ol class="timeline">${product.timeline.map((event) => `<li class="${event.kind === "today" ? "today" : ""}"><span>${escape(event.label)}</span><time datetime="${event.date}">${date(event.date)}</time></li>`).join("")}</ol></div></section></div></div>`;
}
function openWarranty(warranty = null) {
  const product = state.product;
  const edit = Boolean(warranty);
  const latest = product.warranties.map((w) => w.expiry_date).sort().at(-1);
  const start = warranty?.start_date || (latest ? nextDay(latest) : product.purchase_date);
  $("#warranty-dialog-content").innerHTML = `<div class="dialog-heading"><div><p class="eyebrow">${escape(product.name)}</p><h2 id="warranty-title">${edit ? "Edit warranty" : product.warranties.length ? "Add an extended warranty" : "Add original warranty"}</h2></div><button class="icon-button" type="button" data-close="warranty-dialog" aria-label="Close warranty form">×</button></div><p class="muted">Expiry dates are calculated for you. Each warranty period must follow the original without overlapping another period.</p><form id="warranty-form"><div class="fields">
    ${field("Provider", "provider", warranty?.provider || product.brand, 'required maxlength="200"')}${field("Start date", "start_date", start, `type="date" min="${product.purchase_date}" required data-preview`)}${durationFields("duration","duration_unit",warranty?.duration || 12,warranty?.duration_unit || "months")}
    <label class="wide">Coverage<textarea name="coverage" maxlength="10000">${escape(warranty?.coverage)}</textarea></label><label class="wide">Exclusions <span class="field-hint">One exclusion per line</span><textarea name="exclusions">${escape(warranty?.exclusions.join("\n"))}</textarea></label><label class="wide">Service-center conditions<textarea name="service_center_conditions" maxlength="10000">${escape(warranty?.service_center_conditions)}</textarea></label></div><div class="calculation" role="status"><span>Calculated expiry date</span><strong id="expiry-preview">Calculating…</strong></div><div class="form-error" role="alert" hidden></div><div class="form-actions"><button type="button" class="button secondary" data-close="warranty-dialog">Cancel</button><button type="submit" class="button">${edit ? "Save warranty" : "Add warranty"}</button></div></form>`;
  const form = $("#warranty-form");
  form.addEventListener("input", (event) => {if(event.target.matches("[data-preview]")) schedulePreview(form);});
  form.addEventListener("change", (event) => {if(event.target.matches("[data-preview]")) schedulePreview(form);});
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("button[type=submit]",form); button.disabled = true;
    const data = Object.fromEntries(new FormData(form));
    data.duration = Number(data.duration); data.exclusions = data.exclusions.split("\n").map((line) => line.trim()).filter(Boolean);
    try {
      const result = await request(edit ? `/api/warranties/${warranty.id}` : `/api/products/${product.id}/warranties`, {method:edit ? "PUT" : "POST", body:JSON.stringify(data)});
      $("#warranty-dialog").close(); notify(result.message); await renderRoute();
    } catch(error) {formError(form,error);}
    finally {button.disabled = false;}
  });
  $("#warranty-dialog").showModal();
  schedulePreview(form);
}
function confirmDelete(description, action) {
  $("#confirm-description").textContent = description;
  $(".form-error", $("#confirm-dialog")).hidden = true;
  confirmAction = action;
  $("#confirm-dialog").showModal();
}
$("#confirm-delete").addEventListener("click", async (event) => {
  const button = event.currentTarget; button.disabled = true;
  try {await confirmAction(); $("#confirm-dialog").close();}
  catch(error) {formError($("#confirm-dialog"),error);}
  finally {button.disabled = false;}
});
document.addEventListener("click", (event) => {
  const nav = event.target.closest("a[data-nav]");
  if (nav && !event.ctrlKey && !event.metaKey && !event.shiftKey && event.button === 0) {event.preventDefault(); clearTimeout(searchTimer); navigate(nav.getAttribute("href")); return;}
  const close = event.target.closest("[data-close]");
  if (close) {$("#" + close.dataset.close).close(); return;}
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const action = button.dataset.action;
  if (action === "login") openAuth();
  if (action === "retry") renderRoute();
  if (action === "previous") {state.page--; renderRoute({quiet:true});}
  if (action === "next") {state.page++; renderRoute({quiet:true});}
  if (action === "clear-filters") {state.query=state.category=state.status="";state.page=1;renderRoute({quiet:true});}
  if (action === "add-warranty") openWarranty();
  if (action === "edit-warranty") openWarranty(state.product.warranties.find((w) => w.id === Number(button.dataset.id)));
  if (action === "delete-warranty") {
    confirmDelete("This removes the selected warranty record. Warranties used by claims or documents cannot be deleted.", async () => {
      const result = await request(`/api/warranties/${button.dataset.id}`, {method:"DELETE"}); notify(result.message); await renderRoute();
    });
  }
  if (action === "delete-product") {
    confirmDelete(`Delete ${state.product.name} and its warranty records? Products used in claims, documents or repairs cannot be deleted.`, async () => {
      const result = await request(`/api/products/${state.product.id}`, {method:"DELETE"}); navigate("/products",result.message);
    });
  }
});
window.addEventListener("popstate", () => {notify("");renderRoute();});
function refreshVisible() {
  if (!document.hidden && state.tokens && !$("#product-form") && !document.querySelector("dialog[open]")) renderRoute({quiet:true});
}
window.addEventListener("focus", refreshVisible);
document.addEventListener("visibilitychange", refreshVisible);
setInterval(refreshVisible, 60000);
renderRoute();
