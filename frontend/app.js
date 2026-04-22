const SKILL_ICONS = {
  web_search: "🔍",
  code_interpreter: "🖥️",
  file_parser: "📄",
  image_generation: "🎨",
  data_analysis: "📊",
};

const state = {
  conversations: [],
  activeConversationId: null,
  activeConversationDetail: null,
  bookmarks: [],
  bookmarkedMessageIds: new Set(),
  skills: [],
  activeSkills: {},
  models: [],
  modelConfigs: [],
  selectedModel: "",
  selectedContextMsgs: [],
  contextModalTempSelected: [],
  ctxSelectedConvId: null,
  sidebarTab: "chats",
  contextMenuTarget: null,
  messageContextTarget: null,
  isStreaming: false,
  streamingText: "",
  contextTriggeredByAt: false,
  settings: null,
  editingModelId: "",
  settingsPane: "model",
  localSkills: [],
  localSkillHealthByKey: {},
  selectedLocalSkill: "",
  localSkillEnvText: "",
  localSkillEnvLoadedFor: "",
  localSkillPrimaryEnvKey: "",
  replyEngine: "openclaw_local",
};

const REPLY_ENGINE_STORAGE_KEY = "replyEngine";
const REPLY_ENGINE_LABELS = {
  openclaw_local: "OpenClaw",
  api_direct: "API 直连",
};

function loadReplyEnginePreference() {
  const raw = (localStorage.getItem(REPLY_ENGINE_STORAGE_KEY) || "").trim();
  if (raw === "api_direct" || raw === "openclaw_local") return raw;
  return "openclaw_local";
}

function setReplyEnginePreference(value) {
  const v = value === "api_direct" ? "api_direct" : "openclaw_local";
  state.replyEngine = v;
  localStorage.setItem(REPLY_ENGINE_STORAGE_KEY, v);
  renderEnginePicker();
}

function $(s) { return document.querySelector(s); }
function $$(s) { return document.querySelectorAll(s); }

function toast(msg, type = "info") {
  const c = $("#toastContainer");
  if (!c) return;
  const t = document.createElement("div");
  t.className = `toast ${type}`;
  t.textContent = msg;
  c.appendChild(t);
  setTimeout(() => t.remove(), 2600);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    let message = text || `HTTP ${res.status}`;
    try {
      const parsed = JSON.parse(text);
      if (parsed?.detail) message = parsed.detail;
      else if (parsed?.message) message = parsed.message;
    } catch {
      // keep raw text
    }
    throw new Error(message);
  }
  if (res.status === 204) return null;
  return res.json();
}

function handleError(err) {
  console.error(err);
  toast(`错误：${err.message || err}`, "error");
}

