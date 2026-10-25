// Optional UI smoke test for development.
//
//   1) Install jsdom: npm install --no-save --package-lock=false jsdom
//   2) Generate a temporary token and start the server in this shell:
//      export ACTOR_ROOM_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
//      python3 -m actor serve --port 8080 & server_pid=$!
//   3) Wait for /api/health, run `node tests/ui_smoke.mjs`, then stop `$server_pid`.

// Run jsdom against the real server and exercise skill listing, slash completion,
// SSE streaming, the inspector, and skill creation.
import { JSDOM, VirtualConsole } from 'jsdom';

const BASE = process.env.BASE || 'http://127.0.0.1:8080';
const AUTH_TOKEN = process.env.ACTOR_ROOM_TOKEN || '';
const apiRequests = [];
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => errors.push(`jsdomError: ${e.message}`));
vc.on('error', (...a) => errors.push(`console.error: ${a.join(' ')}`));

const dom = await JSDOM.fromURL(`${BASE}/`, {
  runScripts: 'dangerously',
  resources: 'usable',
  pretendToBeVisual: true,
  virtualConsole: vc,
  beforeParse(window) {
    if (AUTH_TOKEN) window.localStorage.setItem('actor.roomToken', AUTH_TOKEN);
  },
});

const { window } = dom;
// Node の fetch / DOM API を注入（jsdom には無い）。UI の API 要求ヘッダーも記録する。
window.fetch = (input, init = {}) => {
  const url = typeof input === 'string' ? new URL(input, BASE)
    : input instanceof URL ? input : new URL(input.url);
  if (url.pathname.startsWith('/api/')) {
    apiRequests.push({ path: url.pathname, headers: new Headers(init.headers || {}) });
  }
  return fetch(url, init);
};
const serverFetch = (input, init = {}) => {
  const headers = new Headers(init.headers || {});
  if (AUTH_TOKEN) headers.set('X-Actor-Token', AUTH_TOKEN);
  return fetch(input, { ...init, headers });
};
window.TextDecoder = TextDecoder;
window.ReadableStream = ReadableStream;
// performance は jsdom 標準のものを利用
window.navigator.clipboard = { writeText: async () => {} };

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const $ = (sel) => window.document.querySelector(sel);
const $$ = (sel) => [...window.document.querySelectorAll(sel)];

const check = (label, ok, extra = '') => {
  console.log(`${ok ? '  ok  ' : ' FAIL '} ${label}${extra ? ` — ${extra}` : ''}`);
  if (!ok) process.exitCode = 1;
};

// boot 完了を待つ（スキル一覧が描画されるまで）
for (let i = 0; i < 60 && $$('#skill-list li').length === 0; i++) await wait(100);

check('起動: スキル一覧が描画される', $$('#skill-list li').length >= 5, `${$$('#skill-list li').length} 件`);
check('起動: プロバイダ表示', /シミュレータ|Gemini/.test($('#provider-pill').textContent), $('#provider-pill').textContent);
check('起動: モデル選択肢', $$('#model-select option').length >= 2, `${$$('#model-select option').length} 個`);

// スキルを固定 → チップが出る
$$('#skill-list li')[0].dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
await wait(60);
check('スキル固定: チップ表示', $$('#pinned-chips .chip').length === 1, $('#pinned-chips').textContent.trim());

// 「/」オートコンプリート
const input = $('#input');
input.value = '/min';
input.dispatchEvent(new window.Event('input', { bubbles: true }));
await wait(60);
const acItems = $$('#autocomplete .item');
check('オートコンプリート: 候補表示', acItems.length >= 1, acItems.map((n) => n.textContent.trim().split(' ')[0]).join(','));
input.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
await wait(60);
check('オートコンプリート: 確定で入力欄に挿入', $('#input').value.startsWith('/minutes-writer'), JSON.stringify($('#input').value));

// 送信（SSE ストリーミング）
$('#input').value = '会議の文字起こしから議事録を作って';
await window.sendMessage();
for (let i = 0; i < 100 && $('#messages').textContent.includes('送信中'); i++) await wait(100);
const streamRequest = apiRequests.find((request) => request.path === '/api/chat/stream');
check('チャット: トークン設定時に認証ヘッダーを送る',
      !AUTH_TOKEN || streamRequest?.headers.get('X-Actor-Token') === AUTH_TOKEN,
      AUTH_TOKEN ? (streamRequest?.headers.get('X-Actor-Token') ? 'header present' : 'header missing') : 'tokenless mode');

