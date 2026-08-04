"use strict";

/*
 * YUMENO Live2D core controller.
 * Renders a Cubism 2 / Cubism 4 model on a PIXI canvas, drives lip sync
 * from text-derived visemes blended with real-time audio energy
 * (Web Audio AnalyserNode; see viseme.js for the phoneme model).
 *
 * Layout modes:
 *   "stage" — the canvas fills the page; the model is centered, fully
 *             visible, draggable, wheel-zoomable, double-click resets.
 *   "float" — bottom-anchored stage used by the desktop floating window.
 *
 * Public API:
 *   PLLive2D.init(container, canvas, layout)
 *   PLLive2D.show() / PLLive2D.hide()      // keep renderer alive
 *   PLLive2D.setModel(id) / setPreferredModel(id)
 *   PLLive2D.setFlip(bool) / setScale(n) / resetPosition()
 *   PLLive2D.setMode("embedded" | "vts")
 *   PLLive2D.setAgentState("thinking" | "idle")
 *   PLLive2D.setVoiceState("listening" | "idle" | "connecting")
 *   PLLive2D.setLipSyncText(text, language?)
 *   PLLive2D.destroy()
 * Events (CustomEvent "yumeno:live2d" on document):
 *   { type: "state", state } | { type: "models", models, current } |
 *   { type: "model", name, id } | { type: "config", ... } |
 *   { type: "status", level, message }
 */
