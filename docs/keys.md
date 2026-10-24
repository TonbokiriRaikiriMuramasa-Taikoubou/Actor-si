# API キーの複数管理と 429 の自動切り替え

プレビュー版のモデル（`gemini-3.8-flash` など）は、無料枠だと
`429 RESOURCE_EXHAUSTED / Quota exceeded` に当たりやすい。
Actor は **複数のキーを持っておけば、そのリクエストを次のキーで投げ直す**。

- 単一キー（`GEMINI_API_KEY`）は今までどおり（後方互換）
- 429 を受けたキーは `ACTOR_KEY_COOLDOWN` 秒だけ避ける（既定 60 秒）
- 全滅したら、どのキーが何秒後に復帰するかを書いたエラーで止まる（黙って嘘は返さない）
- キーの文字列は、ログ・エラー・状態ファイルのどこにも出さない（伏せ字と指紋だけ）

---

## 1. キーの置き場所と優先順位

上から順に使われ、重複したキーは 1 本にまとめられる（先に見つかった方が優先）。

| 優先 | 置き場所 | 例 | 備考 |
| --- | --- | --- | --- |
| 1 | `--api-key` | `python3 -m actor --api-key AIza...` | 一時的に差し替えたいとき |
| 2 | 環境変数（複数） | `GEMINI_API_KEYS=AIza1,AIza2` | カンマ / 空白 / `;` 区切り |
| 3 | 環境変数（単数） | `GEMINI_API_KEY` / `GOOGLE_API_KEY` / `GOOGLE_GENAI_API_KEY` | CI・systemd 向け |
| 4 | `.env`（複数 → 単数） | `GEMINI_API_KEYS=...` | リポジトリ直下（gitignore 済み） |
| 5 | 保存ファイル | `.actor/api_key` | UI の 🔑 / `actor key set`（1 行 1 キー・複数行可） |
| 6 | keys ディレクトリ | `.actor/keys/key-1.key` … | `actor key add`（1 本 1 ファイル・`ACTOR_KEYS_DIR` で変更可） |

`.actor/` は gitignore 済み。`*.key` も gitignore されているので、keys ディレクトリを
リポジトリ内に置いてもコミットには入らない。

---

## 2. よく使うコマンド

```console
$ python3 -m actor key list                    # 全部のキーと状態
API キー 3 本 （2/3 本が使用可能）
  *[0] AIzaSy…7f3k              使用中 .actor/api_key
   [1] AIzaSy…9x2p              待機 .actor/keys/key-1.key
   [2] AIzaSy…q8mn              429 制限中（あと 42 秒） .actor/keys/key-2.key
429 は 60 秒そのキーを避けて次のキーへ回します（ACTOR_KEY_COOLDOWN / ACTOR_KEY_ROTATE=false で停止）。
これまでに 429 で切り替えた回数: 7（内訳は actor key stats）
追加: actor key add   /   削除: actor key rm <番号>   /   確認: actor key check   /   統計: actor key stats

$ python3 -m actor key add --check             # 1 本足す（入力は画面に出ない・その場で接続確認）
$ python3 -m actor key add --env --check       # .env の GEMINI_API_KEYS に追記する
$ python3 -m actor key list --json | jq '.keys[] | {masked, label, ready, wait}'
$ python3 -m actor key check                   # 1 本ずつ「生きているか」を確認
  ✗ [0] AIzaSy…7f3k              429 Quota exceeded for AIzaSy…7f3k
  ✓ [1] AIzaSy…9x2p              OK（41 models）
$ python3 -m actor key stats                   # 429 でよく切れるキーを見る
$ python3 -m actor key rm 2                    # その番号のキーを消す（ファイル / .env の行から）
$ python3 -m actor key clear                   # 保存したキーを全部消す（.env / 環境変数はそのまま）
```

`key set`（従来のコマンド）は `.actor/api_key` に 1 本保存する。複数持つなら
`key add`（`.actor/keys/`）か `key add --env`（`.env` の `GEMINI_API_KEYS`）、
または環境変数 `GEMINI_API_KEYS` を使う。同時に使ってもよい（重複は 1 本にまとまる）。

