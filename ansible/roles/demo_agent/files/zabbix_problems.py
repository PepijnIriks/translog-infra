#!/usr/bin/env python3
"""Prints the open Zabbix problems as JSON (for the demo website). Read-only. Runs on mon01 as root."""
import json
import pathlib
import time
import urllib.request

API = "http://127.0.0.1/api_jsonrpc.php"
SEV = ["niet ingedeeld", "informatie", "waarschuwing", "gemiddeld", "hoog", "ramp"]


def call(method, params, token=None):
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode()
    req = urllib.request.Request(API, body, {"Content-Type": "application/json-rpc"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=10) as r:
        out = json.load(r)
    if "error" in out:
        raise RuntimeError(out["error"].get("data", out["error"]))
    return out["result"]


def main():
    try:
        token = call("user.login", {"username": "Admin",
                                    "password": pathlib.Path("/root/zabbix-admin.txt").read_text().strip()})
        try:
            trig = call("trigger.get", {"output": ["description", "priority", "lastchange"], "filter": {"value": 1},
                                        "monitored": True, "expandDescription": True, "selectHosts": ["host"],
                                        "sortfield": "lastchange", "sortorder": "DESC"}, token)
            hosts = call("host.get", {"output": ["host"], "selectInterfaces": ["ip"]}, token)
        finally:
            call("user.logout", [], token)
        problems = [{"host": (t["hosts"] or [{"host": "?"}])[0]["host"], "name": t["description"],
                     "severity": int(t["priority"]), "severity_name": SEV[int(t["priority"])],
                     "since": int(t["lastchange"])} for t in trig]
        print(json.dumps({"ok": True, "now": int(time.time()), "problems": problems,
                          "hosts": sorted(h["host"] for h in hosts)}))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(e)[:200], "problems": []}))


if __name__ == "__main__":
    main()
