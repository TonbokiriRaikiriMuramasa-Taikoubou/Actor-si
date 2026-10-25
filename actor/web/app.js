/* Actor — Web UI (vanilla JS, no build step) */
'use strict';

// ====================================================================
// 状態
// ====================================================================
const state = {
  config: null,
  skills: [],
  skillDirs: [],
  sessions: [],
  sessionId: null,
  pinned: new Set(),
  messages: [],           // {role, content, meta}
  auto: true,
  think: false,
  models: [],
  model: '',
  selected: null,         // 直近の選択結果
  system: '',             // 直近の system_instruction
  usage: null,
  notices: [],            // 429 / モデル切替の知らせ
  busy: false,
  abort: null,
  detail: null,
  rooms: [],
  room: null,        // 開いているルーム名
  roomCursor: 0,
  roomTimer: null,
  roomToken: localStorage.getItem('actor.roomToken') || '',
  apiAuthPrompted: false,
  attachments: [],   // {name, mime_type, size, data}
  toolEvents: [],    // 直近ターンの tool_call / tool_result
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== false) node.setAttribute(k, v);
  }
  for (const child of children.flat()) {
    if (child == null) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
};
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (n) => (n == null ? '-' : Number(n).toLocaleString('ja-JP'));

function toast(message, kind = '') {
  const node = $('#toast');
  node.textContent = message;
  node.className = `toast ${kind}`;
  clearTimeout(node._t);
  node._t = setTimeout(() => node.classList.add('hidden'), 2600);
}

function authHeaders(extra = {}) {
  const headers = { 'Content-Type': 'application/json', ...extra };
  if (state.roomToken) headers['X-Actor-Token'] = state.roomToken;
  return headers;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: authHeaders(options.headers || {}),
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { error: text }; }
  if (!res.ok) {
    const error = new Error((data && data.error) || `HTTP ${res.status}`);
    error.status = res.status;
    throw error;
  }
  return data;
}

// ====================================================================
// 添付（画像 / PDF）
// ====================================================================
const ATTACH_LIMIT_MB = 10;

