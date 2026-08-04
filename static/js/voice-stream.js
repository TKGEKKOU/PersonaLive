"use strict";

// Hands-free voice streaming client for the YUMENO voice stream API.
// Streams 16 kHz mono PCM to /api/voice/stream/ws while the server runs
// Silero VAD endpointing; partial transcripts arrive while speaking and a
// final transcript is delivered once the utterance ends.
window.PLVoiceStream = class PLVoiceStream {
  constructor({ onState = null, onPartial = null, onFinal = null, onError = null, onClosed = null } = {}) {
    this.onState = onState || (() => {});
    this.onPartial = onPartial || (() => {});
    this.onFinal = onFinal || (() => {});
    this.onError = onError || (() => {});
    this.onClosed = onClosed || (() => {});
    this.ws = null;
    this.audioContext = null;
    this.source = null;
    this.processor = null;
    this.stream = null;
    this.active = false;
  }

  get isActive() {
    return this.active;
  }

  async start() {
    if (this.active) return true;
    if (!navigator.mediaDevices?.getUserMedia) {
      this.onError("当前环境不支持麦克风录音");
      return false;
    }
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        video: false,
      });
    } catch (error) {
      this.onError(this.micErrorMessage(error));
      return false;
    }

    this.active = true;
    this.ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/api/voice/stream/ws");
    this.ws.binaryType = "arraybuffer";
    this.ws.onopen = () => this.sendCommand({ type: "start" });
    this.ws.onmessage = (event) => this.handleMessage(event.data);
    this.ws.onclose = () => this.teardown(false);
    this.ws.onerror = () => { if (this.active) this.onError("语音识别连接失败"); };

    this.audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    this.source = this.audioContext.createMediaStreamSource(this.stream);
    this.processor = this.audioContext.createScriptProcessor(4096, 1, 1);
    this.processor.onaudioprocess = (event) => this.sendPcm(event.inputBuffer.getChannelData(0));
    this.source.connect(this.processor);
    this.processor.connect(this.audioContext.destination);
    return true;
  }

  stop() {
    this.teardown(true);
  }

  sendCommand(payload) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(payload));
  }

  sendPcm(data) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN || !this.audioContext) return;
    let samples = data;
    if (this.audioContext.sampleRate !== 16000) {
      samples = this.resampleLinear(data, this.audioContext.sampleRate, 16000);
    }
    const pcm = new Int16Array(samples.length);
    for (let i = 0; i < samples.length; i++) {
      const value = Math.max(-1, Math.min(1, samples[i]));
      pcm[i] = value < 0 ? value * 0x8000 : value * 0x7fff;
    }
    this.ws.send(pcm.buffer);
  }

  resampleLinear(input, fromRate, toRate) {
    const ratio = fromRate / toRate;
    const out = new Float32Array(Math.floor(input.length / ratio));
    for (let i = 0; i < out.length; i++) {
      const pos = i * ratio;
      const i0 = Math.floor(pos);
      const i1 = Math.min(i0 + 1, input.length - 1);
      const frac = pos - i0;
      out[i] = input[i0] * (1 - frac) + input[i1] * frac;
    }
    return out;
  }

  handleMessage(raw) {
    let message;
    try { message = JSON.parse(raw); } catch { return; }
    if (message.type === "session.ready") {
      this.onState("connecting");
    } else if (message.type === "started") {
      this.onState("ready");
    } else if (message.type === "vad") {
      this.onState(message.state === "speaking" ? "speaking" : "ready");
    } else if (message.type === "partial") {
      this.onPartial(message.text);
    } else if (message.type === "final") {
      this.onFinal(message);
    } else if (message.type === "error") {
      this.onError(message.message || message.code, message.code);
    }
  }

  teardown(sendCancel) {
    if (!this.active && !this.ws && !this.stream) return;
    if (sendCancel) this.sendCommand({ type: "cancel" });
    if (this.ws) { try { this.ws.close(); } catch {} }
    this.ws = null;
    if (this.processor) { try { this.processor.disconnect(); } catch {} }
    if (this.source) { try { this.source.disconnect(); } catch {} }
    if (this.audioContext) { try { this.audioContext.close(); } catch {} }
    if (this.stream) this.stream.getTracks().forEach((track) => track.stop());
    this.processor = null;
    this.source = null;
    this.audioContext = null;
    this.stream = null;
    this.active = false;
    this.onClosed();
  }

  micErrorMessage(error) {
    if (error?.name === "NotAllowedError") return "麦克风权限被拒绝，请在浏览器中允许后重试";
    if (error?.name === "NotFoundError") return "未检测到可用麦克风";
    if (error?.name === "NotReadableError") return "麦克风被其他程序占用";
    return error?.message || "无法访问麦克风";
  }
};
