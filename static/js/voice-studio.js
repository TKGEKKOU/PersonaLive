"use strict";
window.PL.modules.voice = { init: initVoiceStudio };

  const VOICE_FLOW_ORDER = ["video", "audio", "segments", "train"];
const VOICE_PHASE_STEP = {
  idle: null,
  queued: "video",
  extract: "audio",
  convert: "audio",
  separate: "audio",
  audio_ready: "audio",
  slice: "segments",
  write: "segments",
  segments: "segments",
  reference: "segments",
  done: "train",
  failed: null,
  cancelled: null,
};
const VOICE_PHASE_LABEL = {
  idle: "等待输入",
  queued: "排队中",
  extract: "提取音轨",
  convert: "转换音频",
  separate: "分离人声",
  audio_ready: "音频已就绪，等待分离",
  slice: "截取片段",
  write: "写入片段",
  segments: "片段已就绪",
  reference: "参考已生成",
  done: "已保存",
  failed: "处理失败",
  cancelled: "已取消",
};
  const VOICE_STEP_LABEL = { video: "视频", audio: "音频与分离", segments: "片段", train: "训练" };

let voiceSessionId = null;
let voicePollTimer = null;
let voiceSelected = new Set();
let voiceSeparatorReady = false;
let voiceSeparatorInstalling = false;
let voiceSeparatorProgress = null;
let voiceTtsReady = false;
let voiceTtsInstalling = false;
let voiceTtsProgress = null;
let voiceResourcePollTimer = null;
let voiceResourcePolling = false;
let voiceActiveStep = null;
let voiceLastState = null;
let voiceActionStep = null;

function initVoiceStudio() {
  bindVoiceStudioEvents();
  loadVoiceTrainLibrary();
  checkVoiceResources();
  resumeVoiceSession();
}

function bindVoiceStudioEvents() {
  bindSafe("voice-studio-new", "click", resetVoiceSession);
  bindSafe("voice-video", "change", uploadVoiceVideo);
  bindSafe("voice-audio", "change", uploadVoiceAudio);
  bindSafe("voice-start-separate", "click", startVoiceSeparation);
  bindSafe("voice-segments-upload-input", "change", uploadVoiceSegments);
  bindSafe("voice-segments-select-all", "click", toggleSelectAllVoiceSegments);
  bindVoiceDrop("voice-video-drop", "voice-video");
  bindVoiceDrop("voice-audio-drop", "voice-audio");
  bindVoiceDrop("voice-segments-upload", "voice-segments-upload-input");
  bindSafe("voice-train-enter", "click", () => selectVoiceStep("train"));
  bindSafe("voice-train-start", "click", startVoiceTraining);
  bindSafe("voice-train-to-manage", "click", () => { if (window.switchView) window.switchView("manage"); });
  document.querySelectorAll("[data-flow-step]").forEach((node) => {
    node.addEventListener("click", () => selectVoiceStep(node.dataset.flowStep));
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectVoiceStep(node.dataset.flowStep);
      }
    });
  });
  document.querySelectorAll(".voice-step-confirm").forEach((button) => {
    button.addEventListener("click", () => selectVoiceStep(button.dataset.confirmStep));
  });
}

function selectVoiceStep(step) {
  voiceActiveStep = step;
  if (step === "train") {
    renderVoiceTrainMeta();
    loadVoiceTrainLibrary();
  }
  document.querySelectorAll(".voice-step").forEach((panel) => {
    panel.classList.toggle("is-hidden", panel.dataset.stepPanel !== step);
  });
  document.querySelectorAll("[data-flow-step]").forEach((node) => {
    node.classList.toggle("is-selected", node.dataset.flowStep === step);
  });
}