function openConfirmModal({ title, message, okText = "确认" }) {
  return new Promise((resolve) => {
    const overlay = $("#confirmModal");
    const titleEl = $("#confirmTitle");
    const msgEl = $("#confirmMessage");
    const cancelBtn = $("#confirmCancel");
    const okBtn = $("#confirmOk");
    if (!overlay || !titleEl || !msgEl || !cancelBtn || !okBtn) {
      resolve(window.confirm(message || title || "确定继续吗？"));
      return;
    }
    titleEl.textContent = title || "确认操作";
    msgEl.textContent = message || "确定继续吗？";
    okBtn.textContent = okText;
    overlay.classList.add("visible");
    const cleanup = () => {
      overlay.classList.remove("visible");
      cancelBtn.onclick = null;
      okBtn.onclick = null;
      overlay.onclick = null;
    };
    cancelBtn.onclick = () => {
      cleanup();
      resolve(false);
    };
    okBtn.onclick = () => {
      cleanup();
      resolve(true);
    };
    overlay.onclick = (e) => {
      if (e.target === overlay) {
        cleanup();
        resolve(false);
      }
    };
  });
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function formatTime(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "-";
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function timeAgo(iso) {
  const t = new Date(iso).getTime();
  if (!t) return "-";
  const gap = Date.now() - t;
  if (gap < 60_000) return "刚刚";
  if (gap < 3_600_000) return `${Math.floor(gap / 60_000)}分钟前`;
  if (gap < 86_400_000) return `${Math.floor(gap / 3_600_000)}小时前`;
  return `${Math.floor(gap / 86_400_000)}天前`;
}

function mdToHtml(raw, fallbackText = "（无最终输出）") {
  let clean = String(raw || "");
  clean = clean.replace(/<think\b[^>]*>[\s\S]*?<\/think>/gi, "");
  clean = clean.replace(/<function_calls\b[^>]*>[\s\S]*?<\/function_calls>/gi, "");
  clean = clean.replace(/<invoke\b[^>]*>[\s\S]*?<\/invoke>/gi, "");
  clean = clean.replace(/<\/?(function_call|arg)\b[^>]*>/gi, "");
  clean = clean.trim();
  if (!clean) clean = fallbackText;
  let text = escapeHtml(clean);
  text = text.replace(/```([\s\S]*?)```/g, (_m, code) => `<pre><code>${code.trim()}</code></pre>`);
  text = text.replace(/`([^`]+)`/g, "<code>$1</code>");
  text = text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  text = text.replace(/^### (.+)$/gm, "<h3>$1</h3>");
  text = text.replace(/\n/g, "<br>");
  return `<p>${text}</p>`;
}

function getSkillIcon(key) {
  return SKILL_ICONS[key] || "🧩";
}

function renderConversationList(filter = "") {
  const list = $("#convList");
  if (!list) return;
  const q = filter.trim().toLowerCase();
  const rows = state.conversations.filter((c) => !q || c.title.toLowerCase().includes(q));
  const pinned = rows.filter((c) => c.is_pinned);
  const regular = rows.filter((c) => !c.is_pinned);
  let html = "";
  if (pinned.length) html += `<div class="conv-group-label">📌 已置顶</div>${pinned.map(renderConversationItem).join("")}`;
  if (regular.length) html += `<div class="conv-group-label">最近</div>${regular.map(renderConversationItem).join("")}`;
  if (!rows.length) html = '<div style="color:var(--text-secondary);padding:24px 8px;">暂无会话</div>';
  list.innerHTML = html;

  list.querySelectorAll(".conv-item").forEach((item) => {
    item.addEventListener("click", (e) => {
      if (e.target.closest(".conv-actions")) return;
      switchConversation(Number(item.dataset.id)).catch(handleError);
    });
    item.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      openContextMenu(e.clientX, e.clientY, Number(item.dataset.id));
    });
  });
  list.querySelectorAll(".conv-pin-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      togglePin(Number(btn.closest(".conv-item").dataset.id)).catch(handleError);
    });
  });
  list.querySelectorAll(".conv-del-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteConversation(Number(btn.closest(".conv-item").dataset.id)).catch(handleError);
    });
  });
}

function renderConversationItem(c) {
  const active = c.id === state.activeConversationId ? " active" : "";
  return `<div class="conv-item${active}" data-id="${c.id}">
    <div class="conv-info">
      <div class="conv-title">${escapeHtml(c.title)}</div>
      <div class="conv-meta">更新于 ${timeAgo(c.updated_at)}</div>
    </div>
    <div class="conv-actions">
      <button class="conv-pin-btn" title="置顶">📌</button>
      <button class="conv-del-btn" title="删除">🗑️</button>
    </div>
  </div>`;
}

async function renderMessages() {
  const wrap = $("#messagesArea");
  const detail = state.activeConversationDetail;
  if (!wrap) return;
  if (!detail || !detail.messages.length) {
    wrap.innerHTML = '<div style="color:var(--text-secondary);padding:24px;">还没有消息，开始对话吧。</div>';
    return;
  }

  const traceMap = {};
  await Promise.all(
    detail.messages
      .filter((m) => m.role === "assistant")
      .map(async (msg) => {
        try {
          traceMap[msg.id] = await api(`/messages/${msg.id}/trace`);
        } catch {
          traceMap[msg.id] = { context_sources: [], skill_executions: [] };
        }
      })
  );

  const streamBubble = state.isStreaming
    ? `<div class="message ai"><div class="msg-avatar">AI</div><div class="msg-body"><div class="msg-bubble" id="streamingBubble">${mdToHtml(state.streamingText || "思考中...", "思考中...")}</div></div></div>`
    : "";

  wrap.innerHTML =
    detail.messages
      .map((m) => {
        const isAi = m.role === "assistant";
        const trace = traceMap[m.id] || { context_sources: [], skill_executions: [] };
        const skillTags = trace.skill_executions
          .map((x) => `<span class="source-skill-tag"><span class="asc-dot"></span>${getSkillIcon(x.skill_key)} ${x.skill_key}</span>`)
          .join("");
        const refs = trace.context_sources
          .map((x) => `<div class="source-ref-line">📎 ${escapeHtml(x.source_preview || "引用消息")}</div>`)
          .join("");
        const sourceCard = isAi
          ? `<div class="source-card"><div class="source-card-header">📋 溯源信息 <span style="margin-left:auto">▼</span></div><div class="source-card-body">
            <div style="font-size:12px;color:var(--text-secondary);margin-bottom:6px;">引用来源</div>${refs || '<div style="font-size:12px;color:var(--text-secondary);">无</div>'}
            <div style="font-size:12px;color:var(--text-secondary);margin:8px 0 6px;">本次 Skill</div>${skillTags || '<div style="font-size:12px;color:var(--text-secondary);">无</div>'}
          </div></div>`
          : "";
        return `<div class="message ${m.role === "user" ? "user" : "ai"}" data-msg-id="${m.id}">
          <div class="msg-avatar">${m.role === "user" ? "你" : "AI"}</div>
          <div class="msg-body">
            <div class="msg-bubble">${mdToHtml(m.content)}</div>
            ${sourceCard}
            <div class="msg-meta">
              <span class="msg-time">${formatTime(m.created_at)}</span>
              <div class="msg-actions">
                ${isAi ? `<button class="msg-action-btn bookmark-btn ${state.bookmarkedMessageIds.has(m.id) ? "bookmarked" : ""}" data-msg="${m.id}">🔖</button>` : ""}
                <button class="msg-action-btn copy-msg-btn" data-msg="${m.id}">📋</button>
              </div>
            </div>
          </div>
        </div>`;
      })
      .join("") +
    streamBubble;

  wrap.querySelectorAll(".source-card-header").forEach((h) => {
    h.addEventListener("click", () => h.nextElementSibling?.classList.toggle("visible"));
  });
  wrap.querySelectorAll(".copy-msg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const msg = state.activeConversationDetail.messages.find((m) => m.id === Number(btn.dataset.msg));
      if (!msg) return;
      navigator.clipboard.writeText(msg.content).then(() => toast("已复制", "success"));
    });
  });
  wrap.querySelectorAll(".bookmark-btn").forEach((btn) => {
    btn.addEventListener("click", () => toggleBookmark(Number(btn.dataset.msg)).catch(handleError));
  });
  wrap.querySelectorAll(".msg-bubble").forEach((bubble) => {
    bubble.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      const row = bubble.closest(".message");
      const msgId = Number(row?.dataset.msgId || 0);
      if (!msgId) return;
      openMessageContextMenu(e.clientX, e.clientY, msgId);
    });
  });
  wrap.scrollTop = wrap.scrollHeight;
}

function renderActiveSkills() {
  const box = $("#activeSkills");
  if (!box) return;
  const active = state.skills.filter((s) => state.activeSkills[s.key]);
  if (!active.length) {
    box.innerHTML = "";
    return;
  }
  box.innerHTML = `<span class="active-skills-label">已激活：</span>${active
    .map((s) => `<span class="active-skill-chip"><span class="asc-dot"></span>${getSkillIcon(s.key)} ${escapeHtml(s.name)}</span>`)
    .join("")}`;
}

function renderContextTags() {
  const wrap = $("#contextTags");
  const token = $("#tokenEstimate");
  if (!wrap || !token) return;
  if (!state.selectedContextMsgs.length) {
    wrap.innerHTML = "";
    token.textContent = "";
    return;
  }
  wrap.innerHTML = state.selectedContextMsgs
    .map(
      (m, i) =>
        `<div class="ctx-tag"><span class="ctx-source">${escapeHtml(m.conversation_title)}</span><span>${escapeHtml(m.snippet)}</span><button class="ctx-remove" data-idx="${i}">×</button></div>`
    )
    .join("");
  wrap.querySelectorAll(".ctx-remove").forEach((b) => {
    b.addEventListener("click", () => {
      state.selectedContextMsgs.splice(Number(b.dataset.idx), 1);
      renderContextTags();
    });
  });
  const total = state.selectedContextMsgs.reduce((sum, x) => sum + x.snippet.length, 0);
  token.textContent = `已引用上下文预估 ~${Math.ceil(total / 4)} tokens`;
  token.className = `token-estimate ${total > 12000 ? "warning" : ""}`;
}

function renderBookmarks() {
  const box = $("#bookmarkList");
  if (!box) return;
  if (!state.bookmarks.length) {
    box.innerHTML = '<div style="color:var(--text-secondary);padding:24px 8px;">暂无书签</div>';
    return;
  }
  box.innerHTML = state.bookmarks
    .map(
      (b) =>
        `<div class="bookmark-item" data-conv="${b.conversation_id}" data-msg="${b.message_id}">
          <div class="bm-source">会话 #${b.conversation_id}</div>
          <div class="bm-text">${escapeHtml(b.content_preview)}</div>
          <div class="bm-time">${timeAgo(b.created_at)}</div>
        </div>`
    )
    .join("");
  box.querySelectorAll(".bookmark-item").forEach((item) => {
    item.addEventListener("click", async () => {
      await switchConversation(Number(item.dataset.conv));
      setTimeout(() => {
        document.querySelector(`[data-msg-id="${item.dataset.msg}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
      }, 80);
    });
  });
}

