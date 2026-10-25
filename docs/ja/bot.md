# 常駐ボット運用ガイド

Actor は「自分だけのクライアント」と「常駐ボット」の両方を作れる。
このドキュメントは **常駐ボット** 側の話。

```
┌──────────────┐   監視（ポーリング）   ┌────────────────────────────┐
│  actor bot   │ ───────────────────▶  │  actor serve               │
│  （自分のPC） │ ◀─────────────────── │  /api/rooms/<name>         │
│  SKILL.md    │   返事を書き込む       │  （ルーム＝共有ログ）        │
│  + Gemini    │                       └────────────────────────────┘
└──────────────┘                                     ▲
                                    人間は curl / ブラウザ / アプリから発言
```

ボットを動かすマシンにも **API キー**が必要です。いちばん簡単なのは
`.env` に `GEMINI_API_KEY=...` を置く（`actor key set` でも同じ場所に保存できます）。
どのキーが使われるかは `actor key show` / `actor doctor` で確認できます。

常駐させるとレート制限（429）に当たりやすいので、**キーを複数持たせておく**のがおすすめです
（`.env` に `GEMINI_API_KEYS=[伏せ字]...,[伏せ字]...` か `actor key add`）。429 になったキーは一時的に
避けられ、自動で次のキーで返事をします。状態は `actor key list`、生死は `actor key check` で確認できます。

それでも足りないときは **モデルごと逃がします**。Gemini の枠は「プロジェクト × モデル」単位なので、
`ACTOR_MODEL=gemini-3.8-flash` のキーが全滅しても `ACTOR_MODEL_FALLBACKS`（既定
`gemini-3.5-flash,gemini-2.5-flash`）のどれかで返事を続けます。ログには
`🔁 gemini-3.8-flash で全キーが 429 → gemini-3.5-flash に逃がします` と出ます（黙って別モデルには
しません）。逃げ道を使わせたくないときは `--no-model-fallback`。キーの増やし方は `actor key sources`。

ボットは **Actor の完全なインスタンス**（スキル・セッション・履歴をローカルに持つ）として動き、
ルームは「合流点」としてだけ使う。だから同じルームに複数のボット（別のスキル・別のモデル）を
参加させることもできる。

## 1. 最小構成（3ステップ）

```bash
# 1) サーバー（ルームを外から見えるようにする）
python3 -m actor serve --port 8080

# 2) 常駐ボット（ペルソナ「ミカ」を固定適用）
python3 -m actor bot --room actor-x --skills mika --name ミカ \
    --skills-dir skills --skills-dir examples/mika

# 3) 話しかける
curl -s -X POST localhost:8080/api/rooms/actor-x/messages \
  -H 'Content-Type: application/json' \
  -d '{"text":"おはよー","author":"TRMTKB"}'

# 会話を見る
python3 -m actor rooms show actor-x
```

サーバー側でその場で返事を生成したいだけなら、ボットなしでも動く（`reply: true`）:

```bash
curl -s -X POST localhost:8080/api/rooms/actor-x/messages \
  -H 'Content-Type: application/json' \
  -d '{"text":"今日のタスクを整理して","reply":true}' | jq -r '.reply.text'
```

| | ボット（`actor bot`） | サーバー内蔵（`reply: true`） |
| --- | --- | --- |
| スキル | ボットのローカルのもの | サーバーのもの |
| モデル / temperature | ボットの設定 + スキルの上書き | 同左 |
| 向いている用途 | 常時稼働・複数ルーム・スキルを編集しながら調整 | 単発の確認、他のアプリからの呼び出し |

ボットは **道具（function calling）** も使えます。使い方によって `now` / `read_file` を自分で呼び、
`🛠 now({"timezone_name": "Asia/Tokyo"})` のようにログへ出ます（`actor tools` で一覧）。

```bash
# 使わせる道具と範囲を決める（既定は読み取りのみ）
ACTOR_TOOLS=now,read_file,list_files python3 -m actor bot --room actor-x --skills mika
ACTOR_TOOLS_ROOT=~/workspace      # 読み書きできる範囲（サンドボックス）
ACTOR_TOOLS_ALLOW_WRITE=false     # write_file を許可するときだけ true
ACTOR_TOOLS_ALLOW_NETWORK=false   # http_get を許可するときだけ true
```

画像や PDF を投げるのも同じルーム API です（ボットが受け取って Gemini へ渡します）。

