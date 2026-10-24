# Security policy

## Supported versions

Security fixes are applied to the latest released version and the active development branch.

## Reporting a vulnerability

Please do not report exploitable vulnerabilities in public issues. Use GitHub's **Report a vulnerability** feature on the Actor repository (Security → Advisories), or contact the repository owner through the email address listed on the GitHub profile. Include affected versions, impact, and a minimal reproduction. Please allow time for a coordinated fix before public disclosure.

## Deployment guidance

Actor binds to `127.0.0.1` by default. Binding to a non-loopback interface requires `ACTOR_ROOM_TOKEN`; when configured, the token is required for every `/api/*` endpoint. CORS is disabled unless explicitly allowed with `ACTOR_CORS_ORIGINS`. Host headers are restricted to localhost, loopback, the configured bind host, and `ACTOR_ALLOWED_HOSTS`; add your LAN hostname/IP to that allowlist when needed. Keep tokens and API keys out of source control, and do not expose the service to untrusted networks without an authenticated, TLS-terminating reverse proxy.
