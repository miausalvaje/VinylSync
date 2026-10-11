export function setupBackground(store) {
  const backdrop = document.querySelector("#artwork-backdrop");
  const artwork = document.querySelector("#track-artwork");
  store.onState((payload) => {
    const url = payload.track?.cover_url;
    if (!url) return;
    artwork.src = url;
    backdrop.style.backgroundImage = `url("${url}")`;
  });
}
