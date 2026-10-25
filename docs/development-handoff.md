# Development handoff snapshot — Actor（2026-10-13）

> Historical notes retained from the supplied archive. They describe an earlier self-use setup and are not current security guidance. For the current repository state and validation steps, use the root [`HANDOFF.md`](../HANDOFF.md), `README.md`, `SECURITY.md`, and current docs.

引き継ぎ用のサマリです。**いまどこまで出来ていて、どう動かして、次に何をすると良いか**を、
README より少し踏み込んで書いています（コマンドはそのままコピペで動きます）。

最終更新: 2026-10-13 / ブランチ `arena/ad1924bc-actor`（複数 API キー + 429 のキー/モデル自動切り替え）

---

## 1. これは何か

**SKILL.md（Gemini のスキル）を `system_instruction` として読み込み、Gemini API と対話する自分専用クライアント**です。

- 用途の想定は「**自分だけのクライアント / 常駐ボット**」。人に見せる前提ではなく、1人で使う前提の作り
- 依存ゼロ（Python 標準ライブラリのみ）で動く。`google-genai` SDK を入れると SDK 経由にも切り替わる
- CLI・HTTP API・Web UI（Gemini のスキルに寄せた `/` 呼び出し UI）の3つの入口
- API キーが無くても **内蔵シミュレータ（mock）** で全経路が動く（＝オフラインで開発・確認できる）

```
┌────────────┐   /skills/<name>/SKILL.md    ┌──────────────┐
│  CLI       │──┐                           │ system_      │
│  Web UI    │──┼──▶ Actor (ChatEngine) ──▶│ instruction  │──▶ Gemini API
│  HTTP API  │──┘   選択・合成・道具・保存  │ (+ 参照資料) │
│  常駐ボット │──┘                           └──────────────┘
└────────────┘
```

## 2. 現在地（できていること）

| 機能 | 状態 |
| --- | --- |
| SKILL.md の読み込み・合成・自動選択・ZIP 書き出し | ✅ |
| CLI（対話 / 1回実行 / `--json` / パイプ入力） | ✅ |
| HTTP API（`/api/chat`・`/api/chat/stream` の SSE） | ✅ |
| Web UI（スキル呼び出し・インスペクタ・思考ログ・スキル生成） | ✅ |
| 常駐ボット（ルーム監視・再起動しても継続・systemd/cron 手順） | ✅ |
| ルーム（共有ログ・差分ポーリング `after=<seq>`・Web UI から会話） | ✅ |
| 画像 / PDF 入力（`--attach`・📎・`attachments`） | ✅ |
| function calling（道具をスキルの `allowed-tools` で絞る） | ✅ |
| ルームのトークン保護 + Webhook（HMAC 署名・非同期リトライ） | ✅ |
| API キーの置き場所（`.env` / 🔑 UI / `actor key set`） | ✅ |
| 複数キー（`GEMINI_API_KEYS` / `.actor/keys/`）+ 429 の自動ローテーション | ✅ |
| キー管理 CLI（`key list / add / add --env / rm / clear / check / stats / sources`・伏せ字つき） | ✅ |
| 429 が続くときの逃げ道（キー全滅 → **別モデル**へ自動フォールバック・`ACTOR_MODEL_FALLBACKS`） | ✅ |
| テスト 179 件 + UI スモーク 47 チェック + CI（test / sdk / bot） | ✅ |

**やると決めていないこと**（現時点の意思決定）:

- ミカのペルソナ（SKILL.md）は **Google Drive 側に置いて Gemini が読む**想定。
  → Actor 側に Drive 同期やペルソナ管理は**作りません**（そこまで決めていない、という整理）
- Apify Actor（元のリポジトリの説明）対応は不要。README はこの用途で書き直し済み

## 3. クイックスタート（手元のマシンで）

```bash
git clone https://github.com/TonbokiriRaikiriMuramasa-Taikoubou/Actor-si.git
cd Actor-si

# 1) API キーを入れる（3つのどれでも。優先度: --api-key > 環境変数 > .env > .actor/api_key）
cp .env.example .env && vi .env          # GEMINI_API_KEY=AIza...
python3 -m actor key set --check         # または CLI から（入力は非表示）
                                         # 429 が続くなら actor key sources（無料の用意先）→ key add

# 2) まず1回話す
python3 -m actor "会議の文字起こしから議事録を作って"

# 3) サーバー + Web UI（→ http://localhost:8080、左下の 🔑 でもキーを入れられる）
python3 -m actor serve --port 8080 --skills-dir skills --skills-dir examples/mika

# 4) 常駐ボット（ペルソナ「ミカ」）を同じルームに向ける
python3 -m actor bot --room actor-x --skills mika --name ミカ \
    --skills-dir skills --skills-dir examples/mika --from-now
```

