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
                if health and last_attack["t"] and not last_attack.get("recovered"):
                    last_attack["recovered"] = True
                    mins = (now() - last_attack["t"]) / 60
                    event("measure", f"Hersteltijd (aanval tot portaal gezond): {mins:.1f} minuten (eis E4: < 4 uur)")
            ov = b.get("overwritten")
            if prev and ov is not None and prev.get("ov") is not None and ov != prev["ov"]:
                event("bad" if ov > prev["ov"] else "info", f"backup01: {ov} back-upbestanden hebben een nieuwere versie")
            # only real alarms (severity "gemiddeld" or higher); information messages are left out
            probs = {(p["host"], p["name"]) for p in m.get("problems", []) if p.get("severity", 0) >= 3}
            if "probs" in prev:
                for h, n in probs - prev["probs"]:
                    event("alarm", f"Zabbix-alarm: {n} ({h})")
                    if last_attack["t"] and not last_attack.get("detected") and (
                            "ransomware" in n.lower() or "versleutel" in n.lower()):
                        last_attack["detected"] = True
                        secs = now() - last_attack["t"]
                        event("measure", f"Detectietijd: {secs/60:.1f} minuten na de start van de aanval (eis E12: < 5 min)")
                for h, n in prev["probs"] - probs:
                    event("good", f"Zabbix: opgelost - {n} ({h})")
            prev = {"locked": locked, "health": health, "ov": ov, "probs": probs if m.get("ok") else prev.get("probs", set())}
            time.sleep(5)


def emit(r, line):
    with out_lock:
        r["lines"].append(line)
        for q in list(r["q"]):
            q.put(line)


def finish(r, code):
    with out_lock:
        r.update(done=True, ended=now(), code=code)
        for q in list(r["q"]):
            q.put(None)