function fmtSize(bytes) {
  if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)}MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)}KB`;
  return `${bytes}B`;
}

function renderAttachments() {
  const wrap = $('#attach-chips');
  wrap.innerHTML = '';
  if (!state.attachments.length) {
    wrap.classList.add('hidden');
    return;
  }
  wrap.classList.remove('hidden');
  state.attachments.forEach((file, index) => {
    const item = el('span', { class: 'attach-chip' },
      file.mime_type.startsWith('image/')
        ? el('img', { class: 'thumb', src: `data:${file.mime_type};base64,${file.data}`, alt: file.name })
        : null,
      el('span', { class: 'name' }, `${file.name}（${fmtSize(file.size)}）`),
      el('button', { title: '外す', onclick: () => { state.attachments.splice(index, 1); renderAttachments(); } }, '✕'),
    );
    wrap.append(item);
  });
}

function readFiles(fileList) {
  for (const file of fileList) {
    if (state.attachments.length >= 6) { toast('添付は 6 件までです', 'err'); break; }
    if (file.size > ATTACH_LIMIT_MB * 1048576) {
      toast(`${file.name} が大きすぎます（上限 ${ATTACH_LIMIT_MB}MB）`, 'err');
      continue;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = String(reader.result || '');
      const comma = dataUrl.indexOf(',');
      if (comma < 0) return;
      state.attachments.push({
        name: file.name,
        mime_type: file.type || 'application/octet-stream',
        size: file.size,
        data: dataUrl.slice(comma + 1),
      });
      renderAttachments();
    };
    reader.readAsDataURL(file);
  }
}

// ====================================================================
// Markdown（軽量レンダラ）
// ====================================================================
function renderMarkdown(src) {
  const blocks = [];
  let text = String(src ?? '').replace(/\r\n?/g, '\n');
  // コードブロックを退避（閉じフェンスは開きと同じ長さ以上を要求 → ネストした ``` に強い）
  text = text.replace(/(^|\n)(`{3,})([a-zA-Z0-9_+\-]*)[ \t]*\n([\s\S]*?)\n?\2[ \t]*(?=\n|$)/g,
    (_, lead, fence, lang, code) => {
      blocks.push(`<pre><code class="lang-${esc(lang)}">${esc(code.replace(/\n$/, ''))}</code></pre>`);
      return `${lead}\u0000B${blocks.length - 1}\u0000`;
    });
  // 構造判定は生テキストで行い、インライン要素だけをエスケープして組み立てる
  const lines = text.split('\n');
  const out = [];
  let i = 0;
  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

  while (i < lines.length) {
    const line = lines[i];
    if (/^\u0000B\d+\u0000$/.test(line.trim())) { out.push(blocks[+line.trim().match(/\d+/)[0]]); i++; continue; }
    if (!line.trim()) { i++; continue; }
    let m;
    if ((m = line.match(/^(#{1,6})\s+(.*)$/))) { const lvl = m[1].length; out.push(`<h${lvl}>${inline(m[2])}</h${lvl}>`); i++; continue; }
    if (/^\s*(?:---+|\*\*\*+)\s*$/.test(line)) { out.push('<hr>'); i++; continue; }
    if (/^>\s?/.test(line)) {
      const buf = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) { buf.push(lines[i].replace(/^>\s?/, '')); i++; }
      out.push(`<blockquote>${inline(buf.join('<br>'))}</blockquote>`);
      continue;
    }
    // テーブル
    if (line.includes('|') && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1])) {
      const head = splitRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes('|')) { rows.push(splitRow(lines[i])); i++; }
      out.push(
        `<table><thead><tr>${head.map((h) => `<th>${inline(h)}</th>`).join('')}</tr></thead>` +
        `<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table>`
      );
      continue;
    }
    // リスト
    if (/^\s*(?:[-*+]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const items = [];
      while (i < lines.length && /^\s*(?:[-*+]|\d+[.)])\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*(?:[-*+]|\d+[.)])\s+/, ''));
        i++;
      }
      const tag = ordered ? 'ol' : 'ul';
      out.push(`<${tag}>${items.map((it) => `<li>${inline(it)}</li>`).join('')}</${tag}>`);
      continue;
    }
    // 段落
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|>|\s*(?:[-*+]|\d+[.)])\s|\u0000B)/.test(lines[i]) && !lines[i].includes('|')) {
      para.push(lines[i]); i++;
    }
    if (para.length) out.push(`<p>${inline(para.join('<br>'))}</p>`);
    else { out.push(`<p>${inline(lines[i])}</p>`); i++; }
  }
  return out.join('\n');
}
function splitRow(line) {
  return line.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map((c) => c.trim());
}

// ====================================================================
// サイドバー: スキル / セッション
// ====================================================================
async function loadSkills(query = '') {
  const data = await api(`/api/skills${query ? `?q=${encodeURIComponent(query)}` : ''}`);
  state.skills = data.skills || [];
  state.skillDirs = data.dirs || [];
  state.errors = data.errors || [];
  state.matches = Object.fromEntries((data.matches || []).map((m) => [m.name, m.score]));
  renderSkills();
  $('#skill-dirs').textContent = state.skillDirs.join('\n');
}

function renderSkills() {
  const list = $('#skill-list');
  list.innerHTML = '';
  if (!state.skills.length) {
    list.append(el('li', {}, el('div', { class: 'desc', html: 'スキルがありません。<br><code>skills/&lt;name&gt;/SKILL.md</code> を置くか、下のボタンで生成してください。' })));
    return;
  }
  const sorted = [...state.skills].sort((a, b) => (state.matches[b.name] || 0) - (state.matches[a.name] || 0) || a.name.localeCompare(b.name));
  for (const skill of sorted) {
    const badges = [];
    if (skill.references.length) badges.push(el('span', { class: 'badge' }, `参照 ${skill.references.length}`));
    if (skill.version) badges.push(el('span', { class: 'badge' }, `v${skill.version}`));
    if (skill.tags.length) badges.push(el('span', { class: 'badge acc' }, skill.tags[0]));
    if (skill.warnings?.length) badges.push(el('span', { class: 'badge warn' }, '⚠ 要確認'));
    const score = state.matches[skill.name];
    const item = el('li', {
      class: state.pinned.has(skill.name) ? 'active' : '',
      title: 'クリックで固定適用 / ⓘ で詳細',
      onclick: (e) => { if (e.target.tagName !== 'BUTTON') togglePin(skill.name); },
    },
      el('div', { class: 'name' },
        `/${skill.name}`,
        score ? el('span', { class: 'badge warn' }, score.toFixed(1)) : null,
        el('span', { class: 'spacer' }),
        el('button', { class: 'link-btn', title: '詳細', onclick: (e) => { e.stopPropagation(); openDetail(skill.name); } }, 'ⓘ'),
      ),
      el('div', { class: 'desc' }, skill.description || '(説明なし)'),
      badges.length ? el('div', { class: 'badges' }, badges) : null,
    );
    list.append(item);
  }
}

function togglePin(name) {
  if (state.pinned.has(name)) state.pinned.delete(name);
  else state.pinned.add(name);
  renderSkills();
  renderPinned();
}

function renderPinned() {
  const wrap = $('#pinned-chips');
  wrap.innerHTML = '';
  if (!state.pinned.size) {
    wrap.append(el('span', { class: 'muted small' }, state.auto ? 'スキルは自動選択されます' : '自動選択オフ：スキル未指定'));
    return;
  }
  for (const name of state.pinned) {
    wrap.append(el('span', { class: 'chip' }, `/${name}`,
      el('button', { title: '解除', onclick: () => togglePin(name) }, '✕')));
  }
}

async function loadSessions() {
  const data = await api('/api/sessions');
  state.sessions = data.sessions || [];
  const list = $('#session-list');
  list.innerHTML = '';
  if (!state.sessions.length) {
    list.append(el('li', {}, el('div', { class: 'desc' }, 'まだ会話がありません。')));
    return;
  }
  for (const session of state.sessions) {
    const item = el('li', { class: session.id === state.sessionId ? 'active' : '' },
      el('div', { class: 'name' },
        session.title,
        el('span', { class: 'spacer' }),
        el('button', { class: 'link-btn', title: '削除', onclick: async (e) => {
          e.stopPropagation();
          await api(`/api/sessions/${session.id}`, { method: 'DELETE' });
          if (state.sessionId === session.id) newSession();
          await loadSessions();
        } }, '🗑'),
      ),
      el('div', { class: 'desc' }, session.preview || '(空)'),
      el('div', { class: 'badges' },
        el('span', { class: 'badge' }, `${session.turn_count} turn`),
        ...(session.skills || []).map((s) => el('span', { class: 'badge acc' }, `/${s}`)),
        el('span', { class: 'badge' }, (session.updated_at || '').replace('T', ' ').slice(5, 16)),
      ),
    );
    item.addEventListener('click', () => openSession(session.id));
    list.append(item);
  }
}

async function openSession(id) {
  const data = await api(`/api/sessions/${id}`);
  const session = data.session;
  state.sessionId = session.id;
  state.messages = session.messages.map((m) => ({ role: m.role, content: m.content, meta: m }));
  state.pinned = new Set(session.skills || []);
  renderPinned();
  renderSkills();
  renderMessages();
  await loadSessions();
  toast(`セッション「${session.title}」を読み込みました`);
}

function newSession() {
  state.sessionId = null;
  state.messages = [];
  state.selected = null;
  state.system = '';
  state.usage = null;
  state.pinned = new Set();
  renderPinned();
  renderSkills();
  renderMessages();
  renderInspector();
  $('#input').focus();
}

// ====================================================================
// ルーム（常駐ボットとの会話）
// ====================================================================
async function loadRooms() {
  const data = await api('/api/rooms');
  state.rooms = data.rooms || [];
  const list = $('#room-list');
  list.innerHTML = '';
  if (!state.rooms.length) {
    list.append(el('li', {}, el('div', { class: 'desc' }, 'まだルームがありません。上で名前を入れて ＋ を押すと作成できます。')));
    return;
  }
  for (const room of state.rooms) {
    const item = el('li', { class: room.name === state.room ? 'active' : '' },
      el('div', { class: 'name' }, `#${room.name}`,
        el('span', { class: 'spacer' }),
        el('button', { class: 'link-btn', title: '削除', onclick: async (e) => {
          e.stopPropagation();
          if (!confirm(`ルーム「${room.name}」を削除しますか？`)) return;
          await api(`/api/rooms/${encodeURIComponent(room.name)}`, { method: 'DELETE' });
          if (state.room === room.name) leaveRoom();
          await loadRooms();
        } }, '🗑')),
      el('div', { class: 'desc' }, room.preview || '(発言なし)'),
      el('div', { class: 'badges' },
        el('span', { class: 'badge' }, `${room.count} 件`),
        ...(room.skills || []).map((s) => el('span', { class: 'badge acc' }, `/${s}`)),
        el('span', { class: 'badge' }, (room.updated_at || '').slice(5, 16).replace('T', ' '))),
    );
    item.addEventListener('click', () => openRoom(room.name));
    list.append(item);
  }
}

