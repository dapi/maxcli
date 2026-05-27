from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import lz4.block
import logging
import msgpack
import os
import stat
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


APP_NAME = "maxcli"
DEFAULT_PROFILE = "default"
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".matroska"}


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
    patch_maxlib_websocket_origin(maxlib)
    return maxlib


def load_pymax() -> tuple[Any, Any]:
    try:
        from pymax import SocketMaxClient
        from pymax.payloads import UserAgentPayload
    except ImportError as exc:
        die(
            "Python package `maxapi-python` is not installed. "
            "Install it with: python3 -m pip install maxapi-python==1.2.5",
            2,
        )
        raise exc
    return SocketMaxClient, UserAgentPayload


def load_pymax_files() -> tuple[Any, Any, Any]:
    try:
        from pymax.files import File, Photo, Video
    except ImportError as exc:
        die(
            "Python package `maxapi-python` is not installed. "
            "Install it with: python3 -m pip install maxapi-python==1.2.5",
            2,
        )
        raise exc
    return File, Photo, Video


def patch_maxlib_websocket_origin(maxlib: Any) -> None:
    """Make maxlib compatible with MAX's current WebSocket handshake rules.

    maxlib 0.1b1 connects without an Origin header and MAX now rejects that
    handshake with HTTP 403. Keep the patch local so we can remove it when
    maxlib catches up or when this CLI switches transport.
    """
    max_module = getattr(maxlib, "max", None)
    if max_module is None or getattr(max_module, "_maxcli_origin_patch", False):
        return

    original_connect = max_module.connect

    def connect_with_origin(uri: str, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("origin", "https://web.max.ru")
        return original_connect(uri, *args, **kwargs)

    max_module.connect = connect_with_origin
    max_module._maxcli_origin_patch = True


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


def pymax_session_dir() -> Path:
    return config_home() / "pymax-session"


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


def value_from(source: Any, *names: str) -> Any:
    for name in names:
        if isinstance(source, dict) and name in source:
            return source[name]
        if not isinstance(source, dict) and hasattr(source, name):
            return getattr(source, name)
    return None


def attachment_to_dict(attach: Any, index: int) -> dict[str, Any]:
    data: dict[str, Any] = {
        "index": index,
        "type": enum_value(value_from(attach, "type", "_type")),
        "class": attach.__class__.__name__,
    }
    fields = {
        "file_id": ("file_id", "fileId"),
        "photo_id": ("photo_id", "photoId"),
        "video_id": ("video_id", "videoId"),
        "audio_id": ("audio_id", "audioId"),
        "contact_id": ("contact_id", "contactId"),
        "name": ("name", "file_name", "fileName"),
        "size": ("size",),
        "token": ("token",),
        "photo_token": ("photo_token", "photoToken"),
        "url": ("url",),
        "base_url": ("base_url", "baseUrl"),
        "thumbnail": ("thumbnail",),
        "width": ("width",),
        "height": ("height",),
        "duration": ("duration",),
    }
    for key, names in fields.items():
        value = value_from(attach, *names)
        if value not in (None, ""):
            data[key] = enum_value(value)
    return data


def message_to_dict(message: Any) -> dict[str, Any]:
    user = getattr(message, "user", None)
    sender_name = None
    if user:
        sender_name = user_to_dict(user).get("name")
    chat = getattr(message, "chat", None)
    return {
        "id": getattr(message, "id", None),
        "chat_id": getattr(chat, "id", None) or getattr(message, "chat_id", None),
        "sender": getattr(message, "sender", None),
        "sender_name": sender_name,
        "time": getattr(message, "time", None),
        "time_iso": timestamp_to_iso(getattr(message, "time", None)),
        "text": getattr(message, "text", None),
        "type": enum_value(getattr(message, "type", None)),
        "status": enum_value(getattr(message, "status", None)),
        "attaches": [
            attachment_to_dict(attach, index)
            for index, attach in enumerate(getattr(message, "attaches", None) or [])
        ],
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


def enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def compact_message_to_dict(message: Any | None) -> dict[str, Any] | None:
    if message is None:
        return None
    return {
        "id": getattr(message, "id", None),
        "chat_id": getattr(message, "chat_id", None),
        "sender": getattr(message, "sender", None),
        "time": getattr(message, "time", None),
        "time_iso": timestamp_to_iso(getattr(message, "time", None)),
        "type": enum_value(getattr(message, "type", None)),
        "status": enum_value(getattr(message, "status", None)),
    }


def chat_to_dict(chat: Any, kind: str | None = None) -> dict[str, Any]:
    return {
        "id": getattr(chat, "id", None),
        "cid": getattr(chat, "cid", None),
        "kind": kind,
        "type": enum_value(getattr(chat, "type", None)),
        "title": getattr(chat, "title", None),
        "description": getattr(chat, "description", None),
        "link": getattr(chat, "link", None),
        "status": getattr(chat, "status", None),
        "owner": getattr(chat, "owner", None),
        "participants_count": getattr(chat, "participants_count", None),
        "messages_count": getattr(chat, "messages_count", None),
        "created": getattr(chat, "created", None),
        "created_iso": timestamp_to_iso(getattr(chat, "created", None)),
        "modified": getattr(chat, "modified", None),
        "modified_iso": timestamp_to_iso(getattr(chat, "modified", None)),
        "last_event_time": getattr(chat, "last_event_time", None),
        "last_event_time_iso": timestamp_to_iso(getattr(chat, "last_event_time", None)),
        "last_message": compact_message_to_dict(getattr(chat, "last_message", None)),
    }


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def chat_matches(chat: Any, query: str) -> bool:
    normalized_query = normalize_text(query)
    if not normalized_query:
        return True
    haystacks = [
        getattr(chat, "title", None),
        getattr(chat, "description", None),
        getattr(chat, "link", None),
    ]
    normalized_haystack = " ".join(normalize_text(item) for item in haystacks if item)
    if normalized_query in normalized_haystack:
        return True
    terms = [term for term in normalized_query.split(" ") if term]
    return bool(terms) and all(term in normalized_haystack for term in terms)


def patch_pymax_socket_unpack(client: Any) -> None:
    def unpack_packet(data: bytes) -> dict[str, Any] | None:
        ver = int.from_bytes(data[0:1], "big")
        cmd = int.from_bytes(data[1:3], "big")
        seq = int.from_bytes(data[3:4], "big")
        opcode = int.from_bytes(data[4:6], "big")
        packed_len = int.from_bytes(data[6:10], "big", signed=False)
        comp_flag = packed_len >> 24
        payload_length = packed_len & 0xFFFFFF
        payload_bytes = data[10 : 10 + payload_length]

        payload = None
        if payload_bytes:
            if comp_flag != 0:
                original_payload = payload_bytes
                for size in (500_000, 2_000_000, 10_000_000, 50_000_000):
                    try:
                        payload_bytes = lz4.block.decompress(
                            original_payload,
                            uncompressed_size=size,
                        )
                        break
                    except lz4.block.LZ4BlockError:
                        continue
                else:
                    return None
            payload = msgpack.unpackb(payload_bytes, raw=False, strict_map_key=False)

        return {
            "ver": ver,
            "cmd": cmd,
            "seq": seq,
            "opcode": opcode,
            "payload": payload,
        }

    client._unpack_packet = unpack_packet


def quiet_pymax_logger() -> logging.Logger:
    logger = logging.getLogger("maxcli.pymax")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    logger.setLevel(logging.CRITICAL + 1)
    return logger


def create_pymax_client(args: argparse.Namespace) -> Any:
    SocketMaxClient, UserAgentPayload = load_pymax()
    profile = get_profile(args)
    phone = profile.get("phone") or os.environ.get("MAXCLI_PHONE")
    if not phone:
        raise CliError("No phone found. Run `maxcli auth +7...` first.")

    session_dir = pymax_session_dir()
    session_dir.mkdir(parents=True, exist_ok=True)
    client = SocketMaxClient(
        phone=phone,
        token=require_token(args),
        work_dir=str(session_dir),
        headers=UserAgentPayload(device_type="DESKTOP", app_version="25.12.14"),
        logger=quiet_pymax_logger(),
        reconnect=False,
    )
    patch_pymax_socket_unpack(client)
    return client


async def close_pymax_client(client: Any) -> None:
    cleanup = getattr(client, "_cleanup_client", None)
    if cleanup:
        await cleanup()
    else:
        await client.close()


async def sync_pymax_client(args: argparse.Namespace) -> Any:
    client = create_pymax_client(args)
    try:
        await client.connect(client.user_agent)
        await client._sync(client.user_agent)
        return client
    except Exception:
        await close_pymax_client(client)
        raise


def merged_pymax_chats(client: Any) -> list[tuple[str, Any]]:
    merged: list[tuple[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for kind, items in [
        ("dialog", getattr(client, "dialogs", [])),
        ("chat", getattr(client, "chats", [])),
        ("channel", getattr(client, "channels", [])),
    ]:
        for item in items:
            key = (kind, int(getattr(item, "id", 0) or 0))
            if key in seen:
                continue
            seen.add(key)
            merged.append((kind, item))
    return merged


async def collect_pymax_chats(args: argparse.Namespace) -> list[tuple[str, Any]]:
    client = await sync_pymax_client(args)
    try:
        pages = max(0, int(getattr(args, "fetch_pages", 0) or 0))
        marker: int | None = None
        for _ in range(pages):
            fetched = await client.fetch_chats(marker=marker)
            if not fetched:
                break
            marker_values = [
                value
                for chat in fetched
                for value in [
                    getattr(chat, "last_event_time", None),
                    getattr(chat, "modified", None),
                    getattr(chat, "created", None),
                ]
                if isinstance(value, int) and value > 0
            ]
            marker = min(marker_values) - 1 if marker_values else None
            if marker is None:
                break
        return merged_pymax_chats(client)
    finally:
        await close_pymax_client(client)


async def pymax_auth_token(phone: str) -> str:
    SocketMaxClient, UserAgentPayload = load_pymax()
    session_dir = pymax_session_dir()
    session_dir.mkdir(parents=True, exist_ok=True)
    client = SocketMaxClient(
        phone=phone,
        work_dir=str(session_dir),
        headers=UserAgentPayload(device_type="DESKTOP", app_version="25.12.14"),
        logger=quiet_pymax_logger(),
        reconnect=False,
    )

    try:
        await client.connect(client.user_agent)
        temp_token = await client.request_code(phone)
        print("Auth code: ", end="", flush=True)
        code = await asyncio.to_thread(lambda: sys.stdin.readline().strip())
        if len(code) != 6 or not code.isdigit():
            raise CliError("Auth code must contain 6 digits")

        login_resp = await client._send_code(code, temp_token)
        login_attrs = login_resp.get("tokenAttrs", {}).get("LOGIN", {})
        password_challenge = login_resp.get("passwordChallenge")

        if password_challenge and not login_attrs:
            track_id = password_challenge.get("trackId")
            if not track_id:
                raise CliError("MAX requested 2FA but did not return a track id")
            hint = password_challenge.get("hint") or "No hint provided"
            while True:
                password = await asyncio.to_thread(
                    lambda: getpass.getpass(f"2FA password (hint: {hint}): ")
                )
                if not password:
                    print("2FA password is empty, try again.", file=sys.stderr)
                    continue
                token_attrs = await client._check_password(password, track_id)
                if not token_attrs:
                    print("2FA password is incorrect, try again.", file=sys.stderr)
                    continue
                login_attrs = token_attrs.get("LOGIN", {})
                break

        token = login_attrs.get("token")
        if not token:
            raise CliError("Authentication finished without a token")

        database = getattr(client, "_database", None)
        device_id = getattr(client, "_device_id", None)
        if database is not None and device_id is not None:
            database.update_auth_token(device_id, token)
        return token
    finally:
        cleanup = getattr(client, "_cleanup_client", None)
        if cleanup:
            await cleanup()
        else:
            await client.close()


def command_auth(args: argparse.Namespace) -> int:
    phone = args.phone or input("Phone (+7...): ").strip()
    token = asyncio.run(pymax_auth_token(phone))

    profile = get_profile(args)
    profile.update(
        {
            "phone": phone,
            "token": token,
            "updated_at": now_iso(),
            "auth_backend": "pymax.SocketMaxClient",
        }
    )
    save_profile(args, profile)

    print(f"Saved token for profile `{get_profile_name(args)}`: {config_path()}")
    if args.json:
        print_json(
            {
                "profile": get_profile_name(args),
                "phone": phone,
                "config_path": str(config_path()),
            }
        )
    return 0


async def pymax_me(args: argparse.Namespace) -> dict[str, Any]:
    client = await sync_pymax_client(args)
    try:
        data = user_to_dict(client.me)
        data["phone"] = get_profile(args).get("phone")
        data["dialogs"] = len(getattr(client, "dialogs", []))
        data["chats"] = len(getattr(client, "chats", []))
        data["channels"] = len(getattr(client, "channels", []))
        return data
    finally:
        await close_pymax_client(client)


async def resolve_pymax_recipient_chat_id(client: Any, args: argparse.Namespace) -> int:
    if args.chat_id is not None:
        return int(args.chat_id)
    me_id = getattr(getattr(client, "me", None), "id", None)
    if not me_id:
        raise CliError("Cannot resolve recipient: current user id is missing")
    if args.user_id is not None:
        return int(client.get_chat_id(int(me_id), int(args.user_id)))
    if args.phone is not None:
        user = await client.search_by_phone(args.phone)
        return int(client.get_chat_id(int(me_id), int(user.id)))
    raise CliError("Pass one recipient selector: --chat-id, --user-id, or --phone")


async def resolve_pymax_recipient(args: argparse.Namespace) -> dict[str, Any]:
    client = await sync_pymax_client(args)
    try:
        if args.chat_id is not None:
            chat_id = int(args.chat_id)
            for kind, chat in merged_pymax_chats(client):
                if int(getattr(chat, "id", 0) or 0) == chat_id:
                    return chat_to_dict(chat, kind)
            return {"chat_id": chat_id}

        if args.user_id is not None:
            user = await client.get_user(int(args.user_id))
        elif args.phone is not None:
            user = await client.search_by_phone(args.phone)
        else:
            raise CliError("Pass one selector: --chat-id, --user-id, or --phone")

        data = user_to_dict(user)
        me_id = getattr(getattr(client, "me", None), "id", None)
        if me_id and data.get("id"):
            data["chat_id"] = int(client.get_chat_id(int(me_id), int(data["id"])))
        return data
    finally:
        await close_pymax_client(client)


async def pymax_send_message(args: argparse.Namespace, text: str) -> Any:
    client = await sync_pymax_client(args)
    try:
        chat_id = await resolve_pymax_recipient_chat_id(client, args)
        reply_to = int(args.reply_id) if args.reply_id else None
        attachments = build_pymax_attachments(args.file or [])
        return await client.send_message(
            text=text,
            chat_id=chat_id,
            notify=not args.silent,
            attachments=attachments or None,
            reply_to=reply_to,
        )
    finally:
        await close_pymax_client(client)


async def pymax_history(args: argparse.Namespace) -> list[Any]:
    client = await sync_pymax_client(args)
    try:
        chat_id = await resolve_pymax_recipient_chat_id(client, args)
        messages = await client.fetch_history(
            chat_id=chat_id,
            forward=0,
            backward=int(args.limit or 20),
        )
        return list(messages or [])
    finally:
        await close_pymax_client(client)


def validate_file_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.exists():
        raise CliError(f"File does not exist: {path}")
    if not path.is_file():
        raise CliError(f"Not a regular file: {path}")
    try:
        with path.open("rb"):
            pass
    except OSError as exc:
        raise CliError(f"Cannot read file {path}: {exc}") from exc
    return path


def build_pymax_attachments(paths: list[str]) -> list[Any]:
    if not paths:
        return []
    File, Photo, Video = load_pymax_files()
    attachments = []
    for value in paths:
        path = validate_file_path(value)
        suffix = path.suffix.lower()
        if suffix in PHOTO_EXTENSIONS:
            attachments.append(Photo(path=str(path)))
        elif suffix in VIDEO_EXTENSIONS:
            attachments.append(Video(path=str(path)))
        else:
            attachments.append(File(path=str(path)))
    return attachments


def sanitize_filename(value: str | None, fallback: str) -> str:
    name = Path(value or fallback).name
    cleaned = "".join(char if char not in '/\\\0' else "_" for char in name).strip()
    return cleaned or fallback


def output_path_for_attachment(output: str, attach: Any, message_id: str, index: int) -> Path:
    path = Path(output).expanduser()
    if path.exists() and path.is_dir():
        fallback = f"message-{message_id}-attach-{index}"
        return path / sanitize_filename(value_from(attach, "name", "file_name"), fallback)
    if str(output).endswith(os.sep):
        path.mkdir(parents=True, exist_ok=True)
        fallback = f"message-{message_id}-attach-{index}"
        return path / sanitize_filename(value_from(attach, "name", "file_name"), fallback)
    return path


def download_url_to_path(url: str, path: Path, force: bool) -> int:
    if path.exists() and not force:
        raise CliError(f"Output file already exists: {path}. Pass --force to overwrite.")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            total = 0
            tmp = path.with_suffix(path.suffix + ".tmp")
            with tmp.open("wb") as fh:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    total += len(chunk)
            tmp.replace(path)
            return total
    except (OSError, urllib.error.URLError) as exc:
        raise CliError(f"Download failed: {exc}") from exc


def message_id_equals(message: Any, message_id: str) -> bool:
    return str(getattr(message, "id", "")) == str(message_id)


def parse_message_id(value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise CliError(f"Message id must be numeric: {value}") from exc


async def pymax_download_attachment(args: argparse.Namespace) -> dict[str, Any]:
    client = await sync_pymax_client(args)
    try:
        chat_id = await resolve_pymax_recipient_chat_id(client, args)
        messages = await client.fetch_history(
            chat_id=chat_id,
            forward=0,
            backward=int(args.limit or 50),
        )
        message = next(
            (item for item in (messages or []) if message_id_equals(item, args.message_id)),
            None,
        )
        if message is None:
            raise CliError(
                f"Message {args.message_id} not found in last {args.limit} messages"
            )

        attaches = list(getattr(message, "attaches", None) or [])
        if not attaches:
            raise CliError(f"Message {args.message_id} has no attachments")
        if args.attach_index < 0 or args.attach_index >= len(attaches):
            raise CliError(
                f"Invalid attachment index {args.attach_index}; "
                f"message has {len(attaches)} attachment(s)"
            )

        attach = attaches[args.attach_index]
        url = value_from(attach, "url")
        file_id = value_from(attach, "file_id", "fileId")
        video_id = value_from(attach, "video_id", "videoId")
        base_url = value_from(attach, "base_url", "baseUrl")

        if file_id is not None:
            request = await client.get_file_by_id(
                chat_id=chat_id,
                message_id=parse_message_id(args.message_id),
                file_id=int(file_id),
            )
            url = getattr(request, "url", None)
        elif video_id is not None:
            request = await client.get_video_by_id(
                chat_id=chat_id,
                message_id=parse_message_id(args.message_id),
                video_id=int(video_id),
            )
            url = getattr(request, "url", None)
        elif not url and isinstance(base_url, str) and base_url.startswith("http"):
            url = base_url

        if not url:
            raise CliError(
                "Attachment is not downloadable with the current SDK metadata. "
                "Supported types: file, video, audio/url, and direct-url photo metadata."
            )

        output = output_path_for_attachment(
            args.output,
            attach,
            str(args.message_id),
            int(args.attach_index),
        )
        bytes_written = await asyncio.to_thread(
            download_url_to_path,
            str(url),
            output,
            bool(args.force),
        )
        return {
            "chat_id": chat_id,
            "message_id": args.message_id,
            "attach_index": args.attach_index,
            "path": str(output),
            "bytes": bytes_written,
            "attachment": attachment_to_dict(attach, args.attach_index),
        }
    finally:
        await close_pymax_client(client)


def command_me(args: argparse.Namespace) -> int:
    data = asyncio.run(pymax_me(args))
    if args.json:
        print_json(data)
    else:
        for key in ["id", "name", "phone", "dialogs", "chats", "channels"]:
            value = data.get(key)
            if value not in (None, ""):
                print(f"{key}: {value}")
    return 0


def command_chats_list(args: argparse.Namespace) -> int:
    chats = asyncio.run(collect_pymax_chats(args))
    rows = [chat_to_dict(chat, kind) for kind, chat in chats]
    rows.sort(key=lambda row: row.get("last_event_time") or 0, reverse=True)
    if args.limit is not None:
        rows = rows[: args.limit]
    if args.json:
        print_json(rows)
    else:
        for row in rows:
            title = row.get("title") or row.get("description") or "(untitled)"
            print(f"{row.get('kind')} {row.get('id')}: {title}")
    return 0


def command_chats_search(args: argparse.Namespace) -> int:
    chats = asyncio.run(collect_pymax_chats(args))
    rows = [
        chat_to_dict(chat, kind)
        for kind, chat in chats
        if chat_matches(chat, args.query)
    ]
    rows.sort(key=lambda row: row.get("last_event_time") or 0, reverse=True)
    if args.limit is not None:
        rows = rows[: args.limit]
    if args.json:
        print_json(rows)
    else:
        for row in rows:
            title = row.get("title") or row.get("description") or "(untitled)"
            print(f"{row.get('kind')} {row.get('id')}: {title}")
    return 0


def command_resolve(args: argparse.Namespace) -> int:
    data = asyncio.run(resolve_pymax_recipient(args))
    if args.json:
        print_json(data)
    else:
        for key in ["id", "chat_id", "title", "name", "phone", "link", "description"]:
            value = data.get(key)
            if value not in (None, ""):
                print(f"{key}: {value}")
    return 0


def read_message_text(args: argparse.Namespace) -> str:
    if args.stdin:
        return sys.stdin.read()
    if args.text:
        return " ".join(args.text)
    if getattr(args, "file", None):
        return ""
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise CliError("Pass message text or use --stdin")


def command_send(args: argparse.Namespace) -> int:
    text = read_message_text(args)
    if not text.strip() and not args.file:
        raise CliError("Message text is empty. Pass message text, --stdin, or --file.")
    for path in args.file or []:
        validate_file_path(path)

    message = asyncio.run(pymax_send_message(args, text))
    print_message(message, args.json)
    return 0


def command_history(args: argparse.Namespace) -> int:
    messages = asyncio.run(pymax_history(args))
    if args.reverse:
        messages.reverse()
    if args.limit is not None:
        messages = messages[: args.limit]
    if args.json:
        print_json([message_to_dict(message) for message in messages])
    else:
        for message in messages:
            print_message(message, False)
    return 0


def command_download(args: argparse.Namespace) -> int:
    data = asyncio.run(pymax_download_attachment(args))
    if args.json:
        print_json(data)
    else:
        print(data["path"])
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
        description="Unofficial MAX user-account CLI.",
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

    chats = sub.add_parser("chats", help="List and search MAX chats")
    add_common(chats)
    chats_sub = chats.add_subparsers(dest="chats_command", required=True)

    chats_list = chats_sub.add_parser("list", help="List chats from the account")
    add_common(chats_list)
    add_json(chats_list)
    chats_list.add_argument("--limit", type=int, default=50, help="Maximum rows to print")
    chats_list.add_argument(
        "--fetch-pages",
        type=int,
        default=0,
        help="Fetch older chat pages after initial sync",
    )
    chats_list.set_defaults(func=command_chats_list)

    chats_search = chats_sub.add_parser("search", help="Search chats by title/description/link")
    add_common(chats_search)
    add_json(chats_search)
    chats_search.add_argument("query", help="Search query")
    chats_search.add_argument("--limit", type=int, default=20, help="Maximum rows to print")
    chats_search.add_argument(
        "--fetch-pages",
        type=int,
        default=5,
        help="Fetch older chat pages after initial sync",
    )
    chats_search.set_defaults(func=command_chats_search)

    resolve = sub.add_parser("resolve", help="Resolve phone/user/chat to user and DM chat id")
    add_common(resolve)
    add_json(resolve)
    add_recipient(resolve)
    resolve.set_defaults(func=command_resolve)

    send = sub.add_parser("send", help="Send a text message or file attachment")
    add_common(send)
    add_json(send)
    add_recipient(send)
    send.add_argument("text", nargs="*", help="Message text")
    send.add_argument("--stdin", action="store_true", help="Read message text from stdin")
    send.add_argument(
        "--file",
        action="append",
        default=[],
        help="Attach a local file. Can be passed multiple times.",
    )
    send.add_argument("--reply-id", help="Message id to reply to")
    send.add_argument("--silent", action="store_true", help="Do not notify recipient")
    send.set_defaults(func=command_send)

    history = sub.add_parser("history", help="Show recent messages from a chat")
    add_common(history)
    add_json(history)
    add_recipient(history)
    history.add_argument("--limit", type=int, default=20, help="Number of messages to show")
    history.add_argument("--reverse", action="store_true", help="Reverse message order")
    history.set_defaults(func=command_history)

    download = sub.add_parser("download", help="Download a message attachment")
    add_common(download)
    add_json(download)
    add_recipient(download)
    download.add_argument("--message-id", required=True, help="Message id to inspect")
    download.add_argument(
        "--attach-index",
        type=int,
        default=0,
        help="Attachment index from history --json output",
    )
    download.add_argument(
        "--output",
        required=True,
        help="Output file path or existing directory",
    )
    download.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Number of recent messages to search for the message id",
    )
    download.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the output file if it already exists",
    )
    download.set_defaults(func=command_download)

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
