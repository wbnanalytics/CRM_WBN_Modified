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
        setSyncState(data.status, data.status === "queued" ? "Refresh queued…" : "Refreshing Zoho, BIK and Shopify…");
        pollTimer = setTimeout(() => pollSync(showFailure), 3000);
      } else if (data.status === "success") {
        setSyncState(data.status, "All data refreshed successfully");
      } else if (data.status === "failed") {
        setSyncState(data.status, showFailure ? "Refresh finished with an error — please retry" : "Manual refresh available");
      } else {
        setSyncState(data.status, "Zoho, BIK and Shopify");
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
