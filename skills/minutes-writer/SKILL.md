---
name: minutes-writer
description: Turns meeting transcripts or notes into structured minutes with decisions, action items, owners, due dates, and open questions. Use when the user provides meeting material and asks for minutes.
license: MIT
metadata:
  version: "1.0.0"
  tags:
    - 議事録
    - 会議
    - 抽出
---

# 目的
会議の生ログ（文字起こし・メモ・チャット履歴）を、あとから読む人が最短で意思決定を追える議事録に変換する。

# 入力
- 会議の文字起こし、または箇条書きメモ
- 会議名・日時・参加者（不明なら「不明」と書く）
- 出力先の慣習（特に指定がなければ `references/format.md` に従う）

# 手順
1. ログ全体を読み、話題ごとにセクションを分ける。雑談・相槌・脱線は捨てる。
2. **決定事項**を抽出する。「〜で進める」「〜はやらない」など、判断が確定した発言だけを拾う。
3. **アクションアイテム**を抽出する。担当者・期限・成果物の3点を必ず埋める。不明な項目は空欄にせず `要確認` と明記する。
4. **未解決の論点**を列挙する。結論が出なかったが、次回までに決める必要があるもの。
5. 発言の意図が曖昧な箇所は、勝手に補完せず「（解釈: 〜）」と注記する。
6. `references/format.md` のテンプレートに流し込み、最後に自己レビューする。

# 出力フォーマット
`references/format.md` のテンプレートに厳密に従う。見出しの順序・表の列は変更しない。

# 確認事項
- 決定事項とアクションアイテムを混同しない。「決めたこと」と「やること」は別のセクション。
- 共通の判断原則は `references/principles.md` を適用する。
- 参加者名が不明な場合は「不明」とし、役職や仮名で補わない。
- 出力の最後に「要確認」の件数を1行でまとめる。
