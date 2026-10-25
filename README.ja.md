# Actor-si

[![Tests](https://github.com/TonbokiriRaikiriMuramasa-Taikoubou/Actor-si/actions/workflows/tests.yml/badge.svg)](https://github.com/TonbokiriRaikiriMuramasa-Taikoubou/Actor-si/actions/workflows/tests.yml)

Actor-si（CLIコマンドは `actor`）は、Agent Skills の作成・lint・テスト・パッケージ化をローカルで行うワークベンチです。CLI・HTTP API・Web UI から `SKILL.md` を system instruction として読み込み、Gemini で実行できます。コア機能は Python 標準ライブラリだけで動き、スキルの自動選択、参照資料との合成、実行前の形式チェックに対応します。サーバーは既定で loopback に bind し、外部 bind には API トークンが必要です。ブラウザーからの別 Origin アクセスは明示 allowlist がない限り無効です。

## 概要

**SKILL.md を `system_instruction` として読み込み、Gemini API と対話するクライアント。**
CLI・HTTP API・Web UI（Gemini 風の `/` 呼び出し UI）の3つを、**Python 標準ライブラリだけで**提供します。

Google が Gemini に導入した「スキル」は、Markdown ネイティブなオープン標準 **SKILL.md** を採用しています。
Actor-si はその形式のフォルダをそのまま読み込み、

- スキルを **複数スタック**して1つの `system_instruction` に合成
- 依頼文からスキルを **自動選択**（決定的なスコアリング・閾値つき）
- 参照ファイル（テンプレート・チェックリスト・PDF・画像）も `system_instruction` に同梱
- モデルが自分で道具を使う **function calling**（`now` / `read_file` …。スキルの `allowed-tools` で絞れる）
- **画像・PDF を貼って質問**できる（CLI `--attach` / Web UI 📎 / HTTP の `attachments`）
- ルームは **トークンで保護**でき、発言を **Webhook** で外部へ通知できる
- 会話から **新しい SKILL.md を生成**し、Gemini にアップロードできる ZIP で書き出し

という流れを、ローカルで完結させます。

```
┌────────────┐   /skills/<name>/SKILL.md    ┌──────────────┐
│  CLI       │──┐                           │ system_      │
│  Web UI    │──┼──▶ Actor-si ───────────────▶│ instruction  │──▶ Gemini API
│  HTTP API  │──┘     選択・合成・保存      │ (+ 参照資料) │
└────────────┘                              └──────────────┘
```

---

## 利用できること

Actor-si は、Agent Skills をローカルで作成・lint・テストし、ZIP 化するためのワークショップです。ホスト型マーケットプレイスや自動提出サービスではありません。再利用できるスキルを5つ同梱しています。各英語スキルの description には `Use when` と `Do not use when` の境界を記載しています。互換形式のホストで使えますが、第三者ホストでの動作は個別に検証していません。

### 自分のスキルを作成・検証する

`/skill-author` と会話して再利用可能な `SKILL.md` を下書きし、実際にエクスポートされる ZIP をローカルで検査します。

```bash
python3 -m actor skills lint --target agensi
python3 -m actor skills test
```

lint は ZIP の配置、メタデータ、ファイル種別・サイズ、資格情報の漏えいらしき文字列、危険な隠蔽・プロンプトインジェクション指示などを確認します。`skills test` は必須の出力見出しとテストケースをモデル呼び出しなしで検査します。`--live` を指定した場合のみ設定済みプロバイダーを使います。

```bash
python3 -m actor skills export minutes-writer --target agensi -o minutes-writer.zip
```

エクスポートは共有 principles と正規の MIT `LICENSE` を含む、再現可能なスキル単体 ZIP を作ります。ZIP 全体を確認し、提出先を選んで自分で提出してください。Actor-si がアップロード・公開・提出を行うことはありません。

### 同梱の5つのスキル

- **`skill-author`** — メタデータ、手順、参照資料、テスト可能な出力形式を備えた `SKILL.md` を設計します。再利用手順の作成・改善向けで、単発の回答には不要です。
- **`release-notes`** — 提供されたコミット、PR、変更一覧からユーザー向けリリースノートを作ります。固定の全見出しを使い、空欄は `None` とします。実際の変更一覧がない場合やコミットメッセージ作成には使いません。
- **`code-review`** — 提供された差分や PR の不具合・セキュリティリスクを重大度順に報告し、最小限の修正案を示します。問題がなければ `LGTM` のみを返します。実装や一般的なコード説明には使いません。
- **`minutes-writer`** — 文字起こしやメモから決定事項、アクション、担当、期限、未解決点を含む議事録を作ります。日本語版は `i18n/ja/skills` にあります。会議記録用であり、一般的な要約向けではありません。
- **`brand-voice`** — ユーザーが提供した文章を、事実と意味を保ちながら落ち着いた一貫性のある文体に整えます。既存文章の推敲向けで、事実確認や根拠のない新規事実の作成には使いません。

### 提供しないもの

Actor-si はホスト型サービスではありません。同梱スキル自体が外部システムを操作したり、コンテンツを提出したりすることもありません。ユーザーが提供した材料をもとにテキストを作成・支援します。Actor-si の任意ネットワークツールは既定で無効で、使う場合も明示的な有効化が必要です。外部サービスを直接操作するスキルは同梱していません。

## 30秒で試す

```bash
git clone https://github.com/TonbokiriRaikiriMuramasa-Taikoubou/Actor-si.git
cd Actor-si

# 依存インストール不要（Python 3.9+ があれば動きます）
python3 -m actor "会議の文字起こしから議事録を作って"
```

> **API キーの置き場所**（詳細はすぐ下）: リポジトリ直下の **`.env`** が基本。
> `cp .env.example .env` でファイルを作り、API キーはローカルで安全に設定してください。Web UI 左下の **🔑** /
> `python3 -m actor key set` から保存する方法もあります（保存先 `.actor/api_key`・gitignore 済み）。

API キーが無くても **内蔵シミュレータ**が動きます（スキルの選択・合成・ストリーミング・UI はすべて本番と同じ経路）。

### API キーの設定場所（4つの方法）

いちばん分かりやすいのは **リポジトリ直下の `.env`**（1ファイル・gitignore 済み）です。
ファイルを触りたくない場合は、**Web UI の左下 🔑** か **`actor key set`** でも同じ場所に保存できます。

```bash
# ① .env に書く（いちばん明示的・推奨）
echo 'GEMINI_API_KEY=（ローカルの `.env` に安全に設定）' >> .env

# ② UI から入れる: serve して左下の 🔑 →「保存して接続」（.actor/api_key に保存）
python3 -m actor serve --port 8080

# ③ CLI から入れる: 入力は画面に出ません（.actor/api_key に保存）
python3 -m actor key set --check

# ④ 複数キー（プレビュー版モデルの 429 対策）: 何本でも足せる
python3 -m actor key add --check         # .actor/keys/key-1.key … に 1 本ずつ保存
python3 -m actor key add --env           # .env の GEMINI_API_KEYS に追記する（コメントは保つ）
# もしくは環境変数 / .env でまとめて:  複数キーは `.env` または環境変数で設定します。値は表示・共有しないでください。
```

| 置き場所 | パス | 備考 |
| --- | --- | --- |
| `.env` | `<リポジトリ>/.env` | **最優先**。`GEMINI_API_KEY=` でも `GEMINI_API_KEYS=`（複数）でも |
| 環境変数 | `GEMINI_API_KEY` / `GEMINI_API_KEYS` | CI や systemd 向け（カンマ・空白・`;` 区切り） |
| 保存ファイル | `.actor/api_key` | UI / `actor key set` が保存（chmod 600・gitignore 済み） |
| keys ディレクトリ | `.actor/keys/key-*.key` | `actor key add` が保存（1 本 1 ファイル・chmod 600） |

優先度は **`--api-key` > 環境変数 > `.env` > `.actor/api_key` > `.actor/keys/`**。確認と後片付け:

```console
$ python3 -m actor key show
[伏せ字] （/home/user/Actor-si/.actor/api_key）
プロバイダ: gemini / モデル: gemini-3-flash / キー 2 本（2/2 本が使用可能）

$ python3 -m actor key list                 # 全部のキーと状態（429 の残り時間つき）
API キー 2 本 （1/2 本が使用可能）
  *[0] [伏せ字]              使用中 .actor/api_key
   [1] [伏せ字]              429 制限中（あと 42 秒） .actor/keys/key-1.key

$ python3 -m actor key check                # 1 本ずつ接続確認（--json も可）
$ python3 -m actor key stats                # 429 でよく切れるキーを見る（--json / --reset 可）
$ python3 -m actor key rm 1                 # その番号のキーだけ消す（.env の行からも消せる）
$ python3 -m actor key clear                # 保存したキーを消す（.env はそのまま）
```

キーはサーバーから外へ出ません（`/api/config`・`/api/key` は伏せ字・本数・統計だけ）。
Web UI の 🔑 にも「N キー（空き M）/ 429 で K 回切替」が出ます。`ACTOR_ROOM_TOKEN` を設定すると、キー変更だけでなく全 `/api/*` リクエストに同じトークンが必要です。

> 接続確認（🔑 の「保存して接続」/ `actor key set --check`）で API に届かない場合は、
> **キーは保存したままシミュレータで動き続けます**（会話が壊れないように、その場では切り替えません）。
> ネットワークが使える環境で `actor key show` を見れば、次回起動から実モデルになります。

### 429（Quota exceeded）が発生したとき

プレビュー版のモデルは無料枠だと `429 ResourceExhausted / Quota exceeded` がよく出ます。
Actor-si は **キー → モデル** の順に逃げ道を用意しています（どちらも自動・必ず表示されます）。

**① まずキーを替える。** 複数のキーを持たせておけば、その回のリクエストを次のキーへ回します。

- 429 / 401 / 403 を受けたキーは `ACTOR_KEY_COOLDOWN`（既定 60 秒）だけ「使用中」から外れます
  （待ち時間は 429 の `RetryInfo.retryDelay` があればそちらを優先）
- 全部のキーが制限中になったら、**どのキーが何秒後に復帰するか**を書いたエラーで止まります（嘘の応答は返しません）
- どのキーがよく切れるかは `actor key stats`（`uses` / `errors` / 429 の回数）。1 本だけ極端に切れるなら、
  そのキーが Gemini アプリや他のツールと共有されていないか疑ってください
- ログ・エラー・状態ファイルには **キー本体を出しません**（伏せ字と指紋だけ）
- 切り替えが不要なら `ACTOR_KEY_ROTATE=false`（従来どおり同じキーでリトライのみ）

```console
$ python3 -m actor key stats
API キーの利用統計 （.actor/key_state.json）
  通算: リクエスト 120 回 / 429 で切れた 14 回 / 切り替え 14 回
  モデル別
    gemini-3.8-flash     429 12 回 / リクエスト 40 回 / いま 1/2 本が使用可
    gemini-3.5-flash     429 2 回 / リクエスト 80 回 / いま 2/2 本が使用可
  429 でよく切れる順
  [0] [伏せ字]              429 12 回 / 試行 40 回（30%） 使用中 [gemini-3.8-flash 12]
よく切れているキー: [0] [伏せ字]（429 12 回 / 試行 40 回）
いちばん切れているモデル: gemini-3.8-flash（429 12 回）
```

**② それでも切れるなら別モデルへ逃がす。** 枠は「プロジェクト × モデル」単位なので、
`gemini-3.8-flash` が切れていても `gemini-2.5-flash` なら通ります。主モデルのキーが全部 429 になったら、
逃げ道のモデルで自動的に再試行します（既定: `gemini-3.5-flash` → `gemini-2.5-flash`）。

```console
$ python3 -m actor "議事録を作って"
🔁 gemini-3.8-flash で全キーが 429 → gemini-3.5-flash に逃がします
（以下、gemini-3.5-flash の応答）
```

- 逃げたことは必ず表示されます（`🔁` / `--json` の `notices` / Web UI の本文）。黙って別モデルにはしません
- 主モデルが制限中と分かっているターンは最初から逃げ道で試します（無駄な 429 を送らない）
  → `ACTOR_MODEL_FALLBACKS=...` で逃げ道を決める / `--no-model-fallback` で逃げない

**③ 利用可能な認証先を確認する。** `actor key sources` に公式の利用先をまとめています。キーを作り直しても、同じプロジェクト・モデルの割り当てが増えるわけではありません。

```console
$ python3 -m actor key sources
  Google AI Studio [無料枠]              https://aistudio.google.com/apikey
  Vertex AI エクスプレスモード [90 日無料]  https://console.cloud.google.com/expressmode
  Google Cloud 無料トライアル [クレジット]  …
```

- **Vertex AI エクスプレスモード**（新規 Cloud ユーザー・90 日無料・課金設定なし）は API キーで使える別枠です。
  `ACTOR_BASE_URL=https://aiplatform.googleapis.com` を設定すれば Actor-si が API の形（`v1` /
  `publishers/google/models/{model}`）を自動で合わせます
- ⚠️ 複数アカウントで制限を回避するのは規約違反です。無料枠では送信内容が製品改善に使われ得るため、
  機密は送らないでください（詳しくは [docs/keys.md](docs/ja/keys.md)）

| やりたいこと | コマンド |
| --- | --- |
| 対話する（CLI） | `python3 -m actor` |
| Web UI / API を起動 | `python3 -m actor serve --port 8080` |
| 1回だけ実行 | `python3 -m actor "差分をレビューして"` |
| 結果を JSON で受け取る | `python3 -m actor --json "議事録を作って"` |
| スキル一覧 | `python3 -m actor skills list` |
| 使える道具の一覧 | `python3 -m actor tools` |
| 画像を添えて質問 | `python3 -m actor --attach ./図.png "この図を説明して"` |
| API キーの保存 | `python3 -m actor key set --check` |
| キーの一覧 / 追加 / 確認 / 統計 | `python3 -m actor key list` / `key add` / `key check` / `key stats` / `key sources` |
| 環境チェック | `python3 -m actor doctor --check-api` |

`pip install -e .` すると `actor` コマンドとしても使えます（`actor serve` など）。

---

## 常駐ボット

「決まった人格・決まった手順で、ずっとそこに居るボット」を作れます。

```bash
# 端末1: サーバー
python3 -m actor serve --port 8080

# 端末2: ペルソナ「ミカ」を固定適用した常駐ボット
python3 -m actor bot --room actor-x --skills mika --name ミカ \
    --skills-dir skills --skills-dir examples/mika --from-now

# 端末3: 話しかける（どんなアプリからでも・HTTP 1本）
curl -s -X POST localhost:8080/api/rooms/actor-x/messages \
  -H 'Content-Type: application/json' -d '{"text":"おはよー","author":"TRMTKB"}'

# 会話を眺める
python3 -m actor rooms show actor-x
```

- ボットは **Actor-si の完全なインスタンス**（スキル・会話履歴・モデル設定をローカルに持つ）。ルームは合流点
- **SKILL.md を編集すると次のターンから反映**される（動かしたままペルソナを育てられる）
- ルームは追記専用の JSON ログ。`after=<seq>` で差分ポーリングするので、どこからでも合流できる
- 立ち上げ方は端末・nohup・systemd・cron・タスクスケジューラまで → **[docs/bot.md](docs/ja/bot.md)**

サーバー側でその場で答えるだけなら、ボットなしでも 1 リクエストで済みます:

```bash
curl -s -X POST localhost:8080/api/rooms/actor-x/messages \
  -H 'Content-Type: application/json' \
  -d '{"text":"会議メモから議事録を作って","reply":true}' | jq -r '.reply.text'
```

## 道具（function calling）

ボットは与えられた道具を **自分で選んで実行**します（`🛠` として表示・記録されます）。

```bash
python3 -m actor tools                      # 使える道具の一覧
python3 -m actor tools --json               # 宣言（JSON Schema）まで確認
python3 -m actor tools --run now            # 道具だけを直接実行（動作確認）
python3 -m actor "今日は何日？ README.md の冒頭も教えて"   # モデルが now / read_file を呼ぶ
```

| 道具 | 内容 | 既定 |
| --- | --- | --- |
| `now` | 現在の日時・曜日 | 有効 |
| `read_file` / `list_files` | `ACTOR_TOOLS_ROOT` 内の読み取り・一覧 | 有効 |
| `list_skills` / `read_skill` | 読み込み済み SKILL.md を探す・読む | 有効 |
| `write_file` | ファイル書き込み（`ACTOR_TOOLS_ALLOW_WRITE=true`） | 無効（危険） |
| `http_get` | 外部への GET（`ACTOR_TOOLS_ALLOW_NETWORK=true`） | 無効（危険） |

- パスは `ACTOR_TOOLS_ROOT`（既定はカレント）の外へ出られません（`../` や絶対パスは拒否）
- スキルの frontmatter に `allowed-tools: [read_file, now]` と書くと、**そのスキルを使っている間だけ**その和集合に絞られます
- 1ターンの往復回数は `ACTOR_MAX_TOOL_ROUNDS`（既定 4・最後の1回は答えを書くために残す）
- `python3 -m actor serve` の `/api/health` に `tools` が、UI には道具チップが出ます

> シミュレータ（API キーなし）でも道具の流れは同じ経路で動きます（`now` / `list_skills` / `read_file` を自動で試します）。
> 本物の Gemini では、モデルが `functionCall` を返したときに同じループが回ります（REST / SDK の両方）。

## 画像・PDF を添付する

```bash
python3 -m actor --attach ./design.png "この画面の課題を3つ挙げて"
python3 -m actor --attach ./仕様.pdf --attach ./メモ.md "仕様の抜けを指摘して"
python3 -m actor rooms say "この図を見て" --attach ./図.png --reply   # ルーム経由
```

- Web UI では入力欄の **📎** から選択（画像はサムネイル付きで表示されます）
- HTTP API では `attachments: [{name, mime_type, data}]`（`data` は base64）を `POST /api/chat`・`/api/rooms/<name>/messages` に渡す
- 上限は既定で **1件 10MB / 6件**（`ACTOR_MAX_ATTACHMENT_MB`・`ACTOR_MAX_ATTACHMENTS`）
- 添付はプロバイダへ `inlineData` として渡り、セッション / ルームの JSON に保存（`ACTOR_STORE_ATTACHMENTS=false` で当該ターンのみ）
- 一覧 API は base64 を返しません（中身が要るときは `GET /api/rooms/<name>?attachments=1`）

## Portable Agent Skills / Agensi

Agensi 向けエクスポートプロファイル、lint の制約、portable な `SKILL.md` の互換性は [docs/agensi.md](docs/ja/agensi.md) と [docs/skills-compat.md](docs/ja/skills-compat.md) を参照してください。Lint は保守的な近似であり、審査通過を保証しません。変更履歴は [CHANGELOG.md](CHANGELOG.md)、[v0.2.1 セキュリティ修正リリースノート](docs/ja/release-notes-v0.2.1.md)、[v0.2.0 リリースノート](docs/ja/release-notes-v0.2.0.md) を参照してください。

## ルームのトークン保護と Webhook

`ACTOR_ROOM_TOKEN` はすべての `/api/*` エンドポイントを保護します。非ループバックへ bind する際は必須です。Webhook は必要な場合だけ設定してください。

```bash
export ACTOR_ROOM_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" # ローカルで生成
export ACTOR_WEBHOOK_URL='https://example.com/actor-hook'
export ACTOR_WEBHOOK_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" # 署名検証用
python3 -m actor serve --port 8080
```

```bash
curl -s -X POST localhost:8080/api/rooms/actor-x/messages \
  -H "X-Actor-Token: ${ACTOR_ROOM_TOKEN}" \
  -H 'Content-Type: application/json' -d '{"text":"おはよー"}'
# Authorization: Bearer <token> も可。URLクエリにはトークンを付けないでください。
```

- トークン未指定または不正なリクエストは **403**。Web UI は起動時にサーバーがトークンを要求した場合、入力を促します。API クライアントでは `X-Actor-Token` または `Authorization: Bearer` ヘッダーを設定してください。
- 発言があると `message` イベントを JSON で POST します:
  `{"event":"message","ts":...,"room":"actor-x","message":{...},"reply":{...},"count":2}`
- 署名は `HMAC-SHA256(secret, body)` の `sha256=<hex>`（GitHub 方式）なので、受け側で検証できます
- 送信は非同期・失敗しても会話は止まりません（5xx は最大3回リトライ）。`/api/health` の `webhook_stats` で確認できます

## CLI

```
$ python3 -m actor
🎭 Actor-si v0.2.1 — SKILL.md 駆動の Gemini 対話クライアント
/help でコマンド一覧、/exit で終了。
シミュレータで動作中 — GEMINI_API_KEY を設定すると本物の Gemini に切り替わります。
スキル 5 件 / 保存先 /path/to/Actor-si/.actor

you [-,auto|think] › コミットログからリリースノートを書いて
▶ 適用スキル: release-notes(15.0)
  system_instruction: 4,102 文字 ≈ 1,188 tokens
（応答がストリーミング表示されます）
tokens: in 1,240 / out 812  model: gemini-3-flash
```

### スキルの呼び出し方

| 方法 | 例 | 挙動 |
| --- | --- | --- |
| `/` で呼び出す | `/minutes-writer 会議メモから議事録` | そのターンだけ適用 |
| 固定（スタック） | `/use code-review brand-voice` | 以降のターンも適用。複数可 |
| 自動選択 | 何も指定しない | 依頼文のスコアが閾値以上なら適用 |
| 解除 | `/use none` | 固定を解除 |

REPL コマンド: `/help` `/skills` `/use` `/auto` `/think` `/stream` `/model` `/system` `/show` `/new` `/sessions` `/load` `/title` `/create` `/export` `/exit`

便利な機能:

- `/create 週次レポートを毎回同じ形式で作るスキル` … 会話から SKILL.md を生成 → 確認 → 保存
- `/export` … Gemini アプリにアップロードできる ZIP（最上位に `SKILL.md`）を書き出し
- `--json` … パイプや CI から扱いやすい JSON 出力
- 標準入力からのパイプ … `cat meeting.txt | python3 -m actor "これを議事録にして"`

---

## Web UI

`python3 -m actor serve` して `http://localhost:8080` を開くと、Gemini のスキルに寄せた UI が出ます。

- **左**: スキル一覧（クリックでスタック適用、`ⓘ` で詳細）／会話履歴
- **中央**: チャット。入力欄で `/` を打つとスキル候補が出て、`↑↓` + `Enter` で確定
- **右**: インスペクタ。**適用スキル / 自動選択スコア / 実際の system_instruction / トークン数**をリアルタイム表示
- **応答内**: `🧠 思考ログ`（thinkingConfig）と、どのスキルが効いたかのチップ表示
- **「会話からスキルを作る」**: 日本語で説明 → ドラフト生成 → その場で編集して保存

UI はビルド不要の素の HTML/CSS/JS（`actor/web/`）です。CDN も外部フォントも読みません。

---

## HTTP API

| メソッド | パス | 説明 |
| --- | --- | --- |
| GET | `/api/health` | 稼働状況（プロバイダ・スキル数・API キーの本数） |
| GET | `/api/config` | 設定（キーの値は返しません） |
| GET/POST/DELETE | `/api/key` | キーの状態確認（本数・429 の残り時間・通算の統計）/ 保存して即切り替え / 解除（私用向け） |
| GET | `/api/skills?q=議事録` | スキル一覧（`q` で自動選択スコアも返す） |
| GET | `/api/skills/<name>?full=1` | スキル詳細（合成後の `system_instruction` を含む） |
| POST | `/api/skills` | スキル作成 `{name, description, instructions}` |
| PUT | `/api/skills/<name>` | スキル更新 |
| DELETE | `/api/skills/<name>` | スキル削除 |
| POST | `/api/skills/draft` | `{request}` から SKILL.md のドラフトを生成 |
| GET | `/api/skills/export?names=a,b` | ZIP 書き出し |
| POST | `/api/select` | `{message, skills}` の選択結果だけを先読み（LLM を呼ばない） |
| GET/POST | `/api/sessions` | 会話一覧 / 新規作成 |
| GET/DELETE | `/api/sessions/<id>` | 会話の取得 / 削除 |
| GET/POST | `/api/rooms` | ルーム一覧 / 作成 |
| GET | `/api/rooms/<name>?after=<seq>` | ルームの差分取得（常駐ボットの合流点・`attachments=1` で base64 も） |
| POST | `/api/rooms/<name>/messages` | 発言を追記（`reply: true` でその場で返事も生成・`attachments` 可・トークン必須） |
| DELETE | `/api/rooms/<name>` | ルーム削除（トークン必須） |
| POST | `/api/chat` | 1ターン実行（JSON） |
| POST | `/api/chat/stream` | 1ターン実行（**SSE** でトークン逐次配信） |

```bash
# スキルが自動選択される様子を確認（LLM は呼ばれません）
curl -s localhost:8080/api/select -H 'Content-Type: application/json' \
  -d '{"message":"コミットログからリリースノートを書いて"}' | jq
# → {"names":["release-notes"],"auto_scores":[{"name":"release-notes","score":15.0}], ...}

# 1ターン実行
curl -s localhost:8080/api/chat -H 'Content-Type: application/json' \
  -d '{"message":"議事録を作って","skills":["minutes-writer"]}' | jq -r .text

# ストリーミング（SSE）
curl -N localhost:8080/api/chat/stream -H 'Content-Type: application/json' \
  -d '{"message":"この差分をレビューして"}'
```

イベント種別: `open` → `selection` → `prompt`（合成済み system_instruction）→ `thought` / `tool_call` / `tool_result` → `text` → `done`（usage・`tool_calls`・セッションID）。

---

## SKILL.md の書き方

```
skills/
  minutes-writer/
    SKILL.md              ← 必須（フォルダの最上位）
    references/
      format.md           ← 任意。テキストは system_instruction に自動同梱
      style.pdf           ← 任意。画像/PDF は inlineData として添付
  code-review.md          ← 単体 .md ファイルでも可
```

````markdown
---
name: minutes-writer
description: 会議の文字起こしから決定事項とアクションアイテムを抽出して議事録に整形する
version: 1.0.0
tags: [議事録, 会議]
temperature: 0.4           # 任意: スキル単位で生成パラメータを上書きできる
greeting: 議事録を作ります   # 任意: 常駐ボットの起動あいさつ
---

# 目的
（このスキルが何を達成するか）

# 入力
- 必要な材料

# 手順
1. 具体的な動詞で書く
2. 誰が実行しても同じ結果になる粒度にする

# 出力フォーマット
```markdown
## 結論
```

# 確認事項
- やってはいけないこと（捏造・誇張・勝手な補完）
````

ルール（Google のスキル仕様に合わせています）:

- `name` は **ケバブケース**（`my-skill`）。大文字・アンダースコア・日本語は正規化されます（警告つき）
- frontmatter で `temperature` / `top_p` / `max_output_tokens` / `thinking_budget` / `model` を書くと、そのスキルを適用したターンだけパラメータが変わる（複数スキルでは先に適用されたものを優先）
- `SKILL.md` はフォルダの最上位に置く。ZIP にする場合も最上位に必要
- 参照ファイルの合計は 100MB 以下（Gemini 側の制限）。テキストは既定 24,000 文字まで `system_instruction` に同梱
- ネストしたコードフェンス（テンプレート内の ```` ``` ````）も壊れないよう、参照資料は長いフェンスで包んで埋め込みます

## 自動選択の仕組み

依頼文とスキル名／description／タグを、英単語と日本語の文字バイグラムで照合し、決定的にスコアリングします。

| 条件 | 加点 |
| --- | --- |
| `/name` `@name` で明示 | +100 |
| スキル名が依頼文に含まれる | +5 |
| 名前に単語一致 / 部分一致 | +5 / +3 |
| description の単語・バイグラム一致 | +1 〜 +3 |
| タグ一致 | +1.5 |

閾値（既定 `2.0`）以上を最大 `3` 件まで採用。`ACTOR_AUTO_SELECT_THRESHOLD` などで調整できます。
選ばれたスキルは UI のインスペクタでスコア付きで確認できます。

---

## 設定

`.env`（`.env.example` 参照）／環境変数／CLI 引数のいずれでも設定できます。優先度は **CLI > 環境変数 > .env**。

### プロバイダの選択

| `--provider` | 実装 | 追加依存 | 用途 |
| --- | --- | --- | --- |
| `auto`（既定） | キーがあれば `gemini`、無ければ `mock` | なし | とりあえずこれ |
| `gemini` | `urllib` で REST を直叩き | なし | 依存を増やしたくない常駐ボット |
| `genai` | 公式 **`google-genai` SDK** | `pip install google-genai` | 参考コードと同じ SDK で動かしたいとき |
| `mock` | 内蔵シミュレータ | なし | オフライン検証・UI 開発 |

`google-genai` を入れておくと自動で SDK 経由になります（明示するなら `ACTOR_GEMINI_IMPL=genai`）。
キーがどこから読まれたかは `actor doctor` の「API キー」行（出どころつき）で確認できます。

| 変数 | 既定 | 説明 |
| --- | --- | --- |
| `GEMINI_API_KEY` | – | Google AI Studio のキー。未設定ならシミュレータ |
| `GEMINI_API_KEYS` | – | 複数キー（カンマ・空白・`;` 区切り）。429 は自動で次のキーへ |
| `ACTOR_API_KEY_FILE` | `./.actor/api_key` | UI / `actor key set` が保存する場所 |
| `ACTOR_KEYS_DIR` | `./.actor/keys` | `actor key add` 1 本 1 ファイルの保存先（`--env` なら `.env`） |
| `ACTOR_KEY_COOLDOWN` | `60` | 429 のあと、そのキーを避ける秒数 |
| `ACTOR_KEY_ROTATE` | `true` | 429 / 401 / 403 で次のキーへ自動で切り替える |
| `ACTOR_PROVIDER` / `ACTOR_GEMINI_IMPL` | `auto` / `gemini` | `auto`/`gemini`/`genai`/`mock` と、キーがあるときの実装 |
| `ACTOR_MODEL` | `gemini-3-flash` | モデル名 |
| `ACTOR_MODEL_FALLBACKS` | `gemini-3.5-flash,gemini-2.5-flash` | 429 のときだけ使う逃げ道のモデル（空で無効） |
| `ACTOR_BASE_URL` | `https://generativelanguage.googleapis.com` | 互換エンドポイント（Vertex は `https://aiplatform.googleapis.com`） |
| `ACTOR_API_VERSION` / `ACTOR_MODEL_PATH` | `v1beta` / `models/{model}` | Vertex エクスプレスモードでは自動で `v1` / `publishers/google/models/{model}` |
| `ACTOR_TEMPERATURE` / `ACTOR_TOP_P` / `ACTOR_MAX_OUTPUT_TOKENS` | 0.7 / 0.95 / 4096 | 生成パラメータ |
| `ACTOR_THINKING_BUDGET` | `0` | 思考トークン予算。>0 で `thinkingConfig` を有効化 |
| `ACTOR_SKILLS_DIR` | `./skills` | スキルフォルダ（複数可、区切りは `:` か `,`） |
| `ACTOR_AUTO_SELECT` / `ACTOR_MAX_AUTO_SKILLS` / `ACTOR_AUTO_SELECT_THRESHOLD` | true / 3 / 2.0 | 自動選択の制御 |
| `ACTOR_INLINE_REFERENCE_LIMIT` | 24000 | 参照テキストを埋め込む上限（文字） |
| `ACTOR_DATA_DIR` / `ACTOR_HOST` / `ACTOR_PORT` | `./.actor` / 127.0.0.1 / 8080 | 保存先とサーバー |
| `ACTOR_TOOLS` | `now,read_file,list_files,list_skills,read_skill` | 有効にする道具（`actor tools` で確認） |
| `ACTOR_TOOLS_ROOT` | `.` | 道具が読み書きできる範囲（サンドボックス） |
| `ACTOR_TOOLS_ALLOW_WRITE` / `ACTOR_TOOLS_ALLOW_NETWORK` | false | `write_file` / `http_get` を許可する |
| `ACTOR_TOOLS_TIMEOUT` / `ACTOR_MAX_TOOL_ROUNDS` | 15 / 4 | 道具の実行タイムアウトと往復回数 |
| `ACTOR_MAX_ATTACHMENT_MB` / `ACTOR_MAX_ATTACHMENTS` | 10 / 6 | 添付の上限 |
| `ACTOR_STORE_ATTACHMENTS` | true | 添付の base64 を保存する（false なら当該ターンのみ） |
| `ACTOR_ROOM_TOKEN` / `ACTOR_ROOM_PROTECT_READS` | – / false | 設定時は全 API を認証。読み取り限定の旧オプションは互換用 |
| `ACTOR_CORS_ORIGINS` / `ACTOR_ALLOWED_HOSTS` | 空 / 空 | 明示的に許可する CORS Origin と Host（カンマ区切り） |
| `ACTOR_WEBHOOK_URL` / `ACTOR_WEBHOOK_SECRET` | – | 発言を外部へ POST（HMAC 署名つき） |
| `ACTOR_WEBHOOK_EVENTS` / `ACTOR_WEBHOOK_TIMEOUT` | `message` / 10 | 通知するイベントとタイムアウト |

補足:

- API キーは **サーバーの外へ出しません**（`/api/config` / `/api/key` は伏せ字・本数・出どころだけ返します）
- `.actor/sessions/*.json` に会話が保存されます。`.gitignore` 済み
- 5xx / タイムアウトは同じキーで指数バックオフ（最大3回）、429 は **キーを替えて再試行**します。
  キーが全部 429 なら **モデルを替えて再試行**します（状態は `.actor/key_state.json` にキーの指紋とモデル別の枠だけ保存）

---

## 構成

```
actor/
  __main__.py    python -m actor
  cli.py         対話 REPL / 1回実行 / skills・doctor コマンド
  server.py      HTTP サーバー（標準ライブラリのみ・SSE 対応）
  chat.py        1ターンの流れ（選択 → 合成 → 呼び出し → 保存）
  skills.py      SKILL.md の解析・検証・検索・生成・ZIP 書き出し
  prompt.py      複数スキル → system_instruction の合成
  providers.py   Gemini REST クライアント（urllib）+ オフラインシミュレータ
  genai_provider.py  公式 google-genai SDK 経由のバックエンド（任意依存）
  tools.py       道具（function calling）とサンドボックス
  rooms.py       ルーム（常駐ボットの共有ログ）
  webhooks.py    Webhook 送出（HMAC 署名・非同期リトライ）
  bot.py         常駐ボット本体
  sessions.py    会話の永続化
  config.py      設定（.env / 環境変数 / CLI）
  keys.py        複数キーのプール（429 で次のキーへ・伏せ字・状態の保存）
  web/           Web UI（index.html / styles.css / app.js）
skills/          サンプル: minutes-writer, code-review, release-notes, brand-voice, skill-author
examples/mika/   常駐ボット用ペルソナの例（temperature 0.85）
docs/bot.md      常駐ボット運用ガイド（systemd / cron / 複数ボット）
docs/skills-compat.md  SKILL.md 互換メモ（allowed-tools など Actor-si 独自キー）
docs/keys.md      複数キーと 429 の自動切り替え（設定・コマンド・設計）
HANDOFF.md       引き継ぎ資料（全体像・運用手順・現在地）
tests/           unittest + portable skill / security checks + optional UI smoke tests
```

## テスト

```bash
python3 -m unittest discover -s tests -v      # full unit suite（依存ゼロ / google-genai があれば SDK 経路も検証）

# UI のスモークテスト（任意・要 Node）
npm install --no-save --package-lock=false jsdom
export ACTOR_ROOM_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
python3 -m actor serve --port 8080 &
server_pid=$!
for i in $(seq 1 30); do
  curl -fsS -H "X-Actor-Token: $ACTOR_ROOM_TOKEN" http://127.0.0.1:8080/api/health -o /dev/null && break
  sleep 1
done
node tests/ui_smoke.mjs
kill "$server_pid"
```

## セキュリティと注意

- スキルは **ローカルの Markdown** です。Actor-si はスキル内のスクリプトを実行しません（内容を `system_instruction` に渡すだけ）。Gemini 側もアップロードされたスクリプトを実行しません
- 社外秘をスキルに入れないでください。スキルの本文・参照資料は API へ送信されます
- 生成された議事録などをそのまま社外へ出す前に、人が確認してください（サンプルスキルにも「要確認」を残す指示を書いています）
- サーバーは既定で `127.0.0.1` に bind します。LAN / 外部へ bind する場合は `ACTOR_ROOM_TOKEN` を必ず設定してください。未設定なら起動を拒否します。
- トークンを設定すると `/api/*` の全エンドポイント（チャット、履歴、スキル、キー、ルーム等）に必要です。`X-Actor-Token` または `Authorization: Bearer` を使います。
- CORS は既定で無効です。ブラウザから別 Origin でアクセスする場合だけ `ACTOR_CORS_ORIGINS=https://your.example` を設定してください。Host は localhost / loopback / 明示した bind host / `ACTOR_ALLOWED_HOSTS` のみ許可します。ワイルドカード bind でLAN IPを使う場合は `ACTOR_ALLOWED_HOSTS` に追加してください。
- 道具は既定で **読み取り専用** です。`write_file` / `http_get` は明示的に許可したときだけ有効になり、
  パスは `ACTOR_TOOLS_ROOT` の中に閉じ込められます（`../` や絶対パスは拒否）
- 添付した画像・PDF は Gemini API へ送信されます。機密のスクリーンショットを上げる前に確認してください

### 秘密情報の管理

- API キーと `ACTOR_ROOM_TOKEN` をソース管理へ追加しないでください。漏えい時はキーを無効化し、トークンを変更してサーバーを再起動してください。
- ローカルでない環境では TLS を終端する信頼できるリバースプロキシを使い、不要なポート公開を避けてください。
- 脆弱性の連絡先と対応方針は [SECURITY.md](SECURITY.md) を参照してください。

## 参考

- [支援について](FUNDING.ja.md) — GitHub Sponsors の扱い、支援金の用途、プロジェクトの方針
- Google 公式ブログ: *Let skills in Gemini tackle your most repetitive tasks*（2026-09-30）
- Google Workspace Updates: *Introducing skills in the Gemini app and Workspace, plus what's next for Gems*（2026-09-30）
- [SKILL.md 互換メモ](docs/ja/skills-compat.md)
- [API キーの複数管理と 429 の自動切り替え](docs/ja/keys.md)
- [HANDOFF.md](HANDOFF.md) — 引き継ぎ資料（現在地・運用手順・次の一手）

## 参考 SDK との対応

```python
# 参考にされた書き方（google-genai）
client = genai.Client()                       # GEMINI_API_KEY を自動読み込み
chat = client.chats.create(
    model="gemini-2.5-flash",
    config=types.GenerateContentConfig(
        system_instruction=load_skill_prompt("./skills/mika/SKILL.md"),
        temperature=0.85,
    ),
)
response = chat.send_message("こんにちは。")
```

Actor-si での対応:

| 参考コード | Actor-si |
| --- | --- |
| `load_skill_prompt("./skills/mika/SKILL.md")` | `SkillLibrary(...).get("mika")` → 複数スキルを1つの `system_instruction` に合成 |
| `config=temperature=0.85` | SKILL.md の frontmatter に `temperature: 0.85` と書く |
| `client.chats.create(...)` | `ChatEngine` + `.actor/sessions/*.json`（ターンごとにスキルを差し替えたいので履歴は自前で保持） |
| `chat.send_message("こんにちは。")` | `actor "こんにちは"` / `POST /api/chat` / 常駐ボットなら `POST /api/rooms/<room>/messages` |
| `client = genai.Client()` | `--provider genai`（SDK あり）/ `--provider gemini`（SDK なし・urllib 実装） |
| `config=tools=[...]`（function calling） | スキルの `allowed-tools` と `ACTOR_TOOLS`。ループは `ChatEngine.turn()` が回す |
| `types.Part(inline_data=...)`（画像/PDF） | `--attach ./図.png` / UI の 📎 / `attachments` フィールド |
| `print(response.text)` | CLI 表示・Web UI・SSE ストリーム・ルームへの書き込み |

SDK 経由でも `systemInstruction` に SKILL.md が入り、`temperature` がスキル定義で上書きされることを、
偽サーバーを使った回帰テスト（`tests/test_actor.py` の `GenAiWireTest`）で検証しています。

## ライセンス

MIT（[LICENSE](LICENSE)）
