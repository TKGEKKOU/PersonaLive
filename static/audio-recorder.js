"use strict";

(() => {
  const MIME_TYPES = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4;codecs=mp4a.40.2",
    "audio/mp4",
    "audio/ogg;codecs=opus",
    "audio/ogg",
  ];

  class BrowserAudioRecorder {
    constructor({ maxDurationMs = 120000, onLimit = null, onError = null, onUnexpectedStop = null } = {}) {
      this.maxDurationMs = maxDurationMs;
      this.onLimit = onLimit;
      this.onError = onError;
      this.onUnexpectedStop = onUnexpectedStop;
      this.mediaRecorder = null;
      this.stream = null;
      this.chunks = [];
      this.limitTimer = null;
      this.stopPromise = null;
      this.unexpectedHandled = false;
    }

    static isSupported() {
      return Boolean(navigator.mediaDevices?.getUserMedia && window.MediaRecorder);
    }

    async start() {
      if (this.mediaRecorder) throw new Error("Recording is already active");
      if (!BrowserAudioRecorder.isSupported()) throw new Error("Audio recording is not supported");
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
        video: false,
      });
      try {
        const mimeType = MIME_TYPES.find((type) => MediaRecorder.isTypeSupported(type));
        this.mediaRecorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
        this.stream = stream;
        this.chunks = [];
        this.unexpectedHandled = false;
        this.mediaRecorder.addEventListener("dataavailable", (event) => {
          if (event.data?.size) this.chunks.push(event.data);
        });
        this.mediaRecorder.addEventListener("error", (event) => this.handleUnexpectedError(event));
        this.mediaRecorder.addEventListener("stop", () => this.handleUnexpectedStop());
        this.mediaRecorder.start(250);
        this.limitTimer = setTimeout(() => this.onLimit?.(), this.maxDurationMs);
      } catch (error) {
        stream.getTracks().forEach((track) => track.stop());
        this.mediaRecorder = null;
        throw error;
      }
    }

    handleUnexpectedError(event) {
      if (this.stopPromise || this.unexpectedHandled) return;
      this.unexpectedHandled = true;
      this.cleanup();
      this.onError?.(event?.error || new Error("Audio recording failed"));
    }

    handleUnexpectedStop() {
      if (this.stopPromise || this.unexpectedHandled) return;
      this.unexpectedHandled = true;
      this.cleanup();
      this.onUnexpectedStop?.();
    }

    finish() {
      return this.stop(false);
    }

    cancel() {
      return this.stop(true);
    }

    stop(discard) {
      if (this.stopPromise) return this.stopPromise;
      const recorder = this.mediaRecorder;
      if (!recorder) return Promise.resolve(null);
      clearTimeout(this.limitTimer);
      this.stopPromise = new Promise((resolve, reject) => {
        const finalize = () => {
          const mimeType = recorder.mimeType || this.chunks[0]?.type || "audio/webm";
          const blob = discard ? null : new Blob(this.chunks, { type: mimeType });
          this.cleanup();
          if (!discard && !blob.size) reject(new Error("No audio was recorded"));
          else resolve(blob);
        };
        recorder.addEventListener("stop", finalize, { once: true });
        recorder.addEventListener("error", () => {
          this.cleanup();
          reject(new Error("Audio recording failed"));
        }, { once: true });
        if (recorder.state === "inactive") finalize();
        else recorder.stop();
      });
      return this.stopPromise;
    }

    cleanup() {
      clearTimeout(this.limitTimer);
      this.stream?.getTracks().forEach((track) => track.stop());
      this.mediaRecorder = null;
      this.stream = null;
      this.chunks = [];
      this.limitTimer = null;
      this.stopPromise = null;
    }
  }

  window.BrowserAudioRecorder = BrowserAudioRecorder;
})();
