export function setupLyricsSync(store) {
  const container = document.querySelector("#lyrics-lines");
  const column = container ? container.parentElement : null;
  let lines = [];
  let isSynced = false;
  let startedAt = 0;
  let activeIndex = -1;
  let manualScrollTimeout = null;
  let isManualScrolling = false;
  let currentScrollOffset = 0;

  store.onState((payload) => {
    if (payload.elapsed_seconds !== undefined) {
      startedAt = performance.now() / 1000 - Number(payload.elapsed_seconds || 0);
    }
    if (!payload.lyrics || !payload.lyrics.text) {
      lines = [];
      isSynced = false;
      activeIndex = -1;
      isManualScrolling = false;
      currentScrollOffset = 0;
      container.classList.remove("lyrics-lines--plain");
      container.innerHTML = '<p class="lyrics-placeholder">No lyrics available.</p>';
      container.style.setProperty("--lyrics-offset", "0px");
      return;
    }
    isSynced = Boolean(payload.lyrics.synced);
    lines = isSynced
      ? parseLrc(payload.lyrics.text)
      : payload.lyrics.text.split(/\r?\n/).map((text) => ({ time: 0, text }));
    activeIndex = -1;
    isManualScrolling = false;
    currentScrollOffset = 0;
    container.style.setProperty("--lyrics-offset", "0px");
    container.classList.toggle("lyrics-lines--plain", !isSynced);
    renderLines();
    if (isSynced) {
      updateActiveLine(0, true);
    }
  });

  let activeElement = null;
  let nearElements = [];

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
      const elapsed = performance.now() / 1000 - startedAt;
      updateActiveLine(findActiveIndex(elapsed));
    }
    requestAnimationFrame(tick);
  }
  tick();
}

export function parseLrc(text) {
  return text.split(/\r?\n/).flatMap((line) => {
    const matches = [...line.matchAll(/\[(\d+):(\d{2})(?:[.:](\d{1,3}))?\]/g)];
    const textStart = matches.length ? matches[matches.length - 1].index + matches[matches.length - 1][0].length : 0;
    return matches.map((match) => ({
      time: Number(match[1]) * 60 + Number(match[2]) + Number((match[3] || "0").padEnd(3, "0")) / 1000,
      text: line.slice(textStart).trim(),
    }));
  }).sort((a, b) => a.time - b.time);
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (character) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[character]
  ));
}
