/**
 * Browser audio capture and WebSocket streaming manager.
 */

export class AudioCaptureManager {
  constructor() {
    this.clientId = this._initClientId();
    this.mediaStream = null;
    this.audioContext = null;
    this.workletNode = null;
    this.socket = null;
    this.wakeLock = null;
    this.releaseWebLock = null;
    this.isCapturing = false;
    this.mode = null; // "mic" | "display"
    this.deviceId = null;
    this.listeners = [];
    this.reconnectTimer = null;
    this.reconnectAttempts = 0;
    this.shouldReconnect = false;

    // Re-acquire Wake Lock when page visibility returns
    document.addEventListener("visibilitychange", async () => {
      if (document.visibilityState === "visible" && this.isCapturing && !this.wakeLock) {
        await this._acquireWakeLock();
      }
    });
  }

  _initClientId() {
    let id = sessionStorage.getItem("vinylsync_client_id");
    if (!id) {
      id = typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : `client-${Math.random().toString(36).slice(2, 11)}`;
      sessionStorage.setItem("vinylsync_client_id", id);
    }
    return id;
  }

  onStateChange(callback) {
    this.listeners.push(callback);
  }

  _emit(state, detail = {}) {
    this.listeners.forEach((fn) => fn(state, detail));
  }

  getToken() {
    return sessionStorage.getItem("vinylsync_token") || "";
  }

  setToken(token) {
    if (token) {
      sessionStorage.setItem("vinylsync_token", token.trim());
    } else {
      sessionStorage.removeItem("vinylsync_token");
    }
  }

