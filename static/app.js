document.querySelector("#menuButton")?.addEventListener("click", () => document.querySelector("#sidebar")?.classList.toggle("open"));
const todayLabel = document.querySelector("#todayLabel");
if (todayLabel) todayLabel.textContent = new Intl.DateTimeFormat("en-IN", {day: "numeric", month: "short", year: "numeric"}).format(new Date());
const istClock = document.querySelector("[data-ist-clock]");
const updateIstClock = () => {
  if (!istClock) return;
  istClock.textContent = new Intl.DateTimeFormat("en-IN", {timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true}).format(new Date()) + " IST";
};
updateIstClock();
if (istClock) window.setInterval(updateIstClock, 1000);
document.querySelectorAll("[data-ist-time]").forEach((element) => {
  const parsed = new Date(element.dataset.istTime);
  if (!Number.isNaN(parsed.getTime())) element.textContent = new Intl.DateTimeFormat("en-IN", {timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: true}).format(parsed) + " IST";
});
document.querySelectorAll("[data-permission-toggle]").forEach((button) => button.addEventListener("click", () => {
  const checked = button.dataset.permissionToggle === "all";
  button.closest("[data-permission-group]")?.querySelectorAll('input[type="checkbox"][name="permissions"]').forEach((checkbox) => { checkbox.checked = checked; });
}));
document.querySelectorAll("[data-date-filter]").forEach((form) => {
  const preset = form.querySelector("[data-preset]");
  const custom = form.querySelector("[data-custom-dates]");
  preset?.addEventListener("change", () => {
    const isCustom = preset.value === "custom";
    custom?.classList.toggle("visible", isCustom);
    if (!isCustom) form.submit();
  });
});

const announcementDialog = document.querySelector("[data-announcement-dialog]");
const closeAnnouncementDialog = () => {
  if (!announcementDialog) return;
  if (typeof announcementDialog.close === "function") announcementDialog.close();
  else announcementDialog.removeAttribute("open");
};
document.querySelector("[data-announcement-open]")?.addEventListener("click", () => {
  if (!announcementDialog) return;
  if (typeof announcementDialog.showModal === "function") announcementDialog.showModal();
  else announcementDialog.setAttribute("open", "");
  announcementDialog.querySelector("textarea")?.focus();
});
announcementDialog?.querySelectorAll("[data-announcement-close]").forEach((button) => button.addEventListener("click", closeAnnouncementDialog));
announcementDialog?.addEventListener("click", (event) => {
  if (event.target === announcementDialog) closeAnnouncementDialog();
});

const syncButton = document.querySelector("[data-sync-all]");
const syncMessage = document.querySelector("[data-sync-message]");
if (syncButton) {
  let pollTimer;
  let foregroundDeadline = 0;
  const setSyncState = (status, message) => {
    const busy = status === "queued" || status === "running";
    syncButton.disabled = busy;
    syncButton.textContent = busy ? "Refreshing Data…" : "Refresh All Data";
    if (syncMessage && message) syncMessage.textContent = message;
    if (!busy && pollTimer) clearTimeout(pollTimer);
  };
  const pollSync = async (showFailure = false) => {
    try {
      const response = await fetch("/api/sync/status", {headers: {Accept: "application/json"}});
      const data = await response.json();
      if (data.status === "queued" || data.status === "running") {
        if (foregroundDeadline && Date.now() >= foregroundDeadline) {
          syncButton.disabled = false;
          syncButton.textContent = "Refresh All Data";
          if (syncMessage) syncMessage.textContent = "Refresh continues safely in the background";
          pollTimer = setTimeout(() => pollSync(showFailure), 15000);
          return;
        }
        setSyncState(data.status, data.status === "queued" ? "Refresh queued…" : "Refreshing Zoho, BIK, Shopify and Uniware…");
        pollTimer = setTimeout(() => pollSync(showFailure), 3000);
      } else if (data.status === "success") {
        setSyncState(data.status, "All data refreshed successfully");
      } else if (data.status === "failed") {
        setSyncState(data.status, showFailure ? "Refresh finished with an error — please retry" : "Manual refresh available");
      } else {
        setSyncState(data.status, "Zoho, BIK, Shopify and Uniware");
      }
    } catch (_) {
      setSyncState("failed", "Unable to check refresh status");
    }
  };
  syncButton.addEventListener("click", async () => {
    setSyncState("queued", "Requesting a fresh copy of all data…");
    try {
      const response = await fetch("/api/sync/all", {
        method: "POST",
        headers: {"Content-Type": "application/json", "X-CSRF-Token": syncButton.dataset.csrf},
        body: "{}",
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Refresh request failed");
      foregroundDeadline = Date.now() + 55000;
      pollSync(true);
    } catch (error) {
      setSyncState("failed", error.message || "Refresh request failed");
    }
  });
  pollSync(false);
}

document.querySelectorAll("[data-cs-tab]").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll("[data-cs-tab]").forEach((item) => item.classList.toggle("active", item === button));
  document.querySelectorAll("[data-cs-panel]").forEach((panel) => panel.classList.toggle("active", panel.dataset.csPanel === button.dataset.csTab));
}));

