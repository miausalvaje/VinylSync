export function getAuthToken() {
  return localStorage.getItem("vinylsync_token") || sessionStorage.getItem("vinylsync_token") || "";
}

export function setAuthToken(token) {
  if (token) {
    localStorage.setItem("vinylsync_token", token.trim());
    sessionStorage.setItem("vinylsync_token", token.trim());
  } else {
    localStorage.removeItem("vinylsync_token");
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
    this._stateTimer = null;
    this._spectrumTimer = null;
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
    clearTimeout(this._stateTimer);
    clearTimeout(this._spectrumTimer);
    if (this.stateSocket) {
      try {
        this.stateSocket.onclose = null;
        this.stateSocket.onerror = null;
        this.stateSocket.close();
      } catch {}
      this.stateSocket = null;
    }
    if (this.spectrumSocket) {
      try {
        this.spectrumSocket.onclose = null;
        this.spectrumSocket.onerror = null;
        this.spectrumSocket.close();
      } catch {}
      this.spectrumSocket = null;
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
      clearTimeout(this._stateTimer);
      this._stateTimer = window.setTimeout(() => this.connectState(), 1500);
    };
    socket.onerror = () => socket.close();
  }

  connectSpectrum() {
    clearTimeout(this._spectrumTimer);
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
    socket.onclose = () => {
      clearTimeout(this._spectrumTimer);
      this._spectrumTimer = window.setTimeout(() => this.connectSpectrum(), 1500);
    };
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
