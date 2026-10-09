// PDF RAG Agent - frontend (vanilla JS modules, no build step)

import { ApiError, readServerSentEvents, request, setUnauthorizedHandler, uploadWithProgress } from "./api.js";
import { escapeHtml, renderMarkdown } from "./markdown.js";
import { fileExtensionFor, Recorder, voiceSupported } from "./voice.js";

const $ = (selector) => document.querySelector(selector);

const els = {
  auth: $("#auth"), authTitle: $("#auth-title"), authSub: $("#auth-sub"),
  tabLogin: $("#tab-login"), tabRegister: $("#tab-register"), authForm: $("#auth-form"),
  nameField: $("#name-field"), authName: $("#auth-name"), authEmail: $("#auth-email"),
  authPassword: $("#auth-password"), passwordHint: $("#password-hint"),
  authNote: $("#auth-note"), authError: $("#auth-error"), authSubmit: $("#auth-submit"),
  app: $("#app"), backdrop: $("#backdrop"), menuBtn: $("#menu-btn"),
  dropzone: $("#dropzone"), fileInput: $("#file-input"),
  uploadBar: $("#upload-bar"), uploadBarFill: $("#upload-bar-fill"),
  allDocs: $("#all-docs"), allDocsSub: $("#all-docs-sub"),
  docList: $("#doc-list"), docEmpty: $("#doc-empty"),
  userAvatar: $("#user-avatar"), userName: $("#user-name"), userEmail: $("#user-email"), logoutBtn: $("#logout-btn"),
  title: $("#doc-title"), meta: $("#doc-meta"), clearChat: $("#clear-chat"), content: $("#content"),
  composer: $("#composer"), attachBtn: $("#attach-btn"), question: $("#question"),
  recording: $("#recording"), recTime: $("#rec-time"), micBtn: $("#mic-btn"), sendBtn: $("#send-btn"),
  toast: $("#toast"),
};

const ALL = "all";
const POLL_MS = 2000;
const MAX_RECORDING_SECONDS = 60;
const MAX_UPLOAD_MB = Number(els.dropzone.dataset.maxMb || 200);
const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".txt", ".md", ".png", ".jpg", ".jpeg", ".webp"];
const KIND_LABELS = { pdf: "PDF", docx: "Word", text: "Text", image: "Photo" };
const KIND_BADGES = { pdf: "PDF", docx: "DOC", text: "TXT", image: "IMG" };
const SUGGESTIONS = {
  document: [
    "Summarize this document in 5 key points",
    "What are the main topics covered?",
    "List the important terms and their definitions",
  ],
  image: ["Describe this photo", "What text is written in this photo?", "Summarize the key details"],
  all: [
    "Give me a one-line summary of each document",
    "Which documents mention a date or deadline?",
    "Compare the main topics of my documents",
  ],
};
const ICONS = {
  trash: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/></svg>',
  plus: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
};

const state = {
  user: null,
  docs: [],
  activeId: null,        // a document id, ALL, or null (welcome)
  renderedView: null,    // what the content area shows, to avoid needless re-renders
  streaming: false,
  pollTimer: null,
  summaryRequested: new Set(),
  recorder: null,
  recordTimer: null,
  transcribing: false,
  authMode: "login",
};

// ---------- Small helpers --------------------------------------------------------

function formatBytes(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

function isInProgress(doc) {
  return doc.status === "queued" || doc.status === "processing";
}

function isUsable(doc) {
  return doc.status === "ready" && !doc.needs_reindex;
}

function storageKey() {
  return `pdf-rag-agent:active:${state.user?.id}`;
}

function storageGet() {
  try { return localStorage.getItem(storageKey()); } catch { return null; }
}

function storageSet(value) {
  try {
    if (value) localStorage.setItem(storageKey(), value);
    else localStorage.removeItem(storageKey());
  } catch { /* storage unavailable: ignore */ }
}

let toastTimer = null;
function toast(message, isError = false) {
  els.toast.textContent = message;
  els.toast.classList.toggle("error", isError);
  els.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { els.toast.hidden = true; }, isError ? 6000 : 3500);
}

// ---------- Accounts -----------------------------------------------------------------

