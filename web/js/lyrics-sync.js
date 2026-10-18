export function setupLyricsSync(store) {
  const container = document.querySelector("#lyrics-lines");
  const column = container ? container.parentElement : null;
  let lines = [];
  let isSynced = false;
  let activeIndex = -1;
  let manualScrollTimeout = null;
  let isManualScrolling = false;
  let currentScrollOffset = 0;
  // Lyric clock: position at clockAt (performance.now() seconds), advancing at
  // the turntable speed measured by the server.
  let clockElapsed = 0;
  let clockAt = performance.now() / 1000;
  let clockRate = 1;
  let renderedKey = null;
  let lyricsEnded = false;

  function currentElapsed() {
    return clockElapsed + (performance.now() / 1000 - clockAt) * clockRate;
  }

  store.onState((state, payload = state) => {
    if (payload.elapsed_seconds !== undefined) {
      clockElapsed = Number(payload.elapsed_seconds || 0);
      clockAt = performance.now() / 1000;
      clockRate = Number(payload.playback_rate) || 1;
    }
    const lyrics = state.lyrics;
    const key = lyrics && lyrics.text ? `${lyrics.synced ? "s" : "p"}:${lyrics.text}` : "";
    // Most state events only move the clock; rebuilding the lines would
    // flash the view back to the first verse.
    if (key === renderedKey) {
      return;
    }
    renderedKey = key;
    activeIndex = -1;
    isManualScrolling = false;
    currentScrollOffset = 0;
    lyricsEnded = false;
    container.style.setProperty("--lyrics-offset", "0px");
    if (!key) {
      lines = [];
      isSynced = false;
      container.classList.remove("lyrics-lines--plain");
      container.innerHTML = '<p class="lyrics-placeholder">No lyrics available.</p>';
      return;
    }
    isSynced = Boolean(lyrics.synced);
    lines = isSynced
      ? parseLrc(lyrics.text)
      : lyrics.text.split(/\r?\n/).map((text) => ({ time: 0, text }));
    container.classList.toggle("lyrics-lines--plain", !isSynced);
    renderLines();
    if (isSynced && lines.length) {
      updateActiveLine(findActiveIndex(currentElapsed()), true);
    }
  });

  let activeElement = null;
  let nearElements = [];
  const LYRICS_END_GRACE_SECONDS = 3;

  function renderLines() {
    activeElement = null;
    nearElements = [];
    container.innerHTML = lines.map((line, index) => (
      `<p data-index="${index}">${line.text ? escapeHtml(line.text) : '<span class="instrumental-pause" aria-label="Instrumental pause">•••</span>'}</p>`
    )).join("");
  }

  function scrollToActive(immediate = false) {
    if (!container || !column) return;
    const elements = container.children;
    const active = elements[activeIndex];
    if (active) {
      const viewportHeight = column.clientHeight || window.innerHeight * 0.74;
      const focalPoint = Math.round(viewportHeight * 0.36);
      currentScrollOffset = Math.max(0, Math.round(active.offsetTop - focalPoint));
      if (immediate) {
        container.style.transition = "none";
        container.style.setProperty("--lyrics-offset", `${currentScrollOffset}px`);
        void container.offsetHeight;
        container.style.transition = "";
      } else {
        container.style.setProperty("--lyrics-offset", `${currentScrollOffset}px`);
      }
    }
  }

  function updateActiveLine(index, immediate = false) {
    if (index === activeIndex) return;
    activeIndex = index;

    if (activeElement) {
      activeElement.classList.remove("lyrics-line--active");
    }
    nearElements.forEach((el) => {
      el.classList.remove("lyrics-line--near");
    });
    nearElements = [];

    const elements = container.children;
    if (activeIndex >= 0 && activeIndex < elements.length) {
      activeElement = elements[activeIndex];
      activeElement.classList.add("lyrics-line--active");

      if (activeIndex > 0) {
        const prev = elements[activeIndex - 1];
        prev.classList.add("lyrics-line--near");
        nearElements.push(prev);
      }
      if (activeIndex + 1 < elements.length) {
        const next = elements[activeIndex + 1];
        next.classList.add("lyrics-line--near");
        nearElements.push(next);
      }
    } else {
      activeElement = null;
    }

    if (!isManualScrolling) {
      scrollToActive(immediate);
    }
  }

  function findActiveIndex(elapsed) {
    let low = 0;
    let high = lines.length - 1;
    let result = 0;
    while (low <= high) {
      const middle = Math.floor((low + high) / 2);
      if (lines[middle].time <= elapsed) {
        result = middle;
        low = middle + 1;
      } else {
        high = middle - 1;
      }
    }
    return result;
  }

  column?.addEventListener("wheel", (event) => {
    isManualScrolling = true;
    container.style.transition = "transform 0.08s ease-out";
    currentScrollOffset = Math.max(0, currentScrollOffset + event.deltaY);
    container.style.setProperty("--lyrics-offset", `${currentScrollOffset}px`);
    clearTimeout(manualScrollTimeout);
    manualScrollTimeout = setTimeout(() => {
      isManualScrolling = false;
      container.style.transition = "";
      if (isSynced && activeIndex >= 0) {
        scrollToActive();
      }
    }, 3500);
  }, { passive: true });

  let touchY = null;
  function endTouchPause() {
    isManualScrolling = false;
    container.style.transition = "";
    if (isSynced && activeIndex >= 0) {
      scrollToActive();
    }
  }
  column?.addEventListener("touchstart", (event) => {
    if (event.touches.length === 1) {
      touchY = event.touches[0].clientY;
    }
  }, { passive: true });
  column?.addEventListener("touchmove", (event) => {
    if (touchY === null || event.touches.length !== 1) return;
    const y = event.touches[0].clientY;
    const delta = touchY - y;
    touchY = y;
    isManualScrolling = true;
    container.style.transition = "transform 0.08s ease-out";
    const maxOffset = Math.max(0, container.scrollHeight - (column.clientHeight || 1));
    currentScrollOffset = Math.min(maxOffset, Math.max(0, currentScrollOffset + delta));
    container.style.setProperty("--lyrics-offset", `${currentScrollOffset}px`);
    clearTimeout(manualScrollTimeout);
    manualScrollTimeout = setTimeout(endTouchPause, 3500);
  }, { passive: true });
  column?.addEventListener("touchend", () => {
    touchY = null;
  }, { passive: true });
  column?.addEventListener("touchcancel", () => {
    touchY = null;
  }, { passive: true });

  container?.addEventListener("click", (event) => {
    const p = event.target.closest("p[data-index]");
    if (!p) return;
    const index = Number(p.dataset.index);
    if (isNaN(index)) return;
    if (isSynced) {
      isManualScrolling = true;
      clearTimeout(manualScrollTimeout);
      const viewportHeight = column ? column.clientHeight : window.innerHeight * 0.74;
      const focalPoint = viewportHeight * 0.36;
      currentScrollOffset = Math.max(0, p.offsetTop - focalPoint + (p.offsetHeight * 0.1));
      container.style.transition = "";
      container.style.setProperty("--lyrics-offset", `${currentScrollOffset}px`);
      manualScrollTimeout = setTimeout(() => {
        isManualScrolling = false;
        if (isSynced && activeIndex >= 0) {
          scrollToActive();
        }
      }, 5000);
    }
  });

  window.addEventListener("resize", () => {
    if (!isManualScrolling && isSynced && activeIndex >= 0) {
      scrollToActive();
    }
  });

  function tick() {
    if (isSynced && lines.length) {
      const elapsed = currentElapsed();
      const pastEnd = elapsed >= lines[lines.length - 1].time + LYRICS_END_GRACE_SECONDS;
      if (pastEnd && !lyricsEnded) {
        lyricsEnded = true;
        activeIndex = -1;
        container.innerHTML = '<p class="lyrics-placeholder">Waiting for next track…</p>';
        container.style.setProperty("--lyrics-offset", "0px");
      } else if (!pastEnd && lyricsEnded) {
        // The clock was corrected back into the song.
        lyricsEnded = false;
        renderLines();
        updateActiveLine(findActiveIndex(elapsed), true);
      } else if (!lyricsEnded) {
        updateActiveLine(findActiveIndex(elapsed));
      }
    }
    requestAnimationFrame(tick);
  }
  tick();
}

export function parseLrc(text) {
  return text.split(/\r?\n/).flatMap((line) => {
    const matches = [...line.matchAll(/\[(\d+):(\d{2})(?:[.:](\d{1,3}))?\]/g)];
    if (!matches.length) {
      return [];
    }
    const results = [];
    for (let i = 0; i < matches.length; i++) {
      const match = matches[i];
      const start = match.index + match[0].length;
      const end = i + 1 < matches.length ? matches[i + 1].index : line.length;
      const segment = line.slice(start, end).trim();
      // Skip metadata tags like [ti:...], [ar:...], etc.
      if (segment && segment.startsWith("[") && /^\[[A-Za-z]+:.*\]$/.test(segment)) {
        continue;
      }
      const time = Number(match[1]) * 60 + Number(match[2]) + Number((match[3] || "0").padEnd(3, "0")) / 1000;
      results.push({ time, text: segment });
    }
    return results;
  }).sort((a, b) => a.time - b.time);
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (character) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[character]
  ));
}
