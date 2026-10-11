class StateStore {
  constructor() {
    this.state = {};
    this.listeners = [];
    this.spectrumListeners = [];
  }

  onState(listener) {
    this.listeners.push(listener);
  }

  onSpectrum(listener) {
    this.spectrumListeners.push(listener);
  }

  connect() {
    this.connectState();
    this.connectSpectrum();
  }

  connectState() {
    const socket = new WebSocket(`ws://${location.host}/ws`);
    socket.onopen = () => this.setConnection(true);
    socket.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      this.state = { ...this.state, ...payload };
      this.listeners.forEach((listener) => listener(this.state));
    };
    socket.onclose = () => {
      this.setConnection(false);
      window.setTimeout(() => this.connectState(), 1500);
    };
    socket.onerror = () => socket.close();
  }

  connectSpectrum() {
    const socket = new WebSocket(`ws://${location.host}/ws/spectrum`);
    socket.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      this.spectrumListeners.forEach((listener) => listener(payload));
    };
    socket.onclose = () => window.setTimeout(() => this.connectSpectrum(), 1500);
  }

  setConnection(connected) {
    const element = document.querySelector("#connection-status");
    element.textContent = connected ? "Connected" : "Reconnecting…";
    element.classList.toggle("status-pill--error", !connected);
  }
}

export const stateStore = new StateStore();