```bash
python3 -m actor rooms say "この図を見て" --attach ./図.png --reply
curl -s -X POST localhost:8080/api/rooms/actor-x/messages \
  -H 'Content-Type: application/json' \
  -d '{"text":"この図を見て","author":"TRMTKB","attachments":[{"name":"図.png","mime_type":"image/png","data":"'"$(base64 -w0 図.png)"'"}]}'
```

## 2. ボットのオプション

```bash
python3 -m actor bot \
  --room actor-x \                 # 監視するルーム
  --api http://127.0.0.1:8080 \    # 接続先
  --name ミカ \                    # ルーム上の表示名
  --skills mika --skills brand-voice \  # 固定適用（複数可＝スタック）
  --interval 1.5 \                 # ポーリング間隔（秒）
  --mention @actor \               # この文字列を含む発言にだけ反応
  --from-now \                     # 起動後の発言にだけ反応（履歴を読み飛ばす）
  --stream \                       # 生成中のテキストを端末にも流す
  --model gemini-3-pro \           # モデル指定
  --thinking-budget 2048           # 思考させる
```

- `--no-auto` で自動選択を切り、固定したスキルだけで答える（ペルソナの揺れを抑えたいとき）
- `--once` で 1 回だけ処理して終了。cron / タスクスケジューラ向け
- カーソル（どこまで読んだか）は `.actor/bot-<room>.json` に保存され、再起動しても続きから

### 再起動しても会話が続く

ボットの返事は `--room` ごとのセッション（`.actor/sessions/*.json`）に紐づく。
プロセスを止めて再開しても、同じルームなら会話の文脈が維持される。

## 3. SKILL.md を編集しながら育てる

ボットは **ターンごとに SKILL.md を読み直す**。動かしたまま

```bash
$EDITOR examples/mika/SKILL.md     # 口調や禁止事項を書き換える
```

とすれば、次の返事から反映される。ペルソナや手順の試行錯誤はこのループが一番速い。
`/use` 相当の切り替えは `--skills` を変えて再起動、または同じルームに別名でボットを追加。

## 4. 動かし方いろいろ

### 端末で常駐（いちばん簡単）

```bash
python3 -m actor bot --room actor-x --skills mika
# Ctrl-C で停止
```

### nohup / バックグラウンド

```bash
nohup python3 -m actor bot --room actor-x --skills mika \
  > .actor/bot.log 2>&1 &
tail -f .actor/bot.log
```

### systemd（Linux）

```ini
# ~/.config/systemd/user/actor-bot.service
[Unit]
Description=Actor bot (mika)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/Actor
EnvironmentFile=%h/Actor/.env
ExecStart=/usr/bin/python3 -m actor bot --room actor-x --skills mika --from-now
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now actor-bot
journalctl --user -u actor-bot -f
```

### cron（`--once` を定期実行）

```
*/5 * * * * cd $HOME/Actor && /usr/bin/python3 -m actor bot --room actor-x --skills mika --once >> .actor/cron.log 2>&1
```

### Windows（タスクスケジューラ）

```powershell
schtasks /create /tn "ActorBot" /sc minute /mo 5 /tr ^
 "cmd /c cd %USERPROFILE%\Actor && python -m actor bot --room actor-x --skills mika --once"
```

## 5. 複数ボット・複数ルーム

```bash
# 同じルームに別の人格を参加させる（メンションで呼び分け）
python3 -m actor bot --room team --name ミカ  --skills mika         --mention @mika
python3 -m actor bot --room team --name レビ --skills code-review   --mention @review

# 別のルームで、用途別に
python3 -m actor bot --room work   --skills minutes-writer --api http://127.0.0.1:8080
python3 -m actor bot --room devlog --skills release-notes  --interval 5
```

同じルームに複数ボットがいる場合、`--mention` で呼び分けるのが安全
（付けないと全員が同じ発言に反応する）。

## 6. API リファレンス

| メソッド | パス | 説明 |
| --- | --- | --- |
| GET | `/api/rooms` | ルーム一覧 |
| POST | `/api/rooms` | `{name, skills}` で作成（既存なら作成せず返す） |
| GET | `/api/rooms/<name>?after=<seq>&limit=<n>` | 差分取得（`cursor` = 最新 seq / `attachments=1` で base64 も） |
| POST | `/api/rooms/<name>/messages` | `{text, author, role, skills, reply, attachments, tools}` を追記 |
| DELETE | `/api/rooms/<name>` | ルーム削除 |