function setAuthMode(mode, status = null) {
  state.authMode = mode;
  const register = mode === "register";
  const firstRun = status && !status.has_users;
  els.tabLogin.setAttribute("aria-selected", String(!register));
  els.tabRegister.setAttribute("aria-selected", String(register));
  els.nameField.hidden = !register;
  els.passwordHint.hidden = !register;
  els.authPassword.autocomplete = register ? "new-password" : "current-password";
  els.authTitle.textContent = register ? (firstRun ? "Create your account" : "Create an account") : "Welcome back";
  els.authSub.textContent = register
    ? "Your files and chats stay private to your account."
    : "Log in to chat with your documents.";
  els.authSubmit.textContent = register ? "Create account" : "Log in";
  const unclaimed = status?.unclaimed_documents || 0;
  els.authNote.hidden = !(register && firstRun && unclaimed);
  els.authNote.textContent = `Your ${plural(unclaimed, "existing document")} will be added to this first account.`;
  els.authError.hidden = true;
}

function showAuth(message = null, status = null) {
  clearTimeout(state.pollTimer);
  cancelRecording();
  state.user = null;
  els.app.hidden = true;
  els.auth.hidden = false;
  setAuthMode(status && !status.has_users ? "register" : state.authMode, status);
  if (message) {
    els.authError.textContent = message;
    els.authError.hidden = false;
  }
  (state.authMode === "register" ? els.authName : els.authEmail).focus();
}

async function submitAuth(event) {
  event.preventDefault();
  const register = state.authMode === "register";
  const payload = { email: els.authEmail.value.trim(), password: els.authPassword.value };
  if (register) payload.name = els.authName.value.trim();
  els.authSubmit.disabled = true;
  els.authError.hidden = true;
  try {
    const data = await request(`/auth/${register ? "register" : "login"}`, { method: "POST", json: payload });
    els.authPassword.value = "";
    await startApp(data.user, data.claimed_documents || 0);
  } catch (err) {
    els.authError.textContent = err.message;
    els.authError.hidden = false;
  } finally {
    els.authSubmit.disabled = false;
  }
}

async function logout() {
  try { await request("/auth/logout", { method: "POST" }); } catch { /* already logged out */ }
  state.docs = [];
  state.activeId = null;
  state.renderedView = null;
  showAuth(null, { has_users: true });
  setAuthMode("login", { has_users: true });
}

async function startApp(user, claimed = 0) {
  state.user = user;
  state.renderedView = null;
  els.auth.hidden = true;
  els.app.hidden = false;
  els.userAvatar.textContent = (user.name || user.email).trim().charAt(0).toUpperCase();
  els.userName.textContent = user.name;
  els.userEmail.textContent = user.email;
  state.activeId = storageGet();
  if (claimed) toast(`Welcome! ${plural(claimed, "existing document")} ${claimed === 1 ? "was" : "were"} added to your account.`);
  await refreshDocuments({ pickDefault: true });
}

// ---------- Documents ------------------------------------------------------------------

function activeDoc() {
  return state.activeId && state.activeId !== ALL ? state.docs.find((d) => d.id === state.activeId) || null : null;
}

function usableDocs() {
  return state.docs.filter(isUsable);
}

async function refreshDocuments({ pickDefault = false } = {}) {
  try {
    const { documents } = await request("/documents");
    state.docs = documents;
    if (state.activeId && state.activeId !== ALL && !activeDoc()) state.activeId = null;
    if (pickDefault && !state.activeId && usableDocs().length) state.activeId = ALL;
    renderSidebar();
    renderMain();
  } catch (err) {
    if (err.status !== 401) toast(err.message, true);
  }
  schedulePoll();
}

function schedulePoll() {
  clearTimeout(state.pollTimer);
  if (state.user && state.docs.some(isInProgress)) {
    state.pollTimer = setTimeout(refreshDocuments, POLL_MS);
  }
}

function selectScope(id) {
  cancelRecording();
  state.activeId = id;
  storageSet(id);
  renderSidebar();
  renderMain(true);
  els.app.classList.remove("sidebar-open");
}

async function deleteDocument(id) {
  const doc = state.docs.find((d) => d.id === id);
  if (!doc || !confirm(`Delete "${doc.filename}"? Its chat history will be removed too.`)) return;
  try {
    await request(`/documents/${id}`, { method: "DELETE" });
    if (state.activeId === id) state.activeId = null;
    storageSet(state.activeId);
    toast("Document deleted.");
    await refreshDocuments();
  } catch (err) {
    toast(err.message, true);
  }
}

async function reprocessDocument(id, button) {
  button.disabled = true;
  try {
    await request(`/documents/${id}/retry`, { method: "POST" });
    toast("Processing again…");
    await refreshDocuments();
  } catch (err) {
    button.disabled = false;
    toast(err.message, true);
  }
}

