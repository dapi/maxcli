# max-cli

Unofficial CLI for personal MAX Messenger accounts.

It is based on [`maxlib`](https://pypi.org/project/maxlib/), which talks to MAX internal WebSocket APIs. This is not the official Bot API and may break without notice. It may also violate MAX terms of service. Prefer a separate account/number for experiments.

## Install

From this repository:

```bash
python3 -m pip install -e .
```

Or run without installing:

```bash
PYTHONPATH=src python3 -m maxcli --help
```

## Auth

```bash
maxcli auth +79990000000
```

The command asks for the code sent by MAX. If 2FA is enabled, it also asks for
the 2FA password using a hidden terminal prompt. It saves the resulting token to:

```text
~/.config/maxcli/config.json
```

The config file is created with `0600` permissions. You can override the token with `MAXCLI_TOKEN`.

## Commands

```bash
maxcli me
maxcli chats list --limit 50
maxcli chats search "Papado AI company OS"
maxcli resolve --phone +79990000000
maxcli send --phone +79990000000 "hello"
maxcli send --chat-id 123456789 --file ./report.pdf "See attached"
maxcli send --chat-id 123456789 --file ./a.txt --file ./b.txt
maxcli send --chat-id 123456789 --stdin < message.txt
maxcli history --phone +79990000000 --limit 20
maxcli download --chat-id 123456789 --message-id 987654321 --attach-index 0 --output ./downloads/
maxcli listen
maxcli token-path
maxcli token-clear
```

Every command that returns data supports `--json`.

## Attachments

Send one or more local files with `--file`. Text is optional when at least one
file is attached:

```bash
maxcli send --chat-id 123456789 --file ./report.pdf "See attached"
maxcli send --chat-id 123456789 --file ./image.png
maxcli send --chat-id 123456789 --file ./a.txt --file ./b.txt
```

Images with common image extensions are uploaded as photos, common video
extensions are uploaded as videos, and other paths are uploaded as generic
files.

Use JSON history to inspect attachment indexes and metadata:

```bash
maxcli history --chat-id 123456789 --limit 20 --json
```

Download an attachment by message id and attachment index:

```bash
maxcli download --chat-id 123456789 --message-id 987654321 --attach-index 0 --output ./downloads/
maxcli download --chat-id 123456789 --message-id 987654321 --attach-index 0 --output ./file.pdf --force
```

Downloads are supported for files and videos through `maxapi-python` lookup
helpers, plus attachments that already expose a direct URL. Photo download
depends on the URL metadata returned by MAX for that message.

## Notes

- `maxlib` can resolve direct chats by phone or user id.
- Chat history requires a known chat id or a user that can be resolved to a direct chat.
- MAX internal APIs are unstable. Treat this as an agent-facing automation probe, not a production integration.
