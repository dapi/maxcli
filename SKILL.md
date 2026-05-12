---
name: maxcli
description: >
  Use when user wants to read, search, send, or monitor personal MAX Messenger
  messages through the maxcli CLI. Trigger on MAX messenger personal account
  tasks, direct chats, chat history, contact resolution, sending messages, and
  agent automation over a user account. This is for personal MAX accounts, not
  the official MAX Bot API.
---

# maxcli

CLI skill for personal MAX Messenger automation through `maxcli`.

## Install

Install this skill from GitHub:

```bash
npx skills add dapi/max-cli --skill maxcli --agent '*' -g -y
```

Install CLI from the repository:

```bash
cd ~/code/max-cli
python3 -m pip install -e .
```

Authenticate once:

```bash
maxcli auth +79990000000
```

`auth` asks for the MAX code and, if enabled, a hidden 2FA password prompt. Tokens
are stored outside the repo in `~/.config/maxcli/config.json`.

## Execution Rules

- Use `maxcli` for personal MAX account access. Do not use the official Bot API
  unless the user explicitly asks for bot integration.
- Run MAX commands sequentially. The transport is based on unofficial internal
  APIs and can be brittle under concurrent sessions.
- Prefer `--json` for agent workflows.
- Never print, paste, commit, or expose tokens from `~/.config/maxcli/config.json`.
- If `maxcli` is not found, run `cd ~/code/max-cli && python3 -m pip install -e .`.
- If authentication is missing, ask the user to run `maxcli auth <phone>` locally
  when a 2FA password is required.
- If command shape is uncertain, verify it with `maxcli <command> --help`.
- MAX internal APIs are unstable. If a command fails at handshake/auth level,
  inspect the error before retrying repeatedly.

## Core Commands

### Account

```bash
maxcli me --json
maxcli chats list --limit 50 --json
maxcli chats search "project name" --limit 20 --json
maxcli token-path
maxcli token-clear
maxcli token-clear --remote
```

### Resolve Direct Chats

```bash
maxcli resolve --phone +79990000000 --json
maxcli resolve --user-id <user_id> --json
maxcli resolve --chat-id <chat_id> --json
```

Use `chat_id` from `resolve` for later `send`, `history`, or `listen` commands.

### Find Named Chats

```bash
maxcli chats search "Papado AI company OS" --json
maxcli chats search "Papado" --limit 20 --fetch-pages 10 --json
maxcli chats list --limit 50 --json
```

Use `chats search` before guessing chat ids. Increase `--fetch-pages` when the
chat is older than the initial sync window.

### Send Text

```bash
maxcli send --chat-id <chat_id> "Hello" --json
maxcli send --phone +79990000000 "Hello" --json
maxcli send --chat-id <chat_id> --stdin --json < message.txt
maxcli send --chat-id <chat_id> --reply-id <message_id> "Reply text" --json
maxcli send --chat-id <chat_id> --silent "Quiet text" --json
```

### Read History

```bash
maxcli history --chat-id <chat_id> --limit 20 --json
maxcli history --phone +79990000000 --limit 20 --json
maxcli history --user-id <user_id> --limit 20 --json
```

### Listen

```bash
maxcli listen --json
maxcli listen --chat-id <chat_id> --json
```

Stop listening with Ctrl+C.

## Current Boundaries

- `maxcli` currently supports direct chat resolution by phone, user id, or known
  chat id.
- Named chat search/listing is available through `maxcli chats search` and
  `maxcli chats list`.
- Attachments, reactions, editing, deleting, groups, and full-text search are
  not stable public surfaces yet unless the CLI exposes them.
