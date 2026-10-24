# SKILL.md compatibility

Actor's native format is a Markdown instruction file with YAML frontmatter. It can load Actor extensions such as generation overrides and `allowed-tools`; those extensions are not portable across agents.

## Portable subset

The `actor skills export --target agensi` and `--target portable` commands emit a one-skill-per-folder ZIP using `name`, `description`, optional `license`, and optional `when_to_use`, with Actor metadata under `metadata:`. Actor's `version` and `tags` are moved into metadata and the source URL is recorded there. Generation controls (`temperature`, `top_p`, `max_output_tokens`, `thinking_budget`, `model`) are not meaningful in general Agent Skills hosts and are omitted. A skill greeting, when present, is moved into the instruction body. `allowed-tools` is omitted by default because tool names differ by host; `--keep-allowed-tools` retains it only on explicit request.

Portable exports also include `references/principles.md` and the repository MIT `LICENSE`. Only text references are included. Binary files, hidden files, cache directories, tests, and dependencies are excluded. The destination ZIP follows `<name>/SKILL.md`, references, and LICENSE. Directory name must equal the `name` field.

The Agensi lint target checks metadata, descriptions, structure, common unsafe commands, possible secrets, environment collection, external URLs, obfuscation, prompt-injection-like language, file types, and archive limits. It is intentionally conservative and can have false positives or miss risks; review findings and the complete ZIP. Passing it does not guarantee review acceptance.

## Host-specific differences

Agent Skills hosts can vary in metadata validation, relative reference handling, tool names, and discovery locations. Keep the portable core self-contained, avoid assuming a particular runtime, and test in every target host. For Claude Code, unpack to `~/.claude/skills/<name>/`; consult the current official documentation for other agents.
