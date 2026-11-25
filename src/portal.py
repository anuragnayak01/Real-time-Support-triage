"""InsightPulse portal: auth (admin/customer), tickets, analytics, support-inbox poller.
Wire-up in src/api.py (3 lines):
    from portal import router, init_db, start_email_poller
    init_db(); app.include_router(router); start_email_poller()
Also add CORSMiddleware (allow_origins=["*"] for demo) so the Vercel frontend can call it.
Admin is fixed to abcd123@gmail.com; set ADMIN_PASSWORD (required) on the server.
Env: JWT_SECRET, ADMIN_PASSWORD, SUPPORT_EMAIL, SUPPORT_APP_PASSWORD, IMAP_HOST
"""
import os, time, json, hmac, hashlib, base64, sqlite3, imaplib, email, threading, secrets
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel

DB = os.getenv("TICKETS_DB", "tickets.db")
SECRET = os.getenv("JWT_SECRET", "change-me").encode()
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "abcd123@gmail.com").lower()  # the ONLY admin
router = APIRouter()


def db():
    c = sqlite3.connect(DB, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(8)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 100_000).hex()


def check_pw(pw, stored):
    return hmac.compare_digest(hash_pw(pw, stored.split("$")[0]), stored)


def make_token(u):
    body = base64.urlsafe_b64encode(json.dumps({"id": u["id"], "role": u["role"], "cid": u["customer_id"], "exp": time.time() + 86400}).encode()).decode()
    return body + "." + hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()


def current_user(authorization: str = Header(default="")):
    try:
        body, sig = authorization.replace("Bearer ", "").split(".")
        assert hmac.compare_digest(sig, hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest())
        p = json.loads(base64.urlsafe_b64decode(body))
        assert p["exp"] > time.time()
        return p
    except Exception:
        raise HTTPException(401, "Not authenticated")


def admin_only(u=Depends(current_user)):
    if u["role"] != "admin":
        raise HTTPException(403, "Admins only")
    return u


