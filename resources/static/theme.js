(() => {
  const config = window.CPE_THEME_CONFIG;
  const defaults = { mode: "dark", scheme: "violet-blue" };
  const schemes = new Set(config.themes.map(theme => theme.id));
  function stored(key) {
    try { return localStorage.getItem(key); } catch { return null; }
  }
  let current = config.preferences || {
    mode: stored("dashboardTheme") || defaults.mode,
    scheme: stored("dashboardScheme") || defaults.scheme,
  };
  let saveQueue = Promise.resolve();
  function apply(preferences) {
    current = { ...current, ...preferences };
    if (!["dark", "light"].includes(current.mode)) current.mode = defaults.mode;
    if (!schemes.has(current.scheme)) current.scheme = defaults.scheme;
    document.documentElement.dataset.cpeMode = current.mode;
    document.documentElement.dataset.cpeScheme = current.scheme;
    document.documentElement.style.colorScheme = current.mode;
    document.body?.classList.toggle("light-theme", current.mode === "light");
    try {
      localStorage.setItem("dashboardTheme", current.mode);
      localStorage.setItem("dashboardScheme", current.scheme);
    } catch { /* Theme still works when browser storage is disabled. */ }
    document.querySelectorAll("[data-theme-toggle]").forEach(button => {
      button.title = current.mode === "dark" ? "Switch to light mode" : "Switch to dark mode";
      button.setAttribute("aria-label", button.title);
      button.setAttribute("aria-pressed", String(current.mode === "light"));
    });
    const selected = config.themes.find(theme => theme.id === current.scheme);
    const swatches = document.getElementById("themeSwatches");
    if (swatches) {
      swatches.replaceChildren(...selected.colors.map(color => {
        const swatch = document.createElement("span");
        swatch.className = "scheme-swatch";
        const chip = document.createElement("span");
        chip.style.backgroundColor = color;
        const code = document.createElement("span");
        code.textContent = color;
        swatch.append(chip, code);
        return swatch;
      }));
    }
    window.dispatchEvent(new CustomEvent("cpe-theme-change", { detail: { ...current } }));
  }
  function save() {
    if (!config.authenticated) return Promise.resolve();
    const preferences = { ...current };
    saveQueue = saveQueue.catch(() => {}).then(async () => {
      const response = await fetch(config.saveUrl, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": config.csrf },
        body: JSON.stringify(preferences),
      });
      if (!response.ok) throw new Error("Could not save appearance. Please try again.");
    });
    return saveQueue;
  }
  function report(error) {
    const status = document.getElementById("themeSaveStatus");
    if (status) { status.textContent = error.message; status.hidden = false; }
    else if (typeof window.showErrorPopup === "function") window.showErrorPopup("Appearance error", error.message);
  }
  window.CPETheme = { apply, save, get current() { return { ...current }; } };
  apply(current);
  document.addEventListener("DOMContentLoaded", () => {
    apply(current);
    const header = document.querySelector(".dashboard-page .topbar");
    if (header) {
      const sizeHeader = () => document.documentElement.style.setProperty("--dashboard-header-height", `${header.getBoundingClientRect().height}px`);
      new ResizeObserver(sizeHeader).observe(header);
      sizeHeader();
    }
    const mode = document.getElementById("appearanceMode");
    const scheme = document.getElementById("colorScheme");
    if (mode) mode.value = current.mode;
    if (scheme) scheme.value = current.scheme;
    document.querySelectorAll("[data-theme-toggle]").forEach(button => {
      button.addEventListener("click", () => {
        apply({ mode: current.mode === "dark" ? "light" : "dark" });
        if (mode) mode.value = current.mode;
        save().catch(report);
      });
    });
    mode?.addEventListener("change", () => apply({ mode: mode.value }));
    scheme?.addEventListener("change", () => apply({ scheme: scheme.value }));
  });
})();
