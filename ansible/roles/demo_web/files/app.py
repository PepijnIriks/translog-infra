"""TransLog demo website (demo01): live view of the three servers and buttons that start the tests.

demo01 has no special rights: it logs in on app01, backup01 and mon01 with its own SSH key as user "demo",
and that key can only start a fixed list of actions there (translog-demo-cmd). This page only shows and starts.
"""
import datetime
import json
import queue
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

from flask import Flask, Response, jsonify, request, send_file

TZ = ZoneInfo("Europe/Amsterdam")
KEY = "/opt/translog-demo/.ssh/id_ed25519"
HOSTS = {"app01": "10.20.1.10", "backup01": "10.20.3.10", "mon01": "10.20.2.10"}
ACTIONS = {
    "backup":           ("app01", False, "Back-up nu maken (Kopia, versleuteld naar backup01)"),
    "locktest":         ("backup01", False, "Test E1: back-ups proberen te wissen en te wijzigen"),
    "attack":           ("app01", False, "Aanval: T1490 (back-ups vernietigen) + T1486 (bestanden versleutelen)"),
    "attack-encrypt":   ("app01", False, "Aanval: alleen bestanden versleutelen (T1486)"),
    "rollback-preview": ("backup01", True, "Herstel stap 1a: laten zien welke back-upbestanden de aanvaller overschreef"),
    "rollback":         ("backup01", True, "Herstel stap 1b: overschreven back-upbestanden terugzetten"),
    "restore-list":     ("app01", False, "Herstel: lijst van back-ups (snapshots)"),
    "restore":          ("app01", True, "Herstel stap 2: fileshare en database terugzetten van vóór de aanval"),
    "s3-list":          ("backup01", False, "Bekijk de bucket: versies en locks"),
}

app = Flask(__name__)
lock = threading.Lock()
out_lock = threading.Lock()  # lines and listeners change together, so no line is lost or shown twice
runs = {}            # id -> {"action", "lines": [], "done", "code", "started", "ended", "q": [queues]}
events = []          # timeline: {"t", "kind", "text"}
state = {"t": 0, "hosts": {}}
last_attack = {"t": None}


def now():
    return time.time()


def event(kind, text, t=None):
    events.append({"t": t or now(), "kind": kind, "text": text})
    del events[:-200]


def ssh_cmd(host, args):
    return ["ssh", "-i", KEY, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
            "-o", "UserKnownHostsFile=/opt/translog-demo/.ssh/known_hosts", "-o", "ConnectTimeout=5",
            f"demo@{HOSTS[host]}", " ".join(args)]


def ssh_json(host):
    try:
        p = subprocess.run(ssh_cmd(host, ["status"]), capture_output=True, text=True, timeout=20)
        return {"reachable": True, **json.loads(p.stdout or "{}")}
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        return {"reachable": False, "error": type(e).__name__}
    except Exception as e:  # noqa: BLE001
        return {"reachable": False, "error": str(e)[:100]}


def poll():
    """Every 5 s: fetch the state of all servers and turn changes into timeline events (detection, recovery)."""
    prev = {}
    with ThreadPoolExecutor(3) as pool:
        while True:
            res = dict(zip(HOSTS, pool.map(ssh_json, HOSTS)))
            state.update({"t": now(), "hosts": res})
            a, b, m = res.get("app01", {}), res.get("backup01", {}), res.get("mon01", {})
            locked = a.get("locked")
            if prev and locked is not None and prev.get("locked") is not None:
                if locked > 0 and prev["locked"] == 0:
                    event("bad", f"app01: {locked} versleutelde bestanden op de fileshare")
                if locked == 0 and prev["locked"] > 0:
                    event("good", "app01: fileshare weer schoon (0 versleutelde bestanden)")
            health = (a.get("health") or {}).get("ok")
            if prev and health is not None and prev.get("health") is not None and health != prev["health"]:
                event("good" if health else "bad", "Portaal " + ("weer gezond" if health else "NIET gezond"))
                if health and last_attack["t"]:
                    mins = (now() - last_attack["t"]) / 60
                    event("measure", f"Hersteltijd (aanval tot portaal gezond): {mins:.1f} minuten (eis E4: < 4 uur)")
            ov = b.get("overwritten")
            if prev and ov is not None and prev.get("ov") is not None and ov != prev["ov"]:
                event("bad" if ov > prev["ov"] else "info", f"backup01: {ov} back-upbestanden hebben een nieuwere versie")
            probs = {(p["host"], p["name"]) for p in m.get("problems", [])}
            if "probs" in prev:
                for h, n in probs - prev["probs"]:
                    event("alarm", f"Zabbix-alarm: {n} ({h})")
                    if last_attack["t"] and ("ransomware" in n.lower() or "versleutel" in n.lower()):
                        secs = now() - last_attack["t"]
                        event("measure", f"Detectietijd: {secs/60:.1f} minuten na de start van de aanval (eis E12: < 5 min)")
                for h, n in prev["probs"] - probs:
                    event("good", f"Zabbix: opgelost - {n} ({h})")
            prev = {"locked": locked, "health": health, "ov": ov, "probs": probs if m.get("ok") else prev.get("probs", set())}
            time.sleep(5)


