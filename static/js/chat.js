"use strict";
window.PL = window.PL || { modules: {} };
window.PL.modules.chat = { init: initChat };

let chatGlobalEventsBound = false;

function formatMessageTime(iso) {
  const date = iso ? new Date(iso) : new Date();
  if (Number.isNaN(date.getTime())) return "";
  const pad = (value) => String(value).padStart(2, "0");
  const clock = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  const now = new Date();
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();
  return sameDay ? clock : `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${clock}`;
}

function messageActionButton(label, icon, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "message-action";
  button.title = label;
  button.setAttribute("aria-label", label);
  const glyph = document.createElement("i");
  glyph.dataset.lucide = icon;
  const text = document.createElement("span");
  text.textContent = label;
  button.append(glyph, text);
  button.addEventListener("click", onClick);
  return button;
}

async function copyMessageText(text, button) {
  const label = button.querySelector("span");
  const original = label.textContent;
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const helper = document.createElement("textarea");
    helper.value = text;
    document.body.append(helper);
    helper.select();
    try { document.execCommand("copy"); } catch { /* clipboard unavailable */ }
    helper.remove();
  }
  label.textContent = "已复制";
  setTimeout(() => { label.textContent = original; }, 1200);
}

function bindChatGlobalEvents() {
  if (chatGlobalEventsBound) return;
  chatGlobalEventsBound = true;
  document.addEventListener("click", (event) => { if (!event.target.closest(".chat-persona-picker")) closePersonaMenu(); });
  document.addEventListener("click", (event) => { if (!event.target.closest(".chat-settings")) closeChatSettingsMenu(); });
}

function initChat() {
  state.voicePlaybackActive = false;
  state.voicePlaybackQueue = [];
  state.voicePlayingAudio = null;
  state.voiceStreamAbort = null;
  state.voiceFeed = null;
  state.voiceFeedFailed = false;
  state.voiceFeedFullText = "";
  bindChatEvents();
  bindChatGlobalEvents();
  observeChatStatus();
  updateChatStatusCard();
  renderPersonaList();
  if (state.activePersona) {
    loadConversationMessages();
    connectRealtime();
  }
}