window.PLLive2D = (function () {
  const LS = {
    model: "yumeno:live2d:model",
    flip: "yumeno:live2d:flip",
    scale: "yumeno:live2d:scale",
    mode: "yumeno:live2d:mode",
    posX: "yumeno:live2d:posx",
    posY: "yumeno:live2d:posy",
  };

  const LIP_IDS = ["ParamMouthOpenY", "ParamMouthForm"];
  const DEFAULT_MODEL_ID = "sakiko2_vts";

  class Controller {
    constructor() {
      this.app = null;
      this.model = null;
      this.models = [];
      this.currentId = null;
      this.preferredId = null;
      this.container = null;
      this.canvas = null;
      this.layout = "stage";
      this._raf = 0;
      this._ready = false;
      this._visible = false;
      this._talking = false;
      this._dragging = false;

      this.mode = localStorage.getItem(LS.mode) || "embedded";
      this.flip = localStorage.getItem(LS.flip) === "1";
      this.scale = parseFloat(localStorage.getItem(LS.scale)) || 1;
      this.agentState = "idle";
      this.voiceState = "idle";

      this.mouth = 0;
      this.mouthForm = 0;
      this._lipText = "";
      this._lipUnits = [];
      this._lipPoses = [];
      this._env = 0;

      this._audioCtx = null;
      this._analyser = null;
      this._source = null;
      this._currentAudio = null;
      this._connected = new WeakSet();
      this._pcmBuf = null;
      this._hasAutoBlink = true;
      this._blinkAt = 0;
      this._blinkAmount = 0;
      this._wheelAcc = 0;
    }

    /* ---------- lifecycle ---------- */

    async init(container, canvas, layout = "stage") {
      if (this.app) {
        try { this.destroy(); } catch (e) { /* ignore */ }
      }
      this.container = container;
      this.canvas = canvas;
      this.layout = layout === "float" ? "float" : "stage";
      if (!window.PIXI || !window.PIXI.live2d) {
        this._emit({ type: "status", level: "error", message: "Live2D 渲染库未加载" });
        throw new Error("Live2D dependencies missing");
      }
      this.app = new PIXI.Application({
        view: canvas,
        resizeTo: container,
        backgroundAlpha: 0,
        antialias: true,
        autoDensity: true,
        resolution: Math.min(window.devicePixelRatio || 1, 2),
      });
      this.app.renderer.on("resize", () => this._fit());
      this._bindAudioEvents();
      if (this.layout === "stage") this._bindStagePointer();
      this._visible = true;
      this._startLoop();
      await this.refreshModels();
      this._ready = true;
      this._emitState();
    }

    show() {
      this._visible = true;
      if (this.app) this.app.ticker.start();
      this._startLoop();
      requestAnimationFrame(() => this._fit());
    }

    resize() {
      // 主动同步画布尺寸并重新适配模型（舞台高度拖拽后调用，
      // 不依赖 ResizeObserver 的触发时机）。
      if (!this.app || !this.container) return;
      const w = this.container.clientWidth || 0;
      const h = this.container.clientHeight || 0;
      if (w > 0 && h > 0) {
        this.app.renderer.resize(w, h);
        this._fit();
      }
    }

    hide() {
      this._visible = false;
      this._currentAudio = null;
      if (this._raf) cancelAnimationFrame(this._raf);
      this._raf = 0;
      if (this.app) this.app.ticker.stop();
    }

    destroy() {
      this.hide();
      if (this.model) {
        try {
          this.app.stage.removeChild(this.model);
          this.model.destroy();
        } catch (e) { /* ignore */ }
      }
      if (this.app) {
        try { this.app.destroy(true, { children: true }); } catch (e) { /* ignore */ }
      }
      if (this._audioCtx) {
        try { this._audioCtx.close(); } catch (e) { /* ignore */ }
      }
      this.app = null;
      this.model = null;
      this._ready = false;
    }

    /* ---------- models ---------- */

    async refreshModels() {
      let list = [];
      try {
        const response = await fetch("/api/live2d/models");
        if (response.ok) list = (await response.json()).models || [];
      } catch (e) { /* offline / not ready */ }
      this.models = list;
      const saved = localStorage.getItem(LS.model);
      const target = list.find((m) => m.id === this.preferredId)
        || list.find((m) => m.id === saved)
        || (saved ? null : list.find((m) => m.id === DEFAULT_MODEL_ID))
        || list[0] || null;
      if (target) {
        await this.loadModel(target.id);
      } else {
        this._emit({ type: "status", level: "warn", message: "未找到 Live2D 模型，请将模型放入 data/live2d/" });
      }
      this._emit({ type: "models", models: list, current: target ? target.id : null });
      return list;
    }

    async loadModel(id) {
      const entry = this.models.find((m) => m.id === id);
      if (!entry || !this.app) return;
      this._emit({ type: "status", level: "info", message: "正在加载模型…" });
      const url = "/live2d-assets/" + entry.entry;
      let next;
      try {
        next = await window.PIXI.live2d.Live2DModel.from(url, { autoInteract: true });
      } catch (e) {
        this._emit({ type: "status", level: "error", message: "模型加载失败：" + (e && e.message ? e.message : e) });
        return;
      }
      if (this.model) {
        try {
          this.app.stage.removeChild(this.model);
          this.model.destroy();
        } catch (e) { /* ignore */ }
      }
      this.model = next;
      this.app.stage.addChild(next);
      next.on("hit", (areas) => {
        if (Array.isArray(areas) && areas.some((a) => String(a).toLowerCase().includes("body"))) {
          try { next.motion("TapBody"); } catch (e) { /* no tap motion */ }
        }
      });
      this._fit();
      this.currentId = entry.id;
      localStorage.setItem(LS.model, entry.id);
      this._detectAutoBlink();
      this._emit({ type: "model", name: this._displayName(entry), id: entry.id });
      this._emit({ type: "status", level: "ok", message: this._displayName(entry) });
    }

    setModel(id) {
      if (id && id !== this.currentId) this.loadModel(id);
    }

    setPreferredModel(id) {
      this.preferredId = id || null;
      if (id && this._ready && id !== this.currentId) this.loadModel(id);
    }

    _detectAutoBlink() {
      this._hasAutoBlink = true;
      try {
        const settings = this.model.internalModel.settings;
        const groups = (settings && settings.Groups) || [];
        const eye = groups.find((group) => group.Name === "EyeBlink");
        this._hasAutoBlink = Boolean(eye && Array.isArray(eye.Ids) && eye.Ids.length > 0);
      } catch (e) {
        this._hasAutoBlink = true;
      }
      this._blinkAt = performance.now() + 1800 + Math.random() * 2600;
      this._blinkAmount = 0;
    }

    _displayName(entry) {
      const base = entry.entry.split("/").pop() || entry.id;
      return base.replace(/\.model3?\.json$/i, "") || entry.id;
    }

    /* ---------- transform (stage vs float) ---------- */

    _fit() {
      if (!this.model || !this.app) return;
      const w = this.app.renderer.width || (this.container && this.container.clientWidth) || 300;
      const h = this.app.renderer.height || (this.container && this.container.clientHeight) || 400;
      // 使用模型固有尺寸（internalModel 不受缩放影响），避免 scale 反馈环导致
      // 只能在两个固定大小间振荡。
      const internal = this.model.internalModel || {};
      const bw = internal.width || this.model.width || 1;
      const bh = internal.height || this.model.height || 1;
      if (this.layout === "stage") {
        const target = Math.min((w * 0.9) / bw, (h * 0.88) / bh) * this.scale;
        this.model.anchor.set(0.5, 0.5);
        this.model.scale.set(target * (this.flip ? -1 : 1), target);
        const storedX = parseFloat(localStorage.getItem(LS.posX));
        const storedY = parseFloat(localStorage.getItem(LS.posY));
        if (Number.isFinite(storedX) && Number.isFinite(storedY)) {
          this.model.x = storedX * w;
          this.model.y = storedY * h;
        } else {
          this.model.x = w / 2;
          this.model.y = h * 0.52;
        }
      } else {
        const target = Math.min((w * 0.92) / bw, (h * 0.84) / bh) * this.scale;
        this.model.anchor.set(0.5, 1);
        this.model.scale.set(target * (this.flip ? -1 : 1), target);
        this.model.x = w / 2;
        this.model.y = h;
      }
    }

    _bindStagePointer() {
      const canvas = this.canvas;
      if (!canvas) return;
      let startX = 0;
      let startY = 0;
      let baseX = 0;
      let baseY = 0;
      canvas.addEventListener("pointerdown", (event) => {
        if (!this.model) return;
        this._dragging = true;
        try { canvas.setPointerCapture(event.pointerId); } catch (e) { /* ignore */ }
        startX = event.clientX;
        startY = event.clientY;
        baseX = this.model.x;
        baseY = this.model.y;
        canvas.style.cursor = "grabbing";
      });
      canvas.addEventListener("pointermove", (event) => {
        if (!this._dragging || !this.model) return;
        this.model.x = baseX + (event.clientX - startX);
        this.model.y = baseY + (event.clientY - startY);
        this._savePosition();
      });
      const endDrag = () => {
        this._dragging = false;
        canvas.style.cursor = "grab";
      };
      canvas.addEventListener("pointerup", endDrag);
      canvas.addEventListener("pointercancel", endDrag);
      canvas.addEventListener("wheel", (event) => {
        event.preventDefault();
        // 低灵敏度：累积滚轮位移，达到阈值才缩放一次
        this._wheelAcc += event.deltaY;
        if (Math.abs(this._wheelAcc) >= 60) {
          this.setScale(this._wheelAcc > 0 ? -0.06 : 0.06);
          this._wheelAcc = 0;
        }
      }, { passive: false });
      canvas.addEventListener("dblclick", () => this.resetPosition());
      canvas.style.cursor = "grab";
    }

    _savePosition() {
      if (!this.model || !this.app || !Number.isFinite(this.model.x) || !Number.isFinite(this.model.y)) return;
      const w = this.app.renderer.width || 1;
      const h = this.app.renderer.height || 1;
      localStorage.setItem(LS.posX, String(this.model.x / w));
      localStorage.setItem(LS.posY, String(this.model.y / h));
    }

    resetPosition() {
      localStorage.removeItem(LS.posX);
      localStorage.removeItem(LS.posY);
      this.scale = 1;
      localStorage.setItem(LS.scale, "1");
      this._fit();
      this._emit({ type: "config", flip: this.flip, scale: this.scale, mode: this.mode });
    }

    setFlip(flip) {
      this.flip = Boolean(flip);
      localStorage.setItem(LS.flip, this.flip ? "1" : "0");
      if (this.model) this.model.scale.x = Math.abs(this.model.scale.x) * (this.flip ? -1 : 1);
      this._emit({ type: "config", flip: this.flip, scale: this.scale, mode: this.mode });
    }

    setScale(deltaOrValue) {
      if (typeof deltaOrValue === "number" && Math.abs(deltaOrValue) <= 1) {
        this.scale = Math.max(0.3, Math.min(3, this.scale + deltaOrValue));
      } else if (typeof deltaOrValue === "number") {
        this.scale = Math.max(0.3, Math.min(3, deltaOrValue));
      }
      localStorage.setItem(LS.scale, String(this.scale));
      this._fit();
      this._emit({ type: "config", flip: this.flip, scale: this.scale, mode: this.mode });
    }

    setMode(mode) {
      if (mode !== "embedded" && mode !== "vts") return;
      this.mode = mode;
      localStorage.setItem(LS.mode, mode);
      if (mode === "vts" && window.PLVTS) window.PLVTS.connect();
      this._emit({ type: "config", flip: this.flip, scale: this.scale, mode: this.mode });
      this._emitState();
    }

    /* ---------- state ---------- */

    setAgentState(state) {
      this.agentState = state === "thinking" ? "thinking" : "idle";
      this._emitState();
    }

    setVoiceState(state) {
      this.voiceState = state === "listening" || state === "speaking" ? "listening"
        : state === "connecting" ? "connecting" : "idle";
      this._emitState();
    }

    _getState() {
      if (this._talking) return "talking";
      if (this.voiceState === "listening") return "listening";
      if (this.agentState === "thinking") return "thinking";
      if (this.voiceState === "connecting") return "connecting";
      return "idle";
    }

    _emitState() {
      this._emit({ type: "state", state: this._getState() });
    }

    /* ---------- volume / lip sync ---------- */

    _ensureAudio() {
      if (this._audioCtx) return;
      try {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        this._audioCtx = new Ctx();
        if (this._audioCtx.state === "suspended") {
          this._audioCtx.resume().catch(() => {});
        }
        this._analyser = this._audioCtx.createAnalyser();
        this._analyser.fftSize = 1024;
        this._analyser.smoothingTimeConstant = 0.4;
        this._pcmBuf = new Float32Array(this._analyser.fftSize);
      } catch (e) { /* audio blocked until user gesture */ }
    }

    _attachAudio(audio) {
      if (!audio) return;
      if (!this._connected.has(audio)) {
        this._ensureAudio();
        if (this._audioCtx) {
          if (this._audioCtx.state === "suspended") {
            this._audioCtx.resume().catch(() => {});
          }
          try {
            const source = this._audioCtx.createMediaElementSource(audio);
            source.connect(this._analyser);
            this._analyser.connect(this._audioCtx.destination);
            this._source = source;
            this._connected.add(audio);
          } catch (e) { /* element already routed or unsupported */ }
        }
      }
      this._currentAudio = audio;
      this.setLipSyncText((audio.dataset && audio.dataset.lipText) || "");
      const allocate = () => this._allocateLipPoses();
      if (audio.duration && isFinite(audio.duration)) {
        allocate();
      } else {
        audio.addEventListener("loadedmetadata", allocate, { once: true });
        audio.addEventListener("durationchange", allocate, { once: true });
      }
    }

    _bindAudioEvents() {
      const resumeAudio = () => {
        this._ensureAudio();
        if (this._audioCtx && this._audioCtx.state === "suspended") {
          this._audioCtx.resume().catch(() => {});
        }
        if (window.PL && window.PL.unlockAudio) window.PL.unlockAudio();
      };
      ["pointerdown", "keydown", "touchstart", "click"].forEach((type) => {
        document.addEventListener(type, resumeAudio, { once: true, capture: true });
      });
      document.addEventListener("play", (event) => {
        const el = event.target;
        if (el && el.tagName === "AUDIO") this._attachAudio(el);
      }, true);
      const detach = () => { this._currentAudio = null; };
      document.addEventListener("pause", (event) => {
        if (event.target && event.target.tagName === "AUDIO") detach();
      }, true);
      document.addEventListener("ended", (event) => {
        if (event.target && event.target.tagName === "AUDIO") detach();
      }, true);
    }

    _startLoop() {
      if (this._raf) return;
      this._raf = requestAnimationFrame(() => this._tick());
    }

    _tick() {
      if (!this._visible) return;
      this._raf = requestAnimationFrame(() => this._tick());
      let rms = 0;
      let peak = 0;
      if (this._analyser && this._currentAudio && !this._currentAudio.paused) {
        try {
          this._analyser.getFloatTimeDomainData(this._pcmBuf);
          let sum = 0;
          for (let i = 0; i < this._pcmBuf.length; i += 1) {
            const value = this._pcmBuf[i];
            sum += value * value;
            const magnitude = Math.abs(value);
            if (magnitude > peak) peak = magnitude;
          }
          rms = Math.sqrt(sum / this._pcmBuf.length);
        } catch (e) { /* analyser not ready */ }
      }
      // Peak-ish energy envelope (BandoriPet-style), decaying each frame.
      this._env = Math.max(this._env * 0.74, Math.min(Math.max(rms * 4.0, peak * 0.35), 0.7));
      const playing = this._currentAudio && !this._currentAudio.paused;
      let target;
      let formTarget;
      if (playing && this._lipPoses.length && window.PLViseme) {
        const pose = window.PLViseme.probePose(this._lipPoses, this._currentAudio.currentTime);
        const blended = window.PLViseme.computeMouth(this._env, pose.open, pose.form);
        target = blended.open;
        formTarget = blended.form;
      } else {
        target = Math.min(1, this._env * 3.4);
        formTarget = target * 0.6;
      }
      const delta = target - this.mouth;
      this.mouth += delta * (delta > 0 ? 0.46 : 0.16); // fast open, slower close
      const formDelta = formTarget - this.mouthForm;
      this.mouthForm += formDelta * 0.45;
      const open = Math.max(0, Math.min(1, this.mouth));
      const form = Math.max(-1, Math.min(1, this.mouthForm));
      this._applyParams(open, form);
      this._applyLife();
      this._vtsSend(open, form);
      const talking = open > 0.035;
      if (talking !== this._talking) {
        this._talking = talking;
        this._emitState();
      }
    }

    setLipSyncText(text, language) {
      this._lipText = String(text || "");
      this._lipUnits = window.PLViseme
        ? window.PLViseme.estimateVisemeUnits(this._lipText, language || "")
        : [];
      this._allocateLipPoses();
    }

    _allocateLipPoses() {
      if (!this._lipUnits || !this._lipUnits.length || !this._currentAudio) {
        this._lipPoses = [];
        return;
      }
      const duration = this._currentAudio.duration;
      if (!duration || !isFinite(duration)) {
        this._lipPoses = [];
        return;
      }
      this._lipPoses = window.PLViseme
        ? window.PLViseme.allocatePoses(this._lipUnits, duration)
        : [];
    }

    _applyParams(open, form) {
      if (!this.model || !this.model.internalModel || !this.model.internalModel.coreModel) return;
      const core = this.model.internalModel.coreModel;
      for (const id of LIP_IDS) {
        try {
          const value = id === "ParamMouthOpenY" ? open : form;
          if (typeof core.setParameterValueById === "function") {
            core.setParameterValueById(id, value);
          } else if (typeof core.getParamIndex === "function") {
            const index = core.getParamIndex(id);
            if (index >= 0) core.setParamFloat(index, value);
          }
        } catch (e) { /* parameter missing on this model */ }
      }
    }

    _applyLife() {
      if (!this.model || this._hasAutoBlink) return;
      const core = this.model.internalModel && this.model.internalModel.coreModel;
      if (!core) return;
      const now = performance.now();
      if (now >= this._blinkAt) {
        this._blinkAmount = 0.999;
        this._blinkAt = now + 2200 + Math.random() * 3400;
      }
      if (this._blinkAmount > 0) {
        this._blinkAmount *= 0.78;
        if (this._blinkAmount < 0.012) this._blinkAmount = 0;
      }
      const eyeOpen = 1 - this._blinkAmount;
      const breath = 0.5 + 0.42 * Math.sin(now / 2200);
      const set = (id, value) => {
        try {
          if (typeof core.setParameterValueById === "function") {
            core.setParameterValueById(id, value);
          } else if (typeof core.getParamIndex === "function") {
            const index = core.getParamIndex(id);
            if (index >= 0) core.setParamFloat(index, value);
          }
        } catch (e) { /* parameter missing */ }
      };
      set("ParamEyeLOpen", eyeOpen);
      set("ParamEyeROpen", eyeOpen);
      set("ParamBreath", breath);
    }

    _vtsSend(open, form) {
      if (this.mode !== "vts" || !window.PLVTS || !window.PLVTS.connected) return;
      window.PLVTS.setMouth(open, form);
    }

    /* ---------- events ---------- */

    _emit(payload) {
      document.dispatchEvent(new CustomEvent("yumeno:live2d", { detail: payload }));
    }
  }

  return new Controller();
})();