const msgs = $$('#messages .msg');
check('送信: ユーザー + アシスタントの2件', msgs.length === 2, `${msgs.length} 件`);
const reply = msgs[1] ? msgs[1].querySelector('.content').textContent : '';
check('応答: シミュレーション注記を含む', reply.includes('シミュレーションモード'), reply.slice(0, 40).replace(/\n/g, ' '));
check('応答: 適用スキルが選ばれている', msgs[1]?.querySelector('.who')?.textContent.includes('minutes-writer'), msgs[1]?.querySelector('.who')?.textContent.trim());
check('Reply: the English brand-voice template is included', reply.includes('## Polished copy') && reply.includes('## Changes (up to 7 items)'));
// マークダウンレンダラ単体
const md = window.renderMarkdown([
  '## 見出し',
  '',
  '**太字** と `コード` と [リンク](https://example.com)',
  '',
  '| 列A | 列B |',
  '| --- | --- |',
  '| 1 | 2 |',
  '',
  '- 箇条書き1',
  '- 箇条書き2',
  '',
  '1. 番号1',
  '',
  '> 引用文',
  '',
  '```python',
  'x = 1',
  '```',
].join('\n'));
const probe = window.document.createElement('div');
probe.innerHTML = md;
check('マークダウン: 表が table 要素になる', !!probe.querySelector('table') && probe.querySelectorAll('td').length === 2, probe.querySelector('table')?.textContent);
check('マークダウン: コードブロックが pre > code', probe.querySelector('pre code')?.textContent.includes('x = 1'));
check('マークダウン: インライン装飾', !!probe.querySelector('strong') && !!probe.querySelector('a[href="https://example.com"]'));
check('マークダウン: リストと引用', !!probe.querySelector('ul li') && !!probe.querySelector('ol li') && !!probe.querySelector('blockquote'));
check('マークダウン: HTML エスケープ', window.renderMarkdown('<img src=x onerror=alert(1)>').includes('&lt;img'));
check('マークダウン: 見出しが h3 になる', !!msgs[1]?.querySelector('h3, h4'));
check('思考ログ: details が追加される', !!msgs[1]?.querySelector('details'));

// インスペクタ
$$('.inspector .tab')[0].dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
await wait(50);
check('インスペクタ: 適用スキルを表示', $('#inspector-body').textContent.includes('minutes-writer'));
$$('.inspector .tab')[1].dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
await wait(50);
check('インスペクタ: system_instruction を表示', $('#inspector-body').textContent.includes('<skill name="minutes-writer">'));
check('インスペクタ: 参照資料が system に含まれる', $('#inspector-body').textContent.includes('references/format.md'));
$$('.inspector .tab')[2].dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
await wait(50);
check('インスペクタ: 統計タブ', $('#inspector-body').textContent.includes('トークン'));

// スキル作成モーダル（会話からスキルを作る）
$('#open-create').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
$('#draft-request').value = '週次レポートを毎回同じ形式で作る';
$('#draft-btn').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
for (let i = 0; i < 50 && $('#draft-result').classList.contains('hidden'); i++) await wait(100);
check('スキル生成: ドラフトが返る', !$('#draft-result').classList.contains('hidden'));
check('スキル生成: name が kebab-case', /^[a-z0-9-]+$/.test($('#draft-name').value), $('#draft-name').value);
check('スキル生成: 本文にテンプレート見出し', $('#draft-body').value.includes('# 手順'));

// 保存してサイドバーへ反映
$('#save-skill').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
await wait(500);
check('スキル保存: サイドバーへ反映', $$('#skill-list li').length >= 6, `${$$('#skill-list li').length} 件`);
const draftName = $('#draft-name').value;
const deletedDraft = await serverFetch(`${BASE}/api/skills/${encodeURIComponent(draftName)}`, {
  method: 'DELETE', headers: { 'Content-Type': 'application/json' },
});
check('スキル生成: smoke test のスキルを後片付けする', deletedDraft.ok);

// セッション一覧タブ
const tabs = $$('.sidebar .tabs .tab');
tabs[1].dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
await wait(300);
check('履歴タブ: セッションが並ぶ', $$('#session-list li').length >= 1);

// ====================================================================
// ルーム（常駐ボット）UI
// ====================================================================
// API キー（🔑 から保存できる）
// ====================================================================
const configBefore = await (await serverFetch(`${BASE}/api/config`)).json();
const keyBefore = await (await serverFetch(`${BASE}/api/key`)).json();
check('APIキー: /api/config は生のキーを返さない', typeof configBefore.api_key === 'boolean',
      `api_key=${typeof configBefore.api_key} source=${configBefore.api_key_source || '-'}`);

$('#key-btn').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
await wait(250);
check('APIキー: 🔑 でパネルが開く', !$('#key-box').classList.contains('hidden'));
check('APIキー: 状態が出どころつきで表示される', /設定済み|未設定/.test($('#key-status').textContent),
      $('#key-status').textContent);

const tooShort = await serverFetch(`${BASE}/api/key`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ api_key: 'short' }),
});
check('APIキー: 短すぎるキーは 400 で拒否される', tooShort.status === 400,
      (await tooShort.json()).error);
const keyAfter = await (await serverFetch(`${BASE}/api/key`)).json();
check('APIキー: 拒否時は保存されない', keyAfter.has_key === keyBefore.has_key,
      `has_key=${keyAfter.has_key}`);

