# Actor-si 引き継ぎメモ

最終確認: 2026-10-25  
対象リポジトリ: <https://github.com/TonbokiriRaikiriMuramasa-Taikoubou/Actor-si>

この文書は現在の実装と、これまでのセキュリティ・配布・スキル出力形式の点検結果をまとめたものです。過去の手順が残る [`docs/development-handoff.md`](docs/development-handoff.md) は2026-10-13時点のアーカイブとして扱い、現在の挙動はこの文書、README、各docsを参照してください。

## プロジェクト概要

Actor-si は `SKILL.md` を読み込み、Gemini API と対話するローカル優先の CLI / HTTP API / Web UI / 常駐ボットです。コア機能は Python 標準ライブラリのみで動作し、Google の SDK は任意です。API キーがない場合はシミュレーターに切り替わります。

- Python パッケージと CLI: `actor/cli.py`, `pyproject.toml`
- HTTP API・ブラウザ UI: `actor/server.py`, `actor/web/`
- チャット・プロバイダー: `actor/chat.py`, `actor/providers.py`
- スキル読み込み・合成: `actor/skills.py`, `actor/prompt.py`
- portable ZIP の lint / export: `actor/portable.py`
- スキルの出力形式テスト: `actor/skill_tests.py`
- 組み込みスキル: `skills/`（brand-voice, code-review, minutes-writer, release-notes, skill-author）

詳細な利用方法と設定値は [`README.md`](README.md)、常駐ボットは [`docs/bot.md`](docs/bot.md)、スキルの互換性は [`docs/skills-compat.md`](docs/skills-compat.md)、portable export は [`docs/agensi.md`](docs/agensi.md) を参照してください。

## 開発・検証コマンド

リポジトリのルートから実行します。

```sh
# API キー不要の標準テスト
python -m unittest discover -s tests -q

# SKILL.md の出力形式と tests/cases.yaml の静的整合性を確認
python -m actor skills test

# 実際のモデル応答まで検査する（API キーが必要。課金の可能性あり）
python -m actor skills test --live

# portable lint と単一スキル ZIP の作成
python -m actor skills lint --target agensi
python -m actor skills export minutes-writer --target agensi -o minutes-writer.zip

# 起動時チェックとローカルサーバー
python -m actor doctor
python -m actor serve --port 8080
```

2026-10-25時点の点検では unittest **201件成功 / 11件 skip**、形式テスト5スキル成功、Agensi lint 5スキル成功でした。PR CI は Python 3.9 / 3.11 / 3.13、SDK、bot、skill-packaging のジョブを実行します。pip wheel の内容も確認し、`actor/resources/principles.md` と `actor/resources/LICENSE` が含まれることを検証しました。テスト件数は追加・変更で変わるため、上のコマンドで再確認してください。

## セキュリティ上の挙動

- `/api/` の `POST` / `PUT` / `DELETE` は `Content-Type: application/json` を必須とします。
- 変更系 API の Origin は same-origin または `ACTOR_CORS_ORIGINS` の明示 allowlist のみ許可します。それ以外は 403。Origin のない非ブラウザ・サーバー間リクエストは許可されますが、JSON 要件とトークン設定は引き続き適用されます。
- トークンは `X-Actor-Token` / `X-Room-Token` / `Authorization: Bearer` で送ります。URL query の `token` は認証に使いません。
- CSP の `frame-ancestors` は `'self'` に制限しています。外部公開する場合はルームトークンを設定し、Host / CORS allowlist も環境に合わせて構成してください。
- キーやトークンをソース、ログ、fixture、HANDOFF に書かないでください。`.env` や `.actor/` の実データはコミットしないこと。

## portable Agent Skills の仕様

- Export は一度に1スキルを `<name>/SKILL.md` の形式で ZIP 化します。
- ZIP は同一入力から再現可能になるよう固定 timestamp で作成します。HTML コメントは `SKILL.md` から除去されます。
- 共通の `principles.md` と MIT `LICENSE` を必須で同梱します。ソースツリーだけでなく pip 配布内の `actor/resources/` にもリソースを置き、欠落時は export を止めます。
- Lint は原文ではなく実際に作る ZIP の内容を検査します。Google `AIza…`、GitHub `ghp_…` 等の credential、秘密裏の送信・実行指示、危険コマンド、環境情報収集、難読化、外部 URL、形式・容量などを検査します。
- テキスト以外の参照、隠しファイル、cache、`tests/` は ZIP に含めません。除外された参照は lint の警告として確認してください。Lint は保守的な近似であり、安全性や掲載審査の合格を保証しません。ZIP を手動で確認してください。
- 提出や外部アカウント操作は Actor の対象外です。作者が自分で内容を確認し、手動で行います。

## スキル出力形式テスト

`skills/<name>/tests/cases.yaml` は JSON 構文（YAML 1.2互換）のケース定義です。`assert_contains` は出力形式に必須の見出しを列挙します。

- `python -m actor skills test` は `# 出力フォーマット` のコード例と明示参照されたテンプレートから見出しを取得し、期待見出しの不足・古いassertを検査します。これは**形式の検査のみ**で、モデル出力を生成しません。結果の `output` は `null` です。
- `--live` はケース入力を設定済みモデルへ送り、実際の応答を見出しassertと照合します。モデル呼び出しによる課金に注意してください。
- 出力形式を変更したら対応する `tests/cases.yaml` も同じ変更で更新し、両モードの意味を混同しないでください。

## 継続時の注意・未完了項目

- 公開済み `v0.2.0` タグ / Release は現状のままです。新しい変更を過去のReleaseに含めた扱いにせず、タグ付けやRelease作成は別途判断してください。
- 公開済み Git 履歴の書き換えや force-push は破壊的です。明示的な承認なしに実施しないでください。
- `examples/mika` は今回の点検・更新対象外です。明示的な依頼なしに変更しないでください。
- `LISTING.md` はホスト実機での互換性を確認できていない旨を記載しています。各エージェントを実機検証するまでは、対応済みと追記しないでください。
- GitHub About description と Topics は更新を試みましたが、GitHub integration が HTTP 403 を返したため未変更です。権限があるオーナーが GitHub の repository settings から手動更新してください。
- `docs/development-handoff.md` の旧コマンド・数値は現状と異なる場合があります。現行の正として参照しないでください。