`ACTOR_ROOM_TOKEN` を設定すると、ルームだけでなく**すべての `/api/*` エンドポイント**に
`X-Actor-Token: <token>` / `Authorization: Bearer <token>` が必要です。ボット側は
`--token <文字列>` か `ACTOR_ROOM_TOKEN` を使います。外部 bind はトークン必須で、既定の bind は `127.0.0.1` です。

`reply: true` を付けると、サーバー側でモデルを呼んで `reply` を一緒に返す。
レスポンスの `missing_skills` に、サーバーが見つけられなかったスキル名が入る
（`--skills-dir` の指定漏れに気づける）。

## 6.5 トークン保護と Webhook

```bash
# 端末1: サーバー（トークンで保護し、発言を Webhook へ流す）
export ACTOR_ROOM_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" # 全APIに必要
export ACTOR_WEBHOOK_URL='https://example.com/actor-hook'
export ACTOR_WEBHOOK_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
python3 -m actor serve --port 8080

# 端末2: ローカルのシークレットストアから同じ値を読み込み、環境変数として設定して起動
python3 -m actor bot --room actor-x --skills mika --api http://127.0.0.1:8080
```

届く JSON（`Content-Type: application/json`）:

```json
{"event": "message", "ts": "2026-10-13T09:00:00+0900", "room": "actor-x",
 "message": {"seq": 3, "role": "user", "text": "おはよー", "author": "TRMTKB"},
 "reply": null, "count": 3}
```

受け側での署名検証（Node の例）:

```js
const crypto = require('crypto');
const expected = 'sha256=' + crypto.createHmac('sha256', process.env.ACTOR_WEBHOOK_SECRET)
  .update(rawBody).digest('hex');
if (!crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(req.headers['x-actor-signature']))) return res.status(401).end();
```

- 通知は非同期。送れなくても会話は止まらず、5xx / 429 は最大3回リトライします
- 世代（モデル）も同じ。429 が続けば `ACTOR_MODEL_FALLBACKS` の順に逃げるので、
  常駐ボットが「枠切れで黙る」ことはほぼなくなります（`--no-model-fallback` で無効化）
- 送信状況は `GET /api/health` の `webhook_stats`（queued / sent / failed / skipped）で確認できます
- 通知を Slack などへ中継する場合は、受け側で `message.text` をそのまま転送するのが簡単です

## 7. つまずきやすいところ

| 症状 | 原因と対処 |
| --- | --- |
| ボットが無反応 | サーバーが起動しているか（`curl localhost:8080/api/health`）。別マシンなら `--api` を向ける |
| ペルソナが効かない | スキルが見つかっていない。起動ログの「警告: スキルが見つかりません」と `missing_skills` を確認、`--skills-dir` を追加 |
| 過去の会話に全部返事してしまう | `--from-now` を付ける（または `.actor/bot-<room>.json` の cursor を調整） |
| サーバーに繋がらない | `serve` の `--host`（既定 127.0.0.1）とポート、`--api` の URL を確認。外部 bind はトークン必須 |
| 同じ返事が二度出る | 同じルームに同じボットを二重起動していないか（`.actor/bot-<room>.json` は共有される） |
| 応答が遅い | `--stream` で進捗が見える。`--model` を flash 系にする、`--interval` を短くする |

## 8. セキュリティ

- 既定は `127.0.0.1`。非ループバックへ bind するには `ACTOR_ROOM_TOKEN` が必須で、設定時は全APIを保護する。
- CORS は既定無効。別 Origin が必要なら `ACTOR_CORS_ORIGINS` を設定し、LAN の Host 名/IP は `ACTOR_ALLOWED_HOSTS` に追加する。
- 外部公開時は、必要なら信頼できる TLS リバースプロキシを使う。
- 道具は既定で読み取り専用。`write_file` / `http_get` は `ACTOR_TOOLS_ALLOW_*` を true にしたときだけ有効で、
  パスは `ACTOR_TOOLS_ROOT` の中に閉じ込められる（`../` や絶対パスは拒否）
- ルームのログとセッションは `.actor/` に平文で保存される（`.gitignore` 済み）
- スキルの本文・参照資料は毎ターン API へ送られる（機密を入れない）
