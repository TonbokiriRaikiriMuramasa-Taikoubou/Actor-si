---
name: skill-author
description: Designs portable Agent Skills with clear metadata, actionable steps, and a testable output format. Use when the user wants to create or improve a reusable SKILL.md.
license: MIT
metadata:
  version: "1.0.0"
  tags:
    - スキル作成
    - メタ
    - SKILL.md
---

# 目的
ユーザーの「こういう作業を毎回同じ品質でやりたい」を、
Agent Skills の標準的な SKILL.md 形式を満たすファイルとして書き起こす。

# 入力
- 自動化したい作業の説明（口語でよい）
- 既存の手順書・チェックリスト・出力例があれば、それも材料にする

# 仕様（Agent Skills）
- ファイル名は必ず `SKILL.md`。フォルダの最上位に置く。
- 先頭に frontmatter を置く。`name` は小文字・数字・ハイフンの `my-skill` 形式でフォルダ名と一致させ、`description` は英語で「何をするか」と `Use when …` を1〜3文で記載する。
- `license: MIT` と `metadata.version` を含め、タグがある場合は `metadata.tags` に置く。
- 本文は Markdown。見出しは `# 目的` `# 入力` `# 手順` `# 出力フォーマット` `# 確認事項` の5つを基本とする。
- 手順は番号付きで、実行可能な動詞から始める（「確認する」「抽出する」「整形する」）。
- 出力フォーマットはコードブロックで実物を示す。抽象語だけで済ませない。
- 副資料（テンプレート・チェックリスト）は `references/` に分離し、本文からは名前で参照する。
- 全体で 必要な情報だけを含め、詳細な補助資料は `references/` に置く。

# 手順
1. 依頼から **入力 / 処理 / 出力** を抜き出す。曖昧な点は先に質問する（推測で埋めない）。
2. `name` を決める。英語のケバブケースで、作業名そのものにする（`weekly-report` など）。
3. `description` を書く。読んだ人が「使うべきか」を判断できる粒度にする（短く具体的にし、英語で何をするかと `Use when …` の利用条件を記述する）。
4. 手順を5〜8ステップで書く。各ステップは1つの動詞的作業に絞る。
5. 共通原則として `references/principles.md` を参照し、出力フォーマットを実際に貼り付けられる形で書く。
6. 生成したスキルを `actor skills lint --target agensi` で検査し、結果と修正が必要な警告を報告する。
7. 最後に、この SKILL.md を `SKILL.md` として保存できるコードブロックで出力する。

# 出力フォーマット
外側は4個以上のバッククォートを使い、内側の SKILL.md コードフェンスを壊さない。

````markdown
## 設計メモ
- ねらい:
- 入力:
- 出力:
- 落とし穴:

## SKILL.md
```markdown
---
name: example-skill
description: Creates an example result. Use when the user requests this reusable task.
license: MIT
metadata:
  version: "1.0.0"
  tags:
    - example
---

# 目的
（達成する目的）

# 入力
（必要な入力）

# 手順
1. 入力を確認する。

# 出力フォーマット
結果を見出しと項目で示す。

# 確認事項
共通原則は `references/principles.md` を適用する。
```
````

# 確認事項
- 本文は利用者の言語で書き、見出しはこのスキルで指定した形を維持する。
- 作成するスキルにも `references/principles.md` を参照する指示を含める。
- `name` は小文字・数字・ハイフンを使い、保存先フォルダ名と一致させる。
- 「頑張って丁寧に」のような、判定できない指示を書かない。すべて検証可能な基準にする。
- 出力した SKILL.md はそのまま保存できる完全な形にする。省略記号（…）で逃げない。