async function loadVoiceTrainLibrary() {
  const list = $("voice-library");
  if (!list) return;
  try {
    const data = await api(fetch("/api/voice-assets", { cache: "no-store" }));
    const assets = data.items || [];
    setText("voice-library-count", `${assets.length} 个`);
    list.replaceChildren();
    if (!assets.length) {
      const empty = document.createElement("li");
      empty.className = "voice-library-empty";
      empty.textContent = "暂无训练音色，可在上方「训练」环节生成";
      list.append(empty);
      return;
    }
    assets.forEach((asset) => {
      const li = document.createElement("li");
      li.className = "voice-library-item";
      const name = document.createElement("b");
      name.textContent = asset.name;
      const meta = document.createElement("span");
      meta.textContent = asset.status === "ready" ? "GPT-SoVITS · 可绑定" : asset.status === "processing" ? "训练中" : "训练失败";
      li.append(name, meta);
      if (asset.status === "ready") {
        const preview = document.createElement("button");
        preview.type = "button";
        preview.className = "icon-button";
        preview.title = "试听";
        preview.setAttribute("aria-label", "试听");
        preview.innerHTML = '<i data-lucide="play"></i>';
        preview.onclick = () => previewEditAssetById(asset.id);
        const bind = document.createElement("button");
        bind.type = "button";
        bind.className = "button button-secondary";
        bind.textContent = "去绑定";
        bind.onclick = () => { if (window.switchView) window.switchView("manage"); };
        li.append(preview, bind);
      }
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "icon-button icon-button-danger";
      remove.title = "删除";
      remove.setAttribute("aria-label", "删除音色");
      remove.innerHTML = '<i data-lucide="trash-2"></i>';
      remove.onclick = () => deleteVoiceAsset(asset.id, asset.name);
      li.append(remove);
      list.append(li);
    });
    if (window.lucide) window.lucide.createIcons();
  } catch (reason) {
    list.replaceChildren();
    const empty = document.createElement("li");
    empty.className = "voice-library-empty";
    empty.textContent = "训练音色加载失败";
    list.append(empty);
  }
}

async function previewEditAssetById(assetId) {
  try {
    const response = await fetch(`/api/voice-assets/${assetId}/synthesize`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-YUMENO-Request": "web" },
      body: JSON.stringify({ text: "你好，这是我的声音。很高兴认识你。" }),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || "试听失败");
    if (window.PL && window.PL.unlockAudio) window.PL.unlockAudio();
    const audio = new Audio(URL.createObjectURL(await response.blob()));
    audio.play().catch(() => {});
  } catch (reason) {
    setText("voice-library-status", `试听失败：${reason.message || reason}`, true);
  }
}

function renderVoiceTrainMeta() {
  const count = voiceSelected.size;
  const segments = (voiceLastState && voiceLastState.segments) || [];
  const seconds = segments
    .filter((segment) => voiceSelected.has(String(segment.index)))
    .reduce((sum, segment) => sum + (segment.seconds || 0), 0);
  const meta = $("voice-train-meta");
  if (meta) meta.textContent = count
    ? `已选择 ${count} 个片段，合计约 ${seconds.toFixed(0)} 秒（建议 1~10 分钟）`
    : "未选择片段（可在上一环节勾选）";
  setText("voice-train-status", "");
  setText("voice-train-hint", "");
  const progress = $("voice-train-progress");
  if (progress) progress.classList.add("is-hidden");
  const toManage = $("voice-train-to-manage");
  if (toManage) toManage.classList.add("is-hidden");
  const start = $("voice-train-start");
  if (start) start.disabled = !count;
  const state = $("voice-train-state");
  if (state) state.textContent = count ? `${count} 段` : "";
}

async function startVoiceTraining() {
  const name = $("voice-train-name").value.trim();
  const language = $("voice-train-lang") ? $("voice-train-lang").value : "zh";
  if (!name) return setText("voice-train-status", "请填写音色名称", true);
  if (!voiceSelected.size) return setText("voice-train-status", "请先在片段环节勾选素材", true);
  setDisabled("voice-train-start", true);
  const progress = $("voice-train-progress");
  if (progress) {
    progress.classList.remove("is-hidden");
    progress.removeAttribute("value");
  }
  setText("voice-train-status", "正在准备数据（音频转换 + ASR 标注），素材较多时可能需要几分钟…");
  try {
    const result = await api(fetch("/api/voice-assets/train-from-studio", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-YUMENO-Request": "web" },
      body: JSON.stringify({
        name,
        session_id: voiceSessionId,
        segment_indices: [...voiceSelected],
        language,
      }),
    }));
    setText("voice-train-status", "训练已启动（后台进行，页面会自动刷新进度）");
    pollVoiceTraining(result.asset.id);
  } catch (reason) {
    setText("voice-train-status", `启动失败：${friendlyError(reason)}`, true);
    setDisabled("voice-train-start", false);
  }
}

