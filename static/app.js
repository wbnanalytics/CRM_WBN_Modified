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
