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
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${location.host}/ws`);
    socket.onopen = () => this.setConnection(true);
    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        this.state = { ...this.state, ...payload };
        this.listeners.forEach((listener) => listener(this.state));
      } catch {
        // Ignore non-JSON frames and keep the socket alive.
      }
    };
    socket.onclose = () => {
      this.setConnection(false);
      window.setTimeout(() => this.connectState(), 1500);
    };
    socket.onerror = () => socket.close();
  }

  connectSpectrum() {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${location.host}/ws/spectrum`);
    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        this.spectrumListeners.forEach((listener) => listener(payload));
      } catch {
        // Ignore non-JSON frames.
      }
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