  async getAudioInputDevices() {
    if (!navigator.mediaDevices?.enumerateDevices) {
      return [];
    }
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      return devices.filter((device) => device.kind === "audioinput");
    } catch {
      return [];
    }
  }

  async startMicrophone(deviceId = null) {
    this._checkSecureContext();
    this.mode = "mic";
    this.deviceId = deviceId;
    this.shouldReconnect = true;

    const constraints = {
      audio: {
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
        channelCount: 1,
        ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
      },
      video: false,
    };

    try {
      this._emit("starting");
      this.mediaStream = await navigator.mediaDevices.getUserMedia(constraints);
      await this._setupAudioPipeline();
      this._connectSocket();
    } catch (error) {
      this._emit("error", { message: this._friendlyErrorMessage(error) });
      this.stop();
      throw error;
    }
  }

  async startDisplayAudio() {
    this._checkSecureContext();
    this.mode = "display";
    this.deviceId = null;
    this.shouldReconnect = true;

    try {
      this._emit("starting");
      const displayStream = await navigator.mediaDevices.getDisplayMedia({
        audio: {
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
        video: true,
      });

      // Discard unused video tracks immediately
      displayStream.getVideoTracks().forEach((track) => track.stop());

      const audioTracks = displayStream.getAudioTracks();
      if (!audioTracks.length) {
        throw new Error("No audio track was shared. Check the 'Share audio' checkbox in your browser.");
      }

      this.mediaStream = new MediaStream(audioTracks);
      // Listen for user stopping share via browser native banner
      audioTracks[0].addEventListener("ended", () => {
        this._emit("stopped_by_user");
        this.stop();
      });

      await this._setupAudioPipeline();
      this._connectSocket();
    } catch (error) {
      this._emit("error", { message: this._friendlyErrorMessage(error) });
      this.stop();
      throw error;
    }
  }

  _checkSecureContext() {
    if (window.isSecureContext === false) {
      throw new Error(
        "Audio capture requires a secure context (HTTPS or localhost). If accessing over a local network, configure TLS/HTTPS or an SSH/VPN tunnel."
      );
    }
  }

  _friendlyErrorMessage(error) {
    if (error.name === "NotAllowedError" || error.name === "PermissionDeniedError") {
      return "Permission to access audio was denied by the browser.";
    }
    if (error.name === "NotFoundError" || error.name === "DevicesNotFoundError") {
      return "Selected audio input device was not found.";
    }
    if (error.name === "NotReadableError" || error.name === "TrackStartError") {
      return "Audio device is already in use by another application.";
    }
    return error.message || "Failed to start audio capture.";
  }

  async _setupAudioPipeline() {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    this.audioContext = new AudioContextClass();
    if (this.audioContext.state === "suspended") {
      await this.audioContext.resume();
    }

    await this.audioContext.audioWorklet.addModule("/js/audio-worklet.js");
    const sourceNode = this.audioContext.createMediaStreamSource(this.mediaStream);
    this.workletNode = new AudioWorkletNode(this.audioContext, "pcm16-recorder-processor");

    this.workletNode.port.onmessage = (event) => {
      if (this.isCapturing && this.socket && this.socket.readyState === WebSocket.OPEN) {
        this.socket.send(event.data);
      }
    };

    sourceNode.connect(this.workletNode);
    await this._acquireWakeLock();
    this._acquireWebLock();
  }

  async _acquireWakeLock() {
    if ("wakeLock" in navigator && !this.wakeLock) {
      try {
        this.wakeLock = await navigator.wakeLock.request("screen");
        this.wakeLock.addEventListener("release", () => {
          this.wakeLock = null;
        });
      } catch {
        // Wake Lock not supported or rejected; non-fatal
      }
    }
  }

  _acquireWebLock() {
    if (navigator.locks && !this.releaseWebLock) {
      navigator.locks.request("vinylsync-audio-capture", { mode: "shared" }, () => {
        return new Promise((resolve) => {
          this.releaseWebLock = resolve;
        });
      });
    }
  }

  _connectSocket() {
    if (this.socket) {
      try {
        this.socket.close();
      } catch {
        // Ignore
      }
      this.socket = null;
    }

    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const token = this.getToken();
    const url = `${protocol}//${location.host}/ws/audio`;

    const subprotocols = token ? [`vinylsync-auth.${token}`] : [];
    this.socket = subprotocols.length
      ? new WebSocket(url, subprotocols)
      : new WebSocket(url);
    this.socket.binaryType = "arraybuffer";

    this.socket.onopen = () => {
      this.reconnectAttempts = 0;
      // If token wasn't passed via subprotocol, send auth first
      if (token && !subprotocols.length) {
        this.socket.send(JSON.stringify({ type: "auth", token }));
      }
      // Send Hello handshake
      this.socket.send(
        JSON.stringify({
          type: "hello",
          client_id: this.clientId,
          sample_rate: this.audioContext.sampleRate,
          channels: 1,
          format: "s16le",
        })
      );
    };

    this.socket.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "hello_ok") {
          this.isCapturing = true;
          this._emit("capturing", { clientId: this.clientId });
        } else if (msg.type === "busy") {
          this.isCapturing = false;
          this._emit("busy", { capturerId: msg.capturer_id });
        } else if (msg.type === "disconnected") {
          this.isCapturing = false;
          this._emit("disconnected", { reason: msg.reason });
          if (msg.reason === "server_device_selected" || msg.reason === "taken_over") {
            this.shouldReconnect = false;
            this.stop();
          }
        }
      } catch {
        // Non-JSON frames
      }
    };

    this.socket.onclose = (event) => {
      const wasCapturing = this.isCapturing;
      this.isCapturing = false;
      if (this.shouldReconnect) {
        this._emit("reconnecting");
        this._scheduleReconnect();
      } else {
        this._emit("closed", { code: event.code, reason: event.reason });
      }
    };

    this.socket.onerror = () => {
      // Error triggers onclose
    };
  }

  _scheduleReconnect() {
    clearTimeout(this.reconnectTimer);
    const delay = Math.min(1000 * Math.pow(1.5, this.reconnectAttempts), 10000);
    this.reconnectAttempts++;
    this.reconnectTimer = setTimeout(() => {
      if (this.shouldReconnect && this.mediaStream) {
        this._connectSocket();
      }
    }, delay);
  }

  requestTakeover() {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type: "takeover" }));
    }
  }

  stop() {
    this.shouldReconnect = false;
    this.isCapturing = false;
    clearTimeout(this.reconnectTimer);

    if (this.socket) {
      try {
        this.socket.close(1000, "Stopped by user");
      } catch {
        // Ignore
      }
      this.socket = null;
    }

    if (this.mediaStream) {
      this.mediaStream.getTracks().forEach((track) => track.stop());
      this.mediaStream = null;
    }

    if (this.workletNode) {
      try {
        this.workletNode.disconnect();
      } catch {
        // Ignore
      }
      this.workletNode = null;
    }

    if (this.audioContext) {
      try {
        this.audioContext.close();
      } catch {
        // Ignore
      }
      this.audioContext = null;
    }

    if (this.wakeLock) {
      try {
        this.wakeLock.release();
      } catch {
        // Ignore
      }
      this.wakeLock = null;
    }

    if (this.releaseWebLock) {
      this.releaseWebLock();
      this.releaseWebLock = null;
    }

    this._emit("idle");
  }
}

export const audioCaptureManager = new AudioCaptureManager();