### `key add --env` … `.env` に追記する

```console
$ python3 -m actor key add --env
$ cat .env
# Actor の設定                     ← コメントも並び順もそのまま
export GEMINI_API_KEYS=AIzaOLD…,AIzaNEW…   ← 既存の行に足される（新しい行は増やさない）
ACTOR_MODEL=gemini-3-flash
```

- すでに同じキーがあれば増えない（何回実行しても 1 本）
- `export ` 付き・`GEMINI_API_KEY=`（単数）の行でも、その行に足す（`--env` は既定で `GEMINI_API_KEYS`、
  `key set` 系は単数キーなので、混ぜたくないときは keys ディレクトリを使う）
- ファイルが無ければ作る。書き込みは **chmod 600**
- `key rm <番号>` は `.env` のキーも消せる（`AIzaNEW…` を消すと `GEMINI_API_KEYS=` の行は残る）
  → 環境変数（`export GEMINI_API_KEYS=...` を shell で設定した場合）は Actor からは消せないので、
  そのときは「環境変数から読まれている」と表示して終了する

---

## 2.5 どのキーがよく切れるか（`actor key stats`）

`uses` / `errors` / `limits`（429 の回数）と通算カウンタを状態ファイルに貯めているので、
あとから「よく切れるキー」を確認できる。

```console
$ python3 -m actor key stats
API キーの利用統計 （.actor/key_state.json）
  通算: リクエスト 120 回 / 429 で切れた 14 回 / 切り替え 14 回
  モデル別
    gemini-3.8-flash     429 12 回 / リクエスト 40 回 / いま 1/2 本が使用可
    gemini-3.5-flash     429 2 回 / リクエスト 80 回 / いま 2/2 本が使用可
  429 でよく切れる順
  [0] AIzaSy…7f3k              429 12 回 / 試行 40 回（30%） 成功 28 / 失敗 12 / 最終 3 分前 使用中 [gemini-3.8-flash 12]
          .actor/api_key
  [1] AIzaSy…9x2p              429 2 回 / 試行 80 回（3%） 成功 78 / 失敗 2 / 最終 1 分前 待機
          .actor/keys/key-1.key
よく切れているキー: [0] AIzaSy…7f3k（429 12 回 / 試行 40 回）
同じキーを他のツール（Gemini アプリ・他のプロジェクト）と共有していないか確認。足りなければ `actor key add --check`。
いちばん切れているモデル: gemini-3.8-flash（429 12 回）
このモデルが切れたら自動で逃げます: gemini-3.5-flash, gemini-2.5-flash

$ python3 -m actor key stats --json | jq '.totals, .worst'
$ python3 -m actor key stats --reset           # 統計と「制限中」の印をまとめて消す
```

読み方の目安:

| 見え方 | 意味 | すること |
| --- | --- | --- |
| 特定のキーだけ 429 が多い | そのキーが他の用途（Gemini アプリ等）と共有されている / 停止扱い | 専用キーを `key add` する |
| 全キーが満遍なく 429 | 全体の枠が足りない | キーを増やす（`actor key sources`）/ 別モデルへ逃がす |
| 特定のモデルだけ 429 が多い | そのモデルの枠が小さい | `ACTOR_MODEL_FALLBACKS` を調整（既定で逃げます） |
| `errors` が 401 / 403 が多い | 失効・無効なキー | `key rm` で外す |
| 試行回数が極端に少ない | 最近足したばかり | 判断はまだ早い（`--reset` で仕切り直せる） |

`doctor` と `key list` にも通算（429 で切れた回数・よく切れるキー）が 1 行で出る。

`--json` の形（`key list`）:

