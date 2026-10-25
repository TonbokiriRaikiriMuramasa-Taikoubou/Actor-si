# Actor v0.2.1 — セキュリティと互換性の修正

> リリースノート草案です。レビュー済みの変更が main に統合された後に v0.2.1 として公開してください。既存の v0.2.0 タグと Release は変更しません。

## ハイライト

- API トークン保護を有効にした Web UI のチャットを修正し、SSE チャット要求に保存済み API トークンを含めるようにしました。
- トークン保護サーバーを使う UI smoke test を CI に追加し、ブラウザーからストリームへの認証を毎回検証します。
- API の変更系要求に JSON Content-Type と Origin 検証を追加しました。URL クエリのトークンは認証に使わず、frame 埋め込みは同一 Origin に制限します。

## セキュリティと互換性

- Portable lint は `--keep-allowed-tools` を含め、実際に export される ZIP variant を検査します。
- credential らしい表記と、ユーザーに隠して upload / execution を行う指示の検出を拡張しました。
- 議事録とコードレビューのテンプレート見出しを回復しました。Live 出力 assertion は `<...>` をテンプレートのプレースホルダーとして扱います。
- HTML コメントは prose から除去し、fenced / indented code 内の例は変更しません。
- GitHub Sponsors の情報はリポジトリ内に留まり、Agensi listing やスキル ZIP に含めません。

## 保守

- パッケージ化された MIT ライセンスと共通原則の一致を検証するテストを追加しました。
- `FUNDING.md` の支援金用途に関する重複記述を整理しました。
- 必須の Python 依存関係は追加していません。