// ---------- Upload ---------------------------------------------------------------------

function setUploading(active, percent = 0) {
  els.dropzone.classList.toggle("busy", active);
  els.attachBtn.disabled = active;
  els.uploadBar.hidden = !active;
  els.uploadBarFill.style.width = `${percent}%`;
}

async function uploadFile(file) {
  if (!file) return;
  const name = file.name.toLowerCase();
  if (!ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext))) {
    toast("Unsupported file. Add a PDF, Word (.docx), text (.txt, .md) or photo (.png, .jpg, .webp).", true);
    return;
  }
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
    toast(`File is too large. The limit is ${MAX_UPLOAD_MB} MB.`, true);
    return;
  }
  setUploading(true, 0);
  try {
    const data = await uploadWithProgress(file, (percent) => setUploading(true, percent));
    toast(data.duplicate ? "You already added this file. Opening it." : "Added. Reading the file now…");
    await refreshDocuments();
    selectScope(data.document.id);
  } catch (err) {
    if (err.status !== 401) toast(err.message, true);
  } finally {
    setUploading(false);
  }
}

// ---------- Sidebar ----------------------------------------------------------------------

function docStatusText(doc) {
  if (doc.status === "ready") {
    if (doc.needs_reindex) return "Needs re-index";
    return doc.kind === "pdf"
      ? `${plural(doc.page_count, "page")} · ${formatBytes(doc.size_bytes)}`
      : `${KIND_LABELS[doc.kind]} · ${formatBytes(doc.size_bytes)}`;
  }
  if (doc.status === "queued") return "Queued…";
  if (doc.status === "processing") return `Reading ${doc.progress}%`;
  return "Failed";
}

function renderSidebar() {
  const usable = usableDocs().length;
  els.allDocs.classList.toggle("active", state.activeId === ALL);
  els.allDocsSub.textContent = usable ? `Ask across ${plural(usable, "file")}` : "Add files to ask across them";

  els.docEmpty.hidden = state.docs.length > 0;
  els.docList.innerHTML = state.docs.map((doc) => {
    const active = doc.id === state.activeId ? " active" : "";
    const icon = doc.kind === "image"
      ? `<img class="doc-thumb" src="/api/documents/${doc.id}/file" alt="" loading="lazy">`
      : KIND_BADGES[doc.kind] || "PDF";
    const bar = isInProgress(doc) ? `<span class="mini-bar"><span style="width:${doc.progress}%"></span></span>` : "";
    const warn = doc.status === "failed" || doc.needs_reindex ? " warn" : "";
    const del = isInProgress(doc) ? "" :
      `<button class="doc-delete" type="button" data-delete="${doc.id}" aria-label="Delete ${escapeHtml(doc.filename)}">${ICONS.trash}</button>`;
    return `
      <li class="doc-item${active}" data-id="${doc.id}" tabindex="0" role="button" aria-current="${active ? "true" : "false"}">
        <span class="doc-icon kind-${doc.kind}" aria-hidden="true">${icon}</span>
        <span class="doc-body">
          <span class="doc-name" title="${escapeHtml(doc.filename)}">${escapeHtml(doc.filename)}</span>
          <span class="doc-sub${warn}"><span>${docStatusText(doc)}</span>${bar}</span>
        </span>
        ${del}
      </li>`;
  }).join("");
}

// ---------- Main area --------------------------------------------------------------------

function scopeReady() {
  if (state.activeId === ALL) return usableDocs().length > 0;
  const doc = activeDoc();
  return Boolean(doc && isUsable(doc));
}

function renderHeader(doc) {
  if (state.activeId === ALL) {
    const count = usableDocs().length;
    els.title.textContent = "All documents";
    els.meta.textContent = count ? `Answers can come from any of your ${plural(count, "ready file")}` : "No files ready yet";
    return;
  }
  if (!doc) {
    els.title.textContent = state.user ? `Hi, ${state.user.name.split(" ")[0]}` : "Welcome";
    els.meta.textContent = "Add a document or photo to start asking questions";
    return;
  }
  els.title.textContent = doc.filename;
  if (doc.status === "ready" && !doc.needs_reindex) {
    const details = doc.kind === "image"
      ? ["Photo", "text and description extracted"]
      : doc.kind === "pdf"
        ? [plural(doc.page_count, "page"), `${plural(doc.chunk_count, "chunk")} indexed`]
        : [KIND_LABELS[doc.kind], plural(doc.page_count, "section"), `${plural(doc.chunk_count, "chunk")} indexed`];
    els.meta.textContent = [...details, formatBytes(doc.size_bytes)].join(" · ");
  } else if (doc.needs_reindex) {
    els.meta.textContent = "Needs re-index";
  } else if (doc.status === "failed") {
    els.meta.textContent = "Processing failed";
  } else {
    els.meta.textContent = doc.stage || "Reading the file…";
  }
}