```json
{
  "count": 3,
  "ready": 2,
  "active": 0,
  "provider": "gemini",
  "model": "gemini-3-flash",
  "model_fallbacks": ["gemini-3.5-flash", "gemini-2.5-flash"],
  "ready_by_model": {"gemini-3-flash": 2, "gemini-3.5-flash": 3, "gemini-2.5-flash": 3},
  "cooldown": 60.0,
  "rotate": true,
  "keys": [
    {"index": 0, "masked": "AIzaSy…7f3k", "source": "file", "label": "保存ファイル",
     "path": "/home/me/Actor/.actor/api_key", "active": true, "ready": true, "wait": 0.0,
     "status": null, "reason": "", "uses": 12, "errors": 0, "limits": 0,
     "model": "gemini-3-flash", "models": {"gemini-3.8-flash": {"limits": 12, "uses": 28}},
     "limited_models": ["gemini-3.8-flash"]}
  ]
}
```

---

## 3. 429 のときに何が起きるか

```
generateContent / streamGenerateContent
        │
        ├─ 200           → 使ったキーを mark_ok（制限を解除・使用回数 +1）
        ├─ 5xx / 時間切れ → 同じキーで指数バックオフ（最大 3 回・従来どおり）
        ├─ 429 / 401 / 403 → そのキーに「あと N 秒は使わない」印を付けて次のキーへ
        │                     （N = ACTOR_KEY_COOLDOWN、429 の RetryInfo.retryDelay が長ければそれ）
        └─ 他（400 など）→ そのままエラー（キーを無駄に消費しない）
```

- 切り替え先は「いま使っていない中で、制限の切れているキー」。一周したら止まる
- ストリーミング（SSE）で **途中まで返してしまった後**は、混ざった応答を作らないために切り替えない
  （そのターンはエラーになる。次のターンでは別のキーが選ばれる）
- `ACTOR_KEY_ROTATE=false` で切り替えを止められる（429 も同じキーでリトライする従来動作）
- `ACTOR_KEY_COOLDOWN=0` は「制限を覚えない」＝実質オフ（その場で同じキーを使い続ける）

全キーが制限中のときのエラー（CLI / Web UI / `--json` の `error` にそのまま出る）:

```console
$ python3 -m actor "会議メモを議事録に"
エラー: すべての Gemini API キーが使えません（2 本すべて / 最後のエラー: 429 Quota exceeded for AIzaSy…q8mn）
  [0] AIzaSy…7f3k（.actor/api_key）あと 42 秒で復帰
  [1] AIzaSy…q8mn（.actor/keys/key-2.key）あと 60 秒で復帰
対処: 42 秒ほど待つか、別のキーを足してください（`python3 -m actor key add`）
ヒント: 429 は無料枠のレート制限です。待ち時間の調整は ACTOR_KEY_COOLDOWN、自動切り替えを止めるなら ACTOR_KEY_ROTATE=false。
状態の確認: `python3 -m actor key list`
```

---

## 3.5 それでも切れるときは別モデルへ逃がす（`ACTOR_MODEL_FALLBACKS`）

Gemini の枠は **「プロジェクト × モデル」単位**です。つまり `gemini-3.8-flash` が切れていても、
同じキーのまま `gemini-2.5-flash` なら通ります。Actor は主モデルのキーが全部 429 になったら、
**逃げ道のモデルへ自動で切り替えて**そのターンを成立させます。

```console
$ python3 -m actor "議事録を作って"
🔁 gemini-3.8-flash で全キーが 429 → gemini-3.5-flash に逃がします
（以下、gemini-3.5-flash の応答）
```

| 設定 | 既定 | 説明 |
| --- | --- | --- |
| `ACTOR_MODEL_FALLBACKS` | `gemini-3.5-flash,gemini-2.5-flash` | 逃げ道のモデル（順番どおりに試す）。空文字で無効 |
| `--fallback-model <名前>` | – | その実行だけ逃げ道を追加する（複数回指定可） |
| `--no-model-fallback` | – | その実行だけ無効にする |

動きの約束:

- **主モデルが優先**。キーに余裕があるうちは逃げません（逃げるのは 429 のときだけ）
- 主モデルのキーが全部制限中と分かっているターンは、**最初から逃げ道で試します**
  （必ず 429 になる 1 リクエストを無駄にしない）
- 制限は **キー × モデルごと**に覚えます（`key_state.json`）。`ACTOR_KEY_COOLDOWN` 秒たてば
  主モデルをまた試します（＝一時的に逃げているだけ）