function pollVoiceTraining(assetId) {
  const timer = setInterval(async () => {
    try {
      const asset = await api(fetch(`/api/voice-assets/${assetId}`));
      if (asset.status === "processing") {
        const progress = $("voice-train-progress");
        const tp = asset.training_progress || {};
        const epochText = tp.epoch != null ? ` · Epoch ${tp.epoch}/${tp.total_epochs}` : "";
        if (progress) {
          progress.classList.remove("is-hidden");
          if (tp.total_epochs && tp.epoch != null) {
            progress.value = Math.round((tp.epoch / tp.total_epochs) * 100);
          } else {
            progress.removeAttribute("value");
          }
        }
        setText("voice-train-status", `训练中：${asset.training_stage || "处理中"}${epochText}（后台进行，可先离开页面）`);
      } else {
        clearInterval(timer);
        const progress = $("voice-train-progress");
        if (progress) progress.classList.add("is-hidden");
        if (asset.status === "ready") {
          setText("voice-train-status", `训练完成：${asset.name} 已保存到训练音色库`);
          setText("voice-train-hint", "可在下方「训练音色」列表试听，或去角色页绑定");
          const toManage = $("voice-train-to-manage");
          if (toManage) toManage.classList.remove("is-hidden");
          loadVoiceTrainLibrary();
        } else if (asset.status === "failed") {
          setText("voice-train-status", `训练失败：${asset.error_message || "请查看训练日志"}（详情见 data/gpt_sovits/voices/${asset.id}/training.log）`, true);
          setDisabled("voice-train-start", false);
        }
      }
    } catch (reason) {
      const progress = $("voice-train-progress");
      if (progress) progress.classList.add("is-hidden");
      clearInterval(timer);
      setText("voice-train-status", `状态查询失败：${friendlyError(reason)}`, true);
      setDisabled("voice-train-start", false);
    }
  }, 4000);
}

function bindVoiceDrop(zoneId, inputId) {
  const zone = $(zoneId);
  const input = $(inputId);
  if (!zone || !input) return;
  zone.addEventListener("click", () => input.click());
  zone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      input.click();
    }
  });
  zone.addEventListener("dragover", (event) => {
    event.preventDefault();
    zone.classList.add("is-dragging");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("is-dragging"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("is-dragging");
    const files = [...(event.dataTransfer?.files || [])];
    if (!files.length) return;
    const transfer = new DataTransfer();
    transfer.items.add(files[0]);
    input.files = transfer.files;
    input.dispatchEvent(new Event("change"));
  });
}

async function checkVoiceResources() {
  try {
    const separator = await api(fetch("/api/tts/separator/status", { headers: { "X-YUMENO-Request": "web" } }));
    voiceSeparatorReady = separator.ready;
    voiceSeparatorInstalling = Boolean(separator.installing);
    voiceSeparatorProgress = separator.progress_percent ?? null;
  } catch (reason) {
    voiceSeparatorReady = false;
    voiceSeparatorInstalling = false;
    voiceSeparatorProgress = null;
  }
  try {
    const tts = await api(fetch("/api/tts/status", { headers: { "X-YUMENO-Request": "web" } }));
    voiceTtsReady = tts.ready;
    voiceTtsInstalling = Boolean(tts.installing);
    voiceTtsProgress = tts.progress_percent ?? null;
  } catch (reason) {
    voiceTtsReady = false;
    voiceTtsInstalling = false;
    voiceTtsProgress = null;
  }
  renderVoiceBanner();
  renderVoiceResourceGates();
  renderVoiceEntryHints();
  if ((voiceSeparatorInstalling || voiceTtsInstalling) && !voiceResourcePolling) {
    voiceResourcePolling = true;
    voiceResourcePollTimer = setTimeout(async () => {
      voiceResourcePolling = false;
      await checkVoiceResources();
    }, 2500);
  }
}

function renderVoiceBanner() {
  const banner = $("voice-studio-banner");
  if (!banner) return;
  if (!voiceSeparatorReady) {
    banner.textContent = voiceSeparatorInstalling
      ? `人声分离模型安装中${voiceSeparatorProgress != null ? `（${voiceSeparatorProgress}%）` : ""}：安装完成前无法从「视频 / 音频」开始，可先进入「片段」投放干净音频。`
      : "人声分离模型未安装：请在「设置 → 语音合成（TTS）」中安装（约 165 MB），或直接进入「片段」投放干净音频。";
    banner.classList.remove("is-hidden");
  } else {
    banner.classList.add("is-hidden");
  }
}

