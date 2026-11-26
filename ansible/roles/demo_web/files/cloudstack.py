"""Small client for the EduCloud (Apache CloudStack) API, used by the demo website to isolate and rebuild app01.

The API key is pasted once on the demo website and stored in /opt/translog-demo/private/cloudstack.json
(readable only by the website user). It is never shown again and never stored in Git.
Only these API calls are used: listVirtualMachines, stopVirtualMachine, restoreVirtualMachine,
startVirtualMachine, queryAsyncJobResult.
"""
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://educloud.fontysict.nl/client/api"
KEYFILE = "/opt/translog-demo/private/cloudstack.json"


def configured():
    return os.path.exists(KEYFILE)


def save(apikey, secret):
    os.makedirs(os.path.dirname(KEYFILE), mode=0o700, exist_ok=True)
    fd = os.open(KEYFILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"apikey": apikey.strip(), "secret": secret.strip()}, f)


def call(command, **params):
    with open(KEYFILE) as f:
        k = json.load(f)
    params.update(command=command, response="json", apikey=k["apikey"])
    query = "&".join(f"{key}={urllib.parse.quote(str(val), safe='*')}" for key, val in sorted(params.items()))
    sig = base64.b64encode(hmac.new(k["secret"].encode(), query.lower().encode(), hashlib.sha1).digest()).decode()
    url = f"{API}?{query}&signature={urllib.parse.quote(sig, safe='')}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            msg = next(iter(json.loads(body).values())).get("errortext", body)
        except Exception:  # noqa: BLE001
            msg = body[:200]
        raise RuntimeError(f"EduCloud API {command}: {msg}") from None
    return next(iter(data.values()))


def wait_job(jobid, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = call("queryAsyncJobResult", jobid=jobid)
        if r.get("jobstatus") == 1:
            return r.get("jobresult", {})
        if r.get("jobstatus") == 2:
            raise RuntimeError(f"EduCloud job mislukt: {r.get('jobresult', {}).get('errortext', r)}")
        time.sleep(4)
    raise RuntimeError("EduCloud job duurt te lang")


def vm(name):
    vms = call("listVirtualMachines", name=name, listall="true").get("virtualmachine", [])
    vms = [v for v in vms if v["name"] == name]
    if len(vms) != 1:
        raise RuntimeError(f"{len(vms)} servers met de naam {name} gevonden")
    return vms[0]
