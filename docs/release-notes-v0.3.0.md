# Actor-si v0.3.0 — More precise Japanese lint and clearer product identity

## Highlights

- Japanese concealment lint no longer spans sentences or lines, avoiding false positives when ordinary privacy advice is followed by a separate mention of sending a file.
- Negated instructions such as “explain without hiding it from the user” are not treated as concealment.
- Clarified the product name as Actor-si while retaining `actor` as the CLI command and entry point.

## Security and maintenance

- Added detection coverage for “upload without saying anything” and sending without the user's permission, while preserving detection for concealment paired with sending, uploading, or execution.
- Added regression tests for benign multi-sentence privacy advice, negative forms, and risky concealment/action combinations.
- No mandatory Python dependencies were added.
