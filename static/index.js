"use strict";

const state = {
  accessToken: null,
  sessionId: null,
  displayName: "",
  username: "",
  departmentId: "",
  role: "",
  threads: [],
  historyRequestSequence: 0,
  isLoading: false,
  awaitingConfirmation: false,
};

const elements = {
  loginView: document.querySelector("#login-view"), chatView: document.querySelector("#chat-view"),
  loginForm: document.querySelector("#login-form"), username: document.querySelector("#username"),
  password: document.querySelector("#password"),
  loginButton: document.querySelector("#login-button"), loginError: document.querySelector("#login-error"),
  displayName: document.querySelector("#display-name"), userUsername: document.querySelector("#user-username"),
  userDepartment: document.querySelector("#user-department"), userRole: document.querySelector("#user-role"),
  newThreadButton: document.querySelector("#new-thread-button"), threadList: document.querySelector("#thread-list"),
  threadEmpty: document.querySelector("#thread-empty"), threadError: document.querySelector("#thread-error"),
  conversationTitle: document.querySelector("#conversation-title"),
  logoutButton: document.querySelector("#logout-button"), messageList: document.querySelector("#message-list"),
  chatForm: document.querySelector("#chat-form"), messageInput: document.querySelector("#message-input"),
  sendButton: document.querySelector("#send-button"), chatError: document.querySelector("#chat-error"),
};

const ACCESS_TOKEN_STORAGE_KEY = "access_token";
const welcomeCardSource = elements.messageList.firstElementChild;
const welcomeCard = welcomeCardSource ? welcomeCardSource.cloneNode(true) : document.createElement("div");
let tracePanelSequence = 0;

async function requestJson(url, options) {
  const response = await fetch(url, options);
  let payload = null;
  try { payload = await response.json(); } catch { /* 使用统一错误信息。 */ }
  if (!response.ok) {
    const error = new Error(payload?.detail || `请求失败（${response.status}）`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

async function requestEventStream(url, options, onEvent) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let payload = null;
    try { payload = await response.json(); } catch { /* 使用统一错误信息。 */ }
    const error = new Error(payload?.detail || `请求失败（${response.status}）`);
    error.status = response.status;
    throw error;
  }
  if (!response.body) throw new Error("当前浏览器不支持流式响应");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  function consumeEvent(rawEvent) {
    let eventName = "message";
    const dataLines = [];
    rawEvent.split("\n").forEach((line) => {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    });
    if (dataLines.length === 0) return;

    const rawData = dataLines.join("\n");
    let data = rawData;
    try { data = JSON.parse(rawData); } catch { /* 允许服务端发送纯文本 data。 */ }
    onEvent(eventName, data);
  }

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";
    events.forEach(consumeEvent);
    if (done) break;
  }
  if (buffer.trim()) consumeEvent(buffer);
}

function setLoginLoading(loading) {
  elements.loginButton.disabled = loading;
  elements.username.disabled = loading;
  elements.password.disabled = loading;
  elements.loginButton.textContent = loading ? "登录中…" : "登录";
}

function setChatLoading(loading) {
  state.isLoading = loading;
  const composerDisabled = loading || state.awaitingConfirmation || !state.sessionId;
  elements.messageInput.disabled = composerDisabled;
  elements.sendButton.disabled = composerDisabled;
}

function setAwaitingConfirmation(awaiting) {
  state.awaitingConfirmation = awaiting;
  setChatLoading(state.isLoading);
}

function showChatView() {
  elements.displayName.textContent = state.displayName;
  elements.userUsername.textContent = state.username;
  elements.userDepartment.textContent = state.departmentId;
  elements.userRole.textContent = state.role;
  elements.loginView.hidden = true;
  elements.chatView.hidden = false;
  elements.messageInput.focus();
}

function showLoginView() {
  elements.chatView.hidden = true;
  elements.loginView.hidden = false;
  elements.username.focus();
}

