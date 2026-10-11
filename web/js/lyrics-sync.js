export function setupLyricsSync(store) {
  const container = document.querySelector("#lyrics-lines");
  let lines = [];
  let startedAt = 0;
  let activeIndex = -1;

  store.onState((payload) => {
    if (payload.elapsed_seconds !== undefined) {
      startedAt = performance.now() / 1000 - Number(payload.elapsed_seconds || 0);
    }
    if (!payload.lyrics || !payload.lyrics.text) {
      lines = [];
      activeIndex = -1;
      container.innerHTML = '<p class="lyrics-placeholder">No lyrics available.</p>';
      return;
    }
    lines = payload.lyrics.synced
      ? parseLrc(payload.lyrics.text)
      : payload.lyrics.text.split(/\r?\n/).map((text) => ({ time: 0, text }));
    activeIndex = -1;
    renderLines();
    updateActiveLine(0);
  });

  function renderLines() {
    container.innerHTML = lines.map((line, index) => (
      `<p data-index="${index}" style="--distance: 0">${line.text ? escapeHtml(line.text) : '<span class="instrumental-pause" aria-label="Instrumental pause">•••</span>'}</p>`
    )).join("");
  }

  function updateActiveLine(index) {
    if (index === activeIndex) return;
    activeIndex = index;
    const elements = [...container.querySelectorAll("p")];
    elements.forEach((element, itemIndex) => {
      const distance = Math.abs(itemIndex - activeIndex);
      element.style.setProperty("--distance", distance);
      element.classList.toggle("lyrics-line--active", itemIndex === activeIndex);
    });
    const active = elements[activeIndex];
    if (active) {
      const offset = active.offsetTop - (container.clientHeight / 2) + active.offsetHeight / 2;
      container.style.setProperty("--lyrics-offset", `${Math.max(0, offset)}px`);
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

  function tick() {
    if (lines.length) updateActiveLine(findActiveIndex(performance.now() / 1000 - startedAt));
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
