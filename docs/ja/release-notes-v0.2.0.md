# Actor v0.2.0 — リリースノート草案

> 草案です。公開またはタグ作成の前に、最終差分と照合してください。

## ハイライト

- サーバーの安全な既定値: loopback（`127.0.0.1`）への bind、外部 bind 時のトークン必須化、トークン設定時の全 API 認証。
- CORS は既定で無効とし、明示した Origin allowlist を使用。Host ヘッダーも検証します。
- 審査用の1スキル単位 portable ZIP を作る `actor skills lint --target agensi` と `actor skills export <name> --target agensi` を追加。
- 決定的なスキル出力形式テストと、任意のライブテスト（`actor skills test --live`）を追加。
- 同梱5スキルの portable metadata、英語 description、共通 MIT ライセンスを整備。

## セキュリティと互換性

- 非 loopback アドレスへ bind する前に `ACTOR_ROOM_TOKEN` を設定してください。必要な場合のみ `ACTOR_CORS_ORIGINS` と `ACTOR_ALLOWED_HOSTS` を設定してください。
- Portable lint はローカルでの近似であり、マーケットプレイスの審査通過を保証しません。パッケージの提出は手動です。
- Python のサードパーティ依存は追加していません。