async function loadCurrentUser(token) {
  const user = await requestJson("/api/auth/me", {
    headers: { "Authorization": `Bearer ${token}` },
  });
  state.accessToken = token;
  state.displayName = user.display_name;
  state.username = user.username;
  state.departmentId = user.department_id;
  state.role = user.role;
  showChatView();
  setChatLoading(false);
}

function authorizedHeaders(headers = {}) {
  return { ...headers, "Authorization": `Bearer ${state.accessToken}` };
}

function formatThreadTime(createdAt) {
  const date = new Date(createdAt);
  if (Number.isNaN(date.getTime())) return "创建时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(date);
}

function threadDisplayName(thread) {
  return `会话 ${thread.thread_id.slice(0, 8)}`;
}

function renderThreadList() {
  elements.threadList.replaceChildren();
  elements.threadEmpty.hidden = state.threads.length > 0;

  state.threads.forEach((thread) => {
    const button = document.createElement("button");
    const title = document.createElement("span");
    const time = document.createElement("span");
    button.type = "button";
    button.className = "thread-item";
    button.dataset.threadId = thread.thread_id;
    button.classList.toggle("active", thread.thread_id === state.sessionId);
    button.setAttribute("aria-pressed", String(thread.thread_id === state.sessionId));
    title.className = "thread-item-title";
    title.textContent = threadDisplayName(thread);
    time.className = "thread-item-time";
    time.textContent = formatThreadTime(thread.created_at);
    button.append(title, time);
    button.addEventListener("click", () => selectThread(thread.thread_id));
    elements.threadList.appendChild(button);
  });
}

function renderThreadHistory(history) {
  elements.messageList.replaceChildren();
  elements.chatError.textContent = "";
  setAwaitingConfirmation(false);

  const messages = Array.isArray(history?.messages) ? history.messages : [];
  if (messages.length === 0) {
    elements.messageList.appendChild(welcomeCard.cloneNode(true));
  } else {
    messages.forEach((message) => {
      if (message.role === "user" || message.role === "assistant") {
        addMessage(message.role, message.content || "");
      }
    });
  }

  if (history?.needs_confirmation) {
    addConfirmationCard(history.confirmation);
  }
}

async function selectThread(threadId) {
  const thread = state.threads.find((item) => item.thread_id === threadId);
  if (!thread) return;

  const requestSequence = ++state.historyRequestSequence;
  state.sessionId = thread.thread_id;
  elements.conversationTitle.textContent = threadDisplayName(thread);
  resetChatHistory(false);
  renderThreadList();
  setAwaitingConfirmation(false);
  setChatLoading(true);
  const loadingMessage = addLoadingMessage();

  try {
    const history = await requestJson(
      `/api/threads/${encodeURIComponent(thread.thread_id)}/messages`,
      { headers: authorizedHeaders() },
    );
    if (requestSequence !== state.historyRequestSequence) return;
    renderThreadHistory(history);
  } catch (error) {
    if (requestSequence !== state.historyRequestSequence) return;
    loadingMessage.remove();
    if (error.status === 401) {
      logout();
      elements.loginError.textContent = "登录状态已失效，请重新登录";
      return;
    }
    elements.chatError.textContent = error.message || "聊天记录加载失败，请重试";
  } finally {
    if (requestSequence === state.historyRequestSequence) {
      loadingMessage.remove();
      setChatLoading(false);
    }
  }
}

async function loadThreads() {
  elements.threadError.textContent = "";
  elements.newThreadButton.disabled = true;
  try {
    const threads = await requestJson("/api/threads", {
      headers: authorizedHeaders(),
    });
    state.threads = Array.isArray(threads) ? threads : [];
    renderThreadList();
    if (state.threads.length > 0) {
      await selectThread(state.threads[0].thread_id);
    } else {
      state.historyRequestSequence += 1;
      state.sessionId = null;
      elements.conversationTitle.textContent = "请选择一个会话";
      resetChatHistory();
      setChatLoading(false);
    }
  } catch (error) {
    if (error.status === 401) throw error;
    state.threads = [];
    state.sessionId = null;
    renderThreadList();
    setChatLoading(false);
    elements.threadError.textContent = error.message || "会话列表加载失败";
  } finally {
    elements.newThreadButton.disabled = false;
  }
}