`pip install -e .` すると `actor` コマンドとしても使えます。`make chat / serve / bot / rooms / tools / test / doctor` もあります。

> ⚠️ 開発サンドボックス（Arena のプレビュー環境）は外部の `generativelanguage.googleapis.com` に
> 出られません。そこでは常にシミュレータ動作になります。実モデルの確認は**ネットワークが使える手元**で。

## 4. 構成（モジュール）

| ファイル | 役割 |
| --- | --- |
| `actor/cli.py` | REPL / 1回実行 / `skills`・`rooms`・`bot`・`tools`・`key`（list/add/check/stats/sources）・`doctor` |
| `actor/keys.py` | キーのプール（優先順位つき収集・429 で次のキー / モデル・伏せ字・`key_state.json`） |
| `actor/server.py` | HTTP サーバー（標準ライブラリ・SSE・ルーム API・`/api/key`） |
| `actor/chat.py` | 1ターンの流れ（選択 → 合成 → 道具ループ → 保存） |
| `actor/tools.py` | 道具（function calling）とサンドボックス |
| `actor/providers.py` | Gemini REST（urllib）+ シミュレータ（mock） |
| `actor/genai_provider.py` | 公式 `google-genai` SDK 経由（任意依存・無ければ urllib へ） |
| `actor/skills.py` / `prompt.py` | SKILL.md の解析・検証・検索・ZIP / system_instruction の合成 |
| `actor/rooms.py` / `bot.py` / `webhooks.py` | ルーム（共有ログ）/ 常駐ボット / Webhook 送出 |
| `actor/sessions.py` / `config.py` | 会話の永続化 / 設定の解決 |
| `actor/web/` | Web UI（ビルド不要の HTML/CSS/JS） |

データはすべて `./.actor/`（gitignore 済み）:

```
.actor/
  sessions/<id>.json      会話（添付の base64 を含む・/api からは伏せる）
  rooms/<name>.json       ルーム（追記専用・seq で差分ポーリング）
  bot-<room>.json         常駐ボットのカーソル
  api_key                 🔑 / actor key set で保存したキー（chmod 600）
  keys/key-1.key          actor key add で足した予備のキー（chmod 600）
  key_state.json          ローテーションの状態（キーの指紋だけ・生のキーは書かない）
```

## 5. 覚えておくと便利な使い方

```bash
# スキルを固定して1回実行（複数スタック可）
python3 -m actor --use minutes-writer --use brand-voice "会議メモから社外向けの議事録"

# 道具を確認・単体実行（モデルが呼ぶ前に自分で試せる）
python3 -m actor tools
python3 -m actor tools --run now
python3 -m actor tools --json | jq '.names'

# 画像・PDF を添える
python3 -m actor --attach ./design.png "この画面の課題を3つ挙げて"

# ルーム（常駐ボットの合流点）
python3 -m actor rooms list
python3 -m actor rooms show actor-x --after=0
python3 -m actor rooms say "おはよー" --reply          # サーバー側で即返事

# キーの点検と補充（429 対策。何本でも・1 本 1 ファイル）
python3 -m actor key list                    # 状態（429 の残り秒数・モデルごとの空き）つきの一覧
python3 -m actor key check                   # 生きているか 1 本ずつ確認（--all で逃げ道のモデルも）
python3 -m actor key add --check             # 足す（--env なら .env の GEMINI_API_KEYS に追記）
python3 -m actor key stats                   # 429 でよく切れるキー / モデル（--reset で仕切り直し）
python3 -m actor key sources                 # キーを無料で用意できる場所（AI Studio / Vertex エクスプレス …）
python3 -m actor key rm 2                    # その番号だけ消す（.env の行からも）

# トークン保護されたルームへ
curl -s -X POST localhost:8080/api/rooms/actor-x/messages \
  -H 'X-Actor-Token: hireme-please-change' -H 'Content-Type: application/json' \
  -d '{"text":"おはよー"}'
```

**Web UI**（`serve` して開く）:

- 左タブ: スキル / 履歴 / ルーム。**ルームを開くと常駐ボットとそのまま会話**できる（1.5 秒ポーリング）
- 中央: チャット。`/` でスキル呼び出し、`🛠` でツール実行、📎 で画像/PDF、🧠 で思考ログ
- 右: インスペクタ（適用スキル / 自動選択スコア / 実際の `system_instruction` / トークン数）
- 左下: `🔑 API キー`（貼り付けて保存 → その場で実モデルへ。本数と 429 の切り替え回数も出る）と接続状態のピル

## 6. 設定（よく使うもの）

優先度は **コマンドライン引数 > 環境変数 > `.env` > 既定値**。

