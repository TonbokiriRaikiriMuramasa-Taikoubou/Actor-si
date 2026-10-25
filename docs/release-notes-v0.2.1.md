# Actor v0.2.1 — Security, compatibility, and skills workbench

## Highlights

- Added a local workflow to draft, lint, test, and export portable Agent Skills; Actor does not upload or submit packages.
- Translated the CLI and web UI chrome into English while keeping the Japanese skill edition opt-in.
- Fixed Web UI chat with API-token protection: chat requests retain the saved token even when callers supply additional headers.

## Security and compatibility

- Hardened API mutations with JSON content-type and Origin checks; query-string tokens are not accepted, and frame embedding is restricted to the same origin.
- Portable linting checks the exact ZIP variant being exported, including when `--keep-allowed-tools` is used.
- Expanded credential and concealment detection while reducing Japanese false positives for ordinary privacy advice; explicit concealment and send/upload/execute combinations remain flagged.
- Root skill `LICENSE` copies no longer produce an omitted-file warning; exports include the canonical MIT license.
- HTML comments are removed from prose without altering fenced or indented examples. Compatibility lint remains a conservative approximation, not host approval.

## Maintenance

- Restored useful template headings for meeting minutes and code reviews; live output assertions treat `<...>` template text as placeholders.
- Added regression coverage for protected UI requests, portable packaging, CLI help, and lint behavior.
- GitHub Sponsors details remain repository-only and are not included in Agensi listings or skill ZIPs.
- No mandatory Python dependencies were added.