function renderModelPicker() {
  const btn = $("#modelPickerBtn");
  const menu = $("#modelPickerMenu");
  if (!btn || !menu) return;
  if (!state.models.length) {
    btn.textContent = "请先配置模型";
    btn.classList.add("disabled");
    menu.innerHTML = "";
    return;
  }
  btn.classList.remove("disabled");
  const selected = state.models.find((m) => m.id === state.selectedModel) || state.models[0];
  state.selectedModel = selected.id;
  btn.textContent = selected.label;
  menu.innerHTML = state.models
    .map((m) => `<button class="model-picker-item ${m.id === state.selectedModel ? "active" : ""}" data-model-id="${escapeHtml(m.id)}">${escapeHtml(m.label)}</button>`)
    .join("");
  menu.querySelectorAll(".model-picker-item").forEach((item) => {
    item.addEventListener("click", () => {
      state.selectedModel = item.dataset.modelId;
      renderModelPicker();
      closeModelPicker();
    });
  });
}

function renderEnginePicker() {
  const btn = $("#enginePickerBtn");
  const menu = $("#enginePickerMenu");
  if (!btn || !menu) return;
  const selected = REPLY_ENGINE_LABELS[state.replyEngine] ? state.replyEngine : "openclaw_local";
  state.replyEngine = selected;
  btn.textContent = REPLY_ENGINE_LABELS[selected];
  menu.innerHTML = Object.entries(REPLY_ENGINE_LABELS)
    .map(
      ([id, label]) =>
        `<button class="model-picker-item ${id === selected ? "active" : ""}" data-engine-id="${id}">${escapeHtml(label)}</button>`
    )
    .join("");
  menu.querySelectorAll(".model-picker-item").forEach((item) => {
    item.addEventListener("click", () => {
      setReplyEnginePreference(item.dataset.engineId);
      closeEnginePicker();
    });
  });
}

function openEnginePicker() {
  $("#enginePickerMenu")?.classList.remove("hidden");
}

function closeEnginePicker() {
  $("#enginePickerMenu")?.classList.add("hidden");
}

function openModelPicker() {
  const menu = $("#modelPickerMenu");
  if (!menu || !state.models.length) return;
  menu.classList.remove("hidden");
}

function closeModelPicker() {
  $("#modelPickerMenu")?.classList.add("hidden");
}

function renderModelConfigList() {
  const box = $("#modelConfigList");
  if (!box) return;
  if (!state.modelConfigs.length) {
    box.innerHTML = '<div style="font-size:12px;color:var(--text-secondary);padding:8px 0;">暂无模型配置</div>';
    return;
  }
  box.innerHTML = state.modelConfigs
    .map(
      (m) => `<div class="mc-row ${state.editingModelId === m.id ? "active" : ""}" data-model-id="${escapeHtml(m.id)}">
        <div class="mc-main">
          <div class="mc-title">${escapeHtml(m.label)} ${m.is_default ? "· 默认" : ""} ${m.enabled ? "" : "· 已禁用"}</div>
          <div class="mc-sub">${escapeHtml(m.id)} · ${escapeHtml(m.api_base_url || "")}</div>
        </div>
        <button class="row-delete-btn" data-del-model="${escapeHtml(m.id)}" title="删除模型">🗑</button>
      </div>`
    )
    .join("");
  box.querySelectorAll(".mc-row").forEach((row) => {
    row.addEventListener("click", () => {
      const id = row.dataset.modelId;
      const model = state.modelConfigs.find((m) => m.id === id);
      if (!model) return;
      state.editingModelId = model.id;
      $("#mcId").value = model.id;
      $("#mcLabel").value = model.label;
      $("#mcBaseUrl").value = model.api_base_url || "";
      $("#mcApiKey").value = model.api_key || "";
      $("#mcEnabled").checked = !!model.enabled;
      $("#mcDefault").checked = !!model.is_default;
      renderModelConfigList();
    });
  });
  box.querySelectorAll(".row-delete-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const id = btn.dataset.delModel;
      if (!id) return;
      deleteModelConfigApi(id).catch(handleError);
    });
  });
}

