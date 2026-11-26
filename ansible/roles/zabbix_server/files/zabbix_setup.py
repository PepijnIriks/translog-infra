#!/usr/bin/env python3
"""Sets up the TransLog checks and alarms in Zabbix through its API (safe to run again).

- Host group "TransLog"; template group "TransLog templates".
- Template "TransLog-app" (app01): back-up result, time of the last good back-up, changed and encrypted files.
    Alarms: back-up failed (E11), no good back-up for 26 h, many files changed in 1 minute (E12),
    encrypted files found.
- Template "TransLog-backup" (backup01): S3 storage reachable. Disk space (E10, 80 %) comes from the standard
  "Linux by Zabbix agent active" template.
- Auto-registration: every agent with metadata "translog" is added by itself, gets the Linux template and,
  by its role (productie / backup), the TransLog template. A new server needs no clicking (E9).
- The first time, the default Admin password is replaced by a random one in /root/zabbix-admin.txt.
"""
import argparse
import json
import pathlib
import secrets
import sys
import time
import urllib.request

API = "http://127.0.0.1/api_jsonrpc.php"
PWFILE = pathlib.Path("/root/zabbix-admin.txt")
token = None
changes = []


def call(method, params):
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode()
    req = urllib.request.Request(API, body, {"Content-Type": "application/json-rpc"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.load(r)
    if "error" in out:
        raise RuntimeError(f"{method}: {out['error']}")
    return out["result"]


def login():
    global token
    for _ in range(60):                      # the web interface needs a moment after installing
        try:
            call("apiinfo.version", {})
            break
        except Exception:                    # noqa: BLE001
            time.sleep(5)
    if PWFILE.exists():
        token = call("user.login", {"username": "Admin", "password": PWFILE.read_text().strip()})
        return
    token = call("user.login", {"username": "Admin", "password": "zabbix"})
    new = secrets.token_urlsafe(18)
    admin = call("user.get", {"filter": {"username": "Admin"}})[0]
    call("user.update", {"userid": admin["userid"], "current_passwd": "zabbix", "passwd": new})
    PWFILE.write_text(new + "\n")
    PWFILE.chmod(0o600)
    changes.append("updated Admin password (in /root/zabbix-admin.txt)")
    token = call("user.login", {"username": "Admin", "password": new})


def one(method, flt, create):
    """get-or-create; returns the id"""
    kind = method.split(".")[0]
    idkey = {"hostgroup": "groupid", "templategroup": "groupid", "template": "templateid",
             "action": "actionid"}[kind]
    found = call(f"{kind}.get", {"filter": flt, "output": [idkey]})
    if found:
        return found[0][idkey]
    changes.append(f"created {kind} {flt}")
    return call(f"{kind}.create", create)[f"{idkey}s"][0]


def ensure_items(templateid, items):
    have = {i["key_"]: i for i in call("item.get", {"hostids": templateid, "output": ["key_"]})}
    for name, key, delay in items:
        if key not in have:
            call("item.create", {"hostid": templateid, "name": name, "key_": key, "type": 7,
                                 "value_type": 3, "delay": delay})
            changes.append(f"created item {key}")


def ensure_triggers(templateid, triggers):
    have = {t["description"]: t for t in call("trigger.get", {"hostids": templateid, "output": ["description", "expression"],
                                                              "expandExpression": True})}
    for desc, expr, prio in triggers:
        if desc not in have:
            call("trigger.create", {"description": desc, "expression": expr, "priority": prio})
            changes.append(f"created trigger {desc}")
        elif have[desc]["expression"] != expr:
            call("trigger.update", {"triggerid": have[desc]["triggerid"], "expression": expr, "priority": prio})
            changes.append(f"updated trigger {desc}")


def autoreg(name, metadata, operations):
    one("action.get", {"name": name}, {
        "name": name, "eventsource": 2, "status": 0,
        "filter": {"evaltype": 0, "conditions": [{"conditiontype": 24, "operator": 2, "value": metadata}]},
        "operations": operations})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=int, default=50)
    a = ap.parse_args()
    login()

    hg = one("hostgroup.get", {"name": "TransLog"}, {"name": "TransLog"})
    tg = one("templategroup.get", {"name": "TransLog templates"}, {"name": "TransLog templates"})

    app = one("template.get", {"host": "TransLog-app"},
              {"host": "TransLog-app", "name": "TransLog app01: back-ups en ransomware", "groups": [{"groupid": tg}],
               "macros": [{"macro": "{$RANSOMWARE.FILES}", "value": str(a.threshold)}]})
    ensure_items(app, [("Back-up: resultaat laatste back-up (0 = goed)", "translog.backup.status", "1m"),
                       ("Back-up: tijd laatste geslaagde back-up", "translog.backup.lastok", "1m"),
                       ("Fileshare: bestanden gewijzigd in de laatste minuut", "translog.files.changed", "1m"),
                       ("Fileshare: versleutelde bestanden (*.locked)", "translog.files.locked", "1m")])
    ensure_triggers(app, [
        ("Back-up mislukt op {HOST.NAME}", "last(/TransLog-app/translog.backup.status)<>0", 4),
        ("Geen geslaagde back-up in 26 uur op {HOST.NAME}", "fuzzytime(/TransLog-app/translog.backup.lastok,93600)=0", 3),
        ("Mogelijke ransomware op {HOST.NAME}: veel bestanden in 1 minuut gewijzigd",
         "last(/TransLog-app/translog.files.changed)>{$RANSOMWARE.FILES}", 5),
        ("Versleutelde bestanden (*.locked) gevonden op {HOST.NAME}", "last(/TransLog-app/translog.files.locked)>0", 5),
    ])

    bak = one("template.get", {"host": "TransLog-backup"},
              {"host": "TransLog-backup", "name": "TransLog backup01: S3-opslag", "groups": [{"groupid": tg}]})
    ensure_items(bak, [("S3-opslag: HTTP-antwoord", "translog.s3.up", "1m")])
    ensure_triggers(bak, [("S3-opslag niet bereikbaar op {HOST.NAME}", "last(/TransLog-backup/translog.s3.up)=0", 4)])

    linux = call("template.get", {"filter": {"host": "Linux by Zabbix agent active"}, "output": ["templateid"]})
    if not linux:
        sys.exit("Template 'Linux by Zabbix agent active' not found")
    linux = linux[0]["templateid"]

    autoreg("TransLog: alle servers", "translog", [
        {"operationtype": 2},
        {"operationtype": 4, "opgroup": [{"groupid": hg}]},
        {"operationtype": 6, "optemplate": [{"templateid": linux}]}])
    autoreg("TransLog: app01 (productie)", "productie", [
        {"operationtype": 2}, {"operationtype": 6, "optemplate": [{"templateid": app}]}])
    autoreg("TransLog: backup01 (backup)", "backup", [
        {"operationtype": 2}, {"operationtype": 6, "optemplate": [{"templateid": bak}]}])

    print("\n".join(changes) if changes else "Zabbix already set up")


if __name__ == "__main__":
    main()
