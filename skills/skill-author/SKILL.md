---
name: skill-author
description: Designs portable Agent Skills with clear metadata, actionable steps, and a testable output format. Use when the user wants to create or improve a reusable SKILL.md. Do not use when the user needs a one-off result rather than a reusable procedure.
license: MIT
metadata:
  version: "1.0.0"
  tags:
    - skill-creation
    - metadata
    - SKILL.md
---

# Purpose
Turn a user's request for repeatable, consistently high-quality work into a file that follows the standard Agent Skills `SKILL.md` format.

# Input
- A description of the task to automate (informal wording is fine).
- Any existing procedure, checklist, or output example.

# Agent Skills specification
- The file must be named `SKILL.md` and placed at the top level of its skill folder.
- Put frontmatter first. `name` must use lowercase letters, digits, and hyphens (for example, `my-skill`) and match the folder name. Write the English `description` in 1–3 sentences; state what the skill does and include `Use when …` and `Do not use when …` conditions.
- Include `license: MIT` and `metadata.version`; place tags under `metadata.tags` when used.
- Write the body in Markdown. Use these five headings by default: `# Purpose`, `# Input`, `# Procedure`, `# Output format`, and `# Checklist`.
- Use numbered steps that start with actionable verbs (for example, identify, extract, and format).
- Show the actual output format in a code block; do not rely on abstract descriptions alone.
- Put supporting material (templates and checklists) in `references/` and refer to each file by name from the body.
- Keep the skill focused on necessary information; put detailed supporting material in `references/`.

# Procedure
1. Extract the **input / process / output** from the request. Ask about ambiguities before drafting; do not guess.
2. Choose a `name`: use English kebab-case and name the task itself (for example, `weekly-report`).
3. Write a concise, specific `description` that helps readers decide whether to use the skill; state what it does and include `Use when …` and `Do not use when …` conditions.
4. Write 5–8 steps. Limit each step to one actionable operation.
5. Refer to `references/principles.md` for shared principles and include a copy-ready output format.
6. Validate the generated skill with `actor skills lint --target agensi`; report the result and any warnings that need correction.
7. Output the complete `SKILL.md` in a code block that can be saved as-is.

# Output format
Use at least four backticks for the outer fence so the inner `SKILL.md` code fence remains intact.

````markdown
## Design memo
- Goal:
- Input:
- Output:
- Pitfalls:

## SKILL.md
```markdown
---
name: example-skill
description: Creates an example result. Use when the user requests this reusable task. Do not use when the user needs a one-off result.
license: MIT
metadata:
  version: "1.0.0"
  tags:
    - example
---

# Purpose
(What this skill achieves)

# Input
(Required inputs)

# Procedure
1. Review the input.

# Output format
Present the result with clear headings and fields.

# Checklist
Apply the shared principles in `references/principles.md`.
```
````

# Checklist
- Write prose in the user's language while keeping the required headings above exactly as specified.
- Instruct the created skill to apply `references/principles.md`.
- Use lowercase letters, digits, and hyphens in `name`, and make it match the destination folder name.
- Avoid unverifiable instructions such as “try hard to be thorough.” Express every requirement as a checkable criterion.
- Output a complete `SKILL.md` that can be saved as-is. Do not omit content with ellipses.