- 逃げたことは **応答の直前に `🔁` で表示**され、`--json` の `notices` / `requested_model`、
  Web UI の本文にも出ます（黙って別モデルにはしません）
- 全部のモデルが全滅したときだけエラーになります（試したモデルと復帰秒数つき）
- スキルや `--model` でモデルを指定したときは、その指定を最優先で試してから逃げます

```
主モデル → キーを順に（429 なら次のキー）
   └ 全キー 429 → 逃げ道 1 → キーを順に
        └ 全キー 429 → 逃げ道 2 → …
             └ 全部だめ → 「すべての Gemini API キーが使えません（試したモデル: …）」
```

> 逃げ道のモデルは**料金と品質が違います**（例: `gemini-3.5-flash` は 3.8 より軽い）。
> 気になるときは `ACTOR_MODEL_FALLBACKS=gemini-2.5-pro` のように自分の好みで決めてください。

---

## 3.6 キーを無料で用意できる場所（`actor key sources`）

「キーを増やしたい」と思ったときの、利用可能なサービスの一覧です（`actor key sources` で同じ内容が出ます）。

```console
$ python3 -m actor key sources
キーを用意できる場所（枠は「プロジェクト × モデル」単位です）
  Google AI Studio [無料枠]
    https://aistudio.google.com/apikey
    クレジットカード不要。Flash / Flash-Lite / 2.5 Pro などが無料枠で使える。…
  Vertex AI（Gemini Enterprise Agent Platform）エクスプレスモード [90 日無料]
    https://console.cloud.google.com/expressmode
    新しい Google Cloud ユーザーは課金設定なしで 90 日無料（枠内）。API キーで使えて、
    ACTOR_BASE_URL=https://aiplatform.googleapis.com を設定すると Actor からそのまま使える
  …
```

| 場所 | 何がもらえる | 条件 |
| --- | --- | --- |
| **Google AI Studio**（`aistudio.google.com/apikey`） | 無料枠（Flash / Flash-Lite / Gemma など） | カード不要。枠はプロジェクト×モデル単位 |
| **Vertex AI エクスプレスモード** | 90 日無料（枠内）・API キーで使える | 新規 Google Cloud ユーザー（@gmail.com）／課金設定なし |
| **Google Cloud 無料トライアル** | $300 / 90 日 | カード登録が必要。Vertex AI 経由で使う |
| **組織・Workspace の管理者** | 会社・学校のプロジェクトのキー | 発行してもらえることがある |
| **Startups / 研究・学生プログラム** | クレジット | 条件・審査あり |

**同じプロジェクトで新しいキーを作っても枠は増えません**（これがいちばんの落とし穴）。
`actor key list` で「`GEMINI_API_KEY` と `GEMINI_API_KEYS` が同じプロジェクト」なら、
それは同じ枠を共有しています。

> ⚠️ 複数アカウントを作って制限を回避するのは規約違反です（BAN され得ます）。
> 第三者からキーを買う・共有するのも同じくらい危険です。Actor の `actor key sources` は
> 利用可能なサービスと注意点を案内します。
> また、無料枠では送信内容が Google の製品改善に使われ得るので、機密は送らないでください。

### Vertex AI エクスプレスモードを使う（API の形が違う）

エクスプレスモードのキーは `aiplatform.googleapis.com` の **`publishers/google/models/...`** を使います。
Actor は `ACTOR_BASE_URL` に `aiplatform.googleapis.com` を見つけると、**自動でその形に合わせます**
（`ACTOR_API_VERSION=v1` / `ACTOR_MODEL_PATH=publishers/google/models/{model}`）。

```bash
export GEMINI_API_KEY="（エクスプレスモードで発行した API キー）"
export ACTOR_BASE_URL="https://aiplatform.googleapis.com"
python3 -m actor key check --all      # まず疎通確認（キーは伏せ字で表示されます）
python3 -m actor --model gemini-2.5-flash "やあ"
```

`--provider genai`（公式 SDK）は v1beta 前提の部分があるため、エクスプレスモードは
追加依存なしの既定プロバイダ（`ACTOR_GEMINI_IMPL=gemini`）で使ってください。