def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE, pw TEXT, role TEXT, customer_id TEXT);
    CREATE TABLE IF NOT EXISTS portal_tickets(id INTEGER PRIMARY KEY, customer_id TEXT, email TEXT, source TEXT,
      subject TEXT, message TEXT, sentiment REAL, category TEXT, priority TEXT, reply TEXT,
      status TEXT DEFAULT 'open', created REAL);""")
    a, p = ADMIN_EMAIL, os.getenv("ADMIN_PASSWORD")
    if a and p and not c.execute("SELECT 1 FROM users WHERE email=?", (a,)).fetchone():
        c.execute("INSERT INTO users(email,pw,role) VALUES(?,?,'admin')", (a, hash_pw(p)))
    c.commit()


ANALYZER = None


def set_analyzer(fn):
    """api.py registers the real pipeline here: fn(text, source) -> dict."""
    global ANALYZER
    ANALYZER = fn


def analyze(text, source="portal"):
    if ANALYZER:
        try:
            return ANALYZER(text, source)
        except Exception as e:
            print("pipeline failed, using fallback:", e)
    """EDIT HERE: call your real pipeline (LangGraph sentiment/features + CrewAI RAG reply)
    and return dict(sentiment=-1..1, category=str, priority=str, reply=str)."""
    # from pipeline import run_triage; return run_triage(text)
    t = text.lower()
    neg = sum(w in t for w in ["charged", "refund", "angry", "worst", "broken", "down", "cannot", "urgent", "crash"])
    cat = "Billing" if any(w in t for w in ["charge", "refund", "invoice"]) else "Technical" if any(w in t for w in ["error", "crash", "login", "bug"]) else "General"
    return dict(sentiment=round(-min(neg, 4) / 4, 2), category=cat, priority="critical" if neg >= 3 else "high" if neg == 2 else "normal",
                reply="Thanks for reaching out - we're looking into this and will update you shortly.")


def save_ticket(customer_id, mail, source, subject, message):
    a = analyze(subject + "\n" + message, source)
    c = db()
    c.execute("INSERT INTO portal_tickets(customer_id,email,source,subject,message,sentiment,category,priority,reply,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
              (customer_id, mail, source, subject, message, a["sentiment"], a["category"], a["priority"], a["reply"], time.time()))
    c.commit()
    # Critical tickets: reuse your existing Slack alert / Jira card function here.


class Auth(BaseModel):
    email: str
    password: str


class NewTicket(BaseModel):
    subject: str
    message: str


@router.post("/auth/signup")
def signup(a: Auth):
    if a.email.lower() == ADMIN_EMAIL:
        raise HTTPException(403, "This email is reserved")
    c = db()
    cid = "CUST-" + secrets.token_hex(3).upper()
    try:
        c.execute("INSERT INTO users(email,pw,role,customer_id) VALUES(?,?,'customer',?)", (a.email.lower(), hash_pw(a.password), cid))
        c.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Email already registered")
    return login(a)


@router.post("/auth/login")
def login(a: Auth):
    u = db().execute("SELECT * FROM users WHERE email=?", (a.email.lower(),)).fetchone()
    if not u or not check_pw(a.password, u["pw"]):
        raise HTTPException(401, "Invalid credentials")
    return {"token": make_token(u), "role": u["role"], "customer_id": u["customer_id"]}


@router.post("/portal/tickets")
def create_ticket(t: NewTicket, u=Depends(current_user)):
    if u["role"] != "customer":
        raise HTTPException(403, "Only registered customers can file complaints")
    mail = db().execute("SELECT email FROM users WHERE id=?", (u["id"],)).fetchone()["email"]
    save_ticket(u["cid"], mail, "portal", t.subject, t.message)
    return {"ok": True}


@router.get("/portal/tickets")
def my_tickets(u=Depends(current_user)):
    return [dict(r) for r in db().execute("SELECT * FROM portal_tickets WHERE customer_id=? ORDER BY created DESC", (u["cid"],))]


@router.get("/admin/tickets")
def all_tickets(_=Depends(admin_only)):
    return [dict(r) for r in db().execute("SELECT * FROM portal_tickets ORDER BY created DESC LIMIT 500")]


@router.patch("/admin/tickets/{tid}")
def set_status(tid: int, status: str, _=Depends(admin_only)):
    c = db(); c.execute("UPDATE portal_tickets SET status=? WHERE id=?", (status, tid)); c.commit()
    return {"ok": True}


@router.get("/admin/stats")
def stats(_=Depends(admin_only)):
    q = lambda s: [dict(r) for r in db().execute(s)]
    return {"total": q("SELECT COUNT(*) n FROM portal_tickets")[0]["n"],
            "avg_sentiment": q("SELECT ROUND(AVG(sentiment),2) v FROM portal_tickets")[0]["v"],
            "by_priority": q("SELECT priority k, COUNT(*) n FROM portal_tickets GROUP BY priority"),
            "by_source": q("SELECT source k, COUNT(*) n FROM portal_tickets GROUP BY source"),
            "by_category": q("SELECT category k, COUNT(*) n FROM portal_tickets GROUP BY category")}


def _poll_inbox():
    host, user, pw = os.getenv("IMAP_HOST", "imap.gmail.com"), os.getenv("SUPPORT_EMAIL"), os.getenv("SUPPORT_APP_PASSWORD")
    while user and pw:
        try:
            m = imaplib.IMAP4_SSL(host); m.login(user, pw); m.select("INBOX")
            for num in m.search(None, "UNSEEN")[1][0].split():
                msg = email.message_from_bytes(m.fetch(num, "(RFC822)")[1][0][1])
                body = next((p.get_payload(decode=True).decode(errors="ignore") for p in msg.walk() if p.get_content_type() == "text/plain"), "")
                sender = email.utils.parseaddr(msg["From"])[1].lower()
                row = db().execute("SELECT customer_id FROM users WHERE email=?", (sender,)).fetchone()
                save_ticket(row["customer_id"] if row else "GUEST", sender, "email", msg["Subject"] or "(no subject)", body[:4000])
            m.logout()
        except Exception as e:
            print("inbox poll error:", e)
        time.sleep(30)


def start_email_poller():
    threading.Thread(target=_poll_inbox, daemon=True).start()