function renderVoiceEntryHints() {
  const audio = $("voice-audio-entry");
  if (audio) {
    if (voiceSeparatorReady) {
      audio.textContent = "分离引擎已就绪，可从本步骤开始，或直接进入「片段」";
      audio.className = "voice-entry-hint is-ok";
    } else if (voiceSeparatorInstalling) {
      audio.textContent = `分离引擎安装中${voiceSeparatorProgress != null ? `（${voiceSeparatorProgress}%）` : ""}，可先进入「片段」投放干净音频`;
      audio.className = "voice-entry-hint is-warn";
    } else {
      audio.textContent = "分离引擎未安装，建议直接进入「片段」投放干净音频";
      audio.className = "voice-entry-hint is-danger";
    }
  }
  const segments = $("voice-segments-entry");
  const segmentsCount = (voiceLastState && voiceLastState.segments || []).length;
  if (segments) {
    if (segmentsCount > 0) {
      segments.textContent = `已有 ${segmentsCount} 段片段，可进入训练`;
      segments.className = "voice-entry-hint is-ok";
    } else {
      segments.textContent = "支持直接投放干净音频，从本步骤开始";
      segments.className = "voice-entry-hint is-muted";
    }
  }
  const train = $("voice-train-entry");
  if (train) {
    if (!voiceTtsReady) {
      if (voiceTtsInstalling) {
        train.textContent = `训练引擎安装中${voiceTtsProgress != null ? `（${voiceTtsProgress}%）` : ""}，暂无法开始训练`;
        train.className = "voice-entry-hint is-warn";
      } else {
        train.textContent = "训练引擎未安装，无法开始训练";
        train.className = "voice-entry-hint is-danger";
      }
    } else if (voiceSelected.size > 0) {
      train.textContent = `已选 ${voiceSelected.size} 段素材，可开始训练`;
      train.className = "voice-entry-hint is-ok";
    } else {
      train.textContent = "需先在「片段」勾选素材，再进入本步骤";
      train.className = "voice-entry-hint is-muted";
    }
  }
  const audioNode = document.querySelector('.voice-flow li[data-flow-step="audio"]');
  const trainNode = document.querySelector('.voice-flow li[data-flow-step="train"]');
  [audioNode, trainNode].forEach((node) => {
    if (!node) return;
    node.classList.remove("flow-ready", "flow-installing", "flow-missing");
    const ready = node === audioNode ? voiceSeparatorReady : voiceTtsReady;
    const installing = node === audioNode ? voiceSeparatorInstalling : voiceTtsInstalling;
    node.classList.add(ready ? "flow-ready" : installing ? "flow-installing" : "flow-missing");
    const label = node.querySelector(".flow-status");
    if (label) {
      const engine = node === audioNode ? "分离引擎" : "训练引擎";
      const progress = node === audioNode ? voiceSeparatorProgress : voiceTtsProgress;
      label.textContent = ready
        ? `${engine}已就绪`
        : installing
          ? `${engine}安装中${progress != null ? ` ${progress}%` : ""}`
          : `${engine}未安装`;
    }
  });
}

function renderVoiceResourceGates() {
  ["voice-video", "voice-audio"].forEach((id) => {
    const input = $(id);
    if (input) input.disabled = !voiceSeparatorReady;
  });
  const startSeparate = $("voice-start-separate");
  if (startSeparate && voiceSeparatorReady === false) startSeparate.disabled = true;
}

async function resumeVoiceSession() {
  try {
    const data = await api(fetch("/api/voice-studio/sessions", { headers: { "X-YUMENO-Request": "web" } }));
    const drafts = data.sessions || [];
    if (drafts.length) {
      voiceSessionId = drafts[0].session_id;
      await loadVoiceSession();
      return;
    }
  } catch (reason) {
    /* fall through to creating a new session */
  }
  await createVoiceSession();
}

async function createVoiceSession() {
  stopVoicePolling();
  voiceSessionId = null;
  clearVoicePanels();
  try {
    const state = await api(fetch("/api/voice-studio/sessions", { method: "POST", headers: { "X-YUMENO-Request": "web" } }));
    voiceSessionId = state.session_id;
    await loadVoiceSession();
  } catch (reason) {
    setText("voice-video-status", "创建会话失败", true);
  }
}