function renderLocalSkillList() {
  const box = $("#localSkillList");
  if (!box) return;
  if (!state.localSkills.length) {
    box.innerHTML = '<div style="font-size:12px;color:var(--text-secondary);padding:8px 0;">暂无本地技能</div>';
    return;
  }
  box.innerHTML = state.localSkills
    .map((s) => {
      const active = state.selectedLocalSkill === s.key ? "active" : "";
      const sub = `${s.key}${s.version ? ` · v${s.version}` : ""}${s.dir ? ` · ${s.dir}` : ""}`;
      const health = state.localSkillHealthByKey[s.key] || null;
      const hs = health ? `<span class="ls-health-badge ${health.status}">${escapeHtml(health.summary || "-")}</span>` : "";
      const hd = health && Array.isArray(health.details) && health.details.length
        ? `<div class="ls-health-detail">${escapeHtml(health.details[0])}</div>`
        : "";
      return `<div class="ls-row ${active}" data-skill-key="${escapeHtml(s.key)}">
        <div class="ls-main">
          <div class="ls-title">${escapeHtml(s.name || s.key)} ${hs}</div>
          <div class="ls-sub">${escapeHtml(sub)}</div>
          ${hd}
        </div>
        <button class="row-delete-btn" data-del-skill="${escapeHtml(s.key)}" title="删除技能">🗑</button>
      </div>`;
    })
    .join("");
  box.querySelectorAll(".ls-row").forEach((row) => {
    row.addEventListener("click", () => {
      state.selectedLocalSkill = row.dataset.skillKey || "";
      renderLocalSkillList();
      loadSelectedSkillEnv().catch(handleError);
    });
  });
  box.querySelectorAll(".row-delete-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const slug = btn.dataset.delSkill;
      if (!slug) return;
      deleteLocalSkillApi(slug).catch(handleError);
    });
  });
}

async function loadLocalSkillHealth() {
  const rows = await api("/skills/local/health");
  const map = {};
  (rows || []).forEach((x) => {
    if (x?.skill_key) map[x.skill_key] = x;
  });
  state.localSkillHealthByKey = map;
}

function switchSettingsPane(pane) {
  state.settingsPane = pane === "skill" ? "skill" : "model";
  const isModel = state.settingsPane === "model";
  $("#settingsPaneModel")?.classList.toggle("hidden", !isModel);
  $("#settingsPaneSkill")?.classList.toggle("hidden", isModel);
  $("#settingsTabModel")?.classList.toggle("active", isModel);
  $("#settingsTabSkill")?.classList.toggle("active", !isModel);
}

function clearModelForm() {
  state.editingModelId = "";
  $("#mcId").value = "";
  $("#mcLabel").value = "";
  $("#mcBaseUrl").value = "";
  $("#mcApiKey").value = "";
  $("#mcEnabled").checked = true;
  $("#mcDefault").checked = false;
  renderModelConfigList();
}

async function loadLocalSkills() {
  state.localSkills = await api("/skills/local");
  await loadLocalSkillHealth();
  if (state.selectedLocalSkill && !state.localSkills.some((x) => x.key === state.selectedLocalSkill)) {
    state.selectedLocalSkill = "";
  }
  renderLocalSkillList();
  await loadSelectedSkillEnv();
}

function renderSkillEnvEditor() {
  const hint = $("#skillEnvHint");
  const editor = $("#skillEnvEditor");
  if (!hint || !editor) return;
  const key = state.selectedLocalSkill;
  if (!key) {
    hint.textContent = "请先从左侧选择一个技能";
    editor.value = "";
    editor.disabled = true;
    return;
  }
  const primary = state.localSkillPrimaryEnvKey || "";
  hint.textContent = primary
    ? `当前技能：${key}（建议变量名：${primary}）`
    : `当前技能：${key}（请按 KEY=VALUE 格式填写）`;
  editor.disabled = false;
  if (!state.localSkillEnvText && primary) {
    editor.placeholder = `${primary}=your_token_here`;
  } else {
    editor.placeholder = "例如：\nTENCENT_DOCS_TOKEN=your_token_here";
  }
  editor.value = state.localSkillEnvText || "";
}

async function loadSelectedSkillEnv() {
  if (!state.selectedLocalSkill) {
    state.localSkillEnvText = "";
    state.localSkillEnvLoadedFor = "";
    state.localSkillPrimaryEnvKey = "";
    renderSkillEnvEditor();
    return;
  }
  const payload = await api(`/skills/local/${encodeURIComponent(state.selectedLocalSkill)}/env`);
  state.localSkillEnvLoadedFor = state.selectedLocalSkill;
  state.localSkillEnvText = payload.env_text || "";
  state.localSkillPrimaryEnvKey = payload.primary_env_key || "";
  renderSkillEnvEditor();
}

async function saveSelectedSkillEnv() {
  const key = state.selectedLocalSkill;
  if (!key) {
    toast("请先从左侧选择技能", "error");
    return;
  }
  const text = $("#skillEnvEditor")?.value || "";
  const payload = await api(`/skills/local/${encodeURIComponent(key)}/env`, {
    method: "PUT",
    body: JSON.stringify({ env_text: text }),
  });
  state.localSkillEnvLoadedFor = key;
  state.localSkillEnvText = payload.env_text || "";
  state.localSkillPrimaryEnvKey = payload.primary_env_key || "";
  renderSkillEnvEditor();
  toast(`已保存 ${key} 的 .env.local`, "success");
}

function openSettingsModal() {
  const s = state.settings || {};
  $("#settingSkillsDir").value = s.local_skills_dir || "";
  $("#settingAgentEngine").value = s.agent_engine || "openclaw_local";
  $("#settingOpenclawBaseUrl").value = s.openclaw_api_base_url || "";
  $("#settingOpenclawApiKey").value = s.openclaw_api_key || "";
  renderModelConfigList();
  clearModelForm();
  switchSettingsPane(state.settingsPane);
  $("#settingsModal")?.classList.add("visible");
  loadLocalSkills().catch(handleError);
  renderSkillEnvEditor();
}

function closeSettingsModal() {
  $("#settingsModal")?.classList.remove("visible");
}

function openContextMenu(x, y, convId) {
  state.contextMenuTarget = convId;
  const menu = $("#contextMenu");
  if (!menu) return;
  menu.style.left = `${x}px`;
  menu.style.top = `${y}px`;
  menu.classList.add("visible");
}

function closeContextMenu() {
  $("#contextMenu")?.classList.remove("visible");
}

function openMessageContextMenu(x, y, msgId) {
  state.messageContextTarget = msgId;
  const menu = $("#messageContextMenu");
  if (!menu) return;
  menu.style.left = `${x}px`;
  menu.style.top = `${y}px`;
  menu.classList.add("visible");
}

function closeMessageContextMenu() {
  $("#messageContextMenu")?.classList.remove("visible");
}