function setComposerState() {
  const ready = scopeReady();
  const doc = activeDoc();
  els.question.disabled = !ready;
  els.micBtn.hidden = !voiceSupported;
  if (ready) {
    els.question.placeholder = state.activeId === ALL
      ? "Ask a question across all your files…"
      : doc.kind === "image" ? "Ask about this photo…" : "Ask a question about this document…";
  } else if (state.activeId === ALL || !doc) {
    els.question.placeholder = "Click + to add a PDF, Word file or photo";
  } else if (doc.status === "failed" || doc.needs_reindex) {
    els.question.placeholder = "Fix this file first, or click + to add another";
  } else {
    els.question.placeholder = "Reading the file… you can ask once it's ready";
  }
  updateComposer();
}

function updateComposer() {
  const busy = state.streaming || state.transcribing;
  els.sendBtn.disabled = busy || els.question.disabled || !els.question.value.trim();
  els.micBtn.disabled = state.transcribing || (!state.recorder && (els.question.disabled || state.streaming));
}

function renderMain(force = false) {
  const doc = activeDoc();
  renderHeader(doc);

  let view = "welcome";
  if (state.activeId === ALL) view = `all:${scopeReady()}`;
  else if (doc) view = `${doc.id}:${doc.status}:${doc.needs_reindex}`;

  if (!force && view === state.renderedView) {
    if (doc && isInProgress(doc)) updateProgress(doc);
    return;
  }
  state.renderedView = view;
  els.clearChat.hidden = !scopeReady();
  setComposerState();

  if (state.activeId === ALL) renderAllChat();
  else if (!doc) renderWelcome();
  else if (doc.needs_reindex) renderNeedsReindex(doc);
  else if (doc.status === "ready") renderDocumentChat(doc);
  else if (doc.status === "failed") renderFailed(doc);
  else renderProcessing(doc);
}

function renderWelcome() {
  const hasDocs = state.docs.length > 0;
  els.content.innerHTML = `
    <section class="panel">
      <h2>${hasDocs ? "Pick a file, or ask across all of them" : "Chat with your documents and photos"}</h2>
      <p class="lead">Add a PDF, Word file, text file or photo, then ask questions in English or Hinglish, by typing or by voice. Every answer comes from your files, with page numbers so you can check it.</p>
      <button class="btn btn-primary btn-lg" type="button" data-action="upload">${ICONS.plus} Add a file</button>
      <div class="steps">
        <div class="step"><div class="step-num">1</div><h3>Add</h3><p>A PDF (even a 600-page book or a scanned one), a Word or text file, or a photo of notes, a receipt or a chart.</p></div>
        <div class="step"><div class="step-num">2</div><h3>It reads it</h3><p>Text is extracted (photos and scanned pages are read by a vision AI), summarised and indexed for search.</p></div>
        <div class="step"><div class="step-num">3</div><h3>Ask</h3><p>Type or speak. Ask one file, or pick <strong>All documents</strong> to search everything at once.</p></div>
      </div>
    </section>`;
}

function renderProcessing(doc) {
  const photo = doc.kind === "image";
  els.content.innerHTML = `
    <section class="panel">
      <div class="status-card">
        <h2>${photo ? "Reading your photo" : "Reading your document"}</h2>
        <p>${photo
          ? "A vision AI is reading the text in the photo and describing it. This usually takes a few seconds."
          : "Extracting text, reading any scanned pages, and preparing it for search. Big files can take a few minutes; you can keep using the app."}</p>
        <div class="big-bar"><span id="progress-fill" style="width:${doc.progress}%"></span></div>
        <div class="progress-row"><span id="progress-stage"></span><span id="progress-text"></span></div>
      </div>
    </section>`;
  updateProgress(doc);
}

function updateProgress(doc) {
  const fill = document.getElementById("progress-fill");
  if (!fill) return;
  fill.style.width = `${doc.progress}%`;
  document.getElementById("progress-stage").textContent = doc.status === "queued" ? "Waiting to start…" : doc.stage || "Working…";
  document.getElementById("progress-text").textContent = `${doc.progress}%`;
}

