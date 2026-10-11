export function setupSettings(store) {
  const panel = document.querySelector("#settings-panel");
  const button = document.querySelector("#settings-button");
  const backdrop = document.querySelector("#settings-backdrop");
  const sourceSelect = document.querySelector("#source-select");
  const meter = document.querySelector("#level-meter");
  const thresholdMarker = document.querySelector("#threshold-marker");
  const settings = {};
  let saveTimer;

  if (!panel || !button || !sourceSelect || !meter || !thresholdMarker) {
    return { open: () => {}, close: () => {} };
  }

  const open = () => {
    panel.classList.add("side-panel--open");
    panel.setAttribute("aria-hidden", "false");
    backdrop?.classList.add("panel-backdrop--open");
    backdrop?.setAttribute("aria-hidden", "false");
  };

  const close = () => {
    panel.classList.remove("side-panel--open");
    panel.setAttribute("aria-hidden", "true");
    backdrop?.classList.remove("panel-backdrop--open");
    backdrop?.setAttribute("aria-hidden", "true");
  };

  button.addEventListener("click", (event) => {
    event.stopPropagation();
    panel.classList.contains("side-panel--open") ? close() : open();
  });

  const closeButton = panel.querySelector(".panel-close");
  closeButton?.addEventListener("click", (event) => {
    event.stopPropagation();
    close();
  });

  backdrop?.addEventListener("click", close);

  document.addEventListener("pointerdown", (event) => {
    if (panel.classList.contains("side-panel--open")) {
      if (!panel.contains(event.target) && !button.contains(event.target)) {
        close();
      }
    }
  });

  fetch("/api/sources")
    .then((response) => response.ok ? response.json() : Promise.reject(new Error("Unable to load audio sources.")))
    .then((sources) => {
      sourceSelect.replaceChildren();
      if (!sources.length) {
        sourceSelect.add(new Option("No audio sources available", ""));
        sourceSelect.disabled = true;
        return;
      }
      sources.forEach((source) => {
        sourceSelect.add(new Option(
          `${source.type === "loopback" ? "System audio" : "Microphone / Line-in"} — ${source.name}`,
          source.source_id,
        ));
      });
    })
    .catch((error) => showSettingsError(error.message));

  fetch("/api/settings")
    .then((response) => response.json())
    .then((payload) => {
      Object.assign(settings, payload);
      sourceSelect.value = payload.source_id || "";
      setValue("gain-slider", payload.gain_db, "gain-value", " dB");
      setValue("threshold-slider", payload.silence_enter_dbfs, "threshold-value", " dBFS");
      setValue("font-slider", payload.font_size, "font-value", "×");
      document.documentElement.style.setProperty("--lyrics-scale", payload.font_size ?? 1);
      updateOffsetDisplay();
      updateThresholdMarker(payload.silence_enter_dbfs);
    })
    .catch((error) => showSettingsError(error.message));

  sourceSelect.addEventListener("change", () => save({ source_id: sourceSelect.value }));
  bindSlider("gain-slider", "gain_db", "gain-value", " dB");
  bindSlider("threshold-slider", "silence_enter_dbfs", "threshold-value", " dBFS");
  bindSlider("font-slider", "font_size", "font-value", "×");
  document.querySelector("#offset-down").addEventListener("click", () => adjustOffset(-0.5));
  document.querySelector("#offset-up").addEventListener("click", () => adjustOffset(0.5));

  store.onSpectrum((payload) => {
    if (!panel.classList.contains("side-panel--open")) return;
    const dbfs = Math.max(-80, Math.min(0, Number(payload.dbfs ?? -80)));
    const level = ((dbfs + 80) / 80) * 100;
    meter.style.setProperty("--level", `${Math.max(0, Math.min(100, level))}%`);
  });

  function bindSlider(id, key, output, suffix) {
    document.querySelector(`#${id}`).addEventListener("input", (event) => {
      const value = Number(event.target.value);
      settings[key] = value;
      document.querySelector(`#${output}`).textContent = `${value}${suffix}`;
      if (key === "silence_enter_dbfs") updateThresholdMarker(value);
      if (key === "font_size") document.documentElement.style.setProperty("--lyrics-scale", value);
      queueSave({ [key]: value });
    });
  }

  function setValue(id, value, output, suffix) {
    document.querySelector(`#${id}`).value = value;
    document.querySelector(`#${output}`).textContent = `${value}${suffix}`;
  }

  function adjustOffset(delta) {
    const value = Math.round((Number(settings.manual_offset_seconds || 0) + delta) * 10) / 10;
    settings.manual_offset_seconds = value;
    updateOffsetDisplay();
    save({ manual_offset_seconds: value });
  }

  function updateOffsetDisplay() {
    document.querySelector("#offset-value").textContent =
      `${Number(settings.manual_offset_seconds || 0).toFixed(1)} s`;
  }

  function updateThresholdMarker(value) {
    thresholdMarker.style.left = `${((Number(value) + 80) / 80) * 100}%`;
  }

  function queueSave(patch) {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => save(patch), 250);
  }

  function save(patch) {
    Object.assign(settings, patch);
    fetch("/api/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }).catch((error) => showSettingsError(error.message));
  }

  function showSettingsError(message) {
    const error = document.querySelector("#settings-error");
    error.textContent = message;
    error.hidden = false;
  }

  return { open, close };
}
