const toast = document.querySelector("#toast");
function showToast(message) {
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), 3200);
}
document.querySelector("#menuButton")?.addEventListener("click", () => document.querySelector("#sidebar")?.classList.toggle("open"));
document.querySelectorAll("[data-demo-action]").forEach((button) => {
  button.addEventListener("click", () => {
    const messages = {
      sync: "Report sync will be enabled after field mapping is complete.",
      connect: "Connector credentials are present. Live API verification is the next step.",
      configure: "Next, share a sample report so we can map its fields.",
    };
    showToast(messages[button.dataset.demoAction] || "This action is coming next.");
  });
});
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