async function createRoom() {
  const input = $('#room-name');
  const name = input.value.trim();
  if (!name) return toast('ルーム名を入れてください', 'err');
  try {
    const data = await api('/api/rooms', { method: 'POST', body: { name, skills: [...state.pinned] } });
    input.value = '';
    toast(`ルーム #${data.room.name} を開きました`);
    await loadRooms();
    openRoom(data.room.name);
  } catch (err) { toast(err.message, 'err'); }
}

async function openRoom(name) {
  stopRoomPolling();
  state.room = name;
  state.roomCursor = 0;
  $('#room-banner').classList.remove('hidden');
  $('#room-banner-name').textContent = `#${name}`;
  if (state.config?.room_token_required) $('#room-token').classList.remove('hidden');
  $('#room-token').value = state.roomToken;
  $('#room-banner-status').textContent = '常駐ボットの返事を待っています…';
  $('#messages').innerHTML = '';
  await loadRooms();
  await pollRoom();
  state.roomTimer = setInterval(pollRoom, 1500);
  $('#input').focus();
}

function leaveRoom() {
  stopRoomPolling();
  state.room = null;
  $('#room-banner').classList.add('hidden');
  renderMessages();
  loadRooms();
}

function stopRoomPolling() {
  if (state.roomTimer) clearInterval(state.roomTimer);
  state.roomTimer = null;
}

async function pollRoom() {
  if (!state.room) return;
  let data;
  try {
    data = await api(`/api/rooms/${encodeURIComponent(state.room)}?after=${state.roomCursor}`);
  } catch (err) {
    stopRoomPolling();
    toast(`ルームを取得できません: ${err.message}`, 'err');
    return;
  }
  const messages = data.messages || [];
  if (!messages.length) return;
  const wrap = $('#messages');
  if (wrap.querySelector('.empty')) wrap.innerHTML = '';
  for (const message of messages) {
    state.roomCursor = Math.max(state.roomCursor, message.seq);
    const node = buildMessageNode({
      role: message.role,
      content: message.text,
      meta: {
        skills: message.skills, model: message.model, usage: message.usage,
        simulated: message.simulated, author: message.author, ts: message.ts,
        tools: message.tools, attachments: message.attachments,
      },
    });
    if (message.role === 'user') node.classList.add('room-user');
    wrap.append(node);
  }
  scrollToBottom();
  const last = messages[messages.length - 1];
  $('#room-banner-status').textContent =
    `${data.count} 件 / 最終 ${(last.ts || '').slice(11, 19)}（${last.author || last.role}）`;
}

async function sendToRoom(text) {
  const name = state.room;
  const attachments = state.attachments.slice();
  state.attachments = [];
  renderAttachments();
  try {
    await api(`/api/rooms/${encodeURIComponent(name)}/messages`, {
      method: 'POST',
      body: {
        text, author: 'あなた', skills: [...state.pinned],
        attachments: attachments.map((a) => ({
          name: a.name, mime_type: a.mime_type, size: a.size, data: a.data,
        })),
      },
    });
    await pollRoom();
  } catch (err) {
    toast(err.message, 'err');
    if (/トークン/.test(err.message)) $('#room-token').classList.remove('hidden');
  }
}

// ====================================================================
// メッセージ表示
// ====================================================================
function renderMessages() {
  const wrap = $('#messages');
  wrap.innerHTML = '';
  if (!state.messages.length) {
    wrap.append(buildEmptyState());
    return;
  }
  for (const message of state.messages) wrap.append(buildMessageNode(message));
  scrollToBottom(true);
}

function buildEmptyState() {
  const examples = [
    '会議の文字起こしから議事録を作って',
    'このリリースノートを社外向けに書き直して',
    'コードレビューの観点でこの差分を見て',
  ];
  const node = el('div', { class: 'empty' },
    el('h2', {}, 'SKILL.md を読み込んで、Gemini と話す'),
    el('p', { class: 'muted', html: '<code>skills/</code> の <code>SKILL.md</code> がそのまま <code>system_instruction</code> になります。左のスキルをクリックで固定適用（複数可）、入力欄で <kbd>/</kbd> を打つと呼び出せます。' }),
    el('div', { class: 'examples' }, examples.map((t) => el('button', { class: 'example', onclick: () => { $('#input').value = t; sendMessage(); } }, t))),
  );
  return node;
}