function renderFailed(doc) {
  els.content.innerHTML = `
    <section class="panel">
      <div class="status-card failed">
        <h2>Couldn't process this file</h2>
        <p>${escapeHtml(doc.error || "Unknown error.")}</p>
        <div class="actions">
          <button class="btn btn-primary" type="button" data-action="reprocess">Retry</button>
          <button class="btn btn-danger" type="button" data-action="delete">Delete</button>
          <button class="btn btn-ghost" type="button" data-action="upload">Add another file</button>
        </div>
      </div>
    </section>`;
}

function renderNeedsReindex(doc) {
  els.content.innerHTML = `
    <section class="panel">
      <div class="status-card notice">
        <h2>Re-index needed</h2>
        <p><strong>${escapeHtml(doc.filename)}</strong> was indexed with a different AI embedding model than the one this server uses now, so it can't be searched until it is indexed again. This reuses the file you already uploaded.</p>
        <div class="actions">
          <button class="btn btn-primary" type="button" data-action="reprocess">Re-index now</button>
          <button class="btn btn-ghost" type="button" data-action="delete">Delete</button>
        </div>
      </div>
    </section>`;
}

function messagesEl() {
  return document.getElementById("messages");
}

function summaryCard(doc) {
  if (doc.summary) {
    return `
      <details class="summary-card" open>
        <summary>About this ${doc.kind === "image" ? "photo" : "document"}</summary>
        <p>${escapeHtml(doc.summary)}</p>
      </details>`;
  }
  return '<div class="summary-card pending" id="summary-pending"><span class="typing"><span></span><span></span><span></span></span> Writing a short summary…</div>';
}

function renderDocumentChat(doc) {
  const url = `/api/documents/${doc.id}/file`;
  const preview = doc.kind === "image"
    ? `<figure class="image-preview"><a href="${url}" target="_blank" rel="noopener" title="Open full size"><img src="${url}" alt="${escapeHtml(doc.filename)}"></a></figure>`
    : "";
  els.content.innerHTML = `${preview}<div class="intro">${summaryCard(doc)}</div><div class="messages" id="messages"></div>`;
  if (!doc.summary) requestSummary(doc);
  loadMessages();
}

async function requestSummary(doc) {
  if (state.summaryRequested.has(doc.id)) {
    document.getElementById("summary-pending")?.remove();
    return;
  }
  state.summaryRequested.add(doc.id);
  try {
    const { document: updated } = await request(`/documents/${doc.id}/summary`, { method: "POST" });
    const index = state.docs.findIndex((d) => d.id === updated.id);
    if (index >= 0) state.docs[index] = updated;
    const pending = document.getElementById("summary-pending");
    if (pending && state.activeId === updated.id) {
      pending.outerHTML = summaryCard(updated);
      const empty = document.getElementById("chat-empty");
      if (empty) renderChatEmpty(); // show the new suggested questions
    }
  } catch {
    document.getElementById("summary-pending")?.remove();
  }
}

function renderAllChat() {
  const docs = usableDocs();
  if (!docs.length) {
    els.content.innerHTML = `
      <section class="panel">
        <div class="status-card">
          <h2>Ask across all your files</h2>
          <p>Once you add files, you can ask one question and get an answer from whichever file has it, with the file name and page for every fact.</p>
          <div class="actions"><button class="btn btn-primary" type="button" data-action="upload">${ICONS.plus} Add a file</button></div>
        </div>
      </section>`;
    return;
  }
  const chips = docs.slice(0, 12)
    .map((d) => `<span class="file-chip" title="${escapeHtml(d.filename)}">${escapeHtml(d.filename)}</span>`)
    .join("");
  const more = docs.length > 12 ? `<span class="file-chip muted">+${docs.length - 12} more</span>` : "";
  els.content.innerHTML = `
    <div class="intro">
      <div class="summary-card">
        <p class="intro-title">Searching ${plural(docs.length, "file")}</p>
        <div class="file-chips">${chips}${more}</div>
      </div>
    </div>
    <div class="messages" id="messages"></div>`;
  loadMessages();
}

function scopePaths() {
  return state.activeId === ALL
    ? { messages: "/chat/messages", chat: "/chat" }
    : { messages: `/documents/${state.activeId}/messages`, chat: `/documents/${state.activeId}/chat` };
}

