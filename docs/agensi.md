# Agensi-compatible Agent Skills workflow

Actor can prepare a portable, one-skill-per-ZIP package. It does not submit packages to Agensi or operate an Agensi account.

```sh
python -m actor skills lint --target agensi
python -m actor skills export minutes-writer --target agensi -o minutes-writer.zip
```

`portable` is an alias for `agensi`. The exporter checks the local skill, moves Actor-only `version`/`tags` into standard `metadata`, adds the source URL, license and shared principles, moves any greeting into the body, and omits model-generation overrides. It omits `allowed-tools` by default; use `--keep-allowed-tools` only when the tool identifiers are understood by the receiving agent. Pass the same option to both `lint` and `export` so the checked archive matches the archive you intend to create. ZIP layout is `<skill-name>/SKILL.md`, `references/`, and `LICENSE`; repository sponsorship details are not included in listings or skill ZIPs. Binary references, dotfiles, caches, tests, and dependencies are not packaged. A failed lint blocks export unless `--force` is explicitly supplied.

Linting is a conservative local approximation, not Agensi's private scanner or a guarantee of acceptance. External URLs are reported for review; the project source URL is allowed. Manually review each generated archive and submit it yourself. The uploaded skill should work as a standalone instruction set and should not request secrets, environment dumps, unsafe commands, or concealment from the user.

## Install and exercise with Claude Code

1. Unzip the package while preserving its top-level skill folder.
2. Copy that folder into `~/.claude/skills/` (for example, `~/.claude/skills/minutes-writer/`).
3. Confirm the folder contains `SKILL.md` and any required `references/` files.
4. Start a new Claude Code session and request a task described by the skill. Check the selected skill and verify the result against its stated output format.

Other agents use their own skill directories and may support different metadata or tool identifiers; consult each agent's current documentation.

## Test cases

`skills/<name>/tests/cases.yaml` uses JSON syntax (valid YAML 1.2) and lives outside the export package; Actor excludes test folders from reference loading as well. `python -m actor skills test` checks only that the asserted headings match the static output-format specification; it does not call a model or generate sample output. Use `--live` to send case prompts to the configured model and verify its actual response headings; live calls may incur API charges. Neither mode replaces human quality review.