def run_action(rid, action, when):
    host = ACTIONS[action][0]
    r = runs[rid]
    args = [action] + (when.split() if when else [])
    event("start", f"Gestart: {ACTIONS[action][2]}" + (f" (moment {when})" if when else ""))
    if action.startswith("attack"):
        last_attack["t"] = now()
    try:
        p = subprocess.Popen(ssh_cmd(host, args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in p.stdout:
            with out_lock:
                r["lines"].append(line.rstrip("\n"))
                for q in list(r["q"]):
                    q.put(line.rstrip("\n"))
        r["code"] = p.wait()
    except Exception as e:  # noqa: BLE001
        r["lines"].append(f"FOUT: {e}")
        r["code"] = -1
    with out_lock:
        r.update(done=True, ended=now())
        for q in list(r["q"]):
            q.put(None)
    event("good" if r["code"] == 0 else "bad",
          f"Klaar: {action} (code {r['code']}, {r['ended'] - r['started']:.0f} s)")


@app.get("/")
def index():
    return send_file("/opt/translog-demo/index.html")


@app.get("/api/state")
def api_state():
    running = [{"id": k, "action": v["action"]} for k, v in runs.items() if not v["done"]]
    t = last_attack["t"]
    suggest = datetime.datetime.fromtimestamp(t or now(), TZ).strftime("%Y-%m-%d %H:%M")
    return jsonify(state=state, events=events[-60:], running=running, suggest_time=suggest,
                   actions={k: {"host": h, "needs_time": nt, "label": lb} for k, (h, nt, lb) in ACTIONS.items()})


@app.post("/api/run")
def api_run():
    data = request.get_json(force=True)
    action, when = data.get("action"), (data.get("when") or "").strip()
    if action not in ACTIONS:
        return {"error": "onbekende actie"}, 400
    if ACTIONS[action][1]:
        try:
            datetime.datetime.strptime(when, "%Y-%m-%d %H:%M")
        except ValueError:
            return {"error": "tijd moet zijn: JJJJ-MM-DD UU:MM"}, 400
    else:
        when = ""
    with lock:
        if any(not v["done"] for v in runs.values()):
            return {"error": "er loopt al een actie; wacht tot die klaar is"}, 409
        rid = str(int(now() * 1000))
        runs[rid] = {"action": action, "lines": [], "done": False, "code": None, "started": now(), "q": []}
    threading.Thread(target=run_action, args=(rid, action, when), daemon=True).start()
    return {"id": rid}


@app.get("/api/stream/<rid>")
def api_stream(rid):
    r = runs.get(rid)
    if not r:
        return {"error": "onbekend"}, 404

    def gen():
        q = queue.Queue()
        with out_lock:
            r["q"].append(q)
            backlog, finished = list(r["lines"]), r["done"]
        try:
            for line in backlog:
                yield f"data: {json.dumps(line)}\n\n"
            if finished:
                yield f"event: done\ndata: {json.dumps(r['code'])}\n\n"
                return
            while True:
                line = q.get(timeout=3600)
                if line is None:
                    yield f"event: done\ndata: {json.dumps(r['code'])}\n\n"
                    return
                yield f"data: {json.dumps(line)}\n\n"
        finally:
            r["q"].remove(q)

    return Response(gen(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


threading.Thread(target=poll, daemon=True).start()
event("info", "Demo-website gestart")
