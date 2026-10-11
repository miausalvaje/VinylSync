export function setupSettings(store) {
  const panel = document.querySelector("#settings-panel");
  const button = document.querySelector("#settings-button");
  const sourceSelect = document.querySelector("#source-select");
  const settings = {};
  button.addEventListener("click", () => panel.classList.toggle("side-panel--open"));
  fetch("/api/sources").then((response) => response.json()).then((sources) => {
    sources.forEach((source) => {
      const option = document.createElement("option");
      option.value = source.source_id;
      option.textContent = `${source.type === "loopback" ? "System audio" : "Microphone / Line-in"} — ${source.name}`;
      sourceSelect.append(option);
    });
  });
  fetch("/api/settings").then((response) => response.json()).then((payload) => {
    Object.assign(settings, payload);
    sourceSelect.value = payload.source_id || "";
    setValue("gain-slider", payload.gain_db, "gain-value", " dB");
    setValue("threshold-slider", payload.silence_enter_dbfs, "threshold-value", " dBFS");
    setValue("font-slider", payload.font_size, "font-value", "×");
    document.querySelector("#offset-value").textContent = `${payload.manual_offset_seconds || 0} s`;
  });
  sourceSelect.addEventListener("change", () => save({ source_id: sourceSelect.value }));
  bindSlider("gain-slider", "gain_db", "gain-value", " dB");
  bindSlider("threshold-slider", "silence_enter_dbfs", "threshold-value", " dBFS");
  bindSlider("font-slider", "font_size", "font-value", "×");
  document.querySelector("#offset-down").addEventListener("click", () => adjustOffset(-0.5));
  document.querySelector("#offset-up").addEventListener("click", () => adjustOffset(0.5));
  function bindSlider(id, key, output, suffix) {
    document.querySelector(`#${id}`).addEventListener("input", (event) => {
      const value = Number(event.target.value);
      settings[key] = value;
      document.querySelector(`#${output}`).textContent = `${value}${suffix}`;
      save({ [key]: value });
    });
  }
  function setValue(id, value, output, suffix) {
    document.querySelector(`#${id}`).value = value;
    document.querySelector(`#${output}`).textContent = `${value}${suffix}`;
  }
  function adjustOffset(delta) {
    const value = Number(settings.manual_offset_seconds || 0) + delta;
    settings.manual_offset_seconds = Math.round(value * 10) / 10;
    document.querySelector("#offset-value").textContent = `${settings.manual_offset_seconds} s`;
    save({ manual_offset_seconds: settings.manual_offset_seconds });
  }
  function save(patch) {
    Object.assign(settings, patch);
    fetch("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings) })
      .catch(() => store.setConnection(false));
  }
}