function buildMessageNode(message) {
  const isUser = message.role === 'user';
  const meta = message.meta || {};
  const content = el('div', { class: 'content' });
  content.innerHTML = isUser ? `<p>${esc(message.content).replace(/\n/g, '<br>')}</p>` : renderMarkdown(message.content);

  const skills = meta.skills || [];
  const chipNodes = skills.map((s) => el('span', { class: 'chip' }, `/${s}`));
  for (const name of (meta.tools || [])) {
    chipNodes.push(el('span', { class: 'chip tool' }, `🛠 ${name}`));
  }
  const chips = chipNodes.length ? el('div', { class: 'msg-skills' }, chipNodes) : null;

  const files = meta.attachments || [];
  const fileNodes = files.map((file) => el('span', { class: 'attach-chip' },
    file.mime_type && file.mime_type.startsWith('image/') && file.data
      ? el('img', { class: 'thumb', src: `data:${file.mime_type};base64,${file.data}`, alt: file.name })
      : el('span', { class: 'thumb-ico' }, file.mime_type === 'application/pdf' ? '📄' : '📎'),
    el('span', { class: 'name' }, `${file.name}${file.size ? `（${fmtSize(file.size)}）` : ''}`),
  ));

  const body = el('div', { class: 'body' },
    el('div', { class: 'who' }, meta.author || (isUser ? 'あなた' : 'Actor'),
      meta.ts ? el('span', { class: 'msg-meta' }, (meta.ts || '').slice(11, 19)) : null,
      message.meta?.simulated ? el('span', { class: 'badge warn' }, 'シミュレーション') : null,
      message.meta?.model ? el('span', { class: 'muted' }, message.meta.model) : null,
      message.meta?.usage?.candidatesTokenCount ? el('span', { class: 'muted' }, `out ${fmt(message.meta.usage.candidatesTokenCount)} tok`) : null,
    ),
    chips,
    fileNodes.length ? el('div', { class: 'msg-skills' }, fileNodes) : null,
    content,
  );

  if (!isUser && meta.thoughts) {
    body.append(el('details', {},
      el('summary', {}, `🧠 思考ログ（${meta.thoughts.length} 文字）`),
      el('div', { class: 'thought' }, meta.thoughts)));
  }
  if (!isUser) {
    body.append(el('div', { class: 'msg-actions' },
      el('button', { class: 'link-btn', onclick: () => { navigator.clipboard.writeText(message.content); toast('コピーしました'); } }, 'コピー'),
      el('button', { class: 'link-btn', onclick: () => showInInspector('system') }, 'system_instruction'),
    ));
  }
  return el('div', { class: `msg ${isUser ? 'user' : 'assistant'}` },
    el('div', { class: 'avatar' }, isUser ? '🙋' : '🎭'), body);
}

function scrollToBottom(force = false) {
  const wrap = $('#messages');
  const nearBottom = wrap.scrollHeight - wrap.scrollTop - wrap.clientHeight < 160;
  if (force || nearBottom) wrap.scrollTop = wrap.scrollHeight;
}

// ====================================================================
// 送信
// ====================================================================
async function sendMessage(textOverride) {
  if (state.room) {
    const input = $('#input');
    const roomText = (textOverride ?? input.value).trim();
    if (!roomText) return;
    input.value = '';
    autoResize(input);
    hideAutocomplete();
    await sendToRoom(roomText);
    return;
  }
  if (state.busy) return;
  const input = $('#input');
  let text = (textOverride ?? input.value).trim();
  if (!text) return;

  // `/skill 依頼文` 形式の先頭トークンを固定スキルとして扱う
  const pinned = new Set(state.pinned);
  const match = text.match(/^\/([a-z0-9-]+)\s+([\s\S]+)$/i);
  if (match && state.skills.some((s) => s.name === match[1].toLowerCase())) {
    pinned.add(match[1].toLowerCase());
    text = match[2].trim();
    state.pinned = pinned;
    renderPinned();
    renderSkills();
  }

  input.value = '';
  autoResize(input);
  hideAutocomplete();
  state.busy = true;
  setSendButton(true);
  const attachments = state.attachments.slice();
  state.attachments = [];
  renderAttachments();
  state.messages.push({
    role: 'user', content: text,
    meta: { attachments: attachments.map((a) => ({ ...a, data: a.data })) },
  });
  renderMessages();

  const live = el('div', { class: 'content cursor' });
  const who = el('div', { class: 'who' }, 'Actor');
  const chips = el('div', { class: 'msg-skills' });
  const body = el('div', { class: 'body' }, who, chips, live);
  const node = el('div', { class: 'msg assistant' }, el('div', { class: 'avatar' }, '🎭'), body);
  $('#messages').append(node);
  scrollToBottom(true);
  const scroll = () => scrollToBottom();

  let answer = '';
  let thoughts = '';
  let metaSkills = [];
  let doneEvent = null;
  let lastPaint = 0;
  state.toolEvents = [];
  state.notices = [];

  const paint = (force = false) => {
    const now = performance.now();
    if (!force && now - lastPaint < 90) return;
    lastPaint = now;
    live.innerHTML = renderMarkdown(answer);
    scroll();
  };

  state.abort = new AbortController();
  try {
    const payload = {
      message: text,
      session_id: state.sessionId,
      skills: [...state.pinned],
      auto: state.auto,
      attachments: attachments.map((a) => ({ name: a.name, mime_type: a.mime_type, size: a.size, data: a.data })),
    };
    const res = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(payload),
      signal: state.abort.signal,
    });
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf('\n\n')) >= 0) {
        const chunk = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        for (const line of chunk.split('\n')) {
          if (!line.startsWith('data:')) continue;
          let event;
          try { event = JSON.parse(line.slice(5).trim()); } catch { continue; }
          handleEvent(event);
        }
      }
    }
  } catch (err) {
    if (err.name !== 'AbortError') {
      live.innerHTML = `<p class="muted">⚠ ${esc(err.message)}</p>`;
      toast(`エラー: ${err.message}`, 'err');
    }
  }

  function handleEvent(event) {
    switch (event.type) {
      case 'open':
        state.sessionId = event.session_id;
        break;
      case 'selection':
        state.selected = event;
        metaSkills = event.names || [];
        who.append(el('span', { class: badgeClass(event) }, describeSelection(event)),
                  el('span', { class: 'muted' }, `${fmt(event.system_tokens)} tok`));
        chips.innerHTML = '';
        for (const s of event.skills) chips.append(el('span', { class: 'chip' }, `/${s.name}`));
        if (event.missing?.length) chips.append(el('span', { class: 'badge warn' }, `未検出: ${event.missing.join(', ')}`));
        renderInspector();
        scroll();
        break;
      case 'prompt':
        state.system = event.system_instruction || '';
        if ($('#inspector').dataset.tab !== 'system') renderInspector();
        break;
      case 'tool_call': {
        const line = el('div', { class: 'tool-line' },
          `🛠 ${event.name}(${JSON.stringify(event.args || {})})`);
        body.insertBefore(line, live);
        state.toolEvents.push(event);
        scroll();
        break;
      }
      case 'tool_result': {
        const snippet = String(event.result || '').replace(/\s+/g, ' ').slice(0, 160);
        const line = el('div', { class: `tool-line${event.ok === false ? ' err' : ''}` },
          `${event.ok === false ? '⚠ ' : '↳ '}${snippet}`);
        body.insertBefore(line, live);
        scroll();
        break;
      }
      case 'tool_limit':
        body.insertBefore(el('div', { class: 'tool-line err' }, 'ツールの実行回数が上限に達しました'), live);
        break;
      case 'notice': {
        // 429 でキー / モデルを切り替えた、などの知らせ（本文には混ぜない）
        const text = event.text || '';
        if (text) {
          body.insertBefore(el('div', { class: 'tool-line' }, `🔁 ${text}`), live);
          state.notices.push({ ...event });
          scroll();
        }
        break;
      }
      case 'thought':
        thoughts += event.text;
        break;
      case 'text':
        answer += event.text;
        paint();
        break;
      case 'error':
        answer += `\n\n> ⚠ エラー: ${event.text}`;
        paint(true);
        break;
      case 'done':
        doneEvent = event;
        break;
    }
  }

  paint(true);
  live.classList.remove('cursor');

  if (thoughts.trim()) {
    body.append(el('details', {},
      el('summary', {}, `🧠 思考ログ（${thoughts.length} 文字）`),
      el('div', { class: 'thought' }, thoughts)));
  }
  body.append(el('div', { class: 'msg-actions' },
    el('button', { class: 'link-btn', onclick: () => { navigator.clipboard.writeText(answer); toast('コピーしました'); } }, 'コピー'),
    el('button', { class: 'link-btn', onclick: () => showInInspector('system') }, 'system_instruction')));

  const lastNotice = state.notices[state.notices.length - 1];
  if (doneEvent?.requested_model && lastNotice) {
    state.messages.push({
      role: 'system', content: `🔁 ${lastNotice.text}`, meta: { skills: [], notice: true },
    });
  }

  const usage = doneEvent?.usage || {};
  state.usage = usage;
  state.messages.push({
    role: 'assistant',
    content: answer,
    meta: {
      skills: metaSkills, thoughts, usage, model: doneEvent?.model,
      simulated: doneEvent?.simulated,
      tools: (doneEvent?.tool_calls || state.toolEvents.filter((e) => e.type === 'tool_call'))
        .map((c) => `${c.name}(${JSON.stringify(c.args || {}).slice(0, 60)})`),
    },
  });

  state.busy = false;
  state.abort = null;
  setSendButton(false);
  renderInspector();
  loadSessions();
  scroll();
}