async function createThread() {
  if (!state.accessToken || elements.newThreadButton.disabled) return;
  elements.threadError.textContent = "";
  elements.newThreadButton.disabled = true;
  elements.newThreadButton.textContent = "创建中…";
  try {
    const thread = await requestJson("/api/threads", {
      method: "POST",
      headers: authorizedHeaders(),
    });
    state.threads = [thread, ...state.threads.filter((item) => item.thread_id !== thread.thread_id)];
    renderThreadList();
    await selectThread(thread.thread_id);
  } catch (error) {
    if (error.status === 401) {
      logout();
      elements.loginError.textContent = "登录状态已失效，请重新登录";
      return;
    }
    elements.threadError.textContent = error.message || "新会话创建失败，请重试";
  } finally {
    elements.newThreadButton.disabled = false;
    elements.newThreadButton.textContent = "＋ 新建会话";
  }
}

function resetChatHistory(showWelcome = true) {
  const children = showWelcome ? [welcomeCard.cloneNode(true)] : [];
  elements.messageList.replaceChildren(...children);
  elements.chatError.textContent = "";
}

function logout() {
  localStorage.removeItem(ACCESS_TOKEN_STORAGE_KEY);
  state.accessToken = null;
  state.sessionId = null;
  state.displayName = "";
  state.username = "";
  state.departmentId = "";
  state.role = "";
  state.threads = [];
  state.historyRequestSequence += 1;
  state.isLoading = false;
  state.awaitingConfirmation = false;
  resetChatHistory();
  elements.chatForm.reset();
  elements.loginForm.reset();
  elements.threadList.replaceChildren();
  elements.threadError.textContent = "";
  elements.conversationTitle.textContent = "请选择一个会话";
  showLoginView();
}

function formatTraceValue(value) {
  if (typeof value === "string") {
    try { return JSON.stringify(JSON.parse(value), null, 2); } catch { return value; }
  }
  return JSON.stringify(value, null, 2);
}

function addTracePanel(container, trace) {
  if (!Array.isArray(trace) || trace.length === 0) return;

  const toggle = document.createElement("button");
  const panel = document.createElement("div");
  const list = document.createElement("ol");
  tracePanelSequence += 1;
  const panelId = `trace-panel-${tracePanelSequence}`;

  toggle.type = "button";
  toggle.className = "trace-toggle";
  toggle.textContent = `查看调用轨迹（${trace.length} 步）`;
  toggle.setAttribute("aria-expanded", "false");
  toggle.setAttribute("aria-controls", panelId);
  panel.id = panelId;
  panel.className = "trace-panel";
  panel.hidden = true;
  list.className = "trace-list";

  trace.forEach((step) => {
    const item = document.createElement("li");
    const title = document.createElement("strong");
    const content = document.createElement("pre");

    if (step.type === "agent_tool_calls") {
      const calls = Array.isArray(step.calls) ? step.calls : [];
      const names = calls.map((call) => call.name).filter(Boolean).join("、");
      title.textContent = `调用专业 Agent：${names || "未知"}`;
      content.textContent = calls.length
        ? formatTraceValue(calls.map((call) => ({ name: call.name, args: call.args })))
        : "无调用参数";
    } else if (step.type === "agent_tool_result") {
      title.textContent = `专业 Agent 返回：${step.name || "未知"}`;
      content.textContent = formatTraceValue(step.content ?? "无返回内容");
    } else {
      title.textContent = "轨迹步骤";
      content.textContent = formatTraceValue(step);
    }

    item.append(title, content);
    list.appendChild(item);
  });

  panel.appendChild(list);
  container.append(toggle, panel);
  toggle.addEventListener("click", () => {
    const expanded = toggle.getAttribute("aria-expanded") === "true";
    toggle.setAttribute("aria-expanded", String(!expanded));
    toggle.textContent = expanded
      ? `查看调用轨迹（${trace.length} 步）`
      : `收起调用轨迹（${trace.length} 步）`;
    panel.hidden = expanded;
    if (!expanded) elements.messageList.scrollTop = elements.messageList.scrollHeight;
  });
}

