export function startVisualizer(store) {
  const canvas = document.querySelector("#visualizer-canvas");
  const context = canvas.getContext("2d");
  let bands = new Array(64).fill(0);
  let target = bands;
  let rms = 0;

  function resize() {
    canvas.width = window.innerWidth * devicePixelRatio;
    canvas.height = window.innerHeight * devicePixelRatio;
    context.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
  }
  window.addEventListener("resize", resize);
  resize();
  store.onSpectrum((payload) => {
    target = payload.bands || target;
    rms = payload.rms || 0;
  });
  store.onState((payload) => {
    if (payload.source) document.querySelector("#source-label").textContent = payload.source.name;
  });

  function draw() {
    const width = window.innerWidth;
    const height = window.innerHeight;
    context.fillStyle = "#090a0e";
    context.fillRect(0, 0, width, height);
    const glow = Math.min(0.22, rms * 0.8);
    const gradient = context.createRadialGradient(width / 2, height / 2, 10, width / 2, height / 2, height * 0.7);
    gradient.addColorStop(0, `rgba(190, 195, 210, ${glow})`);
    gradient.addColorStop(1, "rgba(9, 10, 14, 0)");
    context.fillStyle = gradient;
    context.fillRect(0, 0, width, height);
    const center = width / 2;
    const baseline = height * 0.56;
    const maxHeight = height * 0.32;
    const gap = Math.max(2, width / 640);
    const barWidth = Math.max(3, (width * 0.68 / 64) - gap);
    bands = bands.map((value, index) => value + (target[index] - value) * 0.18);
    bands.forEach((value, index) => {
      const distance = (index + 0.5) * (barWidth + gap);
      const barHeight = Math.max(3, value * maxHeight);
      context.fillStyle = `rgba(235, 237, 242, ${0.35 + value * 0.65})`;
      context.beginPath();
      context.roundRect(center - distance - barWidth, baseline - barHeight, barWidth, barHeight, barWidth / 2);
      context.roundRect(center + distance, baseline - barHeight, barWidth, barHeight, barWidth / 2);
      context.fill();
    });
    requestAnimationFrame(draw);
  }
  draw();
}