function badgeClass(event) {
  if (event.missing?.length) return 'badge warn';
  return event.names?.length ? 'badge acc' : 'badge';
}
function describeSelection(event) {
  if (event.missing?.length) return `スキル未検出: ${event.missing.join(', ')}`;
  if (!event.names?.length) return 'スキルなし';
  return `適用: ${event.names.map((n) => '/' + n).join(' + ')}`;
}

function setSendButton(busy) {
  const button = $('#send');
  if (state.room) {
    $('.send-label', button).textContent = '送信';
    button.onclick = () => sendMessage();
    return;
  }
  button.classList.toggle('primary', !busy);
  button.classList.toggle('danger', busy);
  $('.send-label', button).textContent = busy ? '停止' : '送信';
  button.onclick = busy ? () => { state.abort?.abort(); toast('停止しました'); } : () => sendMessage();
}

// ====================================================================
// インスペクタ
// ====================================================================
function showInInspector(tab) {
  $$('.inspector .tab').forEach((t) => t.classList.toggle('active', t.dataset.itab === tab));
  $('#inspector').dataset.tab = tab;
  renderInspector();
}

function renderInspector() {
  const tab = $('#inspector').dataset.tab || 'applied';
  const wrap = $('#inspector-body');
  wrap.innerHTML = '';
  if (tab === 'applied') {
    const selection = state.selected;
    if (!selection) {
      wrap.append(el('div', { class: 'card' }, el('p', { class: 'muted small' }, 'まだ送信していません。Message を送ると、どのスキルが適用されたかここに表示されます。')));
      return;
    }
    wrap.append(el('div', { class: 'card' },
      el('h4', {}, '適用スキル'),
      el('div', { class: 'kv' },
        el('dt', {}, '固定'), el('dd', {}, [...state.pinned].join(', ') || 'なし'),
        el('dt', {}, '選択'), el('dd', {}, selection.names.join(', ') || 'なし'),
        el('dt', {}, '文字数'), el('dd', {}, `${fmt(selection.system_chars)} 文字`),
        el('dt', {}, '推定'), el('dd', {}, `${fmt(selection.system_tokens)} tokens`),
      )));
    if (selection.missing?.length) {
      wrap.append(el('div', { class: 'card' }, el('h4', {}, '⚠ 未検出'), el('div', { class: 'muted small' }, selection.missing.join(', '))));
    }
    for (const skill of selection.skills) {
      wrap.append(el('div', { class: 'card' },
        el('h4', {}, `/${skill.name}`),
        el('p', { class: 'muted small' }, skill.description || '(説明なし)'),
        el('div', { class: 'kv' },
          el('dt', {}, 'hash'), el('dd', {}, skill.hash),
          el('dt', {}, '参照'), el('dd', {}, `${skill.references.length} ファイル`),
          el('dt', {}, '本文'), el('dd', {}, `${fmt(skill.instruction_chars)} 文字`),
        ),
        el('button', { class: 'btn ghost', style: 'margin-top:8px', onclick: () => openDetail(skill.name) }, '詳細を開く')));
    }
    const scores = (selection.auto_scores || []).filter((s) => s.score > 0);
    if (scores.length) {
      const max = Math.max(...scores.map((s) => s.score), 1);
      const card = el('div', { class: 'card' }, el('h4', {}, '自動選択スコア'));
      for (const item of scores.slice(0, 6)) {
        card.append(
          el('div', { class: 'small', style: 'display:flex;gap:8px;align-items:center' },
            el('span', { class: 'mono' }, item.name),
            el('span', { class: 'spacer' }),
            el('span', { class: 'muted' }, item.score.toFixed(1))),
          el('div', { class: 'score-bar' }, el('i', { style: `width:${Math.round((item.score / max) * 100)}%` })),
        );
      }
      card.append(el('p', { class: 'muted small', style: 'margin-top:8px' }, `閾値 ${state.config?.auto_select_threshold ?? 2.0} 以上を採用`));
      wrap.append(card);
    }
  } else if (tab === 'system') {
    const card = el('div', { class: 'card' },
      el('h4', {}, 'system_instruction'),
      el('p', { class: 'muted small' }, 'ここに表示された文字列が、そのまま Gemini API の systemInstruction に渡されます。'),
      el('button', { class: 'btn ghost', onclick: () => { navigator.clipboard.writeText(state.system); toast('コピーしました'); } }, 'コピー'));
    wrap.append(card);
    wrap.append(el('pre', { class: 'system' }, state.system || '（まだ送信していません）'));
  } else {
    const usage = state.usage || {};
    wrap.append(el('div', { class: 'card' },
      el('h4', {}, 'トークン'),
      el('div', { class: 'kv' },
        el('dt', {}, '入力'), el('dd', {}, fmt(usage.promptTokenCount)),
        el('dt', {}, '出力'), el('dd', {}, fmt(usage.candidatesTokenCount)),
        el('dt', {}, '思考'), el('dd', {}, fmt(usage.thoughtsTokenCount)),
        el('dt', {}, '合計'), el('dd', {}, fmt(usage.totalTokenCount)),
      )));
    const config = state.config || {};
    wrap.append(el('div', { class: 'card' },
      el('h4', {}, '設定'),
      el('div', { class: 'kv' },
        el('dt', {}, 'provider'), el('dd', {}, config.provider),
        el('dt', {}, 'model'), el('dd', {}, state.model || config.model),
        el('dt', {}, 'temp'), el('dd', {}, String(config.temperature)),
        el('dt', {}, 'max out'), el('dd', {}, fmt(config.max_output_tokens)),
        el('dt', {}, 'thinking'), el('dd', {}, fmt(config.thinking_budget)),
        el('dt', {}, 'スキル'), el('dd', {}, `${state.skills.length} 件`),
        el('dt', {}, '保存先'), el('dd', {}, config.data_dir || ''),
      )));
    if (config.simulated) {
      wrap.append(el('div', { class: 'card' }, el('h4', {}, '🎭 シミュレーションモード'),
        el('p', { class: 'muted small', html: 'API キーが未設定です。<code>.env</code> に <code>GEMINI_API_KEY</code> を設定 → サーバー再起動で、同じ UI のまま実モデルに切り替わります。' })));
    }
  }
}

