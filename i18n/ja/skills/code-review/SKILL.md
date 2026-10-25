---
name: code-review
description: Reviews code changes for actionable defects and security risks, then reports findings by severity. Use when the user asks for a code, diff, or pull-request review.
license: MIT
metadata:
  version: "1.0.0"
  tags:
    - コードレビュー
    - 品質
---

# 目的
差分（diff / コード片 / PR）をレビューし、**バグにつながる指摘だけ**を重大度つきで返す。
好みの押し付けや言い換え提案はノイズなので出さない。このスキルは読み取り専用で、ファイルを変更しない。

# 入力
- レビュー対象のコードまたは diff
- 言語・実行環境・プロジェクトの規約（不明なら一般則で判断）

# 手順
1. `references/checklist.md` を上から順に適用し、該当箇所を探す。
2. 指摘は **重大度**（Critical / High / Medium / Low）を判定する。
   - Critical: データ破壊・認証回避・本番停止に直結
   - High: 特定条件下でバグ・例外・情報漏えい
   - Medium: 保守性・性能に明確な悪影響
   - Low: 軽微（ただしバグの芽のみ。好みは出さない）
3. 各指摘に **最小の修正案**（コード片）を添える。
4. テストが必要な指摘には、確認手順（入力例）を書く。
5. 指摘ゼロなら「LGTM」とだけ書き、無理に何か書かない。

# 出力フォーマット
```markdown
## 結論
指摘 N 件（Critical: x / High: y / Medium: z / Low: w）

## 指摘
### [High] <1行で内容>
- 場所: <ファイル:行 or 関数名>
- 問題: <なぜバグになるか。起動条件を具体的に>
- 修正案:
```言語
// 修正後のコード
```
- 確認: <再現手順 / テスト入力>

## 良い点（任意・1〜2行）
```

# 確認事項
- 本文は利用者の言語で書き、見出しはこのスキルで指定した形を維持する。
- 共通の判断原則は `references/principles.md` を適用する。
- 情報が足りない場合は「差分に含まれていないため未確認」と書く。
- セキュリティ指摘は、再現条件を曖昧にしたまま出さない。
- 命名・インデント・コメント量の好みは指摘しない。
- 重大度の合計と、実際の指摘数が一致しているか最後に確認する。
