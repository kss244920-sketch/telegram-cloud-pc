#!/usr/bin/env python3
import sys
import os
import json
import mimetypes
from pathlib import Path
import urllib.request

def main():
    if len(sys.argv) < 2:
        print("Usage: tg-send <file_path> [caption]")
        sys.exit(1)

    file_arg = sys.argv[1].strip()
    caption = sys.argv[2].strip() if len(sys.argv) > 2 else ""

    bot_token = os.getenv("BOT_TOKEN", "8798239169:AAFbzjM4n7wYzwVNrLbiw-cw0irXIGLI8yI")
    
    # Locate chat_id from state.json or env
    chat_id = os.getenv("ALLOWED_USER_ID")
    data_dir = Path("/app/data")
    if not data_dir.exists():
        data_dir = Path(__file__).resolve().parent / "data"

    state_file = data_dir / "state.json"
    if state_file.exists():
        try:
            with open(state_file, "r") as f:
                sdata = json.load(f)
                chat_id = sdata.get("last_chat_id") or sdata.get("allowed_user_id") or chat_id
        except Exception:
            pass

    if not chat_id:
        chat_id = "8327850869"

    file_path = Path(file_arg)
    if not file_path.exists():
        ws = Path("/app/workspace")
        if not ws.exists():
            ws = Path(__file__).resolve().parent / "workspace"
        matches = list(ws.rglob(file_path.name))
        if matches:
            file_path = matches[0]
        else:
            print(f"Error: File {file_arg} not found.")
            sys.exit(1)

    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    
    body = bytearray()
    
    def add_field(name, value):
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(f"{value}\r\n".encode())

    add_field("chat_id", str(chat_id))
    if caption:
        add_field("caption", caption)

    content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(f'Content-Disposition: form-data; name="document"; filename="{file_path.name}"\r\n'.encode())
    body.extend(f"Content-Type: {content_type}\r\n\r\n".encode())
    with open(file_path, "rb") as f:
        body.extend(f.read())
    body.extend(f"\r\n--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        url,
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status in (200, 201):
                print(f"✅ Successfully sent {file_path.name} to Telegram chat!")
            else:
                print(f"Telegram returned status {resp.status}")
    except Exception as e:
        print(f"❌ Failed to send file to Telegram: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