// ====================================================================
// モーダル
// ====================================================================
function openModal(id) { $(id).classList.remove('hidden'); }
function closeModal(node) { node.classList.add('hidden'); }
$$('.modal').forEach((modal) => {
  modal.addEventListener('click', (e) => {
    if (e.target === modal || e.target.closest('[data-close]')) closeModal(modal);
  });
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') $$('.modal').forEach((m) => closeModal(m));
});

async function openDetail(name) {
  const data = await api(`/api/skills/${encodeURIComponent(name)}?full=1`);
  state.detail = data;
  $('#detail-title').textContent = `/${data.name}`;
  const body = $('#detail-body');
  body.innerHTML = '';
  body.append(el('p', { class: 'muted' }, data.description || '(説明なし)'));
  body.append(el('div', { class: 'kv' },
    el('dt', {}, 'path'), el('dd', {}, data.path),
    el('dt', {}, 'hash'), el('dd', {}, data.hash),
    el('dt', {}, 'system'), el('dd', {}, `${fmt(data.system_tokens)} tokens`),
    el('dt', {}, 'tags'), el('dd', {}, data.tags.join(', ') || '-'),
  ));
  for (const warning of data.warnings || []) body.append(el('div', { class: 'badge warn' }, warning));
  if (data.references?.length) {
    body.append(el('h4', {}, `参照ファイル（${data.references.length}）`));
    const list = el('div', { class: 'field' });
    for (const ref of data.references) {
      list.append(el('div', { class: 'small mono' }, `${ref.relpath}  ·  ${ref.kind}  ·  ${fmt(ref.size)} bytes${ref.error ? `  ⚠ ${ref.error}` : ''}`));
    }
    body.append(list);
  }
  body.append(el('h4', {}, 'SKILL.md'));
  body.append(el('pre', { class: 'system' }, data.instructions || ''));
  openModal('#detail-modal');
}

$('#detail-export').addEventListener('click', () => {
  if (state.detail) window.open(`/api/skills/export?names=${encodeURIComponent(state.detail.name)}`, '_blank');
});
$('#detail-delete').addEventListener('click', async () => {
  if (!state.detail) return;
  if (!confirm(`スキル「${state.detail.name}」を削除しますか？（元に戻せません）`)) return;
  try {
    await api(`/api/skills/${encodeURIComponent(state.detail.name)}`, { method: 'DELETE' });
    toast('削除しました');
    state.pinned.delete(state.detail.name);
    closeModal($('#detail-modal'));
    await loadSkills($('#skill-search').value);
    renderPinned();
  } catch (err) { toast(err.message, 'err'); }
});

