import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, scenario_id TEXT, payload TEXT, created_at TEXT);
                CREATE TABLE IF NOT EXISTS claims (scenario_id TEXT PRIMARY KEY, payload TEXT);
                CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, title TEXT, detail TEXT, created_at TEXT);
                CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS watched_incidents (key TEXT PRIMARY KEY, host TEXT, vendor TEXT, product TEXT, name TEXT, payload TEXT,
                    open INTEGER, simulated INTEGER, first_seen TEXT, last_seen TEXT, resolved_at TEXT);
                CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, incident_key TEXT, customer_id TEXT, customer TEXT, kind TEXT,
                    match_status TEXT, title TEXT, detail TEXT, ticket TEXT, simulated INTEGER, created_at TEXT, read_at TEXT,
                    UNIQUE(incident_key, customer_id, kind));
                CREATE TABLE IF NOT EXISTS watch_state (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS agreement_reads (id TEXT PRIMARY KEY, payload TEXT, created_at TEXT);
                CREATE TABLE IF NOT EXISTS user_cases (id TEXT PRIMARY KEY, payload TEXT, created_at TEXT);
                CREATE TABLE IF NOT EXISTS imported_customers (id TEXT PRIMARY KEY, payload TEXT, created_at TEXT);
                CREATE TABLE IF NOT EXISTS workspace_records (kind TEXT, id TEXT, payload TEXT NOT NULL, PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS attachments (id TEXT PRIMARY KEY, content BLOB NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def event(self, kind: str, title: str, detail: str = "", db=None):
        if db is not None:
            db.execute("INSERT INTO events(kind,title,detail,created_at) VALUES(?,?,?,?)", (kind, title, detail, utcnow()))
        else:
            with self.connect() as conn:
                self.event(kind, title, detail, conn)

    def events(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 100")]

    def get_claim(self, scenario_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM claims WHERE scenario_id=?", (scenario_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def claims(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM claims")]

    def preferences(self):
        with self.connect() as db:
            return {"tone": "polite", **{r[0]: json.loads(r[1]) for r in db.execute("SELECT key,value FROM preferences")}}

    def set_preference(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO preferences VALUES(?,?)", (key, json.dumps(value)))

    # Watcher state: each incident once, and at most one alert of each kind per customer per incident.
    def watched(self, key):
        with self.connect() as db:
            row = db.execute("SELECT * FROM watched_incidents WHERE key=?", (key,)).fetchone()
            return dict(row) if row else None

    def upsert_watched(self, key, host, vendor, product, incident, simulated, now):
        with self.connect() as db:
            db.execute("""INSERT INTO watched_incidents(key,host,vendor,product,name,payload,open,simulated,first_seen,last_seen)
                          VALUES(?,?,?,?,?,?,1,?,?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, name=excluded.name,
                          open=1, last_seen=excluded.last_seen, resolved_at=NULL""",
                       (key, host, vendor, product, incident.get("name"), json.dumps(incident), int(simulated), now, now))

    def open_watched(self, host):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM watched_incidents WHERE host=? AND open=1", (host,))]

    def resolve_watched(self, key, now):
        with self.connect() as db:
            db.execute("UPDATE watched_incidents SET open=0, resolved_at=? WHERE key=?", (now, key))

    def add_alert(self, incident_key, customer_id, customer, kind, match_status, title, detail, ticket, simulated, now) -> int:
        with self.connect() as db:
            cursor = db.execute("""INSERT OR IGNORE INTO alerts(incident_key,customer_id,customer,kind,match_status,title,detail,ticket,simulated,created_at)
                                   VALUES(?,?,?,?,?,?,?,?,?,?)""", (incident_key, customer_id, customer, kind, match_status, title, detail, ticket, int(simulated), now))
            return cursor.rowcount

    def alert_recipients(self, incident_key):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT customer_id, customer, match_status FROM alerts WHERE incident_key=? AND kind='ticket_now'", (incident_key,))]

    def alerts(self, limit=200):
        with self.connect() as db:
            rows = db.execute("""SELECT a.*, w.name AS incident_name, w.vendor, w.product, w.open AS incident_open, w.payload AS incident_payload
                                 FROM alerts a LEFT JOIN watched_incidents w ON w.key = a.incident_key ORDER BY a.id DESC LIMIT ?""", (limit,))
            result = []
            for row in rows:
                item = dict(row)
                payload = json.loads(item.pop("incident_payload") or "{}")
                item.update(simulated=bool(item["simulated"]), incident_open=bool(item["incident_open"]), read=item["read_at"] is not None,
                            shortlink=payload.get("shortlink"), started_at=payload.get("started_at"))
                result.append(item)
            return result

    def mark_alert_read(self, alert_id):
        with self.connect() as db:
            db.execute("UPDATE alerts SET read_at=? WHERE id=? AND read_at IS NULL", (utcnow(), alert_id))

    def unread_alerts(self) -> int:
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM alerts WHERE read_at IS NULL").fetchone()[0]

    def set_state(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO watch_state VALUES(?,?)", (key, json.dumps(value)))

    def get_state(self, key):
        with self.connect() as db:
            row = db.execute("SELECT value FROM watch_state WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def watched_list(self, limit=50):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM watched_incidents ORDER BY first_seen DESC LIMIT ?", (limit,))]

    # Agreements read in this workspace, and the cases started from them.
    def save_read(self, read_id, payload):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO agreement_reads VALUES(?,?,?)", (read_id, json.dumps(payload), utcnow()))

    def get_read(self, read_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM agreement_reads WHERE id=?", (read_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def save_user_case(self, case_id, payload: str):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO user_cases VALUES(?,?,?)", (case_id, payload, utcnow()))

    def user_cases(self) -> list[str]:
        with self.connect() as db:
            return [row[0] for row in db.execute("SELECT payload FROM user_cases ORDER BY created_at")]

    def delete_user_case(self, case_id):
        with self.connect() as db:
            db.execute("DELETE FROM user_cases WHERE id=?", (case_id,))
            db.execute("DELETE FROM claims WHERE scenario_id=?", (case_id,))

    # Customers imported from spend data (Ramp), kept beside the bundled directory.
    def save_imported(self, customer_id, payload: str):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO imported_customers VALUES(?,?,?)", (customer_id, payload, utcnow()))

    def imported_customers(self) -> list[str]:
        with self.connect() as db:
            return [row[0] for row in db.execute("SELECT payload FROM imported_customers ORDER BY created_at")]

    def delete_imported(self, customer_id) -> bool:
        with self.connect() as db:
            return db.execute("DELETE FROM imported_customers WHERE id=?", (customer_id,)).rowcount > 0

    def records(self, kind: str) -> list[dict]:
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM workspace_records WHERE kind=? ORDER BY rowid DESC", (kind,))]

    def record(self, kind: str, id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM workspace_records WHERE kind=? AND id=?", (kind, id)).fetchone()
            return json.loads(row[0]) if row else None

    def save_record(self, kind: str, payload: dict, content: bytes | None = None, only_new: bool = False):
        with self.connect() as db:
            sql = "INSERT OR IGNORE" if only_new else "INSERT OR REPLACE"
            changed = db.execute(f"{sql} INTO workspace_records VALUES(?,?,?)", (kind, payload["id"], json.dumps(payload))).rowcount
            if content is not None and changed:
                db.execute("INSERT OR REPLACE INTO attachments VALUES(?,?)", (payload["id"], content))
            return bool(changed)

    def attachment(self, id: str) -> bytes | None:
        with self.connect() as db:
            row = db.execute("SELECT content FROM attachments WHERE id=?", (id,)).fetchone()
            return bytes(row[0]) if row else None

    def reads(self) -> list[dict]:
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM agreement_reads ORDER BY created_at DESC")]
