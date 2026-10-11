export function setupBackground(store) {
  const backdrop = document.querySelector("#artwork-backdrop");
  const artwork = document.querySelector("#track-artwork");
  store.onState((payload) => {
    const url = payload.track?.cover_url;
    if (!url) return;
    artwork.crossOrigin = "anonymous";
    artwork.src = url;
    backdrop.style.backgroundImage = `url("${url}")`;
    artwork.onload = () => {
      try {
        const canvas = document.createElement("canvas");
        canvas.width = 1;
        canvas.height = 1;
        const context = canvas.getContext("2d");
        context.drawImage(artwork, 0, 0, 1, 1);
        const [red, green, blue] = context.getImageData(0, 0, 1, 1).data;
        document.documentElement.style.setProperty("--artwork-color", `rgb(${red}, ${green}, ${blue})`);
      } catch {
        document.documentElement.style.setProperty("--artwork-color", "#252733");
      }
    };
  });
}