async function loadMessages() {
  const scope = state.activeId;
  try {
    const { messages } = await request(scopePaths().messages);
    const list = messagesEl();
    if (state.activeId !== scope || !list) return; // switched meanwhile
    list.innerHTML = "";
    if (!messages.length) {
      renderChatEmpty();
      return;
    }
    for (const message of messages) {
      if (message.role === "user") {
        addUserMessage(message.content);
      } else {
        const el = createAssistantMessage();
        setAssistantContent(el, message.content, false);
        renderSources(el, message.sources);
      }
    }
    scrollToBottom();
  } catch (err) {
    if (err.status !== 401) toast(err.message, true);
  }
}

function renderChatEmpty() {
  const doc = activeDoc();
  let suggestions = SUGGESTIONS.all;
  let heading = "Ask anything across your files";
  if (doc) {
    suggestions = doc.suggested_questions?.length ? doc.suggested_questions : SUGGESTIONS[doc.kind === "image" ? "image" : "document"];
    heading = "What would you like to know?";
  }
  messagesEl().innerHTML = `
    <div class="chat-empty" id="chat-empty">
      <h2>${heading}</h2>
      <p>Try one of these, type your own, or tap the mic to ask by voice.</p>
      <div class="suggestions">
        ${suggestions.map((s) => `<button type="button" class="suggestion">${escapeHtml(s)}</button>`).join("")}
      </div>
    </div>`;
}

// ---------- Messages -----------------------------------------------------------------------

function addUserMessage(text) {
  const el = document.createElement("div");
  el.className = "msg msg-user";
  el.textContent = text;
  messagesEl().appendChild(el);
}

function createAssistantMessage() {
  const el = document.createElement("div");
  el.className = "msg msg-assistant";
  el.innerHTML = '<div class="msg-body"><span class="typing"><span></span><span></span><span></span></span></div>';
  messagesEl().appendChild(el);
  return el;
}

function setAssistantContent(el, text, streaming) {
  const body = el.querySelector(".msg-body");
  body.innerHTML = renderMarkdown(text);
  body.classList.toggle("cursor", streaming);
}

function showSearchQuery(el, query) {
  const note = document.createElement("div");
  note.className = "msg-search";
  note.textContent = `Searched for: “${query}”`;
  el.prepend(note);
}

function sourceKey(source) {
  return `${source.doc_id}:${source.page}`;
}

function sourceLabel(source, multiFile) {
  if (source.kind === "image") return source.filename;
  return multiFile ? `${source.filename} · p. ${source.page}` : `p. ${source.page}`;
}

function renderSources(el, sources) {
  if (!sources?.length) return;
  const multiFile = new Set(sources.map((s) => s.doc_id)).size > 1 || state.activeId === ALL;
  // A photo on its own is already shown above the chat.
  if (!multiFile && sources.every((s) => s.kind === "image")) return;
  el.sources = sources;
  const wrap = document.createElement("div");
  wrap.className = "sources";
  wrap.innerHTML = `
    <div class="sources-label">Sources</div>
    <div class="source-chips">
      ${sources.map((s) => `<button type="button" class="source-chip" data-key="${sourceKey(s)}" title="${escapeHtml(s.filename)}">${escapeHtml(sourceLabel(s, multiFile))}</button>`).join("")}
    </div>
    <div class="source-snippet" hidden></div>`;
  el.appendChild(wrap);
}

// Models sometimes write file names with typographic characters (a
// non-breaking hyphen in "offsite‑plan.docx"), so compare normalised names.
function sameName(a, b) {
  const normalise = (name) => name.normalize("NFKC").replace(/[‐-―−]/g, "-").replace(/\s+/g, " ").toLowerCase();
  return normalise(a) === normalise(b);
}

function findSource(msgEl, { key, file, page }) {
  const sources = msgEl.sources || [];
  if (key) return sources.find((s) => sourceKey(s) === key);
  return sources.find((s) =>
    (!file || sameName(escapeHtml(s.filename), file)) && (!page || String(s.page) === String(page)));
}

function showSource(msgEl, match, toggle) {
  const snippet = msgEl.querySelector(".source-snippet");
  const source = findSource(msgEl, match);
  if (!snippet || !source) return;
  const key = sourceKey(source);
  const isOpen = !snippet.hidden && snippet.dataset.key === key;
  const open = !(toggle && isOpen);
  msgEl.querySelectorAll(".source-chip").forEach((chip) => chip.classList.toggle("open", open && chip.dataset.key === key));
  snippet.hidden = !open;
  if (!open) return;
  snippet.dataset.key = key;
  const where = source.kind === "pdf" ? `Page ${source.page}` : source.kind === "image" ? "Photo" : `Section ${source.page}`;
  const link = source.kind === "pdf"
    ? ` <a class="open-page" href="/api/documents/${source.doc_id}/file#page=${source.page}" target="_blank" rel="noopener">Open page ${source.page} ↗</a>`
    : ` <a class="open-page" href="/api/documents/${source.doc_id}/file" target="_blank" rel="noopener">Open file ↗</a>`;
  const file = state.activeId === ALL ? `${escapeHtml(source.filename)} — ` : "";
  snippet.innerHTML = `<strong>${file}${where}</strong> · ${escapeHtml(source.snippet)}${link}`;
}

