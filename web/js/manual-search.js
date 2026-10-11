export function setupManualSearch(store) {
  const dialog = document.querySelector("#search-dialog");
  const input = document.querySelector("#track-search");
  const results = document.querySelector("#search-results");
  let timer;
  let controller;
  let candidates = [];
  let selected = 0;

  document.querySelector("#manual-search-button").addEventListener("click", () => {
    dialog.showModal();
    input.focus();
  });
  dialog.addEventListener("close", () => {
    input.value = "";
    results.replaceChildren();
  });
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(search, 300);
  });
  input.addEventListener("keydown", (event) => {
    if (!candidates.length) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      selected = (selected + 1) % candidates.length;
      renderResults();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      selected = (selected - 1 + candidates.length) % candidates.length;
      renderResults();
    } else if (event.key === "Enter") {
      event.preventDefault();
      choose(candidates[selected]);
    }
  });

  async function search() {
    const query = input.value.trim();
    if (query.length < 2) {
      results.innerHTML = '<p class="search-status">Type at least two characters.</p>';
      candidates = [];
      return;
    }
    controller?.abort();
    controller = new AbortController();
    results.innerHTML = '<p class="search-status">Searching…</p>';
    try {
      const response = await fetch(`/api/lyrics/search?q=${encodeURIComponent(query)}`, { signal: controller.signal });
      if (!response.ok) throw new Error("Search failed.");
      candidates = await response.json();
      selected = 0;
      renderResults();
    } catch (error) {
      if (error.name !== "AbortError") results.innerHTML = `<p class="search-status">${escapeHtml(error.message)}</p>`;
    }
  }

  function renderResults() {
    if (!candidates.length) {
      results.innerHTML = '<p class="search-status">No matching tracks found.</p>';
      return;
    }
    results.innerHTML = candidates.map((candidate, index) => (
      `<button class="search-result${index === selected ? " search-result--selected" : ""}" data-index="${index}" type="button"><strong>${escapeHtml(candidate.title)}</strong><span>${escapeHtml(candidate.artist)} · ${escapeHtml(candidate.album || "Unknown album")} ${candidate.synced ? "· synced" : ""}</span></button>`
    )).join("");
    results.querySelectorAll(".search-result").forEach((button) => (
      button.addEventListener("click", () => choose(candidates[Number(button.dataset.index)]))
    ));
  }

  async function choose(candidate) {
    const response = await fetch("/api/track/override", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: candidate.title,
        artist: candidate.artist,
        album: candidate.album,
        duration: candidate.duration,
        synced_lyrics: candidate.syncedLyrics,
        plain_lyrics: candidate.plainLyrics,
      }),
    });
    if (!response.ok) {
      results.innerHTML = '<p class="search-status">Unable to apply track correction.</p>';
      return;
    }
    dialog.close();
  }
}

function escapeHtml(value = "") {
  return value.replace(/[&<>"']/g, (character) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[character]
  ));
}