async function loadConversations(query = "") {
  const suffix = query ? `?query=${encodeURIComponent(query)}` : "";
  state.conversations = await api(`/conversations${suffix}`);
  renderConversationList(query);
  if (!state.activeConversationId && state.conversations.length) {
    await switchConversation(state.conversations[0].id);
  }
}

async function loadSkills() {
  state.skills = await api("/skills");
  state.skills.forEach((s) => {
    // Only explicit toggles in the Skill modal enable a skill; no automatic defaults.
    if (state.activeSkills[s.key] === undefined) state.activeSkills[s.key] = false;
  });
  renderActiveSkills();
}

async function loadModelConfigs() {
  state.modelConfigs = await api("/model-configs");
  state.models = state.modelConfigs
    .filter((m) => m.enabled)
    .map((m) => ({ id: m.id, label: m.label }));
  if (!state.models.length) state.selectedModel = "";
  if (state.models.length && !state.models.some((m) => m.id === state.selectedModel)) {
    const defaultModel = state.modelConfigs.find((m) => m.is_default && m.enabled);
    state.selectedModel = (defaultModel || state.models[0]).id;
  }
  renderModelPicker();
}

async function loadSettings() {
  state.settings = await api("/settings");
}

async function loadBookmarks() {
  state.bookmarks = await api("/bookmarks");
  state.bookmarkedMessageIds = new Set(state.bookmarks.map((x) => x.message_id));
  renderBookmarks();
}

async function switchConversation(id) {
  if (
    state.activeConversationDetail &&
    state.activeConversationDetail.conversation?.id !== id &&
    (state.activeConversationDetail.messages?.length || 0) === 0
  ) {
    // Auto-clean empty draft conversation when user leaves it.
    const staleId = state.activeConversationDetail.conversation.id;
    try {
      await api(`/conversations/${staleId}`, { method: "DELETE" });
      state.activeConversationId = null;
      state.conversations = state.conversations.filter((c) => c.id !== staleId);
    } catch {
      // ignore cleanup failure
    }
  }
  state.activeConversationId = id;
  state.activeConversationDetail = await api(`/conversations/${id}`);
  renderConversationList($("#sidebarSearch")?.value || "");
  $("#chatTitleInput").value = state.activeConversationDetail.conversation.title;
  await loadBookmarks();
  await renderMessages();
}

async function createConversation() {
  if (state.activeConversationDetail && (state.activeConversationDetail.messages?.length || 0) === 0) {
    toast("当前已是空白新会话", "info");
    return;
  }
  for (const conv of state.conversations.slice(0, 8)) {
    try {
      const detail = await api(`/conversations/${conv.id}`);
      if ((detail.messages || []).length === 0) {
        await switchConversation(conv.id);
        toast("已切换到现有空白会话", "info");
        return;
      }
    } catch {
      // ignore per-conversation load errors
    }
  }
  const created = await api("/conversations", {
    method: "POST",
    body: JSON.stringify({ title: "新对话" }),
  });
  await loadConversations($("#sidebarSearch")?.value || "");
  await switchConversation(created.id);
  toast("已创建新会话", "success");
}

async function updateConversation(id, payload) {
  await api(`/conversations/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  await loadConversations($("#sidebarSearch")?.value || "");
  if (state.activeConversationId === id) await switchConversation(id);
}

async function togglePin(id) {
  const conv = state.conversations.find((c) => c.id === id);
  if (!conv) return;
  await updateConversation(id, { is_pinned: !conv.is_pinned });
}

async function deleteConversation(id) {
  const yes = await openConfirmModal({ title: "删除会话", message: "确认删除这个会话吗？", okText: "删除" });
  if (!yes) return;
  await api(`/conversations/${id}`, { method: "DELETE" });
  if (state.activeConversationId === id) state.activeConversationId = null;
  await loadConversations($("#sidebarSearch")?.value || "");
  if (!state.activeConversationId) $("#messagesArea").innerHTML = '<div style="color:var(--text-secondary);padding:24px;">暂无会话</div>';
}

async function parseSSEStream(response, handlers) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  const dispatchEvent = async (eventBlock) => {
    if (!eventBlock || !eventBlock.trim()) return;
    let eventName = "message";
    let dataValue = "";
    eventBlock.split("\n").forEach((line) => {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      if (line.startsWith("data:")) dataValue += line.slice(5).trim();
    });
    if (!dataValue) return;
    let parsed = {};
    try {
      parsed = JSON.parse(dataValue);
    } catch {
      parsed = { raw: dataValue };
    }
    await Promise.resolve(handlers[eventName]?.(parsed));
  };
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";
    for (const eventBlock of events) await dispatchEvent(eventBlock);
  }
  // Some servers close the stream without a final "\n\n"; dispatch remaining fragment.
  if (buffer.trim()) await dispatchEvent(buffer);
}

async function streamSendMessage() {
  const input = $("#messageInput");
  const sendBtn = $("#btnSend");
  if (!input || state.isStreaming) return;
  const content = input.value.trim();
  if (!content) return;
  const hasModel = Boolean((state.selectedModel || "").trim());
  if (!hasModel) {
    toast("请先在设置里配置默认模型，或在模型下拉选择模型。", "error");
    return;
  }
  if (!state.activeConversationId) await createConversation();
  const requestConversationId = state.activeConversationId;

  state.isStreaming = true;
  state.streamingText = "";
  sendBtn.disabled = true;

  if (state.models.length && !state.models.some((m) => m.id === state.selectedModel)) {
    state.selectedModel = state.models[0].id;
    renderModelPicker();
  }

  const payload = {
    content,
    context_message_ids: state.selectedContextMsgs.map((m) => m.message_id),
    enabled_skills: Object.keys(state.activeSkills).filter((key) => state.activeSkills[key]),
    model: state.selectedModel || null,
    reply_engine: state.replyEngine,
  };

  input.value = "";
  input.style.height = "auto";
  state.selectedContextMsgs = [];
  renderContextTags();

  // optimistic user bubble
  if (state.activeConversationDetail) {
    state.activeConversationDetail.messages.push({
      id: Date.now() * -1,
      conversation_id: requestConversationId,
      role: "user",
      content: payload.content,
      token_count: 0,
      created_at: new Date().toISOString(),
    });
    await renderMessages();
  }
  if (!state.activeConversationDetail) {
    await switchConversation(requestConversationId);
  }

  const response = await fetch(`/conversations/${requestConversationId}/messages/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok || !response.body) {
    state.isStreaming = false;
    sendBtn.disabled = false;
    const text = await response.text();
    let message = text || "请求失败";
    try {
      const parsed = JSON.parse(text);
      if (parsed?.detail) message = parsed.detail;
      else if (parsed?.message) message = parsed.message;
    } catch {
      // ignore
    }
    throw new Error(message);
  }

  let streamTerminal = false;
  try {
    await parseSSEStream(response, {
      chunk: async (data) => {
        state.streamingText += data.delta || "";
        const bubble = $("#streamingBubble");
        if (bubble) bubble.innerHTML = mdToHtml(state.streamingText || "思考中...", "思考中...");
        $("#messagesArea").scrollTop = $("#messagesArea").scrollHeight;
      },
      error: (data) => {
        streamTerminal = true;
        toast(data.message || "流式请求失败", "error");
      },
      done: async () => {
        streamTerminal = true;
      },
    });
  } catch (err) {
    streamTerminal = true;
    handleError(err);
  } finally {
    state.isStreaming = false;
    state.streamingText = "";
    sendBtn.disabled = false;
    if (!streamTerminal) {
      toast("回复流意外结束（未收到完成事件），请重试。", "error");
    }
    const switchedAway = state.activeConversationId !== requestConversationId;
    await switchConversation(requestConversationId);
    if (switchedAway) {
      toast("回复已写入原会话，已自动切回该会话。", "info");
    }
    await loadSkills();
  }
}