$('#key-btn').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
check('APIキー: もう一度押すと閉じる', $('#key-box').classList.contains('hidden'));

// ====================================================================
// ツール（function calling）と添付（画像 / PDF）
// ====================================================================
$('#new-session').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
await wait(200);
$('#input').value = '今日は何日？';
$('#send').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
for (let i = 0; i < 60 && $$('#messages .tool-line').length === 0; i++) await wait(100);
await wait(600);
const toolLines = $$('#messages .tool-line');
check('ツール: 🛠 呼び出しと実行結果が表示される', toolLines.length >= 2, toolLines.map((n) => n.textContent.trim().slice(0, 30)).join(' / '));
check('ツール: now が呼ばれる', toolLines.some((n) => n.textContent.includes('now')), toolLines[0]?.textContent.trim());
check('ツール: 実行結果が本文に反映される', /\d{4}-\d{2}-\d{2}/.test($('#messages').textContent), toolLines[1]?.textContent.trim().slice(0, 40));

// 添付（1x1 PNG）
const attachInput = $('#attach-input');
check('添付: クリップボタンがある', !!$('#attach-btn') && !!attachInput);
const pngBytes = Uint8Array.from(atob(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=='
), (c) => c.charCodeAt(0));
const file = new window.File([pngBytes], 'test.png', { type: 'image/png' });
Object.defineProperty(attachInput, 'files', { value: [file], configurable: true });
attachInput.dispatchEvent(new window.Event('change', { bubbles: true }));
for (let i = 0; i < 30 && $$('#attach-chips .attach-chip').length === 0; i++) await wait(100);
check('添付: チップが表示される', $$('#attach-chips .attach-chip').length === 1, $('#attach-chips').textContent.trim());

$('#input').value = 'この画像を見て';
$('#send').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
await wait(1200);
check('添付: 送信後に入力欄のチップが消える', $$('#attach-chips .attach-chip').length === 0);
check('添付: ユーザーの発言にファイル名が出る', [...$$('#messages .msg.user')].some((m) => m.textContent.includes('test.png')));

// サーバー側に base64 が保存されているか（API を直接確認）
const sessions = await (await serverFetch(`${BASE}/api/sessions`)).json();
const sessionId = sessions.sessions[0]?.id;
let storedAttachment = null;
if (sessionId) {
  const detail = await (await serverFetch(`${BASE}/api/sessions/${sessionId}`)).json();
  for (const m of detail.session.messages) {
    storedAttachment = (m.attachments || [])[0] || storedAttachment;
  }
}
check('添付: セッションに保存される（base64 は一覧に載せない）',
      !!storedAttachment && storedAttachment.name === 'test.png' && !('data' in storedAttachment),
      JSON.stringify(storedAttachment || {}));

// ====================================================================
const roomName = 'ui-room-' + Math.random().toString(36).slice(2, 7);

// ルームタブへ
$$('.sidebar .tabs .tab').find((t) => t.dataset.tab === 'rooms').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
await wait(300);
$('#room-name').value = roomName;
$('#room-create').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
for (let i = 0; i < 40 && !$('#room-banner').classList.contains('hidden') === false; i++) await wait(100);
await wait(400);
check('ルーム: 開くとバナーが出る', !$('#room-banner').classList.contains('hidden'), $('#room-banner-name').textContent);

// ルームに発言（サーバー側で即返事させる）
await window.sendToRoom('おはよー');
await wait(300);
check('ルーム: 発言が表示される', $$('#messages .msg').length === 1, `${$$('#messages .msg').length} 件`);

// サーバー側 reply でボットの返事を積む（常駐ボット相当）
await serverFetch(`${BASE}/api/rooms/${roomName}/messages`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ text: 'ミカ、今日のタスクを整理して', reply: true }),
});
await wait(600);
await window.pollRoom();
await wait(200);
const roomMsgs = $$('#messages .msg');
check('ルーム: ポーリングで返事が流れてくる', roomMsgs.length >= 3, `${roomMsgs.length} 件`);
check('ルーム: 話者が区別される', /あなた/.test(roomMsgs[0].textContent) , roomMsgs[0].querySelector('.who').textContent.trim());
check('ルーム: 返事にスキルチップが出る', [...roomMsgs].some((m) => m.querySelector('.chip')));

// 会話モードに戻る
$('#room-leave').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
await wait(200);
check('ルーム: 会話モードに戻れる', $('#room-banner').classList.contains('hidden'));
const deletedRoom = await serverFetch(`${BASE}/api/rooms/${roomName}`, {
  method: 'DELETE', headers: { 'Content-Type': 'application/json' },
});
check('ルーム: smoke test のルームを後片付けする', deletedRoom.ok);

check('JS 実行時エラーなし', errors.length === 0, errors.slice(0, 3).join(' | '));
dom.window.close();
console.log('done');
