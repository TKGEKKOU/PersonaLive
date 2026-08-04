"use strict";

/*
 * VTube Studio WebSocket client (Phase 3 integration).
 * Connects to VTS on ws://127.0.0.1:8001/api and drives MouthOpen /
 * MouthForm parameters from the volume analyser in live2d-core.js.
 * Auth token (when VTS has API auth enabled) is stored in localStorage.
 */
window.PLVTS = (function () {
  const LS_TOKEN = "personalive:vts:token";
  const DEFAULT_URL = "ws://127.0.0.1:8001/api";
  const SEND_INTERVAL_MS = 33; // ~30 fps cap

  class VTSClient {
    constructor() {
      this.ws = null;
      this.connected = false;
      this.authenticated = false;
      this._retryTimer = null;
      this._retryDelay = 3000;
      this._lastSent = 0;
      this._lastOpen = -1;
      this._lastForm = -1;
      this._requestSeq = 0;
    }

    get token() {
      return localStorage.getItem(LS_TOKEN) || "";
    }

    set token(value) {
      if (value) localStorage.setItem(LS_TOKEN, value.trim());
      else localStorage.removeItem(LS_TOKEN);
    }

    connect(url = DEFAULT_URL) {
      if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) return;
      this._emitStatus("connecting", "正在连接 VTube Studio…");
      try {
        this.ws = new WebSocket(url);
      } catch (e) {
        this._emitStatus("error", "VTube Studio 连接失败");
        this._scheduleRetry();
        return;
      }
      this.ws.onopen = () => {
        this._retryDelay = 3000;
        this._authenticate();
      };
      this.ws.onmessage = (event) => this._onMessage(event.data);
      this.ws.onclose = () => {
        this.connected = false;
        this.authenticated = false;
        this._emitStatus("offline", "VTube Studio 未连接");
        this._scheduleRetry();
      };
      this.ws.onerror = () => { /* onclose follows */ };
    }

    disconnect() {
      if (this._retryTimer) clearTimeout(this._retryTimer);
      this._retryTimer = null;
      if (this.ws) {
        try { this.ws.close(); } catch (e) { /* ignore */ }
      }
      this.ws = null;
      this.connected = false;
      this.authenticated = false;
    }

    _scheduleRetry() {
      if (this._retryTimer) return;
      this._retryTimer = setTimeout(() => {
        this._retryTimer = null;
        if (!this.ws || this.ws.readyState === WebSocket.CLOSED) this.connect();
      }, this._retryDelay);
      this._retryDelay = Math.min(60000, this._retryDelay * 1.6);
    }

    _request(messageType, data) {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
      this._requestSeq += 1;
      this.ws.send(JSON.stringify({
        apiName: "VTubeStudioPublicAPI",
        apiVersion: "1.0",
        requestID: "pl-" + this._requestSeq,
        messageType,
        data,
      }));
    }

    _authenticate() {
      this._request("AuthenticationRequest", {
        pluginName: "YUMENO",
        pluginDeveloper: "YUMENO",
        authenticationToken: this.token || undefined,
      });
    }

    _onMessage(raw) {
      let message;
      try {
        message = JSON.parse(raw);
      } catch (e) {
        return;
      }
      if (message.messageType === "AuthenticationResponse") {
        const data = message.data || {};
        this.connected = Boolean(data.authenticated);
        if (!this.token && data.authenticationToken) {
          // VTS offers a token for plugins; store it so later sessions skip auth.
          this.token = data.authenticationToken;
        }
        if (data.authenticated) {
          this._emitStatus("ok", "VTube Studio 已连接");
        } else {
          this._emitStatus("error", "VTS 认证失败：" + (data.reason || "未知原因"));
        }
      }
    }

    setMouth(open, form) {
      if (!this.connected) return;
      const now = performance.now();
      if (now - this._lastSent < SEND_INTERVAL_MS) return;
      const roundedOpen = Math.round(open * 100) / 100;
      const roundedForm = Math.round(form * 100) / 100;
      if (roundedOpen === this._lastOpen && roundedForm === this._lastForm) return;
      this._lastOpen = roundedOpen;
      this._lastForm = roundedForm;
      this._lastSent = now;
      this._request("ParameterValue", { parameterId: "MouthOpen", parameterValue: roundedOpen });
      this._request("ParameterValue", { parameterId: "MouthForm", parameterValue: roundedForm });
    }

    _emitStatus(level, message) {
      document.dispatchEvent(new CustomEvent("personalive:live2d", {
        detail: { type: "vts", level, message },
      }));
    }
  }

  return new VTSClient();
})();
