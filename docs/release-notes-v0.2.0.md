# Actor v0.2.0 — release notes draft

> Draft only. Review this against the final diff before publishing or creating a tag.

## Highlights

- Safer server defaults: local-only (`127.0.0.1`) binding, token-required external binding, and token authentication for every API endpoint when configured.
- CORS is disabled by default and uses an explicit origin allowlist. Host headers are validated.
- Added `actor skills lint --target agensi` and `actor skills export <name> --target agensi` for reviewed, one-skill portable ZIP packages.
- Added deterministic skill contract fixtures and optional live tests (`actor skills test --live`).
- Updated the five bundled skills for portable metadata, English descriptions, and the shared MIT license.

## Security and compatibility

- Set `ACTOR_ROOM_TOKEN` before binding to a non-loopback address. Configure `ACTOR_CORS_ORIGINS` and `ACTOR_ALLOWED_HOSTS` only when required.
- Portable linting is a local approximation and does not guarantee marketplace acceptance. Package submission remains manual.
- No third-party Python dependencies were added.