function bindChatEvents() {
  ensureVoiceChatButton();
  $("question-form").addEventListener("submit", submitQuestion);
  $("question").addEventListener("input", resizeComposer);
  $("voice-chat").addEventListener("click", toggleVoiceChat);
  $("confirm-action").addEventListener("click", () => resumeAgent(true));
  $("cancel-action").addEventListener("click", () => resumeAgent(false));
  $("question").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!$("send-question").disabled) $("question-form").requestSubmit();
    }
  });
  $("clear-conversation").addEventListener("click", clearConversation);
  $("chat-persona-toggle").addEventListener("click", togglePersonaDrawer);
  $("chat-settings-toggle").addEventListener("click", (event) => { event.stopPropagation(); toggleChatSettingsMenu(); });
  document.querySelectorAll("#chat-settings-menu button").forEach((button) => button.addEventListener("click", closeChatSettingsMenu));
  $("assistant-voice-toggle").addEventListener("change", () => localStorage.setItem("yumeno:assistant-voice", $("assistant-voice-toggle").checked ? "on" : "off"));
}
function connectRealtime() {
  if (!state.activePersona) return;
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const url = `${scheme}://${location.host}/ws/personas/${state.activePersona.id}/conversations/${state.conversationId}`;
  const socket = new WebSocket(url);
  state.realtimeSocket = socket;
  socket.addEventListener("message", (message) => {
    if (socket !== state.realtimeSocket) return;
    try { handleRealtimeEvent(JSON.parse(message.data)); }
    catch { setText("chat-error", "实时会话返回了无效数据"); }
  });
  socket.addEventListener("close", () => {
    if (socket !== state.realtimeSocket) return;
    state.realtimeSocket = null;
    if (state.realtimeSubmissionPending) failRealtimeSubmission("实时连接在接收请求前中断，请重新操作");
    if (state.realtimeTurnId) {
      state.realtimeTurnId = null;
      state.realtimeAnswerNode = null;
      setRealtimeBusy(false);
      setText("chat-error", "实时连接已中断，请重新发送消息");
    }
    state.realtimeExecutionPending = false;
    setRealtimeBusy(false);
  });
  socket.addEventListener("error", () => {
    if (socket === state.realtimeSocket) setText("chat-error", "实时连接不可用，将使用普通对话");
  });
}
function closeRealtime() {
  const socket = state.realtimeSocket;
  clearTimeout(state.realtimeAckTimer);
  state.realtimeSocket = null;
  state.realtimeTurnId = null;
  state.realtimeAnswerNode = null;
  state.realtimeExecutionPending = false;
  state.realtimeSubmissionPending = false;
  state.agentRequestPending = false;
  state.realtimePendingQuestion = "";
  state.realtimeAckTimer = null;
  resetPacing();
  setRealtimeBusy(false);
  if (socket && socket.readyState < WebSocket.CLOSING) socket.close();
}
function setRealtimeBusy(busy) {
  state.realtimeBusy = busy;
  setText("question-status", busy ? "角色正在生成回复…" : "");
  if (!$("question-form")) return;
  $("question-form").classList.toggle("is-generating", busy);
  setSendButton(busy);
  $("confirm-action").disabled = busy;
  $("cancel-action").disabled = busy;
  updateComposerControls();
}
function setSendButton(busy) {
  const button = $("send-question");
  if (!button) return;
  button.classList.toggle("is-stop", busy);
  const icon = button.querySelector("i");
  if (icon) icon.dataset.lucide = busy ? "square" : "send-horizontal";
  button.title = busy ? "停止生成" : "发送";
  button.setAttribute("aria-label", button.title);
  if (window.lucide) window.lucide.createIcons();
}
function resetChatProcess() {
  updateChatStatusCard();
}
function showReplyLoading() {
  if (state.pendingReplyNode) return state.pendingReplyNode;
  const node = appendMessage("assistant", ""); node.classList.add("message-loading");
  const body = node.querySelector("p"); body.classList.add("loading-bubble");
  body.innerHTML = "<span></span><span></span><span></span>";
  state.pendingReplyNode = node; return node;
}
function replaceReplyLoading(node, text) {
  if (!node) return appendMessage("assistant", text);
  node.classList.remove("message-loading");
  const body = node.querySelector("p"); body.classList.remove("loading-bubble"); body.textContent = text;
  state.pendingReplyNode = null; return node;
}
function handleRealtimeEvent(event) {
  if (event.type === "session.ready") {
    state.realtimeExecutionPending = false;
    setRealtimeBusy(false);
    flushPendingVoiceQuestion();
    if (window.PLLive2DHub) window.PLLive2DHub.setAgentState("idle");
    return;
  }
  if (event.type === "session.pong" || event.type === "agent.status") return;
  if (event.type === "turn.started") {
    if (window.PLLive2DHub) window.PLLive2DHub.setAgentState("thinking");
    clearRealtimeSubmission();
    state.realtimeTurnId = event.turn_id;
    state.realtimeAnswerNode = null;
    state.voiceFeed = null;
    state.voiceFeedFailed = false;
    state.voiceFeedFullText = "";
    resetPacing();
    resetChatProcess();
    showReplyLoading();
    setRealtimeBusy(true);
    return;
  }
  if (event.turn_id && event.turn_id !== state.realtimeTurnId) return;
  if (event.type === "agent.stage") {
    if (!state.realtimeAnswerNode) state.realtimeAnswerNode = showReplyLoading();
    state.realtimeAnswerNode.classList.remove("message-loading");
    const body = state.realtimeAnswerNode.querySelector("p");
    body.classList.remove("loading-bubble");
    body.textContent = "";
    setReplyStage(state.realtimeAnswerNode, event.stage);
  } else if (event.type === "text.delta") {
    if (!state.realtimeAnswerNode) state.realtimeAnswerNode = showReplyLoading();
    state.realtimeAnswerNode.classList.remove("message-loading");
    state.realtimeAnswerNode.querySelector("p").classList.remove("loading-bubble");
    clearReplyStage(state.realtimeAnswerNode);
    appendPacedText(event.text, state.realtimeAnswerNode);
    feedVoiceText(event.text);
  } else if (event.type === "text.final") {
    if (window.PLLive2DHub) window.PLLive2DHub.setAgentState("idle");
    const target = state.realtimeAnswerNode || (event.answer ? showReplyLoading() : null);
    if (target) clearReplyStage(target);
    state.pendingReplyNode = null;
    if (target) {
      finishPacing(target, event.answer || "");
      finishVoiceFeed();
      if (state.voiceFeedFailed) {
        const fullText = (state.voiceFeedFullText || "").trim();
        if (fullText) synthesizeAnswer(fullText, target);
      }
      state.voiceFeedFailed = false;
      state.voiceFeedFullText = "";
      drainPacedText(() => { if (state.realtimeAnswerNode === target) state.realtimeAnswerNode = null; });
    }
    state.pendingAction = null;
    renderConfirmation();
    state.realtimeTurnId = null;
    setRealtimeBusy(false);
  } else if (event.type === "confirmation.required") {
    if (window.PLLive2DHub) window.PLLive2DHub.setAgentState("idle");
    if (state.realtimeAnswerNode) clearReplyStage(state.realtimeAnswerNode);
    state.pendingAction = { action: event.pending_action, specialist: event.specialist };
    state.realtimeTurnId = null;
    state.realtimeAnswerNode = null;
    abortVoiceStream();
    resetPacing();
    renderConfirmation();
    setRealtimeBusy(false);
  } else if (event.type === "turn.cancelled") {
    if (window.PLLive2DHub) window.PLLive2DHub.setAgentState("idle");
    resetPacing();
    state.realtimeTurnId = null;
    state.realtimeAnswerNode = null;
    state.realtimeExecutionPending = true;
    setRealtimeBusy(false);
  } else if (event.type === "error") {
    if (window.PLLive2DHub) window.PLLive2DHub.setAgentState("idle");
    if (state.realtimeSubmissionPending) failRealtimeSubmission(event.message || "实时会话未接收消息，请重新发送");
    setText("chat-error", event.message || "实时会话发生错误");
    state.realtimeTurnId = null;
    state.realtimeAnswerNode = null;
    if (event.code !== "turn_in_progress") state.realtimeExecutionPending = false;
    setRealtimeBusy(false);
  }
}
function sendRealtime(payload) {
  if (state.realtimeSocket?.readyState !== WebSocket.OPEN) return false;
  try { state.realtimeSocket.send(JSON.stringify(payload)); return true; }
  catch { return false; }
}
function clearRealtimeSubmission() {
  clearTimeout(state.realtimeAckTimer);
  state.realtimeAckTimer = null;
  state.realtimeSubmissionPending = false;
  state.agentRequestPending = false;
  state.realtimePendingQuestion = "";
  updateComposerControls();
}
function failRealtimeSubmission(message) {
  const question = state.realtimePendingQuestion;
  clearRealtimeSubmission();
  if (question) $("question").value = question;
  setText("chat-error", message);
  setRealtimeBusy(false);
}
function awaitRealtimeAcknowledgement(question) {
  state.realtimeSubmissionPending = true;
  state.agentRequestPending = true;
  state.realtimePendingQuestion = question;
  clearTimeout(state.realtimeAckTimer);
  state.realtimeAckTimer = setTimeout(() => {
    if (!state.realtimeSubmissionPending) return;
    failRealtimeSubmission("实时会话响应超时，请重新发送");
    state.realtimeSocket?.close();
  }, 5000);
  updateComposerControls();
}
function cancelRealtimeTurn() {
  stopVoicePlayback();
  if (state.agentStreamController) { state.agentStreamController.abort(); state.agentStreamController = null; }
  if (state.realtimeTurnId) sendRealtime({ type: "generation.cancel" });
}