function isNearBottom() {
  const c = els.content;
  return c.scrollHeight - c.scrollTop - c.clientHeight < 160;
}

function scrollToBottom() {
  els.content.scrollTop = els.content.scrollHeight;
}

async function ask(rawQuestion) {
  const question = rawQuestion.trim();
  if (!question || state.streaming || !scopeReady()) return;
  const scope = state.activeId;

  state.streaming = true;
  updateComposer();
  document.getElementById("chat-empty")?.remove();
  addUserMessage(question);
  const el = createAssistantMessage();
  scrollToBottom();

  let text = "";
  let sources = [];
  let finished = false;
  let frame = null;
  const paint = () => {
    frame = null;
    const stick = isNearBottom();
    setAssistantContent(el, text, true);
    if (stick) scrollToBottom();
  };

  try {
    const res = await request(scopePaths().chat, { method: "POST", json: { question }, raw: true });
    await readServerSentEvents(res, (event, data) => {
      if (event === "sources") {
        sources = data.sources;
        if (data.search_query && data.search_query !== question) showSearchQuery(el, data.search_query);
      } else if (event === "token") {
        text += data.text;
        if (!frame) frame = requestAnimationFrame(paint);
      } else if (event === "done") {
        finished = true;
      } else if (event === "error") {
        throw new ApiError(data.message, 500);
      }
    });
    if (!finished) throw new ApiError("The connection was interrupted. Please try again.", 0);
    if (frame) cancelAnimationFrame(frame);
    if (state.activeId === scope) {
      const stick = isNearBottom();
      setAssistantContent(el, text, false);
      renderSources(el, sources);
      if (stick) scrollToBottom();
    }
  } catch (err) {
    if (frame) cancelAnimationFrame(frame);
    el.classList.add("error");
    const body = el.querySelector(".msg-body");
    body.innerHTML = `<p>${escapeHtml(err.message)}</p>`;
    body.classList.remove("cursor");
  } finally {
    state.streaming = false;
    updateComposer();
  }
}

async function clearChat() {
  if (state.streaming || !confirm("Clear this conversation?")) return;
  try {
    await request(scopePaths().messages, { method: "DELETE" });
    renderMain(true);
  } catch (err) {
    toast(err.message, true);
  }
}

// ---------- Voice ------------------------------------------------------------------------

function setMicState(mode) {
  els.micBtn.classList.toggle("is-recording", mode === "recording");
  els.micBtn.classList.toggle("is-busy", mode === "transcribing");
  els.micBtn.setAttribute("aria-label", mode === "recording" ? "Stop recording" : "Ask with your voice");
  els.recording.hidden = mode !== "recording";
  els.question.hidden = mode === "recording";
  if (mode === "transcribing") els.question.placeholder = "Turning your voice into text…";
}

async function toggleRecording() {
  if (state.transcribing) return;
  if (state.recorder) {
    await finishRecording();
    return;
  }
  const recorder = new Recorder();
  try {
    await recorder.start();
  } catch (err) {
    toast(err.name === "NotAllowedError"
      ? "Microphone access is blocked. Allow it from the icon in the address bar, then try again."
      : "Couldn't start the microphone. Check that one is connected.", true);
    return;
  }
  state.recorder = recorder;
  setMicState("recording");
  updateComposer();
  els.recTime.textContent = "0:00";
  state.recordTimer = setInterval(() => {
    const s = recorder.seconds;
    els.recTime.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
    if (s >= MAX_RECORDING_SECONDS) finishRecording();
  }, 250);
}

function cancelRecording() {
  if (!state.recorder) return;
  clearInterval(state.recordTimer);
  state.recorder.cancel();
  state.recorder = null;
  setMicState("idle");
  setComposerState();
}