async function resetVoiceSession() {
  if (!window.confirm("重置当前草稿？已上传的音频、片段与参考音色都会被清除。")) return;
  if (voiceSessionId) {
    try {
      await api(fetch(`/api/voice-studio/sessions/${voiceSessionId}`, { method: "DELETE", headers: { "X-YUMENO-Request": "web" } }));
    } catch (reason) {
      /* 继续创建新草稿 */
    }
  }
  await createVoiceSession();
}

function clearVoicePanels() {
  voiceActiveStep = null;
  voiceActionStep = null;
  selectVoiceStep("video");
  [
    "voice-video-status",
    "voice-audio-status",
    "voice-segments-status",
    "voice-library-status",
  ].forEach((id) => setText(id));
  setText("voice-audio-state");
  setText("voice-video-state");
  setText("voice-segments-state");
  const audioFiles = $("voice-audio-files");
  if (audioFiles) audioFiles.replaceChildren();
  const meta = $("voice-audio-meta");
  if (meta) meta.classList.add("is-hidden");
  const progress = $("voice-separate-progress");
  if (progress) { progress.classList.add("is-hidden"); progress.removeAttribute("value"); }
  const phase = $("voice-separate-phase");
  if (phase) phase.textContent = "";
  const startSeparate = $("voice-start-separate");
  if (startSeparate) startSeparate.disabled = true;
  voiceSelected = new Set();
}

async function loadVoiceSession() {
  if (!voiceSessionId) return;
  const headers = { "X-YUMENO-Request": "web" };
  const state = await api(fetch(`/api/voice-studio/sessions/${voiceSessionId}`, { headers }));
  renderVoiceState(state);
  if (state.running) startVoicePolling();
}

function startVoicePolling() {
  stopVoicePolling();
  voicePollTimer = setInterval(async () => {
    if (!voiceSessionId) return stopVoicePolling();
    try {
      const state = await api(fetch(`/api/voice-studio/sessions/${voiceSessionId}`, { headers: { "X-YUMENO-Request": "web" } }));
      renderVoiceState(state);
      if (!state.running && !["queued", "extract", "convert", "separate", "slice", "write"].includes(state.phase)) {
        stopVoicePolling();
      }
    } catch (reason) {
      stopVoicePolling();
    }
  }, 1500);
}

function stopVoicePolling() {
  if (voicePollTimer) {
    clearInterval(voicePollTimer);
    voicePollTimer = null;
  }
}

function renderVoiceState(state) {
  if (state.session_id !== voiceSessionId) return;
  voiceLastState = state;
  renderVoiceFlow(state);
  renderVoiceSourceMeta(state);
  renderVoiceProgress(state);
  renderVoiceSegments(state);
  renderVoiceResourceGates();
  renderVoiceEntryHints();
  renderVoiceConfirm(state);
  if (!voiceActiveStep) {
    selectVoiceStep(VOICE_PHASE_STEP[state.phase] || "video");
  }
  advanceVoiceStep(state);
}

function renderVoiceConfirm(state) {
  document.querySelectorAll(".voice-step-confirm").forEach((button) => {
    button.disabled = !state.reference_file;
  });
}

function advanceVoiceStep(state) {
  if (!voiceActionStep) return;
  if (voiceActiveStep !== voiceActionStep) {
    voiceActionStep = null;
    return;
  }
  const busy = ["queued", "extract", "convert", "separate", "slice", "write"].includes(state.phase);
  const failed = state.phase === "failed" || state.phase === "cancelled";
  if (busy || failed) return;
  const done = (state.segments || []).length > 0;
  if (!done) return;
  const next = { video: "segments", audio: "segments" }[voiceActionStep];
  voiceActionStep = null;
  if (next) selectVoiceStep(next);
}