function addMessage(role, text, extraClass = "", trace = []) {
  const row = document.createElement("div");
  const content = document.createElement("div");
  const bubble = document.createElement("div");
  row.className = `message-row ${role}`;
  content.className = "message-content";
  bubble.className = `message-bubble ${extraClass}`.trim();
  bubble.textContent = text;
  content.appendChild(bubble);
  addTracePanel(content, trace);
  row.appendChild(content);
  elements.messageList.appendChild(row);
  elements.messageList.scrollTop = elements.messageList.scrollHeight;
  return row;
}

function addLoadingMessage() {
  const row = document.createElement("div");
  const bubble = document.createElement("div");
  row.className = "message-row assistant";
  bubble.className = "message-bubble pending";
  bubble.setAttribute("aria-label", "助手正在思考");
  bubble.innerHTML = '<span class="typing-dot">●</span> <span class="typing-dot">●</span> <span class="typing-dot">●</span>';
  row.appendChild(bubble);
  elements.messageList.appendChild(row);
  elements.messageList.scrollTop = elements.messageList.scrollHeight;
  return row;
}

function addStreamingMessage() {
  const row = addMessage("assistant", "正在思考…", "pending");
  const content = row.querySelector(".message-content");
  const bubble = row.querySelector(".message-bubble");
  const progress = document.createElement("div");
  const progressList = document.createElement("ol");
  const slowHint = document.createElement("p");
  const slowHintTimers = [];
  let hasContent = false;

  progress.className = "stream-progress";
  progress.setAttribute("aria-live", "polite");
  progressList.className = "stream-progress-list";
  slowHint.className = "stream-hint";
  slowHint.hidden = true;
  progress.append(progressList, slowHint);
  content.prepend(progress);

  // 复杂问题要跑多轮检索和多次模型调用，等待较久时给出提示，避免看着像卡死。
  function scheduleSlowHint(delayMs, text) {
    slowHintTimers.push(setTimeout(() => {
      if (hasContent) return;
      slowHint.textContent = text;
      slowHint.hidden = false;
      elements.messageList.scrollTop = elements.messageList.scrollHeight;
    }, delayMs));
  }

  function clearSlowHints() {
    slowHintTimers.forEach((timer) => clearTimeout(timer));
    slowHintTimers.length = 0;
    slowHint.hidden = true;
  }

  scheduleSlowHint(30000, "问题比较复杂，Agent 正在进行多轮检索，请再等等…");
  scheduleSlowHint(90000, "仍在处理中，这类问题通常需要 1～2 分钟，请不要关闭页面。");

  function addProgressItem(text, key = "") {
    const item = document.createElement("li");
    item.className = "stream-progress-item running";
    item.dataset.key = key;
    item.textContent = text;
    progressList.querySelectorAll(".running").forEach((runningItem) => {
      runningItem.classList.replace("running", "completed");
    });
    item.classList.add("running");
    progressList.appendChild(item);
    elements.messageList.scrollTop = elements.messageList.scrollHeight;
  }

  return {
    row,
    status(message) {
      addProgressItem(message || "Agent 正在处理");
    },
    toolStart(tool) {
      const label = tool?.label || tool?.name || "工具";
      addProgressItem(`正在调用 ${label}`, tool?.name || label);
    },
    toolEnd(tool) {
      const key = tool?.name || tool?.label || "";
      const candidates = [...progressList.querySelectorAll(".stream-progress-item")];
      const item = candidates.reverse().find((candidate) => (
        candidate.classList.contains("running")
        && (!key || candidate.dataset.key === key)
      ));
      if (item) {
        item.classList.replace("running", "completed");
        item.textContent = `${tool?.label || tool?.name || "工具"} 调用完成`;
      } else {
        addProgressItem(`${tool?.label || tool?.name || "工具"} 调用完成`, key);
        progressList.lastElementChild.classList.replace("running", "completed");
      }
    },
    append(text) {
      if (!hasContent) bubble.textContent = "";
      hasContent = true;
      clearSlowHints();
      bubble.classList.remove("pending");
      bubble.textContent += text;
      elements.messageList.scrollTop = elements.messageList.scrollHeight;
    },
    finish(showFallback = true) {
      clearSlowHints();
      progressList.querySelectorAll(".running").forEach((item) => {
        item.classList.replace("running", "completed");
      });
      bubble.classList.remove("pending");
      if (!hasContent && showFallback) {
        bubble.textContent = "助手没有返回可展示的回答";
      } else if (!hasContent) {
        bubble.remove();
      }
    },
  };
}

