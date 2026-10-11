import { stateStore } from "./state.js";
import { startVisualizer } from "./visualizer.js";
import { setupLyricsSync } from "./lyrics-sync.js";
import { setupBackground } from "./background.js";
import { setupFullscreen } from "./fullscreen.js";
import { setupSettings } from "./settings.js";
import { setupManualSearch } from "./manual-search.js";

const visualizerView = document.querySelector("#visualizer-view");
const lyricsView = document.querySelector("#lyrics-view");
const stateLabel = document.querySelector("#state-label");
let forcedView = false;

function setView(name, manual = false) {
  if (manual) forcedView = true;
  if (!manual && forcedView) return;
  const lyrics = name === "lyrics";
  visualizerView.classList.toggle("view--active", !lyrics);
  lyricsView.classList.toggle("view--active", lyrics);
  lyricsView.setAttribute("aria-hidden", String(!lyrics));
}

stateStore.onState((payload) => {
  const state = payload.state || "idle";
  stateLabel.textContent = state === "playing" ? "Track identified" : state;
  const track = payload.track;
  document.querySelector("#track-title").textContent = track?.title || "Waiting for track";
  document.querySelector("#track-artist").textContent = track?.artist || "Waiting for track";
  if (state === "playing") setView("lyrics");
  if (state === "idle" || state === "listening") setView("visualizer");
});

stateStore.connect();
startVisualizer(stateStore);
setupLyricsSync(stateStore);
setupBackground(stateStore);
setupFullscreen();
setupSettings(stateStore);
setupManualSearch(stateStore);

document.addEventListener("keydown", (event) => {
  if (event.target.matches("input, select, textarea")) return;
  if (event.key.toLowerCase() === "v") setView(lyricsView.classList.contains("view--active") ? "visualizer" : "lyrics", true);
  if (event.key.toLowerCase() === "s") document.querySelector("#settings-panel").classList.add("side-panel--open");
  if (event.key === "Escape") {
    forcedView = false;
    setView("visualizer");
    document.querySelector("#settings-panel").classList.remove("side-panel--open");
  }
});