function setReplyStage(node, stage) {
  if (!node) return;
  let status = node.querySelector(".voice-bubble-status.stage-status");
  if (!status) {
    status = document.createElement("span");
    status.className = "voice-bubble-status is-generating stage-status";
    node.append(status);
  }
  status.textContent = stage || "正在思考…";
}

function clearReplyStage(node) {
  if (!node) return;
  const status = node.querySelector(".voice-bubble-status.stage-status");
  if (status) status.remove();
}
function stopVoicePlayback() {
  abortVoiceStream();
  const current = state.voicePlayingAudio;
  if (current) { try { current.pause(); } catch {} }
  state.voicePlayingAudio = null;
  state.voicePlaybackActive = false;
  state.voicePlaybackQueue = [];
  state.voicePlaybackEpoch += 1;
}
const PACE_INTERVAL_MS = 80;
let paceTimer = null;
let paceDone = null;
function appendPacedText(text, node) {
  if (!text) return;
  state.textPaceBuffer += text;
  if (node) state.paceNode = node;
  ensurePacer(null);
}
function ensurePacer(done) {
  if (done) {
    // 已有完成回调时合并而非丢弃：text.final 的 drainPacedText 回调
    // 若被吞掉，realtimeAnswerNode 永远不清除，后续回复会渲染错位。
    const previous = paceDone;
    paceDone = previous ? () => { previous(); done(); } : done;
  }
  if (paceTimer) return;
  paceTimer = setInterval(() => {
    const node = state.paceNode || state.realtimeAnswerNode;
    const buffer = state.textPaceBuffer;
    if (!node || !buffer) {
      clearInterval(paceTimer); paceTimer = null;
      state.paceCharsPerTick = 1;
      const callback = paceDone; paceDone = null;
      if (callback) callback();
      return;
    }
    const step = Math.min(buffer.length, state.paceCharsPerTick);
    node.querySelector("p").textContent += buffer.slice(0, step);
    state.textPaceBuffer = buffer.slice(step);
    if (!state.textPaceBuffer) {
      clearInterval(paceTimer); paceTimer = null;
      state.paceCharsPerTick = 1;
      const callback = paceDone; paceDone = null;
      if (callback) callback();
    }
  }, PACE_INTERVAL_MS);
}
function drainPacedText(done) {
  state.paceCharsPerTick = 5;
  ensurePacer(done);
}
function finishPacing(node, fullAnswer) {
  if (fullAnswer && !node.querySelector("p").textContent && !state.textPaceBuffer) {
    state.textPaceBuffer = fullAnswer;
  }
}
function resetPacing() {
  if (paceTimer) { clearInterval(paceTimer); paceTimer = null; }
  paceDone = null;
  state.textPaceBuffer = "";
  state.paceCharsPerTick = 1;
  state.paceNode = null;
}
function updateChatStatusCard() {
  const status = $("chat-inline-status");
  if (!status) return;
  const hasStatus = ["audio-status", "question-status", "chat-error"].some((id) => $(id)?.textContent.trim());
  status.classList.toggle("is-hidden", !hasStatus);
}
function observeChatStatus() {
  const targets = ["audio-status", "question-status", "chat-error"].map((id) => $(id)).filter(Boolean);
  if (!targets.length) return;
  const observer = new MutationObserver(() => updateChatStatusCard());
  targets.forEach((node) => observer.observe(node, { childList: true, characterData: true, subtree: true }));
}
function updateComposerControls() {
  if (!$("question-form")) return;
  const conversationBusy = isConversationBusy();
  const voiceActive = state.voiceActive;
  $("question-form").classList.toggle("is-voice-active", voiceActive);
  if ($("voice-chat")) {
    $("voice-chat").disabled = !state.asrConfigured || !state.activePersona;
  }
  $("send-question").classList.toggle("is-hidden", voiceActive);
  $("send-question").disabled = conversationBusy || !state.activePersona;
  $("confirm-action").disabled = state.realtimeBusy || voiceActive;
  $("cancel-action").disabled = state.realtimeBusy || voiceActive;
}
function isConversationBusy() {
  return state.realtimeBusy || state.agentRequestPending || state.realtimeSubmissionPending || state.realtimeExecutionPending || Boolean(state.pendingAction);
}
function ensureVoiceChatButton() {
  if ($("voice-chat") || !$("send-question")) return;
  const button = document.createElement("button");
  button.id = "voice-chat";
  button.type = "button";
  button.className = "icon-button";
  button.title = "语音模式";
  button.setAttribute("aria-label", "语音模式");
  button.disabled = true;
  const icon = document.createElement("i");
  icon.dataset.lucide = "audio-lines";
  button.append(icon);
  $("send-question").insertAdjacentElement("beforebegin", button);
  icons();
}
function renderVoiceChatButton() {
  const button = $("voice-chat");
  if (!button) return;
  const active = state.voiceActive;
  button.classList.toggle("is-active", active);
  button.title = active ? "停止语音模式" : "语音模式";
  button.setAttribute("aria-label", button.title);
  const input = $("question");
  if (input) {
    input.readOnly = active;
    input.placeholder = active ? "语音模式：随时说话，说完自动发送" : "输入消息，Enter 发送";
  }
}
async function toggleVoiceChat() {
  if (state.voiceActive) {
    stopVoiceChat();
    return;
  }
  await startVoiceChat();
}
async function startVoiceChat() {
  if (state.voiceActive || !state.asrConfigured || !state.activePersona || isConversationBusy()) return;
  setText("chat-error");
  const stream = new window.PLVoiceStream({
    onState: (voiceState) => {
      if (window.PLLive2DHub) window.PLLive2DHub.setVoiceState(voiceState);
      if (voiceState === "speaking") {
        stopVoicePlayback();
        setText("audio-status", "正在听…");
      }
      else if (voiceState === "ready") setText("audio-status", "就绪，请说话");
      else setText("audio-status", "正在准备语音识别…");
    },
    onPartial: (text) => {
      $("question").value = text;
      resizeComposer();
    },
    onFinal: (message) => {
      const text = (message.text || "").trim();
      if (text) submitVoiceFinal(text);
    },
    onError: (message, code) => {
      if (code === "empty") {
        setText("audio-status", "没听清，请再说一次");
        return;
      }
      setText("chat-error", message);
      stopVoiceChat();
    },
    onClosed: () => {
      state.voiceStream = null;
      state.voiceActive = false;
      renderVoiceChatButton();
      updateComposerControls();
    },
  });
  state.voiceStream = stream;
  const started = await stream.start();
  if (!started) {
    state.voiceStream = null;
    renderVoiceChatButton();
    updateComposerControls();
    return;
  }
  state.voiceActive = true;
  setText("audio-status", "正在准备语音识别…");
  renderVoiceChatButton();
  updateComposerControls();
}
function stopVoiceChat() {
  if (window.PLLive2DHub) window.PLLive2DHub.setVoiceState("idle");
  clearTimeout(state.voiceFlushTimer);
  state.voiceFlushTimer = null;
  const stream = state.voiceStream;
  state.voiceStream = null;
  state.voiceActive = false;
  if (stream) stream.stop();
  const input = $("question");
  if (input) input.value = "";
  resizeComposer();
  setText("audio-status");
  renderVoiceChatButton();
  updateComposerControls();
}
function submitVoiceFinal(text) {
  $("question").value = "";
  resizeComposer();
  if (isConversationBusy()) {
    state.pendingVoiceQuestion = text;
    cancelRealtimeTurn();
    setText("audio-status", "正在打断上一轮回复…");
    schedulePendingVoiceFlush();
    return;
  }
  sendQuestionText(text);
}
function schedulePendingVoiceFlush() {
  clearTimeout(state.voiceFlushTimer);
  state.voiceFlushTimer = setTimeout(() => {
    state.voiceFlushTimer = null;
    if (!state.pendingVoiceQuestion) return;
    if (isConversationBusy()) { schedulePendingVoiceFlush(); return; }
    flushPendingVoiceQuestion();
  }, 400);
}
function flushPendingVoiceQuestion() {
  const question = state.pendingVoiceQuestion;
  if (!question) return;
  state.pendingVoiceQuestion = "";
  sendQuestionText(question);
}function togglePersonaDrawer() {
  const menu = $("chat-persona-menu");
  const open = menu.classList.toggle("is-hidden");
  $("chat-persona-toggle").setAttribute("aria-expanded", String(!open));
}
function closePersonaMenu() { $("chat-persona-menu").classList.add("is-hidden"); $("chat-persona-toggle").setAttribute("aria-expanded", "false"); }
function toggleChatSettingsMenu() {
  const menu = $("chat-settings-menu");
  const button = $("chat-settings-toggle");
  if (!menu || !button) return;
  const open = menu.classList.toggle("is-hidden") === false;
  button.setAttribute("aria-expanded", String(open));
}
function closeChatSettingsMenu() {
  const menu = $("chat-settings-menu");
  const button = $("chat-settings-toggle");
  if (!menu || !button || menu.classList.contains("is-hidden")) return;
  menu.classList.add("is-hidden");
  button.setAttribute("aria-expanded", "false");
}
async function selectPersona(personaId = "") {
  stopVoiceChat();
  stopVoicePlayback();
  resetPacing();
  setText("audio-status");
  closeRealtime();
  state.activePersona = state.personas.find((item) => item.id === personaId) || null;
  if (state.activePersona) {
    const key = `yumeno:conversation:${state.activePersona.id}`;
    state.conversationId = localStorage.getItem(key) || crypto.randomUUID();
    localStorage.setItem(key, state.conversationId);
  } else state.conversationId = crypto.randomUUID();
  state.pendingAction = null; renderConfirmation(); renderPersonaList();
  if (window.PLLive2DHub) {
    window.PLLive2DHub.setPersonaModel(state.activePersona?.profile?.live2d?.model || null);
  }
  $("chat-title").textContent = state.activePersona?.name || "选择角色";
  $("send-question").disabled = !state.activePersona;
  $("chat-log").replaceChildren(empty(state.activePersona ? "开始对话" : "选择角色后开始聊天"));
  $("clear-conversation").disabled = !state.activePersona;
  closePersonaMenu();
  if (state.activePersona) { await loadConversationMessages(); connectRealtime(); }
  updateComposerControls();
}
async function submitQuestion(event) {
  event.preventDefault(); if (!state.activePersona) return;
  if ($("send-question")?.classList.contains("is-stop")) {
    cancelRealtimeTurn();
    return;
  }
  if (state.realtimeTurnId || state.realtimeExecutionPending || state.voiceActive) return;
  const question = $("question").value.trim(); if (!question) return;
  sendQuestionText(question);
}
function sendQuestionText(question) {
  stopVoicePlayback();
  resetPacing();
  state.agentRequestPending = true;
  appendMessage("user", question); resetChatProcess(); showReplyLoading(); setText("chat-error"); updateComposerControls();
  if (sendRealtime({ type: "text.submit", question })) {
    awaitRealtimeAcknowledgement(question);
    $("question-form").reset(); resizeComposer();
    return;
  }
  void streamAgentQuery(question);
  $("question-form").reset(); resizeComposer();
}

