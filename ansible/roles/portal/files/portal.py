"""TransLog customer portal (test application): track a shipment, see the latest shipments and whether the
fileshare looks healthy. Small on purpose; it exists to show that the business keeps running (or not)."""
import os
import time
from contextlib import closing
from html import escape
from pathlib import Path

import psycopg2
from flask import Flask, request

app = Flask(__name__)
FILESHARE = Path(os.environ.get("FILESHARE", "/srv/fileshare"))

PAGE = """<!doctype html><html lang="nl"><head><meta charset="utf-8"><title>TransLog klantportaal</title>
<style>body{{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#1d2433}}
h1{{color:#0b5394}}table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #ddd;padding:.4rem;text-align:left}}
.ok{{color:#137333}}.bad{{color:#b3261e;font-weight:bold}}input{{padding:.4rem}}</style></head><body>
<h1>TransLog klantportaal</h1>{body}<p style="color:#888;font-size:.8rem">app01 &middot; {now}</p></body></html>"""


def db():
    return psycopg2.connect(dbname="translog", user="portal", password=os.environ["DB_PASSWORD"], host="127.0.0.1")


def fileshare_state():
    files = [p for p in FILESHARE.rglob("*") if p.is_file() and p.name != ".testdata"]
    locked = sum(1 for p in files if p.suffix == ".locked")
    return len(files), locked


@app.route("/")
def index():
    code = request.args.get("code", "").strip().upper()
    parts = ['<form><input name="code" placeholder="Trackingcode, bv. TL00001234" value="%s"> '
             '<button>Zoeken</button></form>' % escape(code)]
    with closing(db()) as conn, conn.cursor() as cur:
        if code:
            cur.execute("SELECT z.trackingcode, k.naam, z.van, z.naar, z.status, z.bijgewerkt FROM zendingen z "
                        "JOIN klanten k ON k.id = z.klant_id WHERE z.trackingcode = %s", (code,))
            row = cur.fetchone()
            parts.append("<p>Niet gevonden.</p>" if not row else
                         "<p><b>%s</b> voor %s: %s &rarr; %s, status <b>%s</b> (%s)</p>" % tuple(map(escape, map(str, row))))
        cur.execute("SELECT count(*) FROM zendingen")
        total = cur.fetchone()[0]
        cur.execute("SELECT z.trackingcode, k.naam, z.van, z.naar, z.status FROM zendingen z JOIN klanten k "
                    "ON k.id = z.klant_id ORDER BY z.aangemaakt DESC LIMIT 10")
        rows = cur.fetchall()
    n, locked = fileshare_state()
    share = ('<span class="bad">%d van %d bestanden versleuteld!</span>' % (locked, n) if locked
             else '<span class="ok">%d bestanden, niets versleuteld</span>' % n)
    parts.append("<p>Zendingen in de database: <b>%d</b> &middot; Fileshare: %s</p>" % (total, share))
    parts.append("<table><tr><th>Trackingcode</th><th>Klant</th><th>Van</th><th>Naar</th><th>Status</th></tr>"
                 + "".join("<tr>" + "".join("<td>%s</td>" % escape(str(c)) for c in r) + "</tr>" for r in rows)
                 + "</table>")
    return PAGE.format(body="".join(parts), now=time.strftime("%Y-%m-%d %H:%M:%S"))


@app.route("/health")
def health():
    """For monitoring and the restore tests: 200 when the database answers and nothing is encrypted."""
    try:
        with closing(db()) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM zendingen")
            total = cur.fetchone()[0]
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}, 500
    n, locked = fileshare_state()
    ok = total > 0 and locked == 0
    return {"ok": ok, "zendingen": total, "bestanden": n, "versleuteld": locked}, (200 if ok else 500)