async function finishRecording() {
  const recorder = state.recorder;
  if (!recorder) return;
  state.recorder = null;
  clearInterval(state.recordTimer);
  state.transcribing = true;
  setMicState("transcribing");
  updateComposer();
  try {
    const blob = await recorder.stop();
    if (recorder.seconds < 1 || blob.size < 2000) {
      throw new ApiError("That was too short. Click the mic, ask your question, then click it again.", 0);
    }
    const form = new FormData();
    form.append("audio", blob, `question.${fileExtensionFor(blob.type)}`);
    const { text } = await request("/transcribe", { method: "POST", body: form });
    els.question.value = els.question.value.trim() ? `${els.question.value.trim()} ${text}` : text;
    toast("Check the text, then press Enter to ask.");
  } catch (err) {
    if (err.status !== 401) toast(err.message, true);
  } finally {
    state.transcribing = false;
    setMicState("idle");
    setComposerState();
    autoGrow();
    els.question.focus();
  }
}

// ---------- Composer ---------------------------------------------------------------------

function autoGrow() {
  els.question.style.height = "auto";
  els.question.style.height = `${Math.min(els.question.scrollHeight, 180)}px`;
}

// ---------- Events -----------------------------------------------------------------------

els.tabLogin.addEventListener("click", () => setAuthMode("login"));
els.tabRegister.addEventListener("click", () => setAuthMode("register"));
els.authForm.addEventListener("submit", submitAuth);
els.logoutBtn.addEventListener("click", logout);

els.fileInput.addEventListener("change", () => {
  uploadFile(els.fileInput.files[0]);
  els.fileInput.value = ""; // allow picking the same file again
});
els.dropzone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    els.fileInput.click();
  }
});
els.attachBtn.addEventListener("click", () => els.fileInput.click());

// Accept drops anywhere on the page, and stop the browser from opening the file.
let dragDepth = 0;
window.addEventListener("dragenter", (e) => {
  e.preventDefault();
  dragDepth += 1;
  els.dropzone.classList.add("dragover");
});
window.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) els.dropzone.classList.remove("dragover");
});
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0;
  els.dropzone.classList.remove("dragover");
  if (state.user) uploadFile(e.dataTransfer.files[0]);
});

els.allDocs.addEventListener("click", () => selectScope(ALL));
els.docList.addEventListener("click", (e) => {
  const del = e.target.closest("[data-delete]");
  if (del) {
    e.stopPropagation();
    deleteDocument(del.dataset.delete);
    return;
  }
  const item = e.target.closest(".doc-item");
  if (item) selectScope(item.dataset.id);
});
els.docList.addEventListener("keydown", (e) => {
  const item = e.target.closest(".doc-item");
  if (item && e.target === item && (e.key === "Enter" || e.key === " ")) {
    e.preventDefault();
    selectScope(item.dataset.id);
  }
});

els.content.addEventListener("click", (e) => {
  const chip = e.target.closest(".source-chip");
  const cite = e.target.closest(".cite");
  const suggestion = e.target.closest(".suggestion");
  const action = e.target.closest("[data-action]")?.dataset.action;
  if (chip) showSource(chip.closest(".msg"), { key: chip.dataset.key }, true);
  else if (cite) showSource(cite.closest(".msg"), { file: cite.dataset.file, page: cite.dataset.page }, false);
  else if (suggestion) ask(suggestion.textContent);
  else if (action === "upload") els.fileInput.click();
  else if (action === "delete") deleteDocument(state.activeId);
  else if (action === "reprocess") reprocessDocument(state.activeId, e.target.closest("[data-action]"));
});

els.composer.addEventListener("submit", (e) => {
  e.preventDefault();
  const question = els.question.value;
  if (!question.trim() || state.streaming) return;
  els.question.value = "";
  autoGrow();
  ask(question);
});
els.question.addEventListener("input", () => {
  autoGrow();
  updateComposer();
});
els.question.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    els.composer.requestSubmit();
  }
});
els.micBtn.addEventListener("click", toggleRecording);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && state.recorder) cancelRecording();
});

els.clearChat.addEventListener("click", clearChat);
els.menuBtn.addEventListener("click", () => els.app.classList.toggle("sidebar-open"));
els.backdrop.addEventListener("click", () => els.app.classList.remove("sidebar-open"));

// ---------- Start --------------------------------------------------------------------------

setUnauthorizedHandler((message) => showAuth(message || "Your session has ended. Please log in again.", { has_users: true }));

(async function boot() {
  try {
    const status = await request("/auth/status");
    if (status.user) await startApp(status.user);
    else showAuth(null, status);
  } catch (err) {
    showAuth(err.message);
  }
})();
