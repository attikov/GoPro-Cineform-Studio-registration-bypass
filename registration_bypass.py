"""Temporary loopback registration responder for legacy GoPro / CineForm Studio."""
import argparse
import json
import os
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener
from urllib.parse import urlsplit
import winreg

KEY = r"Software\GoPro\CineFormStudio"
VALUE = "RegistrationURL"
STATE = Path(os.environ["LOCALAPPDATA"]) / "GoProRegistrationBypass" / "previous-setting.json"
ROUTE = "/api/v1/registrations/check"


class Responder(BaseHTTPRequestHandler):
    def do_GET(self):
        if urlsplit(self.path).path != ROUTE:
            self.send_error(404)
            return
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
        print("Studio registration check answered locally: HTTP 200", flush=True)

    def log_message(self, *args):
        pass  # Do not log serial numbers from query strings.


def read_setting(key):
    try:
        value, kind = winreg.QueryValueEx(key, VALUE)
        return {"exists": True, "value": value, "kind": kind}
    except FileNotFoundError:
        return {"exists": False}


def restore():
    if not STATE.exists():
        print("No saved registration override to restore.")
        return
    state = json.loads(STATE.read_text(encoding="utf-8"))
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, KEY, 0,
                            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
        current = read_setting(key)
        if current.get("value") == state["applied_url"]:
            previous = state["previous"]
            if previous["exists"]:
                winreg.SetValueEx(key, VALUE, 0, previous["kind"], previous["value"])
            else:
                winreg.DeleteValue(key, VALUE)
            print("Original registration-server setting restored.")
        else:
            print("RegistrationURL was changed elsewhere; leaving its current value untouched.")
    STATE.unlink()


def self_test():
    with ThreadingHTTPServer(("127.0.0.1", 0), Responder) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{server.server_port}"
        client = build_opener(ProxyHandler({}))
        try:
            with client.open(base + ROUTE + "?serial=TEST&product=2", timeout=3) as response:
                assert response.status == 200 and response.read() == b"{}"
            try:
                client.open(base + "/Firmware/firmware.xml", timeout=3)
                raise AssertionError("Unrelated endpoint was accepted")
            except HTTPError as error:
                assert error.code == 404
        finally:
            server.shutdown()
            worker.join(timeout=3)
    print("PASS: loopback registration returns 200; unrelated paths return 404; no registry changes.")


def run():
    if STATE.exists():
        raise RuntimeError("A saved override already exists. Close the old responder and run Restore registration.cmd first.")
    with ThreadingHTTPServer(("127.0.0.1", 0), Responder) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, KEY, 0,
                                winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
            previous = read_setting(key)
            STATE.parent.mkdir(parents=True, exist_ok=True)
            with STATE.open("x", encoding="utf-8") as state_file:
                json.dump({"previous": previous, "applied_url": url}, state_file, indent=2)
            try:
                winreg.SetValueEx(key, VALUE, 0, winreg.REG_SZ, url)
                print("Local GoPro Studio registration responder is active.")
                print("In Studio: Help > Device Window > Refresh.")
                print("Keep this window open until you are finished. Ctrl+C restores the setting.")
                print("If this window is closed forcibly, run Restore registration.cmd afterwards.")
                print("This utility does not download or flash firmware.", flush=True)
                server.serve_forever()
            except KeyboardInterrupt:
                print("\nStopping responder.")
            finally:
                restore()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--restore", action="store_true")
    mode.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.restore:
        restore()
    elif args.self_test:
        self_test()
    else:
        run()