def exec_into(r, action, when=""):
    """Runs one action over SSH and streams its output into run r. Returns the exit code."""
    host = ACTIONS[action][0]
    args = [action] + (when.split() if when else [])
    event("start", f"Gestart: {ACTIONS[action][2]}" + (f" (moment {when})" if when else ""))
    if action.startswith("attack"):
        last_attack.update(t=now(), detected=False, recovered=False)
    t0 = now()
    try:
        p = subprocess.Popen(ssh_cmd(host, args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in p.stdout:
            emit(r, line.rstrip("\n"))
        code = p.wait()
    except Exception as e:  # noqa: BLE001
        emit(r, f"FOUT: {e}")
        code = -1
    event("good" if code == 0 else "bad", f"Klaar: {action} (code {code}, {now() - t0:.0f} s)")
    return code


def run_action(rid, action, when):
    r = runs[rid]
    finish(r, exec_into(r, action, when))


# ---------------------------------------------------------------- complete demo with one button
STEPS = [
    ("Uitgangssituatie", "We kijken eerst of alles normaal werkt: het portaal draait, er zijn geen versleutelde "
     "bestanden en Zabbix heeft geen alarmen."),
    ("Verse back-up (E5)", "app01 maakt nu een back-up van de database en de fileshare. Kopia versleutelt die op app01 "
     "en zet hem op backup01. Daar wordt elk bestand automatisch 14 dagen vergrendeld (object lock, COMPLIANCE)."),
    ("Back-ups zijn onaantastbaar (E1)", "Voordat de aanval komt, laten we zien dat de back-ups niet te vernietigen "
     "zijn: met de sleutel van app01 én als beheerder van backup01 proberen we ze te wissen, te overschrijven en de "
     "bewaartijd in te korten. Alles moet geweigerd worden."),
    ("Moment van vóór de aanval vastleggen", "We wachten tot de volgende hele minuut en noteren die tijd. Alles wat "
     "daarna gebeurt, hoort bij de aanval. Het herstel zet alles terug naar dit moment."),
    ("De aanval (T1490 + T1486)", "Het testscript doet een echte ransomware-aanval na, op testdata. Stap 1: de "
     "aanvaller heeft app01 overgenomen en gebruikt de back-upsleutel die daar staat om alle back-ups te wissen en te "
     "overschrijven. Stap 2: hij versleutelt alle bestanden op de fileshare en laat een losgeldbericht achter."),
    ("Detectie door Zabbix (E12)", "Zabbix kijkt elke minuut naar de fileshare. We wachten op het alarm. Eis: binnen "
     "5 minuten na de start van de aanval."),
    ("Schade aan de back-ups bekijken", "De aanvaller kon niets wissen, maar wel nieuwe versies over back-upbestanden "
     "schrijven. We kijken welke bestanden dat zijn. De oude versies staan er nog: die zijn vergrendeld."),
    ("Herstel 1: back-upopslag terugzetten", "Op backup01 maken we de vergrendelde versies van vóór de aanval weer de "
     "actuele versie. Er wordt niets gewist."),
    ("Herstel 2: app01 terugzetten (E6)", "app01 haalt de laatste back-up van vóór de aanval op en zet de fileshare en "
     "de database terug. Zo komen er geen versleutelde bestanden mee terug."),
    ("Controle en uitslag (E4)", "We controleren of het portaal weer werkt, of er geen versleutelde bestanden meer "
     "zijn en of het Zabbix-alarm vanzelf verdwijnt. Daarna de gemeten tijden."),
]
scenario = {"active": False, "current": -1, "status": [], "results": {}, "stop": False, "rid": None}


def h(name):
    return state.get("hosts", {}).get(name, {})


def wait_for(cond, timeout, r, every=5, note=None):
    t0 = now()
    while now() - t0 < timeout:
        if scenario["stop"]:
            return False
        if cond():
            return True
        if note and int(now() - t0) % 30 < every:
            emit(r, f"   … {note} ({int(now() - t0)} s)")
        time.sleep(every)
    return False


def alarm():
    for p in h("mon01").get("problems", []):
        if p.get("severity", 0) >= 4 and ("versleutel" in p["name"].lower() or "ransomware" in p["name"].lower()):
            return p
    return None


def run_scenario(rid):
    r = runs[rid]
    res = scenario["results"]
    ok = True

    def step(i):
        if scenario["stop"]:
            raise RuntimeError("gestopt op verzoek")
        scenario["current"] = i
        scenario["status"][i] = "bezig"
        title, text = STEPS[i]
        emit(r, "")
        emit(r, f"=== STAP {i + 1} van {len(STEPS)}: {title} ===")
        emit(r, f"   {text}")
        emit(r, "")
        event("start", f"Demo stap {i + 1}: {title}")

    def done(i, good=True, note=""):
        scenario["status"][i] = "goed" if good else "fout"
        emit(r, f"   >> {'GOED' if good else 'NIET GOED'}{': ' + note if note else ''}")

    try:
        # 1
        step(0)
        a, m = h("app01"), h("mon01")
        health = (a.get("health") or {})
        emit(r, f"   Portaal werkt: {'ja' if health.get('ok') else 'NEE'} · zendingen: {health.get('zendingen')} · "
                f"bestanden: {a.get('files')} · versleuteld: {a.get('locked')}")
        emit(r, f"   Open Zabbix-alarmen (ernst gemiddeld of hoger): "
                f"{sum(1 for p in m.get('problems', []) if p.get('severity', 0) >= 3)}")
        good = bool(health.get("ok")) and a.get("locked") == 0 and alarm() is None
        done(0, good, "omgeving is gezond" if good else
             "eerst herstellen (portaal, versleutelde bestanden of open ransomware-alarm) voordat de demo kan starten")
        if not good:
            raise RuntimeError("omgeving niet gezond")
        # 2
        step(1)
        c = exec_into(r, "backup")
        done(1, c == 0, "back-up gemaakt en gecontroleerd" if c == 0 else f"back-up mislukt (code {c})")
        if c != 0:
            raise RuntimeError("back-up mislukt")
        # 3
        step(2)
        c = exec_into(r, "locktest")
        res["e1"] = c == 0
        done(2, c == 0, "geen enkele poging gelukt" if c == 0 else "een poging is gelukt")
        # 4
        step(3)
        wait = 61 - datetime.datetime.now(TZ).second
        emit(r, f"   We wachten {wait} seconden tot de volgende minuut…")
        time.sleep(wait)
        moment = datetime.datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
        res["moment"] = moment
        emit(r, f"   Moment van vóór de aanval: {moment}")
        done(3, True, moment)
        time.sleep(3)
        # 5
        step(4)
        t_attack = now()
        res["attack_start"] = t_attack
        exec_into(r, "attack")
        done(4, True, "aanval uitgevoerd; kijk hierboven: '0 deleted' betekent dat geen back-up gewist kon worden")
        # 6
        step(5)
        got = wait_for(lambda: alarm() is not None, 360, r, note="wachten op het alarm van Zabbix")
        if got:
            p = alarm()
            det = max(0, p["since"] - t_attack)
            res["detect_s"] = det
            emit(r, f"   Zabbix-alarm: {p['name']} (ernst: {p['severity_name']})")
            done(5, det < 300, f"gedetecteerd na {det / 60:.1f} minuten (eis: binnen 5 minuten)")
        else:
            done(5, False, "geen alarm binnen 6 minuten")
        # 7
        step(6)
        exec_into(r, "rollback-preview", moment)
        done(6, True)
        # 8
        step(7)
        c = exec_into(r, "rollback", moment)
        done(7, c == 0, "back-upopslag staat weer zoals vóór de aanval" if c == 0 else f"code {c}")
        # 9
        step(8)
        c = exec_into(r, "restore", moment)
        done(8, c == 0, "fileshare en database teruggezet" if c == 0 else f"herstel mislukt (code {c})")
        # 10
        step(9)
        healthy = wait_for(lambda: (h("app01").get("health") or {}).get("ok") and h("app01").get("locked") == 0,
                           120, r, note="wachten tot het portaal weer gezond is")
        t_ok = now()
        res["recover_s"] = t_ok - t_attack
        cleared = wait_for(lambda: alarm() is None, 150, r, note="wachten tot Zabbix het alarm opheft")
        a = h("app01")
        emit(r, "")
        emit(r, "=== UITSLAG ===")
        emit(r, f"   E1  back-ups niet te vernietigen ........ {'GEHAALD' if res.get('e1') else 'NIET GEHAALD'}")
        if "detect_s" in res:
            emit(r, f"   E12 detectie binnen 5 minuten ........... {res['detect_s'] / 60:.1f} min · "
                    f"{'GEHAALD' if res['detect_s'] < 300 else 'NIET GEHAALD'}")
        emit(r, f"   E6  geen versleutelde bestanden terug .... {a.get('locked')} over · "
                f"{'GEHAALD' if a.get('locked') == 0 else 'NIET GEHAALD'}")
        emit(r, f"   E4  hersteltijd (aanval tot werkend portaal) {res['recover_s'] / 60:.1f} min · "
                f"{'GEHAALD' if healthy and res['recover_s'] < 4 * 3600 else 'NIET GEHAALD'}")
        emit(r, f"   Zabbix-alarm vanzelf opgeheven: {'ja' if cleared else 'nog niet'}")
        event("measure", f"Demo klaar: detectie {res.get('detect_s', 0) / 60:.1f} min, herstel {res['recover_s'] / 60:.1f} min")
        done(9, bool(healthy), "omgeving is hersteld" if healthy else "portaal nog niet gezond")
    except Exception as e:  # noqa: BLE001
        ok = False
        emit(r, f"   DEMO GESTOPT: {e}")
        event("bad", f"Demo gestopt: {e}")
    scenario["active"] = False
    finish(r, 0 if ok else 1)


@app.get("/")
def index():
    return send_file("/opt/translog-demo/index.html")


@app.get("/api/state")
def api_state():
    running = [{"id": k, "action": v["action"]} for k, v in runs.items() if not v["done"]]
    t = last_attack["t"]
    suggest = datetime.datetime.fromtimestamp(t or now(), TZ).strftime("%Y-%m-%d %H:%M")
    sc = {"active": scenario["active"], "current": scenario["current"], "rid": scenario["rid"],
          "results": scenario["results"],
          "steps": [{"title": t, "text": x, "status": scenario["status"][i] if i < len(scenario["status"]) else ""}
                    for i, (t, x) in enumerate(STEPS)]}
    if scenario["active"]:
        running = [{"id": scenario["rid"], "action": "scenario"}]
    return jsonify(state=state, events=events[-60:], running=running, suggest_time=suggest, scenario=sc,
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
        if scenario["active"] or any(not v["done"] for v in runs.values()):
            return {"error": "er loopt al een actie; wacht tot die klaar is"}, 409
        rid = str(int(now() * 1000))
        runs[rid] = {"action": action, "lines": [], "done": False, "code": None, "started": now(), "q": []}
    threading.Thread(target=run_action, args=(rid, action, when), daemon=True).start()
    return {"id": rid}


@app.post("/api/scenario")
def api_scenario():
    with lock:
        if scenario["active"] or any(not v["done"] for v in runs.values()):
            return {"error": "er loopt al iets; wacht tot het klaar is"}, 409
        rid = str(int(now() * 1000))
        runs[rid] = {"action": "scenario", "lines": [], "done": False, "code": None, "started": now(), "q": []}
        scenario.update(active=True, current=-1, status=[""] * len(STEPS), results={}, stop=False, rid=rid)
    threading.Thread(target=run_scenario, args=(rid,), daemon=True).start()
    return {"id": rid}


@app.post("/api/scenario/stop")
def api_scenario_stop():
    scenario["stop"] = True
    return {"ok": True}


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
