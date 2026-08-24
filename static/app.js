document.querySelector("#menuButton")?.addEventListener("click", () => document.querySelector("#sidebar")?.classList.toggle("open"));
const todayLabel = document.querySelector("#todayLabel");
if (todayLabel) todayLabel.textContent = new Intl.DateTimeFormat("en-IN", {day: "numeric", month: "short", year: "numeric"}).format(new Date());
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
  const pollSync = async () => {
    try {
      const response = await fetch("/api/sync/status", {headers: {Accept: "application/json"}});
      const data = await response.json();
      if (data.status === "queued" || data.status === "running") {
        setSyncState(data.status, data.status === "queued" ? "Refresh queued…" : "Refreshing Zoho, BIK and Shopify…");
        pollTimer = setTimeout(pollSync, 3000);
      } else if (data.status === "success") {
        setSyncState(data.status, "All data refreshed successfully");
      } else if (data.status === "failed") {
        setSyncState(data.status, "Refresh finished with an error — please retry");
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
      pollSync();
    } catch (error) {
      setSyncState("failed", error.message || "Refresh request failed");
    }
  });
  pollSync();
}

const targetDialog = document.querySelector("#targetDialog");
const targetForm = document.querySelector("[data-target-form]");
document.querySelectorAll("[data-edit-target]").forEach((button) => {
  button.addEventListener("click", () => {
    targetForm.elements.employee_key.value = button.dataset.employeeKey;
    targetForm.elements.period.value = button.dataset.period;
    targetForm.elements.period_start.value = button.dataset.periodStart;
    targetForm.elements.target_amount.value = button.dataset.target;
    targetDialog.querySelector("[data-target-employee]").textContent = button.dataset.employeeName;
    targetDialog.querySelector("[data-target-period]").textContent = button.dataset.periodLabel;
    targetDialog.querySelector("[data-target-message]").textContent = "";
    targetDialog.showModal();
    targetForm.elements.target_amount.focus();
    targetForm.elements.target_amount.select();
  });
});
document.querySelectorAll("[data-close-target]").forEach((button) => button.addEventListener("click", () => targetDialog?.close()));
targetForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = targetForm.querySelector("button[type='submit']");
  const message = targetForm.querySelector("[data-target-message]");
  submit.disabled = true;
  message.textContent = "Saving…";
  try {
    const payload = Object.fromEntries(new FormData(targetForm));
    const response = await fetch("/api/shopify/targets", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-CSRF-Token": targetForm.dataset.csrf},
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Target could not be saved");
    message.textContent = "Target saved";
    window.setTimeout(() => window.location.reload(), 450);
  } catch (error) {
    message.textContent = error.message || "Target could not be saved";
    submit.disabled = false;
  }
});
