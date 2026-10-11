export function setupLyricsSync(store) {
  const container = document.querySelector("#lyrics-lines");
  let lines = [];
  let startedAt = 0;
  store.onState((payload) => {
    if (!payload.lyrics || !payload.lyrics.text) {
      container.innerHTML = '<p class="lyrics-placeholder">No lyrics available.</p>';
      return;
    }
    lines = payload.lyrics.synced ? parseLrc(payload.lyrics.text) : payload.lyrics.text.split(/\r?\n/).map((text) => ({ time: 0, text }));
    startedAt = performance.now() / 1000 - (payload.elapsed_seconds || 0);
    renderLines();
  });
  function renderLines() {
    container.innerHTML = lines.map((line, index) => `<p data-index="${index}">${escapeHtml(line.text || " ")}</p>`).join("");
  }
  function tick() {
    if (lines.length && lines[0].time !== undefined) {
      const elapsed = performance.now() / 1000 - startedAt;
      let active = 0;
      lines.forEach((line, index) => { if (line.time <= elapsed) active = index; });
      container.querySelectorAll("p").forEach((element, index) => element.classList.toggle("lyrics-line--active", index === active));
      const activeElement = container.querySelector(".lyrics-line--active");
      activeElement?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    requestAnimationFrame(tick);
  }
  tick();
}

export function parseLrc(text) {
  return text.split(/\r?\n/).flatMap((line) => {
    const matches = [...line.matchAll(/\[(\d+):(\d{2})(?:[.:](\d{1,3}))?\]/g)];
    return matches.map((match) => ({
      time: Number(match[1]) * 60 + Number(match[2]) + Number((match[3] || "0").padEnd(3, "0")) / 1000,
      text: line.slice(matches[matches.length - 1].index + matches[matches.length - 1][0].length).trim(),
    }));
  }).sort((a, b) => a.time - b.time);
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[character]));
}