function getConfirmationMessage(confirmation) {
  if (typeof confirmation === "string") return confirmation;
  if (confirmation?.message) return String(confirmation.message);
  if (confirmation?.description) return String(confirmation.description);
  if (confirmation && typeof confirmation === "object") {
    return JSON.stringify(confirmation, null, 2);
  }
  return "该操作会修改业务数据，是否继续？";
}

function addConfirmationCard(confirmation, trace = []) {
  setAwaitingConfirmation(true);

  const row = document.createElement("div");
  const content = document.createElement("div");
  const card = document.createElement("div");
  const title = document.createElement("strong");
  const message = document.createElement("p");
  const actions = document.createElement("div");
  const rejectButton = document.createElement("button");
  const approveButton = document.createElement("button");

  row.className = "message-row assistant";
  content.className = "message-content";
  card.className = "confirmation-card";
  title.textContent = "需要你的确认";
  message.textContent = getConfirmationMessage(confirmation);
  actions.className = "confirmation-actions";
  rejectButton.type = "button";
  rejectButton.className = "confirmation-button reject";
  rejectButton.textContent = "拒绝";
  approveButton.type = "button";
  approveButton.className = "confirmation-button approve";
  approveButton.textContent = "确认执行";

  actions.append(rejectButton, approveButton);
  card.append(title, message, actions);
  content.appendChild(card);
  addTracePanel(content, trace);
  row.appendChild(content);
  elements.messageList.appendChild(row);
  elements.messageList.scrollTop = elements.messageList.scrollHeight;

  rejectButton.addEventListener("click", () => submitConfirmation(false, row));
  approveButton.addEventListener("click", () => submitConfirmation(true, row));
}

function handleAgentResult(result) {
  if (result.needs_confirmation) {
    addConfirmationCard(result.confirmation, result.trace);
    return;
  }
  setAwaitingConfirmation(false);
  addMessage("assistant", result.answer || "助手没有返回可展示的回答", "", result.trace);
}

async function submitConfirmation(approved, confirmationRow) {
  if (state.isLoading) return;

  elements.chatError.textContent = "";
  setChatLoading(true);
  const buttons = confirmationRow.querySelectorAll("button");
  buttons.forEach((button) => { button.disabled = true; });
  const loadingMessage = addLoadingMessage();

  try {
    const threadId = encodeURIComponent(state.sessionId);
    const result = await requestJson(`/api/${threadId}/confirm`, {
      method: "POST",
      headers: authorizedHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ approved }),
    });
    loadingMessage.remove();
    confirmationRow.querySelector(".confirmation-actions").replaceChildren(
      Object.assign(document.createElement("span"), {
        className: `confirmation-result ${approved ? "approved" : "rejected"}`,
        textContent: approved ? "已确认执行" : "已拒绝执行",
      }),
    );
    setAwaitingConfirmation(false);
    handleAgentResult(result);
  } catch (error) {
    loadingMessage.remove();
    if (error.status === 401) {
      logout();
      elements.loginError.textContent = "会话已失效，请重新登录";
      return;
    }
    elements.chatError.textContent = error.message || "确认操作提交失败，请重试";
    buttons.forEach((button) => { button.disabled = false; });
  } finally {
    setChatLoading(false);
  }
}

function resizeMessageInput() {
  elements.messageInput.style.height = "auto";
  elements.messageInput.style.height = `${Math.min(elements.messageInput.scrollHeight, 140)}px`;
}