function renderVoiceFlow(state) {
  const activeStep = VOICE_PHASE_STEP[state.phase] || null;
  const activeIndex = activeStep ? VOICE_FLOW_ORDER.indexOf(activeStep) : -1;
  document.querySelectorAll("[data-flow-step]").forEach((node) => {
    const index = VOICE_FLOW_ORDER.indexOf(node.dataset.flowStep);
    node.classList.remove("is-complete", "is-active", "is-error", "is-pending");
    let className = "is-pending";
    if (state.phase === "done") {
      className = "is-complete";
    } else if (state.phase === "reference" && state.reference_file) {
      className = index <= 2 ? "is-complete" : "is-pending";
    } else if (state.phase === "failed" || state.phase === "cancelled") {
      className = index <= activeIndex ? "is-complete" : "is-pending";
      if (index === activeIndex) className = "is-error";
    } else if (activeIndex >= 0) {
      if (index < activeIndex) className = "is-complete";
      else if (index === activeIndex) className = "is-active";
    }
    node.classList.remove("is-pending");
    node.classList.add(className);
    const percent = node.querySelector(".flow-percent");
    if (percent) percent.remove();
    if (className === "is-active") {
      const em = node.querySelector("em");
      if (em) em.textContent = `${VOICE_PHASE_LABEL[state.phase] || "处理中"} ${state.progress || 0}%`;
    } else if (className === "is-complete") {
      const em = node.querySelector("em");
      if (em) em.textContent = VOICE_STEP_LABEL[node.dataset.flowStep];
    }
  });
}

function renderVoiceSourceMeta(state) {
  const meta = $("voice-audio-meta");
  const list = $("voice-audio-files");
  const startSeparate = $("voice-start-separate");
  if (state.source_kind) {
    if (meta) meta.classList.remove("is-hidden");
    if (meta) {
      setText("voice-audio-name", state.source_name);
      setText("voice-audio-detail", `${state.source_kind === "video" ? "来自视频" : "上传音频"}${state.source_duration ? ` · 约 ${Math.round(state.source_duration)} 秒` : ""}`);
    }
  } else {
    if (meta) meta.classList.add("is-hidden");
  }
  if (list) {
    list.replaceChildren();
    (state.audio_files || []).forEach((item) => {
      const li = document.createElement("li");
      li.className = "voice-audio-file";
      const name = document.createElement("b");
      name.textContent = item.name;
      const detail = document.createElement("span");
      detail.textContent = item.seconds ? `${Math.round(item.seconds)} 秒 · 已转为 WAV` : "已转为 WAV";
      const ok = document.createElement("i");
      ok.setAttribute("data-lucide", "check");
      li.append(name, detail, ok);
      list.append(li);
    });
    if (window.lucide) window.lucide.createIcons();
  }
  const ready = state.phase === "audio_ready";
  const busy = ["queued", "convert", "separate", "slice", "write"].includes(state.phase);
  if (startSeparate) startSeparate.disabled = !ready || !voiceSeparatorReady;
  setText(
    "voice-video-state",
    state.source_kind === "video" && !busy ? (state.phase === "failed" ? "失败" : "已处理") : ""
  );
  if (state.source_kind) {
    if (busy) setText("voice-audio-state", "处理中");
    else if (ready) setText("voice-audio-state", "已就绪，等待分离");
    else if (state.phase === "segments" || state.phase === "reference" || state.phase === "done") setText("voice-audio-state", "已完成");
    else setText("voice-audio-state", state.phase === "failed" ? "失败" : "");
  } else {
    setText("voice-audio-state");
  }
}

function renderVoiceProgress(state) {
  const progress = $("voice-separate-progress");
  const phase = $("voice-separate-phase");
  if (!progress || !phase) return;
  const busy = ["queued", "extract", "convert", "separate", "slice", "write"].includes(state.phase);
  progress.classList.toggle("is-hidden", !busy);
  if (busy) progress.value = state.progress || 0;
  phase.textContent = busy ? `${VOICE_PHASE_LABEL[state.phase] || "处理中"} ${state.progress || 0}%` : "";
  if (state.phase === "failed") {
    setText("voice-video-status", state.error || "处理失败", true);
    setText("voice-audio-status", state.error || "处理失败", true);
  }
}