async function streamAgentQuery(question) {
  state.agentRequestPending = true;
  const controller = new AbortController();
  state.agentStreamController = controller;
  setSendButton(true);
  try {
    const response = await fetch(`/api/personas/${state.activePersona.id}/agent/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-YUMENO-Request": "web" },
      body: JSON.stringify({ question, conversation_id: state.conversationId }),
      signal: controller.signal,
    });
    if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || `HTTP ${response.status}`);
    if (!response.body) throw new Error("浏览器不支持流式响应");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop();
      for (const part of parts) {
        const line = part.split("\n").find((item) => item.startsWith("data: "));
        if (!line) continue;
        let event;
        try { event = JSON.parse(line.slice(6)); } catch { continue; }
        handleAgentStreamEvent(event);
      }
    }
  } catch (reason) {
    if (reason && reason.name !== "AbortError") setText("chat-error", reason.message || reason);
  } finally {
    state.agentStreamController = null;
    state.agentRequestPending = false;
    setSendButton(false);
    updateComposerControls();
  }
}

function handleAgentStreamEvent(event) {
  if (event.kind === "stage") {
    if (!state.pendingReplyNode) state.pendingReplyNode = showReplyLoading();
    state.pendingReplyNode.classList.remove("message-loading");
    const body = state.pendingReplyNode.querySelector("p");
    body.classList.remove("loading-bubble");
    body.textContent = "";
    setReplyStage(state.pendingReplyNode, event.stage);
  } else if (event.kind === "token") {
    if (!state.pendingReplyNode) state.pendingReplyNode = showReplyLoading();
    state.pendingReplyNode.classList.remove("message-loading");
    const body = state.pendingReplyNode.querySelector("p");
    body.classList.remove("loading-bubble");
    clearReplyStage(state.pendingReplyNode);
    appendPacedText(event.text, state.pendingReplyNode);
    feedVoiceText(event.text);
  } else if (event.kind === "result") {
    if (state.pendingReplyNode) clearReplyStage(state.pendingReplyNode);
    handleAgentResult(event.result);
  } else if (event.kind === "error") {
    setText("chat-error", event.message || event.error || "生成失败");
  }
}
function resizeComposer() {
  const input = $("question");
  input.style.height = "40px";
  const height = Math.min(input.scrollHeight, 104);
  input.style.height = `${height}px`;
  input.style.overflowY = input.scrollHeight > 104 ? "auto" : "hidden";
}
function appendMessage(type, text, createdAt) {
  if ($("chat-log").querySelector(".empty-state")) $("chat-log").replaceChildren();
  const node = document.createElement("article");
  node.className = `message message-${type}`;
  const body = document.createElement("p");
  body.textContent = text;
  node.append(body);
  const actions = document.createElement("footer");
  actions.className = "message-actions";
  const stamp = document.createElement("time");
  stamp.className = "message-time";
  stamp.dateTime = createdAt || new Date().toISOString();
  stamp.textContent = formatMessageTime(createdAt);
  actions.append(stamp);
  actions.append(messageActionButton("复制", "copy", (event) => {
    copyMessageText(text, event.currentTarget);
  }));
  if (type === "user") {
    actions.append(messageActionButton("编辑", "pencil", () => {
      const input = $("question");
      if (!input) return;
      input.value = text;
      resizeComposer();
      input.focus();
      input.scrollIntoView({ block: "center" });
    }));
  }
  node.append(actions);
  $("chat-log").append(node);
  node.scrollIntoView({ block: "nearest" });
  icons();
  return node;
}
function loadAudioSource(audio, url) {
  if (!url) return Promise.resolve();
  const key = `${Date.now()}-${Math.random()}`;
  audio.dataset.srcKey = key;
  return fetch(url)
    .then((response) => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.blob(); })
    .then((blob) => {
      if (audio.dataset.srcKey !== key) return;
      if (audio.srcObjectUrl) URL.revokeObjectURL(audio.srcObjectUrl);
      audio.srcObjectUrl = URL.createObjectURL(blob);
      audio.src = audio.srcObjectUrl;
    })
    .catch(() => { if (audio.dataset.srcKey === key) audio.src = url; });
}
function playAudioRobust(audio) {
  const playOnce = () => audio.play().catch((error) => {
    if (error && error.name === "NotAllowedError") {
      audio.muted = true;
      return audio.play().then(() => { audio.muted = false; }).catch(() => {});
    }
    throw error;
  });
  if (audio.readyState >= 1) return playOnce();
  return new Promise((resolve) => {
    audio.addEventListener("loadedmetadata", () => resolve(playOnce()), { once: true });
    audio.addEventListener("error", () => resolve(Promise.reject(new Error("audio load failed"))), { once: true });
  });
}
function appendVoiceControl(node, audio) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "voice-play-button";
  button.title = "播放语音";
  button.setAttribute("aria-label", "播放语音");
  const icon = document.createElement("i");
  icon.dataset.lucide = "volume-2";
  button.append(icon);
  button.addEventListener("click", () => {
    if (audio.paused) playAudioRobust(audio).catch(() => {});
    else audio.pause();
  });
  audio.addEventListener("play", () => button.classList.add("is-playing"));
  audio.addEventListener("pause", () => button.classList.remove("is-playing"));
  audio.addEventListener("ended", () => button.classList.remove("is-playing"));
  audio.addEventListener("error", () => {
    button.classList.add("is-failed");
    button.title = "语音加载失败，点击重试";
    if (state.voicePlayingAudio === audio) {
      state.voicePlayingAudio = null;
      state.voicePlaybackActive = false;
      playNextVoiceAudio();
    }
  });
  node.append(button, audio);
  icons();
  return button;
}
function appendAudioMessage(message) {
  if ($("chat-log").querySelector(".empty-state")) $("chat-log").replaceChildren();
  const node = document.createElement("article");
  node.className = `message message-${message.role} message-audio`; node.dataset.messageId = message.id;
  const audio = document.createElement("audio"); audio.controls = false; audio.preload = "metadata"; audio.className = "voice-audio-source"; loadAudioSource(audio, message.audio_url);
  audio.dataset.lipText = message.content || "";
  if (message.role === "assistant") {
    const body = document.createElement("p"); body.textContent = message.content; const status = document.createElement("span"); status.className = "voice-bubble-status"; status.textContent = "语音回复"; audio.controls = false; audio.className = "voice-audio-source"; node.append(body, status); appendVoiceControl(node, audio);
    $("chat-log").append(node); node.scrollIntoView({ block: "nearest" }); return node;
  }
  const voiceLabel = document.createElement("span"); voiceLabel.className = "voice-bubble-label"; voiceLabel.textContent = "语音消息";
  node.append(voiceLabel); appendVoiceControl(node, audio);
  const transcript = document.createElement("details"); transcript.className = "voice-transcript";
  const summary = document.createElement("summary"); summary.textContent = message.status === "failed" ? "识别失败" : "查看转写";
  const text = document.createElement("p"); text.textContent = message.transcript || (message.status === "failed" ? message.error_message : "正在识别…");
  transcript.append(summary, text); node.append(audio, transcript);
  if (message.status === "failed") {
    const retry = document.createElement("button"); retry.type = "button"; retry.className = "voice-retry"; retry.textContent = "重试";
    retry.addEventListener("click", () => retryVoiceMessage(message.id)); node.append(retry);
  }
  $("chat-log").append(node); node.scrollIntoView({ block: "nearest" }); return node;
}
function updateAudioMessage(message) {
  const current = $("chat-log").querySelector(`[data-message-id="${message.id}"]`);
  if (current) current.remove();
  appendAudioMessage(message);
}
async function retryVoiceMessage(messageId) {
  try {
    const result = await api(fetch(`/api/voice-messages/${messageId}/transcribe`, { method: "POST", headers: { "X-YUMENO-Request": "web" } }));
    updateAudioMessage(result.message); handleAgentResult(result.turn);
  } catch (reason) { setText("chat-error", reason); await loadConversationMessages(); }
}
async function loadConversationMessages() {
  if (!state.activePersona) return;
  try {
    const messages = await api(fetch(`/api/personas/${state.activePersona.id}/conversations/${state.conversationId}/messages`));
    $("chat-log").replaceChildren();
    if (!messages.length) return $("chat-log").append(empty("开始对话"));
    for (const message of messages) message.kind === "audio" ? appendAudioMessage(message) : appendMessage(message.role, message.content, message.created_at);
  } catch (reason) { setText("chat-error", reason); }
}
async function clearConversation() {
  if (!state.activePersona || !confirm("永久删除当前对话、转写和音频？")) return;
  try {
    await api(fetch(`/api/personas/${state.activePersona.id}/conversations/${state.conversationId}`, { method: "DELETE", headers: { "X-YUMENO-Request": "web" } }));
    closeRealtime();
    state.conversationId = crypto.randomUUID();
    localStorage.setItem(`yumeno:conversation:${state.activePersona.id}`, state.conversationId);
    $("chat-log").replaceChildren(empty("开始对话")); connectRealtime();
  } catch (reason) { setText("chat-error", reason); }
}
function handleAgentResult(result) {
  if (!$("chat-log")) return;
  state.pendingAction = result.status === "pending_confirmation" ? { action: result.pending_action, specialist: result.specialist } : null;
  renderConfirmation(); $("send-question").disabled = Boolean(state.pendingAction) || !state.activePersona;
  if (result.answer) {
    const node = replaceReplyLoading(state.pendingReplyNode, result.answer);
    appendResultDetails(node, result);
    synthesizeAnswer(result.answer, node);
  } else if (state.pendingReplyNode) { state.pendingReplyNode.remove(); state.pendingReplyNode = null; }
}
function appendAnswer(result) { const node = replaceReplyLoading(state.pendingReplyNode, ""); appendPacedText(result.answer, node); synthesizeAnswer(result.answer, node); }
const VOICE_FEED_MAX_CHARS = 300;
const VOICE_FEED_FORCE_CHARS = 60;
const VOICE_SENTENCE_MARKS = "。！？!?；;\n";

function startVoiceFeed() {
  const voice = state.activePersona?.profile?.tts;
  const node = state.realtimeAnswerNode;
  if (!state.ttsConfigured || !voice?.enabled || !$("assistant-voice-toggle").checked || !node) return;
  if (state.voiceFeed) return;
  if (typeof WebSocket === "undefined") { state.voiceFeedFailed = true; return; }
  if (window.PL && window.PL.unlockAudio) window.PL.unlockAudio();
  const autoPlay = voice.auto_play !== false;
  const epoch = state.voicePlaybackEpoch;
  const status = document.createElement("span");
  status.className = "voice-bubble-status is-generating";
  status.textContent = "正在生成语音…";
  node.append(status);
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const url = `${scheme}://${location.host}/api/tts/personas/${state.activePersona.id}/conversations/${state.conversationId}/synthesize/ws`;
  const ws = new WebSocket(url);
  const feed = {
    ws, node, epoch, autoPlay, status,
    sentChars: 0, buffer: "", closed: false, segments: 0, opened: false, finished: false,
    pendingSends: [],
  };
  state.voiceFeed = feed;
  state.voiceFeedFailed = false;
  ws.addEventListener("open", () => {
    feed.opened = true;
    const pending = feed.pendingSends;
    feed.pendingSends = [];
    for (const payload of pending) {
      if (feed.ws.readyState === WebSocket.OPEN) feed.ws.send(payload);
    }
  });
  ws.addEventListener("message", (message) => {
    let event;
    try { event = JSON.parse(message.data); } catch { return; }
    if (event.type === "segment") {
      feed.segments += 1;
      if (!autoPlay) return;
      const blobUrl = URL.createObjectURL(b64ToWavBlob(event.audio));
      const audio = document.createElement("audio");
      audio.preload = "auto";
      audio.src = blobUrl;
      audio.dataset.lipText = event.text || "";
      voiceQueueHost().append(audio);
      const cleanup = () => { URL.revokeObjectURL(blobUrl); audio.remove(); };
      audio.addEventListener("ended", cleanup, { once: true });
      audio.addEventListener("error", cleanup, { once: true });
      enqueueVoiceAudio(audio);
    } else if (event.type === "done") {
      feed.finished = true;
      if (epoch !== state.voicePlaybackEpoch) { status.remove(); return; }
      status.textContent = "语音已生成";
      status.classList.remove("is-generating");
      const audio = document.createElement("audio");
      audio.controls = false;
      audio.preload = "metadata";
      audio.className = "voice-audio-source";
      audio.loaded = loadAudioSource(audio, event.message.audio_url);
      appendVoiceControl(node, audio);
    } else if (event.type === "error") {
      if (!feed.opened) state.voiceFeedFailed = true;
      status.textContent = "语音生成失败";
      status.classList.remove("is-generating");
      setText("chat-error", `文字回复正常，语音生成失败：${event.message || "语音合成失败"}`);
    }
  });
  ws.addEventListener("close", () => {
    if (state.voiceFeed === feed) state.voiceFeed = null;
    if (!feed.finished && !feed.errored) status.remove();
  });
  ws.addEventListener("error", () => {
    feed.errored = true;
    if (!feed.opened) state.voiceFeedFailed = true;
    status.textContent = "语音生成失败";
    status.classList.remove("is-generating");
    setText("chat-error", "文字回复正常，语音连接失败");
  });
}

function feedVoiceText(text) {
  if (!text) return;
  if (state.voiceFeedFailed) return;
  if (state.voiceFeedFullText.length < VOICE_FEED_MAX_CHARS * 4) state.voiceFeedFullText += text;
  if (!state.voiceFeed) startVoiceFeed();
  const feed = state.voiceFeed;
  if (!feed || feed.closed) return;
  feed.buffer += text;
  let start = 0;
  for (let i = 0; i < feed.buffer.length; i += 1) {
    if (VOICE_SENTENCE_MARKS.includes(feed.buffer[i])) {
      const sentence = feed.buffer.slice(start, i + 1).trim();
      if (sentence) writeVoiceSentence(feed, sentence);
      start = i + 1;
    }
  }
  feed.buffer = feed.buffer.slice(start);
  if (feed.buffer.length >= VOICE_FEED_FORCE_CHARS) {
    const forced = feed.buffer.trim();
    feed.buffer = "";
    if (forced) writeVoiceSentence(feed, forced);
  }
}

function writeVoiceSentence(feed, sentence) {
  if (feed.closed || !sentence) return;
  const room = VOICE_FEED_MAX_CHARS - feed.sentChars;
  if (room <= 0) { finishVoiceFeed(); return; }
  const part = sentence.length > room ? sentence.slice(0, room) : sentence;
  if (!part) { finishVoiceFeed(); return; }
  feed.sentChars += part.length;
  const payload = JSON.stringify({ type: "text", text: part });
  if (feed.ws.readyState === WebSocket.OPEN) feed.ws.send(payload);
  else feed.pendingSends.push(payload);
  if (part.length < sentence.length) finishVoiceFeed();
}

function finishVoiceFeed() {
  const feed = state.voiceFeed;
  if (!feed || feed.closed) return;
  feed.closed = true;
  state.voiceFeed = null;
  const rest = feed.buffer.trim();
  feed.buffer = "";
  if (rest) {
    const room = VOICE_FEED_MAX_CHARS - feed.sentChars;
    if (room > 0) {
      const part = rest.length > room ? rest.slice(0, room) : rest;
      feed.sentChars += part.length;
      const payload = JSON.stringify({ type: "text", text: part });
      if (feed.ws.readyState === WebSocket.OPEN) feed.ws.send(payload);
      else feed.pendingSends.push(payload);
    }
  }
  const donePayload = JSON.stringify({ type: "done" });
  if (feed.ws.readyState === WebSocket.OPEN) feed.ws.send(donePayload);
  else feed.pendingSends.push(donePayload);
}
function enqueueVoiceAudio(audio) {
  state.voicePlaybackQueue.push(audio);
  playNextVoiceAudio();
}
function b64ToWavBlob(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: "audio/wav" });
}
function voiceQueueHost() {
  let host = document.getElementById("voice-queue-host");
  if (!host) {
    host = document.createElement("div");
    host.id = "voice-queue-host";
    host.hidden = true;
    document.body.append(host);
  }
  return host;
}
function abortVoiceStream() {
  const feed = state.voiceFeed;
  if (feed) {
    state.voiceFeed = null;
    try { feed.ws.close(); } catch (e) { /* ignore */ }
  }
  if (state.voiceStreamAbort) {
    try { state.voiceStreamAbort.abort(); } catch (e) { /* ignore */ }
    state.voiceStreamAbort = null;
  }
}
function playNextVoiceAudio() {
  if (state.voicePlaybackActive || !state.voicePlaybackQueue.length) return;
  state.voicePlaybackActive = true;
  const audio = state.voicePlaybackQueue.shift();
  if (!audio || !audio.src) {
    state.voicePlaybackActive = false;
    playNextVoiceAudio();
    return;
  }
  state.voicePlayingAudio = audio;
  const advance = () => {
    if (state.voicePlayingAudio === audio) {
      state.voicePlayingAudio = null;
      state.voicePlaybackActive = false;
      playNextVoiceAudio();
    }
  };
  const watchdog = setTimeout(advance, 30000);
  const advanceClean = () => { clearTimeout(watchdog); advance(); };
  audio.addEventListener("ended", advanceClean, { once: true });
  audio.addEventListener("error", advanceClean, { once: true });
  (audio.loaded || Promise.resolve()).then(() => playAudioRobust(audio)).catch(advanceClean);
}
async function synthesizeAnswer(text, node, options = {}) {
  const voice = state.activePersona?.profile?.tts;
  if (!state.ttsConfigured || !voice?.enabled || !$("assistant-voice-toggle").checked || !text) return;
  if (window.PL && window.PL.unlockAudio) window.PL.unlockAudio();
  const epoch = state.voicePlaybackEpoch;
  const autoPlay = voice.auto_play !== false;
  const status = document.createElement("span");
  status.className = "voice-bubble-status is-generating";
  status.textContent = "正在生成语音…";
  node.append(status);
  const controller = new AbortController();
  state.voiceStreamAbort = controller;
  let segments = 0;
  try {
    const response = await fetch(`/api/tts/personas/${state.activePersona.id}/conversations/${state.conversationId}/synthesize/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-YUMENO-Request": "web" },
      body: JSON.stringify({ text }),
      signal: controller.signal,
    });
    if (!response.ok || !response.body) throw new Error(`语音流式合成失败（HTTP ${response.status}）`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, nl).trim();
        buffer = buffer.slice(nl + 1);
        if (!line) continue;
        let event;
        try { event = JSON.parse(line); } catch { continue; }
        if (event.type === "segment") {
          segments += 1;
          if (!autoPlay) continue;
          const url = URL.createObjectURL(b64ToWavBlob(event.audio));
          const audio = document.createElement("audio");
          audio.preload = "auto";
          audio.src = url;
          audio.dataset.lipText = event.text || "";
          // 必须挂进 DOM：Live2D 口型依赖 document 级 play 事件，脱离 DOM 的元素事件不会冒泡
          voiceQueueHost().append(audio);
          const cleanup = () => { URL.revokeObjectURL(url); audio.remove(); };
          audio.addEventListener("ended", cleanup, { once: true });
          audio.addEventListener("error", cleanup, { once: true });
          enqueueVoiceAudio(audio);
        } else if (event.type === "done") {
          if (epoch !== state.voicePlaybackEpoch) { status.remove(); return; }
          status.textContent = "语音已生成";
          status.classList.remove("is-generating");
          const audio = document.createElement("audio");
          audio.controls = false;
          audio.preload = "metadata";
          audio.className = "voice-audio-source";
          audio.loaded = loadAudioSource(audio, event.message.audio_url);
          appendVoiceControl(node, audio);
        } else if (event.type === "error") {
          throw new Error(event.message || "语音合成失败");
        }
      }
    }
    if (epoch !== state.voicePlaybackEpoch) { status.remove(); return; }
    if (!segments && !node.querySelector(".voice-audio-source")) status.remove();
  } catch (reason) {
    if (reason && reason.name === "AbortError") { status.remove(); return; }
    status.textContent = "语音生成失败";
    status.classList.remove("is-generating");
    setText("chat-error", `文字回复正常，语音生成失败：${reason && reason.message ? reason.message : reason}`);
  } finally {
    if (state.voiceStreamAbort === controller) state.voiceStreamAbort = null;
  }
}
function appendResultDetails(node, result) { if (result.evidence?.length) node.append(details("引用", result.evidence)); }
function renderConfirmation() {
  const panel = $("confirmation-panel");
  if (!panel) return;
  panel.classList.toggle("is-hidden", !state.pendingAction); if (!state.pendingAction) return;
  const action = state.pendingAction.action || {}; $("confirmation-title").textContent = action.title || "确认操作"; $("confirmation-detail").textContent = `${action.target || "当前角色"} · ${JSON.stringify(action.arguments || {})}`;
}
async function resumeAgent(approved) {
  if (!state.pendingAction || !state.activePersona) return;
  $("confirm-action").disabled = true; $("cancel-action").disabled = true;
  if (sendRealtime({ type: "confirmation.respond", specialist: state.pendingAction.specialist, approved })) {
    awaitRealtimeAcknowledgement("");
    return;
  }
  try { const result = await api(fetch(`/api/personas/${state.activePersona.id}/agent/resume`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ conversation_id: state.conversationId, specialist: state.pendingAction.specialist, approved }) })); handleAgentResult(result); }
  catch (reason) { setText("chat-error", reason); }
  finally { $("confirm-action").disabled = false; $("cancel-action").disabled = false; }
}