// スキル作成
$('#open-create').addEventListener('click', () => { $('#draft-result').classList.add('hidden'); openModal('#create-modal'); });
$('#draft-btn').addEventListener('click', async () => {
  const request = $('#draft-request').value.trim();
  if (!request) return toast('作りたい作業を書いてください', 'err');
  const button = $('#draft-btn');
  button.disabled = true; button.textContent = '生成中…';
  try {
    const data = await api('/api/skills/draft', { method: 'POST', body: { request } });
    $('#draft-name').value = data.draft.name;
    $('#draft-desc').value = data.draft.description;
    $('#draft-body').value = data.draft.instructions;
    $('#draft-result').classList.remove('hidden');
    $('#save-skill').disabled = false;
  } catch (err) { toast(err.message, 'err'); }
  finally { button.disabled = false; button.textContent = 'ドラフトを生成'; }
});
$('#save-skill').addEventListener('click', async () => {
  try {
    const data = await api('/api/skills', {
      method: 'POST',
      body: {
        name: $('#draft-name').value.trim(),
        description: $('#draft-desc').value.trim(),
        instructions: $('#draft-body').value,
        overwrite: true,
      },
    });
    toast(`スキル「${data.skill.name}」を保存しました`);
    closeModal($('#create-modal'));
    await loadSkills();
    togglePin(data.skill.name);
  } catch (err) { toast(err.message, 'err'); }
});

$('#help-btn').addEventListener('click', () => openModal('#help-modal'));

// ====================================================================
// 入力欄: オートコンプリート
// ====================================================================
const input = $('#input');
let acIndex = 0;
let acItems = [];

function autoResize(node) {
  node.style.height = 'auto';
  node.style.height = `${Math.min(node.scrollHeight, 220)}px`;
}

function currentSlashToken() {
  const value = input.value;
  const caret = input.selectionStart;
  const before = value.slice(0, caret);
  const match = before.match(/(^|\s)\/([a-zA-Z0-9-]*)$/);
  if (!match) return null;
  return { query: match[2], start: caret - match[2].length - 1, end: caret };
}

function showAutocomplete(token) {
  const query = token.query.toLowerCase();
  const items = state.skills.filter((s) => s.name.includes(query) || (s.description || '').toLowerCase().includes(query)).slice(0, 8);
  if (!items.length) return hideAutocomplete();
  acItems = items;
  acIndex = 0;
  const box = $('#autocomplete');
  box.innerHTML = '';
  items.forEach((skill, index) => {
    const item = el('div', { class: `item ${index === 0 ? 'active' : ''}` },
      el('span', { class: 'n' }, `/${skill.name}`),
      el('span', { class: 'd' }, skill.description || ''),
      state.matches?.[skill.name] ? el('span', { class: 's' }, state.matches[skill.name].toFixed(1)) : null);
    item.addEventListener('mousedown', (e) => { e.preventDefault(); applyAutocomplete(index, true); });
    box.append(item);
  });
  box.classList.remove('hidden');
}

function hideAutocomplete() { $('#autocomplete').classList.add('hidden'); acItems = []; }

function applyAutocomplete(index, addSpace = false) {
  const skill = acItems[index];
  const token = currentSlashToken();
  if (!skill || !token) return;
  const value = input.value;
  const replacement = `/${skill.name} `;
  input.value = value.slice(0, token.start) + replacement + value.slice(token.end);
  const caret = token.start + replacement.length;
  input.setSelectionRange(caret, caret);
  hideAutocomplete();
  input.focus();
  if (addSpace) togglePin(skill.name);
}

input.addEventListener('input', () => {
  autoResize(input);
  const token = currentSlashToken();
  if (token) showAutocomplete(token); else hideAutocomplete();
  updateFontHint();
});
input.addEventListener('keydown', (e) => {
  const open = !$('#autocomplete').classList.contains('hidden');
  if (open) {
    if (e.key === 'ArrowDown') { e.preventDefault(); moveAc(1); return; }
    if (e.key === 'ArrowUp') { e.preventDefault(); moveAc(-1); return; }
    if (e.key === 'Tab' || (e.key === 'Enter' && acItems.length && !e.shiftKey)) {
      e.preventDefault(); applyAutocomplete(acIndex, true); return;
    }
    if (e.key === 'Escape') { e.preventDefault(); hideAutocomplete(); return; }
  }
  if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
    e.preventDefault();
    sendMessage();
  }
});
function moveAc(delta) {
  acIndex = (acIndex + delta + acItems.length) % acItems.length;
  $$('#autocomplete .item').forEach((node, i) => node.classList.toggle('active', i === acIndex));
  $$('#autocomplete .item')[acIndex]?.scrollIntoView({ block: 'nearest' });
}
function updateFontHint() {
  const token = currentSlashToken();
  $('#composer-hint').textContent = token
    ? '↑↓ で選択 / Enter か Tab で確定'
    : (state.pinned.size ? `${state.pinned.size} 件のスキルを固定中` : '');
}

