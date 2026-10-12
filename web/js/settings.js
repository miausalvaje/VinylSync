import { fetchWithAuth, setAuthToken } from "./state.js";
import { audioCaptureManager } from "./audio-capture.js";

export function setupSettings(store) {
  const panel = document.querySelector("#settings-panel");
  const button = document.querySelector("#settings-button");
  const backdrop = document.querySelector("#settings-backdrop");
  const sourceSelect = document.querySelector("#source-select");
  const micSelect = document.querySelector("#mic-select");
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
    refreshMicrophoneDevices();
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

  // Check secure context
  if (window.isSecureContext === false) {
    const warning = document.querySelector("#secure-context-warning");
    if (warning) warning.hidden = false;
  }

  // Audio source mode radios
  const modeServer = document.querySelector("#mode-server");
  const modeMic = document.querySelector("#mode-mic");
  const modeDisplay = document.querySelector("#mode-display");
  const serverGroup = document.querySelector("#server-source-group");
  const micGroup = document.querySelector("#browser-mic-group");
  const displayGroup = document.querySelector("#browser-display-group");

  function setMode(mode) {
    if (serverGroup) serverGroup.hidden = mode !== "server";
    if (micGroup) micGroup.hidden = mode !== "mic";
    if (displayGroup) displayGroup.hidden = mode !== "display";

    if (mode === "server") {
      if (audioCaptureManager.isCapturing) {
        audioCaptureManager.stop();
      }
      if (sourceSelect.value) {
        save({ source_id: sourceSelect.value });
      }
    } else if (mode === "mic") {
      refreshMicrophoneDevices();
    }
  }

  modeServer?.addEventListener("change", () => modeServer.checked && setMode("server"));
  modeMic?.addEventListener("change", () => modeMic.checked && setMode("mic"));
  modeDisplay?.addEventListener("change", () => modeDisplay.checked && setMode("display"));

  async function refreshMicrophoneDevices() {
    if (!micSelect) return;
    const devices = await audioCaptureManager.getAudioInputDevices();
    micSelect.replaceChildren();
    if (!devices.length) {
      micSelect.add(new Option("Default microphone", ""));
    } else {
      devices.forEach((dev, idx) => {
        micSelect.add(new Option(dev.label || `Microphone ${idx + 1}`, dev.deviceId));
      });
    }
  }

  // Browser capture buttons
  const startMicBtn = document.querySelector("#start-mic-btn");
  const stopMicBtn = document.querySelector("#stop-mic-btn");
  const startDisplayBtn = document.querySelector("#start-display-btn");
  const stopDisplayBtn = document.querySelector("#stop-display-btn");
  const takeoverBtn = document.querySelector("#takeover-btn");

  startMicBtn?.addEventListener("click", async () => {
    try {
      await audioCaptureManager.startMicrophone(micSelect?.value || null);
    } catch (err) {
      showSettingsError(err.message);
    }
  });

  stopMicBtn?.addEventListener("click", () => audioCaptureManager.stop());

  startDisplayBtn?.addEventListener("click", async () => {
    try {
      await audioCaptureManager.startDisplayAudio();
    } catch (err) {
      showSettingsError(err.message);
    }
  });

  stopDisplayBtn?.addEventListener("click", () => audioCaptureManager.stop());

  takeoverBtn?.addEventListener("click", () => audioCaptureManager.requestTakeover());

  // Capturer status handling
  function updateCapturerUI(capturer) {
    const hasCapturer = Boolean(capturer?.has_capturer);
    const capturerId = capturer?.capturer_id;
    const isMe = hasCapturer && capturerId === audioCaptureManager.clientId;

    const hudBanner = document.querySelector("#capturer-banner");
    const hudText = document.querySelector("#capturer-banner-text");
    const hudBtn = document.querySelector("#capturer-banner-btn");

    const statusBox = document.querySelector("#browser-capture-status");
    const statusText = document.querySelector("#capture-status-text");
    const takeoverButton = document.querySelector("#takeover-btn");

    if (isMe) {
      if (hudBanner) {
        hudBanner.hidden = false;
        hudText.textContent = "Capturing from this browser";
        hudBtn.textContent = "Stop";
        hudBtn.onclick = () => audioCaptureManager.stop();
      }
      if (statusBox) {
        statusBox.hidden = false;
        statusText.textContent = "This browser is currently capturing audio";
        if (takeoverButton) takeoverButton.hidden = true;
      }
      if (stopMicBtn) stopMicBtn.hidden = audioCaptureManager.mode !== "mic";
      if (startMicBtn) startMicBtn.hidden = audioCaptureManager.mode === "mic";
      if (stopDisplayBtn) stopDisplayBtn.hidden = audioCaptureManager.mode !== "display";
      if (startDisplayBtn) startDisplayBtn.hidden = audioCaptureManager.mode === "display";
    } else if (hasCapturer) {
      if (hudBanner) {
        hudBanner.hidden = false;
        hudText.textContent = "Another device is capturing audio";
        hudBtn.textContent = "Take over";
        hudBtn.onclick = () => audioCaptureManager.requestTakeover();
      }
      if (statusBox) {
        statusBox.hidden = false;
        statusText.textContent = "Another device is currently capturing audio";
        if (takeoverButton) {
          takeoverButton.hidden = false;
          takeoverButton.onclick = () => audioCaptureManager.requestTakeover();
        }
      }
      if (stopMicBtn) stopMicBtn.hidden = true;
      if (startMicBtn) startMicBtn.hidden = false;
      if (stopDisplayBtn) stopDisplayBtn.hidden = true;
      if (startDisplayBtn) startDisplayBtn.hidden = false;
    } else {
      if (hudBanner) hudBanner.hidden = true;
      if (statusBox) statusBox.hidden = true;
      if (stopMicBtn) stopMicBtn.hidden = true;
      if (startMicBtn) startMicBtn.hidden = false;
      if (stopDisplayBtn) stopDisplayBtn.hidden = true;
      if (startDisplayBtn) startDisplayBtn.hidden = false;
    }
  }

  store.onState((payload) => {
    updateCapturerUI(payload.capturer);
    const source = payload.source;
    if (source && source.source_id === "browser" && !modeMic?.checked && !modeDisplay?.checked) {
      // If server is using browser source, sync mode if not capturing locally
      if (audioCaptureManager.isCapturing) {
        if (audioCaptureManager.mode === "mic" && modeMic) modeMic.checked = true;
        if (audioCaptureManager.mode === "display" && modeDisplay) modeDisplay.checked = true;
      }
    }
  });

  audioCaptureManager.onStateChange((state, detail) => {
    if (state === "capturing") {
      updateCapturerUI({ has_capturer: true, capturer_id: audioCaptureManager.clientId });
    } else if (state === "idle") {
      updateCapturerUI({ has_capturer: false, capturer_id: null });
    } else if (state === "error") {
      showSettingsError(detail.message || "Capture error");
    }
  });

  // Load sources from server
  fetchWithAuth("/api/sources")
    .then((response) => response.ok ? response.json() : Promise.reject(new Error("Unable to load audio sources.")))
    .then((sources) => {
      sourceSelect.replaceChildren();
      const serverSources = sources.filter((s) => s.type !== "browser");
      if (!serverSources.length) {
        sourceSelect.add(new Option("No local server audio devices", ""));
        sourceSelect.disabled = true;
        return;
      }
      serverSources.forEach((source) => {
        sourceSelect.add(new Option(
          `${source.type === "loopback" ? "System audio" : "Microphone / Line-in"} — ${source.name}`,
          source.source_id,
        ));
      });
    })
    .catch((error) => showSettingsError(error.message));

  // Load user settings
  fetchWithAuth("/api/settings")
    .then((response) => response.json())
    .then((payload) => {
      Object.assign(settings, payload);
      if (payload.source_id && payload.source_id !== "browser") {
        sourceSelect.value = payload.source_id;
        if (modeServer) modeServer.checked = true;
        setMode("server");
      } else if (payload.source_id === "browser") {
        if (modeMic) modeMic.checked = true;
        setMode("mic");
      }
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
    fetchWithAuth("/api/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }).catch((error) => showSettingsError(error.message));
  }

  function showSettingsError(message) {
    const error = document.querySelector("#settings-error");
    if (error) {
      error.textContent = message;
      error.hidden = false;
    }
  }

  // User session and invitations UI
  const userAccountSection = document.querySelector("#user-account-section");
  const loggedUserName = document.querySelector("#logged-user-name");
  const logoutBtn = document.querySelector("#logout-btn");
  const adminInvitationsArea = document.querySelector("#admin-invitations-area");
  const createInviteBtn = document.querySelector("#create-invite-btn");
  const invitationsList = document.querySelector("#invitations-list");

  let currentUser = null;

  async function refreshInvitations() {
    if (!adminInvitationsArea || adminInvitationsArea.hidden) return;
    try {
      const res = await fetchWithAuth("/api/invitations");
      if (!res.ok) return;
      const invites = await res.json();
      invitationsList.replaceChildren();
      if (!invites.length) {
        const empty = document.createElement("p");
        empty.className = "settings-help";
        empty.textContent = "No active invitations. Click '+ Generate code' to create one.";
        invitationsList.appendChild(empty);
        return;
      }
      invites.forEach((inv) => {
        const item = document.createElement("div");
        item.className = "invite-item";

        const left = document.createElement("div");
        const codeEl = document.createElement("div");
        codeEl.className = "invite-item-code";
        codeEl.textContent = inv.code;
        const statusEl = document.createElement("div");
        statusEl.className = `invite-item-status ${inv.is_used ? "invite-item-status--used" : ""}`;
        statusEl.textContent = inv.is_used ? `Used by ${inv.used_by}` : "Available";
        left.appendChild(codeEl);
        left.appendChild(statusEl);

        const right = document.createElement("div");
        right.className = "invite-item-actions";

        if (!inv.is_used) {
          const copyBtn = document.createElement("button");
          copyBtn.type = "button";
          copyBtn.className = "invite-btn-copy";
          copyBtn.textContent = "Copy";
          copyBtn.onclick = () => {
            navigator.clipboard?.writeText(inv.code);
            copyBtn.textContent = "Copied!";
            setTimeout(() => (copyBtn.textContent = "Copy"), 2000);
          };
          right.appendChild(copyBtn);

          const delBtn = document.createElement("button");
          delBtn.type = "button";
          delBtn.className = "invite-btn-del";
          delBtn.textContent = "×";
          delBtn.title = "Delete invitation";
          delBtn.onclick = async () => {
            await fetchWithAuth(`/api/invitations/${inv.code}`, { method: "DELETE" });
            refreshInvitations();
          };
          right.appendChild(delBtn);
        }

        item.appendChild(left);
        item.appendChild(right);
        invitationsList.appendChild(item);
      });
    } catch {
      // Ignore
    }
  }

  function updateUserUI(user) {
    currentUser = user;
    if (!userAccountSection) return;
    if (user) {
      userAccountSection.hidden = false;
      if (loggedUserName) {
        loggedUserName.textContent = `${user.username} (${user.role})`;
      }
      if (adminInvitationsArea) {
        adminInvitationsArea.hidden = user.role !== "admin";
        if (user.role === "admin") {
          refreshInvitations();
        }
      }
    } else {
      userAccountSection.hidden = true;
    }
  }

  logoutBtn?.addEventListener("click", () => {
    setAuthToken("");
    updateUserUI(null);
    window.location.reload();
  });

  createInviteBtn?.addEventListener("click", async () => {
    try {
      const res = await fetchWithAuth("/api/invitations", { method: "POST" });
      if (res.ok) {
        refreshInvitations();
      }
    } catch (err) {
      showSettingsError(err.message);
    }
  });

  window.addEventListener("vinylsync:user_changed", (event) => {
    updateUserUI(event.detail);
  });

  return { open, close };
}
