# Actor v0.2.1 — Security and compatibility fixes

> Release notes draft. Publish as v0.2.1 only after the reviewed changes have merged. The existing v0.2.0 tag and Release remain unchanged.

## Highlights

- Fixed Web UI chat when API-token protection is enabled: the SSE chat request now includes the saved API token.
- Added a token-protected UI smoke test to CI so the browser-to-stream authorization path is exercised on every change.
- Hardened API mutation requests with JSON content-type and Origin checks; URL query tokens are not accepted, and frame embedding is restricted to the same origin.

## Security and compatibility

- Portable linting now checks the exact ZIP variant being exported, including when `--keep-allowed-tools` is used.
- Expanded lint detection for credential patterns and instructions to conceal uploads or execution from users.
- Restored useful template headings for meeting minutes and code reviews. Live output assertions now treat `<...>` template text as placeholders.
- HTML comments are stripped from prose without altering examples inside fenced or indented code blocks.
- GitHub Sponsors details remain repository-only and are not included in Agensi listings or skill ZIPs.

## Maintenance

- Added a resource-consistency test for the packaged MIT license and shared principles.
- Removed duplicate wording about support-fund usage from `FUNDING.md`.
- No mandatory Python dependencies were added.