elements.loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const username = elements.username.value.trim();
  const password = elements.password.value;
  elements.loginError.textContent = "";
  if (!username) {
    elements.loginError.textContent = "请输入用户名";
    elements.username.focus();
    return;
  }
  if (!password) {
    elements.loginError.textContent = "请输入密码";
    elements.password.focus();
    return;
  }
  setLoginLoading(true);
  try {
    const result = await requestJson("/api/auth/token", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!result?.access_token) throw new Error("登录接口未返回 token");
    localStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, result.access_token);
    await loadCurrentUser(result.access_token);
    await loadThreads();
  } catch (error) {
    localStorage.removeItem(ACCESS_TOKEN_STORAGE_KEY);
    state.accessToken = null;
    showLoginView();
    elements.loginError.textContent = error.message || "登录失败，请稍后重试";
  } finally {
    setLoginLoading(false);
  }
});

elements.chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = elements.messageInput.value.trim();
  elements.chatError.textContent = "";
  if (!message || state.isLoading) return;

  addMessage("user", message);
  elements.messageInput.value = "";
  resizeMessageInput();
  setChatLoading(true);
  const streamingMessage = addStreamingMessage();
  let needsConfirmation = false;
  try {
    const threadId = encodeURIComponent(state.sessionId);
    await requestEventStream(`/api/v2/chat/${threadId}/stream`, {
      method: "POST",
      headers: authorizedHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ message }),
    }, (eventName, data) => {
      if (eventName === "text_delta" && data?.text) {
        streamingMessage.append(String(data.text));
      } else if (eventName === "status") {
        streamingMessage.status(data?.message);
      } else if (eventName === "tool_start") {
        streamingMessage.toolStart(data);
      } else if (eventName === "tool_end") {
        streamingMessage.toolEnd(data);
      } else if (eventName === "confirmation") {
        needsConfirmation = true;
        addConfirmationCard(data);
      } else if (eventName === "done" && data?.status === "waiting_confirmation") {
        needsConfirmation = true;
      } else if (eventName === "error") {
        throw new Error(data?.message || "消息生成失败，请稍后重试");
      }
    });
    streamingMessage.finish(!needsConfirmation);
  } catch (error) {
    streamingMessage.row.remove();
    if (error.status === 401) {
      logout();
      elements.loginError.textContent = "会话已失效，请重新登录";
      return;
    }
    elements.chatError.textContent = error.message || "消息发送失败，请稍后重试";
  } finally {
    setChatLoading(false);
    if (!elements.chatView.hidden) elements.messageInput.focus();
  }
});

elements.logoutButton.addEventListener("click", logout);
elements.newThreadButton.addEventListener("click", createThread);
elements.messageInput.addEventListener("input", resizeMessageInput);
elements.messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    elements.chatForm.requestSubmit();
  }
});

document.querySelectorAll(".account-chip").forEach((button) => {
  button.addEventListener("click", () => {
    elements.username.value = button.dataset.username;
    elements.username.focus();
  });
});

async function initializeAuth() {
  let token = null;
  try {
    token = localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY);
  } catch (error) {
    // 浏览器禁用本地存储时 localStorage 会直接抛错，这里退回登录页并说明原因。
    console.error(error);
    showLoginView();
    elements.loginError.textContent = "浏览器禁用了本地存储，无法保存登录状态";
    return;
  }

  if (!token) {
    showLoginView();
    return;
  }

  try {
    await loadCurrentUser(token);
    await loadThreads();
  } catch {
    try {
      localStorage.removeItem(ACCESS_TOKEN_STORAGE_KEY);
    } catch (error) {
      /* 存储不可用时忽略清理失败。 */
    }
    state.accessToken = null;
    showLoginView();
    elements.loginError.textContent = "登录状态已失效，请重新登录";
  }
}

// 无论初始化过程中发生什么，页面都不应该停在空白状态。
initializeAuth().catch(() => {
  showLoginView();
});