| 変数 | 既定 | 説明 |
| --- | --- | --- |
| `GEMINI_API_KEY` | – | AI Studio のキー。未設定ならシミュレータ |
| `GEMINI_API_KEYS` | – | 複数キー（`,` `;` 空白区切り）。429 は自動で次のキーへ |
| `ACTOR_MODEL_FALLBACKS` | `gemini-3.5-flash,gemini-2.5-flash` | 429 のときだけ使う逃げ道のモデル |
| `ACTOR_API_VERSION` / `ACTOR_MODEL_PATH` | `v1beta` / `models/{model}` | Vertex エクスプレスは自動で `v1` / `publishers/...` |
| `ACTOR_KEYS_DIR` | `./.actor/keys` | `actor key add` の保存先 |
| `ACTOR_KEY_COOLDOWN` | `60` | 429 のあと、そのキーを避ける秒数（0 で無効） |
| `ACTOR_KEY_ROTATE` | `true` | 429 / 401 / 403 で次のキーへ切り替える |
| `ACTOR_PROVIDER` / `ACTOR_GEMINI_IMPL` | `auto` / `gemini` | `auto`/`gemini`/`genai`/`mock` と、キーがあるときの実装 |
| `ACTOR_MODEL` | `gemini-3-flash` | モデル名（`actor models --all` で一覧） |
| `ACTOR_SKILLS_DIR` | `./skills` | スキルフォルダ（区切りは `:` か `,`、複数可） |
| `ACTOR_DATA_DIR` | `./.actor` | 保存先 |
| `ACTOR_TOOLS` | `now,read_file,list_files,list_skills,read_skill` | 有効にする道具 |
| `ACTOR_TOOLS_ROOT` / `ACTOR_TOOLS_ALLOW_WRITE` / `ACTOR_TOOLS_ALLOW_NETWORK` | `.` / false / false | 道具が見える範囲と、危険な道具の許可 |
| `ACTOR_MAX_TOOL_ROUNDS` | 4 | 1ターンの道具往復（最後の1回は答え用に残す） |
| `ACTOR_MAX_ATTACHMENT_MB` / `ACTOR_MAX_ATTACHMENTS` | 10 / 6 | 添付の上限 |
| `ACTOR_ROOM_TOKEN` / `ACTOR_ROOM_PROTECT_READS` | – / false | ルームの保護（読み取りも守るか） |
| `ACTOR_WEBHOOK_URL` / `ACTOR_WEBHOOK_SECRET` | – | 発言を外部へ POST（`X-Actor-Signature: sha256=...`） |
| `ACTOR_API_KEY_FILE` | `./.actor/api_key` | UI / CLI がキーを保存する場所 |

スキル単位の上書きは frontmatter に書きます（`temperature` / `top_p` / `max_output_tokens` / `thinking_budget` / `model` / `greeting` / `allowed-tools`）。複数スタック時は**先に適用されたスキルが優先**。

## 7. テストと CI

```bash
python3 -m unittest discover -s tests -v      # 179 tests（google-genai があれば SDK 経路も検証）
npm install --no-save jsdom && node tests/ui_smoke.mjs   # 47 チェック（serve しておく）
```

CI（`.github/workflows/tests.yml`）は3ジョブ:

| ジョブ | 内容 |
| --- | --- |
| `test` | 標準ライブラリのみで 179 tests |
| `sdk` | `pip install google-genai` して全テスト + `doctor` |
| `bot` | サーバーを起動し、ルームへ発言 → `--once` ボットで返事 → 往復を検証 |

## 8. 設計上の決定（あとから「なぜ？」とならないように）

- **履歴は自前で保持**（`.actor/sessions/*.json`）。SDK の `chats` は使わず、ステートレスな
  `models.generate_content(_stream)` を呼ぶ。ターンごとにスキル（`system_instruction`）とパラメータを差し替えたいため
- **道具は既定で読み取り専用**。`write_file` / `http_get` は明示許可 + `ACTOR_TOOLS_ROOT` の中だけ
- **スキルの `allowed-tools`** は「そのスキルを使っている間の道具の和集合」に効く
- **ルームは追記専用の JSON**（DB なし・`seq` で差分ポーリング）。ボットも UI も curl も同じ1本の API を使う
- **Webhook は非同期**（別スレッド）。送れなくても会話は止めない。5xx/429 は3回リトライ
- **添付は一覧では伏せる**（base64 を返さない）。中身が要る時だけ `?attachments=1`
- **キーが繋がらないときは保存だけ**して、その場ではプロバイダを切り替えない（会話を壊さない）
- **`.env` が最優先**。UI から入れ直しても `.env` の値が勝つ（「明示した設定が正」）
- **キーは 1 本のリスト**（`Settings.api_keys`）として扱い、`api_key` はその先頭（後方互換）。
  優先順位は `--api-key` > 環境変数 > `.env` > `.actor/api_key` > `.actor/keys/`
