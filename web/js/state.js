export function getAuthToken() {
  return sessionStorage.getItem("vinylsync_token") || "";
}

export function setAuthToken(token) {
  if (token) {
    sessionStorage.setItem("vinylsync_token", token.trim());
  } else {
    sessionStorage.removeItem("vinylsync_token");
  }
}

export async function fetchWithAuth(url, options = {}) {
  const token = getAuthToken();
  const headers = new Headers(options.headers || {});
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401) {
    window.dispatchEvent(new CustomEvent("vinylsync:auth_required"));
  }
  return response;
}

class StateStore {
  constructor() {
    this.state = {};
    this.listeners = [];
    this.spectrumListeners = [];
    this.stateSocket = null;
    this.spectrumSocket = null;
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

  reconnect() {
    if (this.stateSocket) {
      try { this.stateSocket.close(); } catch {}
    }
    if (this.spectrumSocket) {
      try { this.spectrumSocket.close(); } catch {}
    }
    this.connectState();
    this.connectSpectrum();
  }

  connectState() {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const token = getAuthToken();
    const url = `${protocol}//${location.host}/ws`;
    const subprotocols = token ? [`vinylsync-auth.${token}`] : [];

    const socket = subprotocols.length
      ? new WebSocket(url, subprotocols)
      : new WebSocket(url);
    this.stateSocket = socket;

    socket.onopen = () => {
      this.setConnection(true);
      if (token && !subprotocols.length) {
        socket.send(JSON.stringify({ type: "auth", token }));
      }
    };
    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        this.state = { ...this.state, ...payload };
        this.listeners.forEach((listener) => listener(this.state));
      } catch {
        // Ignore non-JSON frames and keep the socket alive.
      }
    };
    socket.onclose = (event) => {
      this.setConnection(false);
      if (event.code === 1008) {
        window.dispatchEvent(new CustomEvent("vinylsync:auth_required"));
      }
      window.setTimeout(() => this.connectState(), 1500);
    };
    socket.onerror = () => socket.close();
  }

  connectSpectrum() {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const token = getAuthToken();
    const url = `${protocol}//${location.host}/ws/spectrum`;
    const subprotocols = token ? [`vinylsync-auth.${token}`] : [];

    const socket = subprotocols.length
      ? new WebSocket(url, subprotocols)
      : new WebSocket(url);
    this.spectrumSocket = socket;

    socket.onopen = () => {
      if (token && !subprotocols.length) {
        socket.send(JSON.stringify({ type: "auth", token }));
      }
    };
    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        this.spectrumListeners.forEach((listener) => listener(payload));
      } catch {
        // Ignore non-JSON frames.
      }
    };
    socket.onclose = () => window.setTimeout(() => this.connectSpectrum(), 1500);
    socket.onerror = () => socket.close();
  }

  setConnection(connected) {
    const element = document.querySelector("#connection-status");
    if (element) {
      element.textContent = connected ? "Connected" : "Reconnecting…";
      element.classList.toggle("status-pill--error", !connected);
    }
  }
}

export const stateStore = new StateStore();
