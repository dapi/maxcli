# Issue #1 Implementation Plan

Goal: add practical attachment support for personal MAX automation.

1. Extend `maxcli send` with repeatable `--file PATH`.
   - Allow file-only messages.
   - Keep text, stdin, reply, and silent behavior.
   - Classify local paths as photo, video, or generic file for `pymax`.

2. Expand message attachment JSON.
   - Include index, type, class, ids, tokens, names, sizes, dimensions, duration,
     and direct URLs where available.
   - Support both typed `pymax` attachments and dict-like `maxlib` payloads.

3. Add `maxcli download`.
   - Resolve recipient, fetch recent history, find `--message-id`, select
     `--attach-index`, request a download URL when needed, and save to a file or
     directory.
   - Return clear errors for missing messages, absent attachments, unsupported
     attachment types, invalid indexes, and existing output paths.

4. Update docs.
   - Document send, inspect, and download workflows in `README.md` and `SKILL.md`.

5. Verify.
   - Run parser/help checks and Python compile checks.
   - Smoke-test real file send/download with an authenticated MAX account when
     credentials are available.
