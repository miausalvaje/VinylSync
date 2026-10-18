import { createPlaybackClock } from "./playback-clock.js";

export function formatTime(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = String(total % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${secs}` : `${minutes}:${secs}`;
}

/**
 * Read-only progress bar for the recognized track: there is nothing to seek
 * on a turntable, it only shows where the needle is.
 */
export function setupTrackProgress(store) {
  const root = document.querySelector("#track-progress");
  if (!root) return;
  const fill = root.querySelector(".track-progress__fill");
  const elapsedLabel = document.querySelector("#track-progress-elapsed");
  const durationLabel = document.querySelector("#track-progress-duration");
  // The playback clock, not the lyric one: the manual offset only shifts lyrics.
  const clock = createPlaybackClock("playback_elapsed_seconds");
  let duration = null;
  let running = false;
  let shownSecond = null;
  let shownScale = null;

  store.onState((state, payload = state) => {
    clock.update(payload);
    const track = state.track;
    const visible = Boolean(track) && state.state === "playing";
    const total = Number(track?.duration);
    duration = visible && total > 0 ? total : null;
    root.hidden = !visible;
    root.classList.toggle("track-progress--unknown", duration === null);
    durationLabel.textContent = duration === null ? "--:--" : formatTime(duration);
    if (duration === null) {
      root.removeAttribute("aria-valuemax");
    } else {
      root.setAttribute("aria-valuemax", String(Math.round(duration)));
    }
    shownSecond = null;
    shownScale = null;
    if (visible && !running) {
      running = true;
      requestAnimationFrame(render);
    }
  });

  function render() {
    if (root.hidden) {
      running = false;
      return;
    }
    // A pressing can run past the reference duration: hold the bar full.
    const position = Math.max(0, duration === null ? clock.now() : Math.min(clock.now(), duration));
    if (duration !== null) {
      const scale = (position / duration).toFixed(4);
      if (scale !== shownScale) {
        shownScale = scale;
        fill.style.transform = `scaleX(${scale})`;
      }
    }
    const second = Math.floor(position);
    if (second !== shownSecond) {
      shownSecond = second;
      elapsedLabel.textContent = formatTime(position);
      root.setAttribute("aria-valuenow", String(second));
      root.setAttribute(
        "aria-valuetext",
        duration === null ? formatTime(position) : `${formatTime(position)} of ${formatTime(duration)}`
      );
    }
    requestAnimationFrame(render);
  }
}
