# SKILL.md の互換性

Actor のネイティブ形式は YAML frontmatter を持つ Markdown の指示ファイルです。生成パラメータや `allowed-tools` など Actor 固有の拡張も読み込めますが、これらは他のエージェントへ portable ではありません。

## Portable な範囲

`actor skills export --target agensi` と `--target portable` は、`name`、`description`、任意の `license` と `when_to_use` を使う、1スキル1フォルダーの ZIP を出力します。Actor の `version` と `tags` は `metadata` に移し、ソースURLも記録します。一般的な Agent Skills ホストでは意味を持たない生成設定（`temperature`、`top_p`、`max_output_tokens`、`thinking_budget`、`model`）は除外します。スキルの挨拶がある場合は指示本文に移します。ホストによってツール名が異なるため、`allowed-tools` は既定で除外し、明示指定した場合のみ `--keep-allowed-tools` で保持します。

Portable export には `references/principles.md` とリポジトリの MIT `LICENSE` も含まれます。テキスト参照のみが同梱され、バイナリ、隠しファイル、cache、テスト、依存関係は除外されます。ZIP は `<name>/SKILL.md`、references、LICENSE の構成です。フォルダー名は `name` と一致させてください。

Agensi lint はメタデータ、description、構造、危険なコマンド、秘密情報らしき文字列、環境情報収集、外部URL、難読化、prompt injection に似た表現、ファイル形式、アーカイブの容量を検査します。保守的な判定のため誤検出や見落としがあり得ます。結果と ZIP 全体を確認してください。Lint 通過は審査合格を保証しません。

## ホストごとの差

Agent Skills ホストによって、メタデータの検証、相対参照の扱い、ツール名、検出ディレクトリが異なります。Portable な中核は自己完結させ、特定ランタイムに依存せず、利用先ごとに動作確認してください。Claude Code では `~/.claude/skills/<name>/` に展開します。他のエージェントは各公式ドキュメントを確認してください。
