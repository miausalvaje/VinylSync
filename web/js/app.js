import { stateStore, fetchWithAuth, getAuthToken, setAuthToken } from "./state.js";
import { startVisualizer } from "./visualizer.js";
import { setupLyricsSync } from "./lyrics-sync.js";
import { setupBackground } from "./background.js";
import { setupFullscreen } from "./fullscreen.js";
import { setupSettings } from "./settings.js";
import { setupManualSearch } from "./manual-search.js";

const visualizerView = document.querySelector("#visualizer-view");
const lyricsView = document.querySelector("#lyrics-view");
const stateLabel = document.querySelector("#state-label");
const identifying = document.querySelector("#identifying-indicator");
const authDialog = document.querySelector("#auth-dialog");
const tabLoginBtn = document.querySelector("#tab-login-btn");
const tabRegisterBtn = document.querySelector("#tab-register-btn");
const loginForm = document.querySelector("#login-form");
const registerForm = document.querySelector("#register-form");
const loginError = document.querySelector("#login-error");
let forcedView = false;

function setView(name, manual = false) {
  if (manual) forcedView = true;
  if (!manual && forcedView) return;
  const lyrics = name === "lyrics";
  visualizerView.classList.toggle("view--active", !lyrics);
  lyricsView.classList.toggle("view--active", lyrics);
  lyricsView.setAttribute("aria-hidden", String(!lyrics));
}

function openAuthDialog() {
  if (authDialog && !authDialog.open) {
    authDialog.showModal();
  }
}

window.addEventListener("vinylsync:auth_required", openAuthDialog);

tabLoginBtn?.addEventListener("click", () => {
  tabLoginBtn.classList.add("auth-tab--active");
  tabRegisterBtn.classList.remove("auth-tab--active");
  loginForm.hidden = false;
  registerForm.hidden = true;
  if (loginError) loginError.hidden = true;
});

tabRegisterBtn?.addEventListener("click", () => {
  tabRegisterBtn.classList.add("auth-tab--active");
  tabLoginBtn.classList.remove("auth-tab--active");
  registerForm.hidden = false;
  loginForm.hidden = true;
  if (registerError) registerError.hidden = true;
});

loginForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const username = document.querySelector("#login-username-input")?.value.trim() || "";
  const password = document.querySelector("#login-password-input")?.value || "";

  try {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const data = await response.json();
    if (response.ok && data.token) {
      setAuthToken(data.token);
      if (loginError) loginError.hidden = true;
      authDialog?.close();
      stateStore.reconnect();
      window.dispatchEvent(new CustomEvent("vinylsync:user_changed", { detail: data.user }));
    } else {
      if (loginError) {
        loginError.hidden = false;
        loginError.textContent = data.detail || "Invalid credentials.";
      }
    }
  } catch {
    if (loginError) {
      loginError.hidden = false;
      loginError.textContent = "Unable to connect to server.";
    }
  }
});

registerForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const invite_code = document.querySelector("#register-invite-input")?.value.trim() || "";
  const username = document.querySelector("#register-username-input")?.value.trim() || "";
  const password = document.querySelector("#register-password-input")?.value || "";

  try {
    const response = await fetch("/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ invite_code, username, password }),
    });
    const data = await response.json();
    if (response.ok && data.token) {
      setAuthToken(data.token);
      if (registerError) registerError.hidden = true;
      authDialog?.close();
      stateStore.reconnect();
      window.dispatchEvent(new CustomEvent("vinylsync:user_changed", { detail: data.user }));
    } else {
      if (registerError) {
        registerError.hidden = false;
        registerError.textContent = data.detail || "Registration failed.";
      }
    }
  } catch {
    if (registerError) {
      registerError.hidden = false;
      registerError.textContent = "Unable to connect to server.";
    }
  }
});

// Check if authentication is required or if token needs validation on load
fetch("/api/health")
  .then((r) => r.json())
  .then(async (data) => {
    if (data.auth_required) {
      const token = getAuthToken();
      if (!token) {
        openAuthDialog();
        return;
      }
      const meRes = await fetchWithAuth("/api/auth/me");
      if (meRes.ok) {
        const meData = await meRes.json();
        if (meData.user) {
          window.dispatchEvent(new CustomEvent("vinylsync:user_changed", { detail: meData.user }));
        }
        stateStore.connect();
      } else {
        openAuthDialog();
      }
    } else {
      stateStore.connect();
    }
  })
  .catch(() => {
    stateStore.connect();
  });

stateStore.onState((payload) => {
  const state = payload.state || "idle";
  stateLabel.textContent = state === "playing" ? "Track identified" : state;
  identifying.hidden = state !== "identifying";
  if (state === "no_source") stateLabel.textContent = "Waiting for audio source…";
  if (state === "error") stateLabel.textContent = payload.message || "Server error";
  const track = payload.track;
  document.querySelector("#track-title").textContent = track?.title || "Waiting for track";
  document.querySelector("#track-artist").textContent = track?.artist || "Waiting for track";
  const locked = Boolean(track?.locked);
  document.querySelector("#track-lock").hidden = !locked;
  document.querySelector("#unlock-track").hidden = !locked;
  if (state === "playing") setView("lyrics");
  if (state === "idle" || state === "listening" || state === "no_source") setView("visualizer");
});

startVisualizer(stateStore);
setupLyricsSync(stateStore);
setupBackground(stateStore);
setupFullscreen();
setupSettings(stateStore);
setupManualSearch(stateStore);

document.querySelector("#unlock-track").addEventListener("click", async () => {
  await fetchWithAuth("/api/track/unlock", { method: "POST" });
});

let inactivityTimer;
function resetInactivity() {
  document.body.classList.remove("ui-idle");
  clearTimeout(inactivityTimer);
  const panelOpen = document.querySelector("#settings-panel")?.classList.contains("side-panel--open");
  const dialogOpen = document.querySelector("#search-dialog")?.open;
  const authOpen = authDialog?.open;
  if (panelOpen || dialogOpen || authOpen) return;
  inactivityTimer = setTimeout(() => document.body.classList.add("ui-idle"), 3000);
}
["mousemove", "keydown", "touchstart"].forEach((eventName) => {
  document.addEventListener(eventName, resetInactivity, { passive: true });
});
resetInactivity();

document.addEventListener("keydown", (event) => {
  if (event.target.matches("input, select, textarea")) return;
  if (event.key.toLowerCase() === "v") setView(lyricsView.classList.contains("view--active") ? "visualizer" : "lyrics", true);
  if (event.key.toLowerCase() === "s") {
    document.querySelector("#settings-button")?.click();
  }
  if (event.key.toLowerCase() === "m") document.querySelector("#manual-search-button").click();
  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    document.querySelector(event.key === "ArrowLeft" ? "#offset-down" : "#offset-up").click();
  }
  if (event.key === "Escape") {
    const settingsPanel = document.querySelector("#settings-panel");
    const searchDialog = document.querySelector("#search-dialog");
    if (settingsPanel?.classList.contains("side-panel--open")) {
      const closeBtn = settingsPanel.querySelector(".panel-close");
      closeBtn ? closeBtn.click() : settingsPanel.classList.remove("side-panel--open");
      return;
    }
    if (searchDialog?.open) {
      searchDialog.close();
      return;
    }
    forcedView = false;
    setView("visualizer");
  }
});
