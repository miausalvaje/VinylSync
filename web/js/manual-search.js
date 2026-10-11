export function setupManualSearch(store) {
  const dialog = document.querySelector("#search-dialog");
  const input = document.querySelector("#track-search");
  const results = document.querySelector("#search-results");
  document.querySelector("#manual-search-button").addEventListener("click", () => {
    dialog.showModal();
    input.focus();
  });
  let timer;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      if (input.value.trim().length < 2) return;
      const response = await fetch(`/api/lyrics/search?q=${encodeURIComponent(input.value.trim())}`);
      const candidates = await response.json();
      results.innerHTML = candidates.map((candidate, index) => `<button class="search-result" data-index="${index}" type="button"><strong>${escapeHtml(candidate.title)}</strong><span>${escapeHtml(candidate.artist)} · ${escapeHtml(candidate.album || "Unknown album")} ${candidate.synced ? "· synced" : ""}</span></button>`).join("");
      results.querySelectorAll(".search-result").forEach((button) => button.addEventListener("click", async () => {
        const candidate = candidates[Number(button.dataset.index)];
        await fetch("/api/track/override", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: candidate.title, artist: candidate.artist, album: candidate.album, duration: candidate.duration, synced_lyrics: candidate.syncedLyrics, plain_lyrics: candidate.plainLyrics }) });
        dialog.close();
      }));
    }, 300);
  });
}

function escapeHtml(value = "") {
  return value.replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[character]));
}
