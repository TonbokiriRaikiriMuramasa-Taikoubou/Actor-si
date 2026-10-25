# Agensi 対応 Agent Skills のワークフロー

Actor は、スキルを1つずつ収めた portable ZIP を作成できます。Agensi への提出やアカウント操作は行いません。

```sh
python -m actor skills lint --target agensi
python -m actor skills export minutes-writer --target agensi -o minutes-writer.zip
```

`portable` は `agensi` の別名です。exporter はスキルを検査し、Actor 独自の `version` / `tags` を標準の `metadata` に移し、ソースURL・ライセンス・共通原則を追加し、挨拶があれば本文へ移動し、生成パラメータを除外します。`allowed-tools` は既定で除外されます。受け入れ側がツール名を理解している場合のみ `--keep-allowed-tools` を使ってください。lint と export には同じオプションを指定し、実際に作成する ZIP を検査します。ZIP の構成は `<skill-name>/SKILL.md`、`references/`、`LICENSE` です。リポジトリの支援情報は listing やスキル ZIP に含めません。バイナリ参照、隠しファイル、cache、テスト、依存関係はパッケージされません。lint が失敗した場合、`--force` を明示しない限り export は停止します。

Lint は保守的なローカル近似であり、Agensi の非公開スキャナーでも、審査通過の保証でもありません。外部URLは確認対象として報告されますが、プロジェクトのソースURLは許可されます。作成した ZIP を手動で確認してから、自分で提出してください。スキルは単体で機能し、秘密情報・環境情報の収集・危険なコマンド・ユーザーに隠した処理を求めてはいけません。

## Claude Code でインストールして試す

1. ZIP を展開し、最上位のスキルフォルダーを維持します。
2. そのフォルダーを `~/.claude/skills/`（例: `~/.claude/skills/minutes-writer/`）へコピーします。
3. フォルダーに `SKILL.md` と必要な `references/` ファイルがあることを確認します。
4. 新しい Claude Code セッションを開始し、スキルが扱うタスクを依頼します。選択されたスキルを確認し、出力形式と結果を照合します。

他のエージェントは独自のスキルディレクトリを使い、メタデータやツール名の対応も異なる場合があります。各エージェントの最新ドキュメントを確認してください。

## テストケース

`skills/<name>/tests/cases.yaml` は JSON 構文（YAML 1.2互換）で、ZIP には含まれません。Actor は参照ファイルの読込からも `tests/` を除外します。`python -m actor skills test` は assertion の見出しが静的な出力形式と一致するかだけを確認し、モデルを呼び出したりサンプル出力を生成したりしません。`--live` は設定済みモデルへケースを送り、実際の応答見出しを検証します。ライブ呼び出しには API 料金が発生する場合があります。どちらのモードも、人による品質確認の代わりにはなりません。