---

## 4. 状態ファイル（`.actor/key_state.json`）

プロセスを跨いでも「どのキーが制限中か」を覚えるために、キーの **指紋（sha256 の先頭12文字）** だけを書く。
生のキーは書かない。

```json
{
  "version": 2,
  "updated": 1786000000.0,
  "totals": {"requests": 120, "limits": 14, "rotations": 14},
  "by_model": {
    "gemini-3.8-flash": {"requests": 40, "limits": 12},
    "gemini-3.5-flash": {"requests": 80, "limits": 2}
  },
  "keys": {
    "9f2c1ab77d10": {
      "models": {
        "gemini-3.8-flash": {"limited_until": 1786000060.0, "status": 429,
                             "reason": "429 Quota exceeded for AIzaSy…q8mn", "uses": 12, "errors": 3,
                             "limits": 1, "last_used": 1786000000.0},
        "gemini-3.5-flash": {"limited_until": 0.0, "status": null, "reason": "",
                             "uses": 8, "errors": 0, "limits": 0, "last_used": 1786000040.0}
      }
    }
  }
}
```

- `models` の下に **モデルごと**の状態が入る（枠がモデル単位のため）。
  バージョン 1 のファイル（モデルの区別なし）は、読み込み時に「モデルを問わない印」として引き継ぐ
- `by_model` は「どのモデルでよく切れるか」の集計（`actor key stats` のモデル別セクション）

- 消しても害はない（次回起動で作り直される）
- `limited_until` を過ぎたキーは自動で使えるようになる
- `actor key clear` は状態ファイルを消さない（統計は残す）。仕切り直したいときは `actor key stats --reset`
- 消したキーの記録は指紋だけが残る（`stats` が「消したキーの記録が N 件残っています」と教える）

---

## 5. 伏せ字（マスク）のルール

| 場所 | 出るもの |
| --- | --- |
| CLI（`key list` / `show` / `check` / `doctor`） | `AIzaSy…7f3k` のような伏せ字だけ |
| エラーメッセージ | API のエラー本文にキーが混ざっていても `redact()` で置換 |
| `/api/config` / `/api/health` | 真偽値と本数だけ |
| `/api/key` | 伏せ字・出どころ・状態（`uses` / `errors` / 残り秒） |
| `.actor/key_state.json` | 指紋と伏せ字だけ |
| `Settings` の `repr` | `api_key` などは `repr=False` なので出ない |

キーが漏れた可能性があるときは Google AI Studio で **そのキーを無効化**し、
`actor key rm <番号>` / `actor key clear` でローカルからも消す（`ACTOR_ROOM_TOKEN` も変える）。

---

## 6. テスト

```bash
python3 -m unittest discover -s tests -v      # KeyPoolTest / KeyRotationTest / KeyCliTest など
```

- `KeyPoolTest` … 集め方・優先順位・重複・429 の待ち時間・伏せ字・状態ファイル・並行アクセス
- `EnvFileKeyTest` … `.env` への追記 / 削除（コメントと並び順を壊さない・chmod 600）
- `ModelFallbackTest` / `GenAiModelFallbackTest` … モデル単位の 429 → 別モデルへ逃げる（偽 Gemini）
- `VertexExpressTest` … エクスプレスモードの URL の形（自動判定・上書き・実リクエスト）
- `KeySourcesTest` / `KeyCliModelTest` … `key sources` と、CLI からの逃げ道の見え方
- `KeyStatsTest` / `KeyStatsCliTest` … 429 の統計（よく切れるキー・リセット・生キーを出さない）
- `KeyRotationTest` … 偽 Gemini（キーごとに 429 / 200）で切り替えを検証（ネットワーク不要）
- `GenAiKeyRotationTest` … `pip install google-genai` があるときだけ SDK 経路も検証
- `KeyCliTest` … `actor key add / list / rm / clear / check` をサブプロセスで検証
- `KeyRotationChatTest` … 会話（`ChatEngine.turn`）まで通して切り替えと全滅時のエラーを検証