// ====================================================================
// トグル / ヘッダ
// ====================================================================
$('#auto-toggle').addEventListener('change', (e) => {
  state.auto = e.target.checked;
  localStorage.setItem('actor.auto', state.auto ? '1' : '0');
  renderPinned();
  toast(`自動選択: ${state.auto ? 'ON' : 'OFF'}`);
});
$('#think-toggle').addEventListener('change', (e) => {
  state.think = e.target.checked;
  localStorage.setItem('actor.think', state.think ? '1' : '0');
  toast(`思考ログ: ${state.think ? 'ON' : 'OFF'}（thinkingConfig はサーバー起動時の ACTOR_THINKING_BUDGET で有効化）`);
});
$('#model-select').addEventListener('change', (e) => {
  state.model = e.target.value;
  toast(`モデル表示を ${state.model} に変更（サーバー側の実モデルは再起動時に反映）`);
});
$('#export-btn').addEventListener('click', () => {
  const names = [...state.pinned];
  const url = names.length ? `/api/skills/export?names=${encodeURIComponent(names.join(','))}` : '/api/skills/export';
  window.open(url, '_blank');
});
$('#new-session').addEventListener('click', () => { leaveRoom(); newSession(); });
$('#room-create').addEventListener('click', createRoom);
$('#attach-btn').addEventListener('click', () => $('#attach-input').click());
$('#attach-input').addEventListener('change', (e) => { readFiles(e.target.files); e.target.value = ''; });
$('#room-token-save').addEventListener('click', async () => {
  state.roomToken = $('#room-token').value.trim();
  localStorage.setItem('actor.roomToken', state.roomToken);
  if (state.room) { state.roomCursor = 0; await openRoom(state.room); }
  toast(state.roomToken ? 'トークンを保存しました' : 'トークンを消しました');
});
$('#room-name').addEventListener('keydown', (e) => { if (e.key === 'Enter') createRoom(); });
$('#room-leave').addEventListener('click', leaveRoom);
$('#skill-search').addEventListener('input', (e) => {
  clearTimeout(window._searchTimer);
  window._searchTimer = setTimeout(() => loadSkills(e.target.value).catch(() => {}), 160);
});
$$('.tabs .tab[data-tab]').forEach((tab) => tab.addEventListener('click', () => {
  $$('.tabs .tab[data-tab]').forEach((t) => t.classList.toggle('active', t === tab));
  const which = tab.dataset.tab;
  $('#panel-skills').classList.toggle('hidden', which !== 'skills');
  $('#panel-sessions').classList.toggle('hidden', which !== 'sessions');
  $('#panel-rooms').classList.toggle('hidden', which !== 'rooms');
  if (which === 'sessions') loadSessions();
  if (which === 'rooms') loadRooms();
}));
$$('.inspector .tab').forEach((tab) => tab.addEventListener('click', () => showInInspector(tab.dataset.itab)));
$('#key-btn').addEventListener('click', async () => {
  $('#key-box').classList.toggle('hidden');
  if (!$('#key-box').classList.contains('hidden')) await refreshKeyStatus();
});
$('#key-save').addEventListener('click', async () => {
  const input = $('#key-input');
  const value = input.value.trim();
  if (!value) return toast('キーを貼り付けてください', 'err');
  const button = $('#key-save');
  button.disabled = true;
  $('#key-result').textContent = '確認しています…';
  try {
    const info = await api('/api/key', { method: 'POST', body: { api_key: value, check: true } });
    input.value = '';
    const check = info.check || {};
    if (check.ok) {
      $('#key-result').textContent = `接続 OK（${check.models?.length ?? 0} models）`;
      toast(`Gemini に接続しました（${info.model}）`);
    } else if (check.error) {
      $('#key-result').textContent =
        `キーは保存しましたが接続できませんでした（いまはシミュレータのまま）: ${check.error}`;
      toast('保存しましたが接続できませんでした', 'err');
    } else {
      $('#key-result').textContent = '保存しました';
      toast('API キーを保存しました');
    }
    await refreshProviderUI();
  } catch (err) {
    $('#key-result').textContent = err.message;
    toast(err.message, 'err');
  } finally {
    button.disabled = false;
  }
});
$('#key-clear').addEventListener('click', async () => {
  try {
    await api('/api/key', { method: 'DELETE' });
    $('#key-result').textContent = '解除しました（シミュレータに戻りました）';
    toast('API キーを解除しました');
    await refreshProviderUI();
  } catch (err) {
    toast(err.message, 'err');
  }
});

// ====================================================================
// API キー（私用ならここが分かりやすい）
// ====================================================================
async function refreshKeyStatus() {
  try {
    const info = await api('/api/key');
    const status = $('#key-status');
    if (info.has_key) {
      const source = { env: '環境変数', '.env': '.env', file: '.actor/api_key', keys: '.actor/keys' }[info.source] || info.source;
      const count = info.key_count > 1 ? ` / ${info.key_count} キー（空き ${info.keys_ready}）` : '';
      const limits = info.stats?.limits ? ` / 429 で ${info.stats.limits} 回切替` : '';
      status.textContent = `設定済み: ${info.masked}（${source}）${count}${limits} / ${info.provider}`;
    } else {
      status.textContent = '未設定（シミュレータで動作中）';
    }
    $('#key-clear').disabled = !info.has_key;
  } catch (err) {
    $('#key-status').textContent = `確認できません: ${err.message}`;
  }
}

async function refreshProviderUI() {
  const config = await api('/api/config');
  state.config = config;
  const pill = $('#provider-pill');
  if (config.simulated) {
    pill.className = 'pill sim';
    pill.textContent = '🎭 シミュレータ（APIキー未設定）';
  } else {
    pill.className = 'pill ok';
    pill.textContent = `Gemini: ${config.model}`;
  }
  $('#composer-hint').textContent = config.simulated
    ? 'シミュレータで動作中（🔑 から API キーを入れると実モデルに切り替わります）'
    : '';
  await loadSkills().catch(() => {});
  await refreshKeyStatus();
}

// ====================================================================
// 起動
// ====================================================================
async function boot() {
  state.auto = localStorage.getItem('actor.auto') !== '0';
  state.think = localStorage.getItem('actor.think') === '1';
  $('#auto-toggle').checked = state.auto;
  $('#think-toggle').checked = state.think;

  try {
    const config = await api('/api/config');
    state.config = config;
    const pill = $('#provider-pill');
    if (config.simulated) {
      pill.className = 'pill sim';
      pill.textContent = '🎭 シミュレータ（APIキー未設定）';
    } else {
      pill.className = 'pill ok';
      pill.textContent = `Gemini: ${config.model}`;
    }
    state.model = config.model;
    const data = await api('/api/models');
    state.models = data.models || [];
    const select = $('#model-select');
    select.innerHTML = '';
    for (const model of state.models) {
      select.append(el('option', { value: model, selected: model === config.model }, model));
    }
    if (!state.models.includes(config.model)) select.append(el('option', { selected: true }, config.model));
    $('#composer-hint').textContent = config.simulated
      ? 'シミュレータで動作中（左下の 🔑 から API キーを入れると実モデルに切替）'
      : '';
    await refreshKeyStatus();
  } catch (err) {
    if (err.status === 403 && !state.apiAuthPrompted) {
      state.apiAuthPrompted = true;
      const token = window.prompt('この Actor サーバーの API トークンを入力してください');
      if (token) {
        state.roomToken = token.trim();
        localStorage.setItem('actor.roomToken', state.roomToken);
        return boot();
      }
    }
    toast(`設定の取得に失敗: ${err.message}`, 'err');
  }

  await loadSkills();
  await loadSessions();
  await loadRooms();
  renderPinned();
  input.focus();
}

function renderInventory() { /* 予約: 将来の拡張用 */ }

boot();