const productFilter = document.querySelector("[data-product-filter]");
if (productFilter) {
  const field = productFilter.querySelector("[data-product-field]");
  const query = productFilter.querySelector("[data-product-query]");
  const rows = [...document.querySelectorAll("[data-product-row]")];
  const count = document.querySelector("[data-product-count]");
  const exportLink = document.querySelector("[data-product-export]");
  const applyProductFilter = () => {
    const needle = query.value.trim().toLowerCase();
    let visible = 0;
    rows.forEach((row) => {
      const haystack = field.value === "all" ? [row.dataset.employee, row.dataset.role, row.dataset.product, row.dataset.sku].join(" ") : row.dataset[field.value];
      const matches = !needle || (haystack || "").includes(needle);
      row.hidden = !matches;
      visible += Number(matches);
    });
    if (count) count.textContent = `${visible} of ${rows.length} combinations`;
    if (exportLink) {
      const url = new URL(exportLink.href, window.location.origin);
      if (needle) {
        url.searchParams.set("product_field", field.value);
        url.searchParams.set("product_query", query.value.trim());
      } else {
        url.searchParams.delete("product_field");
        url.searchParams.delete("product_query");
      }
      exportLink.href = url.toString();
    }
  };
  query.addEventListener("input", applyProductFilter);
  field.addEventListener("change", applyProductFilter);
  productFilter.querySelector("[data-product-clear]").addEventListener("click", () => { query.value = ""; field.value = "all"; applyProductFilter(); query.focus(); });
}

const salesChart = document.querySelector("[data-sales-chart]");
const salesChartTooltip = salesChart?.querySelector("[data-sales-chart-tooltip]");
if (salesChart && salesChartTooltip) {
  const money = new Intl.NumberFormat("en-IN", {style: "currency", currency: "INR", maximumFractionDigits: 0});
  const showSalesPoint = (event) => {
    const point = event.currentTarget;
    const rows = [
      ["Selling price", money.format(Number(point.dataset.sales || 0))],
      ["Discount", money.format(Number(point.dataset.discount || 0))],
      ["MRP", money.format(Number(point.dataset.mrp || 0))],
      ["Effective discount", `${Number(point.dataset.rate || 0).toFixed(2)}%`],
    ];
    salesChartTooltip.replaceChildren();
    const heading = document.createElement("strong");
    heading.textContent = point.dataset.label || "Sales detail";
    salesChartTooltip.append(heading);
    rows.forEach(([label, value]) => {
      const row = document.createElement("span");
      const name = document.createElement("i");
      const amount = document.createElement("b");
      name.textContent = label;
      amount.textContent = value;
      row.append(name, amount);
      salesChartTooltip.append(row);
    });
    salesChartTooltip.hidden = false;
    const bounds = salesChart.getBoundingClientRect();
    const tooltipWidth = salesChartTooltip.offsetWidth;
    const rawLeft = event.clientX - bounds.left + salesChart.scrollLeft + 12;
    const left = Math.min(rawLeft, salesChart.scrollWidth - tooltipWidth - 8);
    salesChartTooltip.style.left = `${Math.max(8, left)}px`;
    salesChartTooltip.style.top = `${Math.max(8, event.clientY - bounds.top + salesChart.scrollTop - 90)}px`;
  };
  salesChart.querySelectorAll(".chart-point").forEach((point) => {
    point.addEventListener("pointerenter", showSalesPoint);
    point.addEventListener("pointermove", showSalesPoint);
    point.addEventListener("pointerleave", () => { salesChartTooltip.hidden = true; });
  });
}