function renderVoiceSegments(state) {
  const list = $("voice-segments");
  if (!list) return;
  list.replaceChildren();
  const segments = state.segments || [];
  setText("voice-segments-state", segments.length ? `${segments.length} 条` : "");
  setText("voice-segments-status", segments.length ? "" : "还没有可用片段，请先完成 01~02 步或上传干净音频");
  if (segments.length) {
    voiceSelected = new Set((state.selected || []).map((index) => String(index)));
  }
  segments.forEach((segment) => {
    const li = document.createElement("li");
    li.className = "voice-segment";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = segment.index;
    checkbox.checked = voiceSelected.has(String(segment.index));
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) voiceSelected.add(String(segment.index));
      else voiceSelected.delete(String(segment.index));
      syncVoiceBuildButton();
    });
    const badge = document.createElement("span");
    badge.className = "voice-segment-index";
    badge.textContent = String(segment.index + 1).padStart(2, "0");
    const info = document.createElement("div");
    info.className = "voice-segment-info";
    const name = document.createElement("b");
    name.textContent = `${segment.seconds.toFixed(1)} 秒`;
    const meta = document.createElement("span");
    meta.textContent = segment.source === "upload" ? "自行上传" : `自动截取 · RMS ${(segment.rms * 100).toFixed(1)}%`;
    info.append(name, meta);
    const play = document.createElement("button");
    play.type = "button";
    play.className = "icon-button";
    play.title = "试听";
    play.setAttribute("aria-label", "试听片段");
    play.innerHTML = '<i data-lucide="play"></i>';
    play.addEventListener("click", () => playVoiceSegment(state.session_id, segment.index));
    const download = document.createElement("a");
    download.className = "icon-button";
    download.title = "下载";
    download.setAttribute("aria-label", "下载片段");
    download.href = "#";
    download.addEventListener("click", (event) => {
      event.preventDefault();
      downloadVoiceSegment(state.session_id, segment.index);
    });
    download.innerHTML = '<i data-lucide="download"></i>';
    li.append(checkbox, badge, info, play, download);
    if (segment.source === "upload") {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "icon-button icon-button-danger";
      remove.title = "移除";
      remove.setAttribute("aria-label", "移除该上传片段");
      remove.innerHTML = '<i data-lucide="trash-2"></i>';
      remove.addEventListener("click", () => deleteVoiceSegment(state.session_id, segment.index));
      li.append(remove);
    }
    list.append(li);
  });
  if (window.lucide) window.lucide.createIcons();
  syncVoiceBuildButton();
}

async function deleteVoiceSegment(sessionId, index) {
  try {
    await api(fetch(`/api/voice-studio/sessions/${sessionId}/segments/${index}`, { method: "DELETE", headers: { "X-YUMENO-Request": "web" } }));
    await loadVoiceSession();
  } catch (reason) {
    setText("voice-segments-status", `移除失败：${reason.message || reason}`, true);
  }
}
function syncVoiceBuildButton() {
  const trainEnter = $("voice-train-enter");
  if (trainEnter) trainEnter.disabled = voiceSelected.size === 0;
  const count = $("voice-segments-count");
  if (count) count.textContent = voiceSelected.size ? `${voiceSelected.size} 段已选` : "";
  renderVoiceEntryHints();
}

function toggleSelectAllVoiceSegments() {
  const segments = (voiceLastState && voiceLastState.segments) || [];
  if (!segments.length) return;
  const allSelected = segments.every((segment) => voiceSelected.has(String(segment.index)));
  segments.forEach((segment) => {
    if (allSelected) voiceSelected.delete(String(segment.index));
    else voiceSelected.add(String(segment.index));
  });
  const list = $("voice-segments");
  if (list) {
    list.querySelectorAll('input[type="checkbox"]').forEach((checkbox) => {
      checkbox.checked = !allSelected;
    });
  }
  syncVoiceBuildButton();
  const count = $("voice-segments-count");
  if (count) count.textContent = `${voiceSelected.size} 段已选`;
}


async function playVoiceSegment(sessionId, index) {
  try {
    const response = await fetch(`/api/voice-studio/sessions/${sessionId}/segments/${index}/audio`, { headers: { "X-YUMENO-Request": "web" } });
    if (!response.ok) throw new Error("片段不可用");
    if (window.PL && window.PL.unlockAudio) window.PL.unlockAudio();
    const audio = new Audio(URL.createObjectURL(await response.blob()));
    const play = window.PL && window.PL.audio ? window.PL.audio.play(audio) : audio.play();
    play.catch(() => {});
  } catch (reason) {
    setText("voice-segments-status", reason.message || reason, true);
  }
}

async function downloadVoiceSegment(sessionId, index) {
  try {
    const response = await fetch(`/api/voice-studio/sessions/${sessionId}/segments/${index}/audio`, { headers: { "X-YUMENO-Request": "web" } });
    if (!response.ok) throw new Error("片段不可用");
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = `segment_${String(index + 1).padStart(3, "0")}.wav`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 3000);
  } catch (reason) {
    setText("voice-segments-status", `下载失败：${reason.message || reason}`, true);
  }
}