async function saveSettings() {
  const payload = {
    local_skills_dir: $("#settingSkillsDir").value.trim(),
    agent_engine: $("#settingAgentEngine").value.trim() || "openclaw_local",
    openclaw_api_base_url: $("#settingOpenclawBaseUrl").value.trim(),
    openclaw_api_key: $("#settingOpenclawApiKey").value.trim(),
  };
  state.settings = await api("/settings", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  closeSettingsModal();
  await Promise.all([loadSettings(), loadSkills(), loadModelConfigs(), loadLocalSkills()]);
  renderActiveSkills();
  toast("设置已保存", "success");
}

async function deleteLocalSkillApi(explicitSlug = "") {
  const slug = explicitSlug || state.selectedLocalSkill;
  if (!slug) {
    toast("请先选择或输入要删除的技能 slug", "error");
    return;
  }
  const yes = await openConfirmModal({ title: "删除技能", message: `确定删除技能 ${slug} 吗？`, okText: "删除" });
  if (!yes) return;
  const installDir = ($("#settingSkillsDir").value || "").trim();
  const qs = installDir ? `?install_dir=${encodeURIComponent(installDir)}` : "";
  await api(`/skills/local/${encodeURIComponent(slug)}${qs}`, { method: "DELETE" });
  delete state.activeSkills[slug];
  state.selectedLocalSkill = "";
  await Promise.all([loadLocalSkills(), loadSkills()]);
  renderSkillGrid();
  renderActiveSkills();
  toast(`技能已删除：${slug}`, "success");
}

function _collectModelForm() {
  return {
    id: $("#mcId").value.trim(),
    label: $("#mcLabel").value.trim(),
    api_base_url: $("#mcBaseUrl").value.trim(),
    api_key: $("#mcApiKey").value.trim(),
    enabled: $("#mcEnabled").checked,
    is_default: $("#mcDefault").checked,
    provider: "openai_compatible",
  };
}

async function createModelConfig() {
  const payload = _collectModelForm();
  if (!payload.id || !payload.label || !payload.api_base_url || !payload.api_key) {
    toast("请填写完整模型字段（ID/名称/BaseURL/Key）", "error");
    return;
  }
  state.modelConfigs = await api("/model-configs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  await loadModelConfigs();
  renderModelConfigList();
  toast("模型已新增", "success");
}

async function updateModelConfigApi() {
  const payload = _collectModelForm();
  if (!payload.id) {
    toast("模型 ID 不能为空", "error");
    return;
  }
  const targetId = state.editingModelId || payload.id;
  if (!targetId) {
    toast("请先从列表选择要更新的模型", "error");
    return;
  }
  if (!state.modelConfigs.some((m) => m.id === targetId)) {
    toast("当前更新目标不存在，请重新选择模型。", "error");
    return;
  }
  state.modelConfigs = await api(`/model-configs/${encodeURIComponent(targetId)}`, {
    method: "PUT",
    body: JSON.stringify({
      id: payload.id,
      label: payload.label || undefined,
      api_base_url: payload.api_base_url || undefined,
      api_key: payload.api_key || undefined,
      enabled: payload.enabled,
      is_default: payload.is_default,
      provider: payload.provider,
    }),
  });
  await loadModelConfigs();
  state.editingModelId = payload.id;
  renderModelConfigList();
  toast("模型已更新", "success");
}

async function deleteModelConfigApi(explicitId = "") {
  const modelId = explicitId || state.editingModelId || ($("#mcId").value || "").trim();
  if (!modelId) {
    toast("请先从列表选择要删除的模型", "error");
    return;
  }
  if (!state.modelConfigs.some((m) => m.id === modelId)) {
    toast("当前删除目标不存在，请重新选择模型。", "error");
    return;
  }
  const yes = await openConfirmModal({ title: "删除模型", message: `确定删除模型 ${modelId} 吗？`, okText: "删除" });
  if (!yes) return;
  state.modelConfigs = await api(`/model-configs/${encodeURIComponent(modelId)}`, {
    method: "DELETE",
  });
  await loadModelConfigs();
  clearModelForm();
  renderModelConfigList();
  toast("模型已删除", "success");
}

async function toggleBookmark(messageId) {
  if (state.bookmarkedMessageIds.has(messageId)) {
    await api(`/messages/${messageId}/bookmark`, { method: "DELETE" });
    toast("已移除书签", "info");
  } else {
    await api(`/messages/${messageId}/bookmark`, { method: "POST" });
    toast("已添加书签", "success");
  }
  await loadBookmarks();
  await renderMessages();
}

async function openContextModal(triggeredByAt = false) {
  state.contextTriggeredByAt = triggeredByAt;
  state.contextModalTempSelected = [...state.selectedContextMsgs];
  state.ctxSelectedConvId = state.activeConversationId;
  $("#ctxModal")?.classList.add("visible");
  renderCtxConvList();
  await renderCtxMsgList();
}

function closeContextModal() {
  $("#ctxModal")?.classList.remove("visible");
  state.contextTriggeredByAt = false;
}

function renderCtxConvList() {
  const box = $("#ctxConvList");
  if (!box) return;
  box.innerHTML = `
    <div class="ctx-conv-search"><input type="text" placeholder="搜索会话…" id="ctxConvSearch"></div>
    ${state.conversations
      .map(
        (c) => `<div class="ctx-conv-item ${c.id === state.ctxSelectedConvId ? "active" : ""}" data-id="${c.id}">
          <div class="cci-title">${escapeHtml(c.title)}</div>
          <div class="cci-meta">${timeAgo(c.updated_at)}</div>
        </div>`
      )
      .join("")}
  `;
  box.querySelectorAll(".ctx-conv-item").forEach((item) => {
    item.addEventListener("click", async () => {
      state.ctxSelectedConvId = Number(item.dataset.id);
      renderCtxConvList();
      await renderCtxMsgList();
    });
  });
  box.querySelector("#ctxConvSearch")?.addEventListener("input", () => {
    const q = box.querySelector("#ctxConvSearch").value.trim().toLowerCase();
    box.querySelectorAll(".ctx-conv-item").forEach((x) => {
      const title = x.querySelector(".cci-title")?.textContent?.toLowerCase() || "";
      x.style.display = !q || title.includes(q) ? "block" : "none";
    });
  });
}

async function renderCtxMsgList() {
  const box = $("#ctxMsgList");
  if (!box) return;
  if (!state.ctxSelectedConvId) {
    box.innerHTML = '<div class="ctx-msg-empty">请选择左侧会话查看消息</div>';
    return;
  }
  const detail = await api(`/conversations/${state.ctxSelectedConvId}`);
  box.innerHTML = detail.messages
    .map((m) => {
      const selected = state.contextModalTempSelected.some((x) => x.message_id === m.id);
      return `<div class="ctx-msg-item ${selected ? "selected" : ""}" data-id="${m.id}">
        <div class="ctx-msg-check"></div>
        <div class="ctx-msg-content">
          <div class="ctx-msg-role ${m.role === "assistant" ? "ai" : ""}">${m.role}</div>
          <div class="ctx-msg-text">${escapeHtml(m.content.slice(0, 130))}</div>
        </div>
        <span class="ctx-msg-time">${formatTime(m.created_at)}</span>
      </div>`;
    })
    .join("");
  box.querySelectorAll(".ctx-msg-item").forEach((item) => {
    item.addEventListener("click", () => {
      const id = Number(item.dataset.id);
      const target = detail.messages.find((m) => m.id === id);
      if (!target) return;
      const idx = state.contextModalTempSelected.findIndex((x) => x.message_id === id);
      if (idx >= 0) state.contextModalTempSelected.splice(idx, 1);
      else {
        state.contextModalTempSelected.push({
          message_id: target.id,
          conversation_id: target.conversation_id,
          conversation_title: detail.conversation.title,
          snippet: target.content.slice(0, 40),
        });
      }
      item.classList.toggle("selected");
      $("#ctxSelectedCount").innerHTML = `已选择 <strong>${state.contextModalTempSelected.length}</strong> 条消息`;
    });
  });
  $("#ctxSelectedCount").innerHTML = `已选择 <strong>${state.contextModalTempSelected.length}</strong> 条消息`;
}

function confirmContextSelection() {
  state.selectedContextMsgs = [...state.contextModalTempSelected];
  renderContextTags();
  const input = $("#messageInput");
  if (state.contextTriggeredByAt && input) {
    // Remove leading "@" inserted by trigger.
    if (input.value.startsWith("@")) input.value = input.value.slice(1);
  }
  closeContextModal();
}

function openSkillModal() {
  $("#skillModal")?.classList.add("visible");
  renderSkillGrid();
}

function closeSkillModal() {
  $("#skillModal")?.classList.remove("visible");
}

function renderSkillGrid() {
  const grid = $("#skillGrid");
  if (!grid) return;
  grid.innerHTML = state.skills
    .map((s) => {
      const active = !!state.activeSkills[s.key];
      return `<div class="skill-card ${active ? "active" : ""}" data-key="${s.key}">
        <div class="skill-icon">${getSkillIcon(s.key)}</div>
        <div class="skill-info"><div class="skill-name">${escapeHtml(s.name)}</div><div class="skill-desc">${escapeHtml(s.description)}</div></div>
        <button class="skill-toggle ${active ? "on" : ""}"></button>
      </div>`;
    })
    .join("");
  grid.querySelectorAll(".skill-card").forEach((card) => {
    card.addEventListener("click", () => {
      const key = card.dataset.key;
      state.activeSkills[key] = !state.activeSkills[key];
      renderSkillGrid();
    });
  });
}

function resetSkills() {
  state.skills.forEach((s) => {
    state.activeSkills[s.key] = false;
  });
  renderSkillGrid();
  renderActiveSkills();
}

function exportConversation() {
  if (!state.activeConversationDetail) return;
  const c = state.activeConversationDetail;
  let md = `# ${c.conversation.title}\n\n`;
  c.messages.forEach((m) => {
    md += `**${m.role}** (${formatTime(m.created_at)})\n\n${m.content}\n\n---\n\n`;
  });
  const blob = new Blob([md], { type: "text/markdown" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${c.conversation.title}.md`;
  a.click();
  URL.revokeObjectURL(url);
  toast("已导出 Markdown", "success");
}

function shouldTriggerContextModalOnAt(input, event) {
  if (event.key !== "@") return false;
  const atStart = input.selectionStart === 0;
  const isEmpty = input.value.length === 0;
  return isEmpty || atStart;
}

function bindEvents() {
  $("#btnNewChat")?.addEventListener("click", () => createConversation().catch(handleError));
  $("#sidebarSearch")?.addEventListener("input", (e) => renderConversationList(e.target.value || ""));

  $$(".sidebar-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      $$(".sidebar-tab").forEach((x) => x.classList.remove("active"));
      tab.classList.add("active");
      state.sidebarTab = tab.dataset.tab;
      $("#convList").style.display = state.sidebarTab === "chats" ? "block" : "none";
      $("#bookmarkList").style.display = state.sidebarTab === "bookmarks" ? "block" : "none";
      if (state.sidebarTab === "bookmarks") loadBookmarks().catch(handleError);
    });
  });

  $("#chatTitleInput")?.addEventListener("change", async (e) => {
    const title = e.target.value.trim();
    if (!title || !state.activeConversationId) return;
    await updateConversation(state.activeConversationId, { title });
  });
  $("#chatTitleInput")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") e.target.blur();
  });

  $("#modelPickerBtn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    const menu = $("#modelPickerMenu");
    if (!menu) return;
    if (menu.classList.contains("hidden")) {
      closeEnginePicker();
      openModelPicker();
    } else closeModelPicker();
  });

  setReplyEnginePreference(loadReplyEnginePreference());
  $("#enginePickerBtn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    const menu = $("#enginePickerMenu");
    if (!menu) return;
    if (menu.classList.contains("hidden")) {
      closeModelPicker();
      openEnginePicker();
    } else closeEnginePicker();
  });
  $("#btnSend")?.addEventListener("click", () => streamSendMessage().catch(handleError));
  $("#messageInput")?.addEventListener("keydown", (e) => {
    const input = e.target;
    if (shouldTriggerContextModalOnAt(input, e)) {
      e.preventDefault();
      openContextModal(true).catch(handleError);
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      streamSendMessage().catch(handleError);
    }
  });
  $("#messageInput")?.addEventListener("input", (e) => {
    e.target.style.height = "auto";
    e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
  });

  $("#btnContext")?.addEventListener("click", () => openContextModal(false).catch(handleError));
  $("#btnSkill")?.addEventListener("click", openSkillModal);

  $("#ctxModalClose")?.addEventListener("click", closeContextModal);
  $("#ctxCancel")?.addEventListener("click", closeContextModal);
  $("#ctxConfirm")?.addEventListener("click", confirmContextSelection);
  $("#ctxModal")?.addEventListener("click", (e) => {
    if (e.target.id === "ctxModal") closeContextModal();
  });

  $("#skillModalClose")?.addEventListener("click", closeSkillModal);
  $("#skillReset")?.addEventListener("click", resetSkills);
  $("#skillConfirm")?.addEventListener("click", () => {
    closeSkillModal();
    renderActiveSkills();
  });
  $("#skillModal")?.addEventListener("click", (e) => {
    if (e.target.id === "skillModal") closeSkillModal();
  });

  $("#settingsModalClose")?.addEventListener("click", closeSettingsModal);
  $("#settingsCancel")?.addEventListener("click", closeSettingsModal);
  $("#settingsSave")?.addEventListener("click", () => saveSettings().catch(handleError));
  $("#skillEnvReloadBtn")?.addEventListener("click", () => loadSelectedSkillEnv().catch(handleError));
  $("#skillEnvSaveBtn")?.addEventListener("click", () => saveSelectedSkillEnv().catch(handleError));
  $("#skillEnvEditor")?.addEventListener("input", (e) => {
    state.localSkillEnvText = e.target.value || "";
  });
  $("#settingsTabModel")?.addEventListener("click", () => switchSettingsPane("model"));
  $("#settingsTabSkill")?.addEventListener("click", () => switchSettingsPane("skill"));
  $("#mcCreateBtn")?.addEventListener("click", () => createModelConfig().catch(handleError));
  $("#mcUpdateBtn")?.addEventListener("click", () => updateModelConfigApi().catch(handleError));
  $("#mcClearBtn")?.addEventListener("click", clearModelForm);
  $("#settingsModal")?.addEventListener("click", (e) => {
    if (e.target.id === "settingsModal") closeSettingsModal();
  });

  document.addEventListener("click", () => {
    closeContextMenu();
    closeMessageContextMenu();
    closeModelPicker();
    closeEnginePicker();
  });
  $("#contextMenu")?.addEventListener("click", (e) => {
    const action = e.target.closest(".ctx-menu-item")?.dataset.action;
    if (!action || !state.contextMenuTarget) return;
    const conv = state.conversations.find((c) => c.id === state.contextMenuTarget);
    if (action === "copy" && conv) {
      navigator.clipboard.writeText(conv.title).then(() => toast("会话标题已复制", "success"));
    }
    if (action === "quote") {
      openContextModal(false).catch(handleError);
    }
    if (action === "pin") togglePin(state.contextMenuTarget).catch(handleError);
    if (action === "delete") deleteConversation(state.contextMenuTarget).catch(handleError);
    if (action === "rename") {
      if (!conv) return;
      const next = prompt("重命名会话", conv.title);
      if (next && next.trim()) updateConversation(conv.id, { title: next.trim() }).catch(handleError);
    }
    closeContextMenu();
  });
  $("#messageContextMenu")?.addEventListener("click", (e) => {
    const action = e.target.closest(".ctx-menu-item")?.dataset.action;
    const msgId = state.messageContextTarget;
    if (!action || !msgId || !state.activeConversationDetail) return;
    const msg = state.activeConversationDetail.messages.find((m) => m.id === msgId);
    if (!msg) return;
    if (action === "copy") {
      navigator.clipboard.writeText(msg.content).then(() => toast("已复制", "success"));
    } else if (action === "quote") {
      const exists = state.selectedContextMsgs.some((x) => x.message_id === msg.id);
      if (!exists) {
        state.selectedContextMsgs.push({
          message_id: msg.id,
          conversation_id: msg.conversation_id,
          conversation_title: state.activeConversationDetail.conversation.title,
          snippet: (msg.content || "").slice(0, 40),
        });
        renderContextTags();
        toast("已加入引用上下文", "success");
      }
    }
    closeMessageContextMenu();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeContextModal();
      closeSkillModal();
      closeSettingsModal();
      closeContextMenu();
      closeMessageContextMenu();
      closeModelPicker();
      closeEnginePicker();
    }
  });
}

async function init() {
  bindEvents();
  await Promise.all([loadSettings(), loadSkills(), loadModelConfigs(), loadLocalSkills()]);
  await loadConversations();
  await loadBookmarks();
  renderContextTags();
}

window.__openDesktopSettings = openSettingsModal;

init().catch(handleError);