// Excel-style table filters, live filtered totals and one-view export.
document.querySelectorAll("main .panel table").forEach((table, tableIndex) => {
  if (!table.tHead?.rows?.length || table.dataset.excelFilterReady) return;
  table.dataset.excelFilterReady = "1";
  const header = table.tHead.rows[0];
  const headers = [...header.cells].map((cell) => cell.textContent.trim());
  const bodyRows = [...(table.tBodies[0]?.rows || [])].filter((row) => !row.querySelector(".empty-state") && !row.classList.contains("team-member-detail"));
  if (!bodyRows.length) return;
  let filteredRows = [...bodyRows];
  let currentPage = 1;
  let pageSize = 50;
  let pageLabel = null;
  let previousPage = null;
  let nextPage = null;
  const filters = headers.map(() => ({type: "text", value: ""}));
  const filterRow = table.tHead.insertRow();
  filterRow.className = "excel-filter-row";
  const normalizedCell = (row, index) => (row.cells[index]?.innerText || "").replace(/\s+/g, " ").trim();
  bodyRows.forEach((row) => {
    const target = row.querySelector('a[href]:not(.mini-export)');
    if (!target) return;
    [...row.cells].forEach((cell) => {
      if (cell.querySelector("a,button,input,select,details") || !/₹|%|^[\s\d,.]+$/.test(cell.innerText.trim())) return;
      cell.classList.add("numeric-drill-cell");
      cell.tabIndex = 0;
      cell.setAttribute("role", "link");
      const open = () => { window.location.href = target.href; };
      cell.addEventListener("click", open);
      cell.addEventListener("keydown", (event) => { if (event.key === "Enter") open(); });
    });
  });

  headers.forEach((label, index) => {
    const cell = document.createElement("th");
    filterRow.append(cell);
    const isMulti = /agent|employee|team|role/i.test(label);
    if (isMulti) {
      filters[index] = {type: "multi", values: new Set()};
      const values = [...new Set(bodyRows.map((row) => normalizedCell(row, index)).filter(Boolean))].sort((a, b) => a.localeCompare(b));
      const details = document.createElement("details");
      details.className = "column-multiselect";
      const summary = document.createElement("summary");
      summary.textContent = "All";
      const options = document.createElement("div");
      values.forEach((value) => {
        const option = document.createElement("label");
        const input = document.createElement("input");
        input.type = "checkbox";
        input.value = value;
        input.addEventListener("change", () => {
          if (input.checked) filters[index].values.add(value); else filters[index].values.delete(value);
          summary.textContent = filters[index].values.size ? `${filters[index].values.size} selected` : "All";
          applyFilters();
        });
        option.append(input, document.createTextNode(value));
        options.append(option);
      });
      details.append(summary, options);
      cell.append(details);
    } else {
      const input = document.createElement("input");
      input.type = "search";
      input.placeholder = "Filter…";
      input.setAttribute("aria-label", `Filter ${label}`);
      input.addEventListener("input", () => { filters[index].value = input.value.trim().toLowerCase(); applyFilters(); });
      cell.append(input);
    }
  });

  const isNumericText = (value) => /^(?:₹\s*)?-?[\d,]+(?:\.\d+)?%?$/.test(value.replace(/\s/g, ""));
  const numeric = (value) => Number(value.replace(/[^0-9.-]/g, "")) || 0;
  const formatTotal = (value, sample, label) => {
    if (sample.includes("₹")) return new Intl.NumberFormat("en-IN", {style: "currency", currency: "INR", maximumFractionDigits: 0}).format(value);
    if (sample.includes("%")) return `${value.toFixed(1)}%`;
    if (/aov|average|rate|resolution|csat/i.test(label)) return value.toFixed(1);
    return new Intl.NumberFormat("en-IN", {maximumFractionDigits: 2}).format(value);
  };
  const updateTotals = (visible) => {
    if (!table.tFoot) table.createTFoot();
    table.tFoot.replaceChildren();
    const row = table.tFoot.insertRow();
    headers.forEach((label, index) => {
      const cell = document.createElement("th");
      if (index === 0) cell.textContent = `Filtered total · ${visible.length} row${visible.length === 1 ? "" : "s"}`;
      else {
        const values = visible.map((item) => normalizedCell(item, index)).filter(isNumericText);
        if (values.length >= Math.max(1, Math.ceil(visible.length * .6))) {
          const numbers = values.map(numeric);
          const useAverage = values[0]?.includes("%") || /aov|average|rate|resolution|csat/i.test(label);
          const total = useAverage ? numbers.reduce((a, b) => a + b, 0) / numbers.length : numbers.reduce((a, b) => a + b, 0);
          cell.textContent = formatTotal(total, values[0] || "", label);
        }
      }
      row.append(cell);
    });
  };
  const renderPage = () => {
    const pageCount = Math.max(1, Math.ceil(filteredRows.length / pageSize));
    currentPage = Math.min(currentPage, pageCount);
    const first = (currentPage - 1) * pageSize;
    const pageRows = new Set(filteredRows.slice(first, first + pageSize));
    bodyRows.forEach((row) => { row.hidden = !pageRows.has(row); });
    updateTotals(filteredRows);
    if (pageLabel) pageLabel.textContent = `Page ${currentPage} of ${pageCount} · ${filteredRows.length} rows`;
    if (previousPage) previousPage.disabled = currentPage <= 1;
    if (nextPage) nextPage.disabled = currentPage >= pageCount;
  };
  const applyFilters = () => {
    filteredRows = bodyRows.filter((row) => {
      const matches = filters.every((filter, index) => {
        const value = normalizedCell(row, index);
        return filter.type === "multi" ? (!filter.values.size || filter.values.has(value)) : (!filter.value || value.toLowerCase().includes(filter.value));
      });
      return matches;
    });
    currentPage = 1;
    renderPage();
  };

  const wrap = table.closest(".table-wrap") || table.parentElement;
  const toolbar = document.createElement("div");
  toolbar.className = "excel-view-toolbar";
  const clear = document.createElement("button");
  clear.type = "button";
  clear.className = "outline-button";
  clear.textContent = "Clear table filters";
  clear.addEventListener("click", () => {
    filterRow.querySelectorAll('input[type="search"]').forEach((input) => { input.value = ""; input.dispatchEvent(new Event("input")); });
    filterRow.querySelectorAll('input[type="checkbox"]:checked').forEach((input) => { input.checked = false; input.dispatchEvent(new Event("change")); });
  });
  const download = document.createElement("button");
  download.type = "button";
  download.className = "primary-button";
  download.textContent = "Download this filtered view";
  download.addEventListener("click", async () => {
    const visible = filteredRows.map((row) => [...row.cells].map((cell) => cell.innerText.replace(/\s+/g, " ").trim()));
    const totals = table.tFoot?.rows?.[0] ? [...table.tFoot.rows[0].cells].map((cell) => cell.innerText.replace(/\s+/g, " ").trim()) : [];
    download.disabled = true;
    download.textContent = "Preparing Excel…";
    try {
      const response = await fetch("/api/export-view.xlsx", {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]')?.content || ""}, body: JSON.stringify({title: table.closest(".panel")?.querySelector("h2")?.textContent || `Filtered report ${tableIndex + 1}`, headers, rows: visible, totals})});
      if (!response.ok) throw new Error("Export failed");
      const blob = await response.blob();
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "filtered-report-view.xlsx";
      link.click();
      URL.revokeObjectURL(link.href);
    } catch (_) { window.alert("Unable to prepare this Excel view. Please try again."); }
    finally { download.disabled = false; download.textContent = "Download this filtered view"; }
  });
  toolbar.append(clear, download);
  if (wrap?.previousElementSibling?.classList.contains("excel-view-toolbar")) return;
  wrap.before(toolbar);
  if (bodyRows.length > 50) {
    const pager = document.createElement("nav");
    pager.className = "client-table-pagination";
    previousPage = document.createElement("button");
    previousPage.type = "button";
    previousPage.textContent = "Previous";
    previousPage.addEventListener("click", () => { currentPage -= 1; renderPage(); });
    pageLabel = document.createElement("span");
    nextPage = document.createElement("button");
    nextPage.type = "button";
    nextPage.textContent = "Next";
    nextPage.addEventListener("click", () => { currentPage += 1; renderPage(); });
    const size = document.createElement("select");
    [25, 50, 100].forEach((value) => { const option = document.createElement("option"); option.value = value; option.textContent = `${value} rows`; option.selected = value === pageSize; size.append(option); });
    size.addEventListener("change", () => { pageSize = Number(size.value); currentPage = 1; renderPage(); });
    pager.append(previousPage, pageLabel, nextPage, size);
    wrap.after(pager);
  }
  applyFilters();
});