async function uploadVoiceVideo() {
  const input = $("voice-video");
  const file = input && input.files && input.files[0];
  if (!file || !voiceSessionId) return;
  input.value = "";
  if (!voiceSeparatorReady) return setText("voice-video-status", "请先安装人声分离模型（设置 → 语音合成(TTS)）", true);
  const extension = (file.name.split(".").pop() || "").toLowerCase();
  if (!["mp4", "mkv", "webm", "mov", "m4a", "avi"].includes(extension)) return setText("voice-video-status", "仅支持 mp4 / mkv / webm / mov / m4a / avi", true);
  if (file.size > 400 * 1024 * 1024) return setText("voice-video-status", "视频超过 400 MB 上限", true);
  setText("voice-video-status", "正在上传视频…");
  const form = new FormData();
  form.append("video", file);
  try {
    const state = await api(fetch(`/api/voice-studio/sessions/${voiceSessionId}/video`, { method: "POST", headers: { "X-YUMENO-Request": "web" }, body: form }));
    voiceSessionId = state.session_id;
    voiceActionStep = "video";
    await loadVoiceSession();
  } catch (reason) {
    setText("voice-video-status", `上传失败：${reason.message || reason}`, true);
  }
}

async function uploadVoiceAudio() {
  const input = $("voice-audio");
  const files = input && input.files ? [...input.files] : [];
  if (!files.length || !voiceSessionId) return;
  input.value = "";
  if (!voiceSeparatorReady) return setText("voice-audio-status", "请先安装人声分离模型（设置 → 语音合成(TTS)）", true);
  if (files.some((file) => file.size > 200 * 1024 * 1024)) return setText("voice-audio-status", "单个音频超过 200 MB 上限", true);
  setText("voice-audio-status", `正在上传 ${files.length} 个音频并转换为 WAV…`);
  const form = new FormData();
  files.forEach((file) => form.append("files", file));
  try {
    const state = await api(fetch(`/api/voice-studio/sessions/${voiceSessionId}/audio`, { method: "POST", headers: { "X-YUMENO-Request": "web" }, body: form }));
    voiceSessionId = state.session_id;
    await loadVoiceSession();
  } catch (reason) {
    setText("voice-audio-status", `上传失败：${reason.message || reason}`, true);
  }
}

async function startVoiceSeparation() {
  if (!voiceSessionId) return;
  const button = $("voice-start-separate");
  if (button) button.disabled = true;
  setText("voice-audio-status", "正在分离人声…");
  try {
    await api(fetch(`/api/voice-studio/sessions/${voiceSessionId}/separate`, { method: "POST", headers: { "X-YUMENO-Request": "web" } }));
    voiceActionStep = "audio";
    await loadVoiceSession();
  } catch (reason) {
    setText("voice-audio-status", `分离失败：${reason.message || reason}`, true);
    if (button) button.disabled = false;
  }
}

async function uploadVoiceSegments() {
  const input = $("voice-segments-upload-input");
  const files = input && input.files ? [...input.files] : [];
  if (!files.length || !voiceSessionId) return;
  input.value = "";
  if (files.some((file) => file.size > 200 * 1024 * 1024)) return setText("voice-segments-status", "单个片段超过 200 MB 上限", true);
  setText("voice-segments-status", `正在上传 ${files.length} 个片段…`);
  const form = new FormData();
  files.forEach((file) => form.append("files", file));
  try {
    const state = await api(fetch(`/api/voice-studio/sessions/${voiceSessionId}/segments/upload`, { method: "POST", headers: { "X-YUMENO-Request": "web" }, body: form }));
    setText("voice-segments-status", `已添加 ${files.length} 个上传片段，可勾选用于生成参考音色`);
    await loadVoiceSession();
  } catch (reason) {
    setText("voice-segments-status", `上传失败：${reason.message || reason}`, true);
  }
}

async function deleteVoiceAsset(assetId, name) {
  if (!window.confirm(`删除音色「${name}」？使用该音色的角色将需要重新选择。`)) return;
  try {
    await api(fetch(`/api/voice-assets/${assetId}`, { method: "DELETE", headers: { "X-YUMENO-Request": "web" } }));
    await loadVoiceTrainLibrary();
  } catch (reason) {
    setText("voice-library-status", `删除失败：${reason.message || reason}`, true);
  }
}
