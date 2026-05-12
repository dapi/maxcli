from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


APP_NAME = "maxcli"
DEFAULT_PROFILE = "default"


class CliError(Exception):
    pass


def die(message: str, code: int = 1) -> None:
    print(f"maxcli: {message}", file=sys.stderr)
    raise SystemExit(code)


def load_maxlib() -> Any:
    try:
        import maxlib
    except ImportError as exc:
        die(
            "Python package `maxlib` is not installed. "
            "Install it with: python3 -m pip install maxlib==0.1b1",
            2,
        )
        raise exc
    return maxlib


def config_home() -> Path:
    override = os.environ.get("MAXCLI_CONFIG_DIR")
    if override:
        return Path(override).expanduser()

    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg).expanduser() / APP_NAME

    return Path.home() / ".config" / APP_NAME


def config_path() -> Path:
    return config_home() / "config.json"


def empty_config() -> dict[str, Any]:
    return {"default_profile": DEFAULT_PROFILE, "profiles": {}}


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return empty_config()
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise CliError(f"Cannot parse config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CliError(f"Config {path} must contain a JSON object")
    data.setdefault("default_profile", DEFAULT_PROFILE)
    data.setdefault("profiles", {})
    return data


def save_config(data: dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    tmp.replace(path)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def get_profile_name(args: argparse.Namespace) -> str:
    return args.profile or os.environ.get("MAXCLI_PROFILE") or DEFAULT_PROFILE


def get_profile(args: argparse.Namespace) -> dict[str, Any]:
    cfg = load_config()
    profiles = cfg.get("profiles", {})
    return profiles.get(get_profile_name(args), {})


def save_profile(args: argparse.Namespace, profile: dict[str, Any]) -> None:
    cfg = load_config()
    cfg.setdefault("profiles", {})[get_profile_name(args)] = profile
    save_config(cfg)


def configured_token(args: argparse.Namespace) -> str | None:
    return (
        args.token
        or os.environ.get("MAXCLI_TOKEN")
        or get_profile(args).get("token")
    )


def require_token(args: argparse.Namespace) -> str:
    token = configured_token(args)
    if not token:
        raise CliError(
            "No token found. Run `maxcli auth +7...` first, or set MAXCLI_TOKEN."
        )
    return token


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def timestamp_to_iso(value: Any) -> str | None:
    try:
        ts: float = int(value)
    except (TypeError, ValueError):
        return None
    if ts > 10_000_000_000:
        ts = ts / 1000
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def contact_name(contact: Any) -> str | None:
    names = getattr(contact, "names", None) or []
    if names:
        name = getattr(names[0], "name", None)
        if name:
            return name
        first = getattr(names[0], "first_name", None)
        last = getattr(names[0], "last_name", None)
        full = " ".join(part for part in [first, last] if part)
        if full:
            return full
    return None


def user_to_dict(user: Any) -> dict[str, Any]:
    contact = getattr(user, "contact", user)
    chat = getattr(user, "chat", None)
    return {
        "id": getattr(contact, "id", None),
        "chat_id": getattr(chat, "id", None),
        "name": contact_name(contact),
        "phone": getattr(contact, "phone", None),
        "link": getattr(contact, "link", None),
        "description": getattr(contact, "description", None),
    }


def message_to_dict(message: Any) -> dict[str, Any]:
    user = getattr(message, "user", None)
    sender_name = None
    if user:
        sender_name = user_to_dict(user).get("name")
    return {
        "id": getattr(message, "id", None),
        "chat_id": getattr(getattr(message, "chat", None), "id", None),
        "sender": getattr(message, "sender", None),
        "sender_name": sender_name,
        "time": getattr(message, "time", None),
        "time_iso": timestamp_to_iso(getattr(message, "time", None)),
        "text": getattr(message, "text", None),
        "type": getattr(message, "type", None),
        "attaches": getattr(message, "attaches", None) or [],
    }


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def print_user(user: Any, json_output: bool) -> None:
    data = user_to_dict(user)
    if json_output:
        print_json(data)
        return

    for key in ["id", "chat_id", "name", "phone", "link", "description"]:
        value = data.get(key)
        if value not in (None, ""):
            print(f"{key}: {value}")


def print_message(message: Any, json_output: bool) -> None:
    data = message_to_dict(message)
    if json_output:
        print_json(data)
        return

    time_part = data.get("time_iso") or data.get("time") or ""
    sender = data.get("sender_name") or data.get("sender") or "unknown"
    msg_id = data.get("id")
    text = data.get("text") or ""
    print(f"{time_part} {sender} #{msg_id}: {text}")


def connect_client(args: argparse.Namespace) -> Any:
    maxlib = load_maxlib()
    client = maxlib.MaxClient(token=require_token(args))
    client.connect()
    return client


def close_client(client: Any) -> None:
    try:
        client.disconnect()
    except Exception:
        pass


def resolve_chat_id(client: Any, args: argparse.Namespace) -> int:
    if args.chat_id is not None:
        return int(args.chat_id)
    if args.user_id is not None:
        return int(client.get_user(id=int(args.user_id)).chat.id)
    if args.phone is not None:
        return int(client.get_user(phone=args.phone).chat.id)
    raise CliError("Pass one recipient selector: --chat-id, --user-id, or --phone")


def command_auth(args: argparse.Namespace) -> int:
    maxlib = load_maxlib()
    phone = args.phone or input("Phone (+7...): ").strip()
    client = maxlib.MaxClient()

    try:
        user = client.auth(phone)
    finally:
        websocket = getattr(client, "websocket", None)
        if websocket:
            try:
                websocket.close()
            except Exception:
                pass

    token = getattr(client, "auth_token", None)
    if not token:
        raise CliError("Authentication finished without a token")

    profile = get_profile(args)
    profile.update(
        {
            "phone": phone,
            "token": token,
            "updated_at": now_iso(),
            "user": user_to_dict(user),
        }
    )
    save_profile(args, profile)

    print(f"Saved token for profile `{get_profile_name(args)}`: {config_path()}")
    print_user(user, args.json)
    return 0


def command_me(args: argparse.Namespace) -> int:
    client = connect_client(args)
    try:
        print_user(client.me, args.json)
    finally:
        close_client(client)
    return 0


def command_resolve(args: argparse.Namespace) -> int:
    client = connect_client(args)
    try:
        if args.chat_id is not None:
            user = client.get_user(chat_id=int(args.chat_id))
        elif args.user_id is not None:
            user = client.get_user(id=int(args.user_id))
        elif args.phone is not None:
            user = client.get_user(phone=args.phone)
        else:
            raise CliError("Pass one selector: --chat-id, --user-id, or --phone")
        print_user(user, args.json)
    finally:
        close_client(client)
    return 0


def read_message_text(args: argparse.Namespace) -> str:
    if args.stdin:
        return sys.stdin.read()
    if args.text:
        return " ".join(args.text)
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise CliError("Pass message text or use --stdin")


def command_send(args: argparse.Namespace) -> int:
    text = read_message_text(args)
    if not text.strip():
        raise CliError("Message text is empty")

    client = connect_client(args)
    try:
        chat_id = resolve_chat_id(client, args)
        message = client.send_message(
            chat_id,
            text,
            reply_id=args.reply_id,
            notify=not args.silent,
        )
        print_message(message, args.json)
    finally:
        close_client(client)
    return 0


def command_history(args: argparse.Namespace) -> int:
    maxlib = load_maxlib()
    client = connect_client(args)
    try:
        chat_id = resolve_chat_id(client, args)
        chat = maxlib.Chat(client, chat_id)
        messages = list(getattr(chat, "messages", []) or [])
        if args.reverse:
            messages.reverse()
        if args.limit is not None:
            messages = messages[: args.limit]
        if args.json:
            print_json([message_to_dict(message) for message in messages])
        else:
            for message in messages:
                print_message(message, False)
    finally:
        close_client(client)
    return 0


def command_listen(args: argparse.Namespace) -> int:
    maxlib = load_maxlib()
    client = maxlib.MaxClient(token=require_token(args))
    chat_filter = int(args.chat_id) if args.chat_id is not None else None

    def any_message(_client: Any, message: Any) -> bool:
        if chat_filter is None:
            return True
        message_chat_id = getattr(getattr(message, "chat", None), "id", None)
        return int(message_chat_id) == chat_filter

    @client.on_message(any_message)
    def on_message(_client: Any, message: Any) -> None:
        print_message(message, args.json)
        sys.stdout.flush()

    try:
        client.run()
        print("Listening. Press Ctrl+C to stop.", file=sys.stderr)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        client.stop()
    finally:
        close_client(client)
    return 0


def command_token_path(args: argparse.Namespace) -> int:
    print(config_path())
    return 0


def command_token_clear(args: argparse.Namespace) -> int:
    profile_name = get_profile_name(args)
    cfg = load_config()
    profile = cfg.get("profiles", {}).get(profile_name)
    if not profile:
        print(f"Profile `{profile_name}` does not exist.")
        return 0

    if args.remote:
        client = connect_client(args)
        try:
            client.session_exit()
        finally:
            close_client(client)

    cfg["profiles"].pop(profile_name, None)
    save_config(cfg)
    print(f"Removed local token for profile `{profile_name}`.")
    return 0


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile",
        help=f"Config profile name. Default: {DEFAULT_PROFILE}",
    )
    parser.add_argument(
        "--token",
        help="Override auth token. Also supported: MAXCLI_TOKEN.",
    )


def add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Print JSON output")


def add_recipient(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--chat-id", type=int, help="MAX chat id")
    group.add_argument("--user-id", type=int, help="MAX user/contact id")
    group.add_argument("--phone", help="Phone number, for example +79990000000")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maxcli",
        description="Unofficial MAX user-account CLI based on maxlib.",
    )
    add_common(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    auth = sub.add_parser("auth", help="Log in by phone and save local token")
    add_common(auth)
    add_json(auth)
    auth.add_argument("phone", nargs="?", help="Phone number, for example +79990000000")
    auth.set_defaults(func=command_auth)

    me = sub.add_parser("me", help="Show current account")
    add_common(me)
    add_json(me)
    me.set_defaults(func=command_me)

    resolve = sub.add_parser("resolve", help="Resolve phone/user/chat to user and DM chat id")
    add_common(resolve)
    add_json(resolve)
    add_recipient(resolve)
    resolve.set_defaults(func=command_resolve)

    send = sub.add_parser("send", help="Send a text message")
    add_common(send)
    add_json(send)
    add_recipient(send)
    send.add_argument("text", nargs="*", help="Message text")
    send.add_argument("--stdin", action="store_true", help="Read message text from stdin")
    send.add_argument("--reply-id", help="Message id to reply to")
    send.add_argument("--silent", action="store_true", help="Do not notify recipient")
    send.set_defaults(func=command_send)

    history = sub.add_parser("history", help="Show recent messages from a chat")
    add_common(history)
    add_json(history)
    add_recipient(history)
    history.add_argument("--limit", type=int, default=20, help="Number of messages to show")
    history.add_argument("--reverse", action="store_true", help="Reverse maxlib message order")
    history.set_defaults(func=command_history)

    listen = sub.add_parser("listen", help="Print incoming messages")
    add_common(listen)
    add_json(listen)
    listen.add_argument("--chat-id", type=int, help="Only print messages from this chat")
    listen.set_defaults(func=command_listen)

    token_path = sub.add_parser("token-path", help="Print config path")
    add_common(token_path)
    token_path.set_defaults(func=command_token_path)

    token_clear = sub.add_parser("token-clear", help="Remove saved token for a profile")
    add_common(token_clear)
    token_clear.add_argument(
        "--remote",
        action="store_true",
        help="Also revoke the active MAX session token remotely",
    )
    token_clear.set_defaults(func=command_token_clear)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CliError as exc:
        die(str(exc))
    except BrokenPipeError:
        return 1

