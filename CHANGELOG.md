# Changelog

## 0.3.0 — precise Japanese lint and product identity

- Prevent Japanese concealment checks from spanning sentences or lines and suppress false positives for negated concealment statements.
- Detect additional risky instructions such as uploading without saying anything or sending without the user's permission.
- Clarify Actor-si as the product name and `actor` as the CLI command in the READMEs and CLI output.
- Add regression coverage for benign multi-sentence privacy advice, negations, and concealment/action combinations.

## 0.2.1 — security, compatibility, and skills workbench

- Add a local workflow to draft, lint, test, and export portable Agent Skills; Actor-si does not upload or submit packages.
- Translate the CLI and web UI chrome into English while keeping the Japanese skill edition opt-in.
- Fix token-authenticated Web UI streaming: `/api/chat/stream` now retains the room token when callers supply custom headers.
- Require JSON content types and same-origin/allowlisted Origins for API mutations; stop accepting query-string tokens and restrict frame ancestors.
- Lint the exact portable ZIP, including `--keep-allowed-tools`; refine Japanese concealment detection and suppress the unnecessary root-LICENSE warning.
- Preserve code examples while removing prose HTML comments; restore variable template headings for meeting minutes and code reviews, and recognize `<...>` placeholders in live output checks.
- Add GitHub Sponsors repository metadata and clarify that voluntary support funds future concept development without feature, priority, or maintenance guarantees.
- Keep sponsor links out of Agensi listings and exported skill ZIPs. No mandatory Python dependencies were added.

## 0.2.0 — release notes draft

- Harden the local HTTP server: loopback-only default, token required for all API routes when configured, external binds rejected without a token, explicit CORS origin allowlist, and Host validation.
- Add portable/Agensi skill lint and export, one-skill ZIP layout, shared principles, and explicit manual-submission workflow.
- Add skill contract test fixtures with simulator and opt-in live execution.
- Normalize five bundled skills for portable Agent Skills metadata and MIT licensing.
- Add security policy, compatibility and Agensi guides, and this changelog.
- Preserve the prior development handoff under `docs/`.

This is a proposed release entry; maintainers should verify the final diff and version before tagging a release.