- **429 の枠は「プロジェクト × モデル」単位**。だからキーを替えるだけでなく、
  キーが全滅したら **別モデルへ逃がす**（`ACTOR_MODEL_FALLBACKS`）。逃げたことは必ず表示する
- **429 は「キーを替える」**。5xx / タイムアウトだけ同じキーでバックオフする。
  切り替えたキーは `ACTOR_KEY_COOLDOWN` 秒避ける（429 の `retryDelay` が長ければそちら）
- **全滅したら止まる**（黙ってシミュレータに落ちない）。どのキーが何秒で復帰するかを出す
- **ストリーミングは途中まで返したら切り替えない**（混ざった本文を作らない）
- **キーはどこにも書かない**。状態ファイルは指紋のみ・エラー本文も `redact()` を通す

## 9. つまずきやすいところ

- サンドボックスでは Google へ出られない → 必ずシミュレータ。実モデルは手元で
- `--skills-dir` を渡し忘れると `examples/mika` のペルソナが見つからない
  （ルーム API は `missing_skills` で教えてくれる）
- ルームのトークンを設定したら、ボット側にも `--token`（または `ACTOR_ROOM_TOKEN`）が必要
- `ACTOR_MOCK_DELAY` は**コマンドライン**で渡す（`os.environ` からは拾わない）
- 429 が続くときは `actor key list`（残り秒数）→ `actor key stats`（どのキー / モデルで切れるか）→
  `actor key add` / `key sources`（足す）。すぐ試したいときは `ACTOR_KEY_COOLDOWN=0` で
  「覚えない」動作にもできる
- キーを替えても駄目なら、Actor は `ACTOR_MODEL_FALLBACKS` の順に **別モデルへ逃げる**。
  主モデルが制限中と分かっているターンは最初から逃げ道で試す（無駄な 429 を送らない）。
  逃げたくなければ `ACTOR_MODEL_FALLBACKS=`（空）か `--no-model-fallback`。
  逃げたことは応答の直前に `🔁` で出るので、黙って別モデルにはならない
- プレビュー環境の 8080 は1プロセスだけ。古いプロセスが残っていると
  `Address already in use` になる（リスナーを確認して停止）
- 道具を使う返事は、既定（非ストリーム）でも同じループを通る。シミュレータでも `now` / `read_file` が動く

## 10. 次の一手（アイデア）

0. **キーの運用（一段落）** … 複数キー・429 の自動切り替え・`key add --env`・`key stats`・
   `key sources`・**モデルの逃げ道**（`ACTOR_MODEL_FALLBACKS`）まで実装済み。次にやるなら
   「残り枠を Google のドキュメントから読んで見せる」「`doctor` で Vertex エクスプレスモードを案内する」など
1. **スキルの Drive 連携** … いまは「Drive の SKILL.md を Gemini が読む」前提で Actor 側は何もしない。
   やるなら「Drive から SKILL.md を取ってきて `skills/` に置く」薄いコマンド1つから
2. **道具の追加** … `read_file` の範囲を広げる / `list_rooms` / Webhook の受け口を Actor 自身に持たせる
3. **複数ボットのオーケストレーション** … ルームを見て役割分担する（`docs/bot.md` に下地あり）
4. **音声・スクリーンショット** … 添付が通るので、入力を作る側だけ足せば届く
5. **長期記憶** … セッションは日付ごとの JSON。要約して持ち回す仕組みは未着手

## 11. 引き継ぎチェックリスト

- [ ] `python3 -m actor doctor` で、キーの出どころと道具の状態を確認
- [ ] `python3 -m unittest discover -s tests` が 179 件通る
- [ ] `serve` して Web UI の「🔑 / ルーム / 🛠 / 📎」をひととおり触る
- [ ] `actor bot --room <名前>` を常駐させ、`docs/bot.md` の systemd 手順を自分の環境に合わせる
- [ ] 外に出してよいルームだけ `ACTOR_ROOM_TOKEN` を設定（読み取りは `ACTOR_ROOM_PROTECT_READS`）
- [ ] 秘密は2つだけ: **API キー**と**ルームのトークン**。漏れたら `actor key clear` + トークン変更
- [ ] `actor key list` で状態（429 の残り秒数）を確認 → 足りなければ `actor key add` /`key add --env`
- [ ] `actor key stats` で「よく切れるキー / モデル」を見て、共有していそうなキーを入れ替える
- [ ] 逃げ道のモデル（`ACTOR_MODEL_FALLBACKS`）を自分の用途に合わせる。足りなければ `actor key sources`

---

困ったら: `actor help` / `actor doctor` / `docs/bot.md` / `docs/skills-compat.md` / README。
