"""screen-control end-to-end test: open Notepad, type Unicode text, save, verify.

Every step is verified with a screenshot + OCR. All commands go through the
HTTP API, so the server must be running (python server.py).

Note: this test drives the REAL desktop — a Notepad window opens and a file
is saved to the Desktop/Documents folder of the logged-in user.
"""
import glob
import json
import os
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8745"
FILE_NAME = "screen-control-test.txt"
TEXT = "Merhaba! Bu metin screen-control tarafindan yazildi. Turkce harfler: ğüşöçı ĞÜŞÖÇİ."

# The server requires an auth token on every request (see README → Security).
try:
    TOKEN = open(".token", encoding="utf-8").read().strip()
except FileNotFoundError:
    print("ERROR: .token not found — start the server first (python server.py)")
    sys.exit(1)


def post(path, body):
    """POST JSON to the API with the auth token; return the parsed response."""
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Auth-Token": TOKEN})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def key(body):
    return post("/api/key", body)


def ocr():
    return post("/api/ocr", {}).get("text", "")


def step(label, secs):
    """Wait, then OCR the screen — helper for the verify-every-step loop."""
    time.sleep(secs)
    t = ocr()
    print(f"\n--- {label} ---")
    print("OCR:", repr(t[:400]))
    return t


def main():
    """Run the end-to-end scenario; returns a process exit code."""
    if len(sys.argv) > 1 and sys.argv[1] == "--skip-open":
        print("Assuming Notepad is already open (--skip-open)")
    else:
        print("1) Open the Run dialog (Win+R)")
        key({"action": "hotkey", "keys": ["win", "r"]})
        time.sleep(1.2)

        print("2) Type 'notepad' and press Enter")
        key({"action": "type", "text": "notepad"})
        time.sleep(0.8)
        key({"action": "press", "key": "enter"})
        t = step("Did Notepad open?", 2.5)
        if not any(w in t.lower() for w in ("not", "untitled", "defter")):
            print("WARNING: OCR found no sign of Notepad — continuing anyway...")

    print("\n3) Type Unicode text (Turkish characters included)")
    key({"action": "type", "text": TEXT})
    step("Was the text typed?", 1.5)

    print("\n4) Open the save dialog (Ctrl+S)")
    key({"action": "hotkey", "keys": ["ctrl", "s"]})
    step("Did the save dialog open?", 1.5)

    print(f"\n5) Type the file name: {FILE_NAME}")
    key({"action": "type", "text": FILE_NAME})
    time.sleep(0.5)
    key({"action": "press", "key": "enter"})
    step("Was the file saved?", 2.0)

    print("\n6) Verify the file on disk")
    candidates = [
        os.path.join(os.path.expanduser("~/Desktop"), FILE_NAME),
        os.path.join(os.path.expanduser("~/Documents"), FILE_NAME),
        os.path.join(os.path.expanduser("~/OneDrive/Desktop"), FILE_NAME),
        os.path.join(os.path.expanduser("~/OneDrive/Documents"), FILE_NAME),
    ]
    found = None
    for p in candidates:
        if os.path.exists(p):
            found = p
            break
    if found is None:
        hits = glob.glob(os.path.expanduser("~/**/" + FILE_NAME), recursive=True)
        found = hits[0] if hits else None
    if found:
        print("FOUND:", found)
        with open(found, encoding="utf-8") as fh:
            content = fh.read()
        print("FILE CONTENT:", repr(content))
        print("Content matches:", content == TEXT)
        return 0 if content == TEXT else 2
    print("FILE NOT FOUND — the save dialog may still be open")
    return 1


if __name__ == "__main__":
    sys.exit(main())