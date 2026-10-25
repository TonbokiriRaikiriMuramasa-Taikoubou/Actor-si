# Changelog

## 0.2.1 — security and compatibility fixes (draft)

- Fix token-authenticated Web UI chat and run an authenticated UI smoke test in CI.
- Require JSON content types and same-origin/allowlisted Origins for API mutations; stop accepting query-string tokens and restrict frame ancestors.
- Lint the exact portable ZIP, including `--keep-allowed-tools`, detect additional concealment language, and preserve code examples while removing prose HTML comments.
- Restore variable template headings for meeting minutes and code reviews; recognize `<...>` placeholders in live output checks.
- Add GitHub Sponsors repository metadata and clarify that voluntary support funds future concept development without feature, priority, or maintenance guarantees.
- Keep sponsor links out of Agensi listings and exported skill ZIPs.

This entry is prepared for v0.2.1. Do not move or rewrite the published v0.2.0 tag / Release.

## 0.2.0 — release notes draft

- Harden the local HTTP server: loopback-only default, token required for all API routes when configured, external binds rejected without a token, explicit CORS origin allowlist, and Host validation.
- Add portable/Agensi skill lint and export, one-skill ZIP layout, shared principles, and explicit manual-submission workflow.
- Add skill contract test fixtures with simulator and opt-in live execution.
- Normalize five bundled skills for portable Agent Skills metadata and MIT licensing.
- Add security policy, compatibility and Agensi guides, and this changelog.
- Preserve the prior development handoff under `docs/`.

This is a proposed release entry; maintainers should verify the final diff and version before tagging a release.
