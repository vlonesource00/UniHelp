"""UniHelp - servidor da comunidade (só biblioteca padrão do Python, nada a instalar).

Executar:   python servidor.py
Configuração: cria um ficheiro chamado .env (na mesma pasta deste ficheiro) - vê o .env.example.
Variáveis opcionais (no .env ou no ambiente do Windows):
  HOST (127.0.0.1; 0.0.0.0 para aceitar ligações de outros computadores)  PORT (8765)
  UNIHELP_DB (ficheiro)  ADMIN_PASSWORD (obrigatória fora do 127.0.0.1)
  TRUST_PROXY=1  -> quando está atrás de um proxy/túnel (Cloudflare, Fly.io, Render...), para ler o IP real
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM  -> para enviar emails a sério.
Sem SMTP_HOST, os emails (códigos de 6 dígitos) aparecem escritos NESTA consola, para testares.
"""
import base64, binascii, json, os, re, secrets, hashlib, smtplib, sqlite3, struct, sys, threading, time
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs


# pasta da app: ao lado do UniHelp.exe (versão compilada) ou deste ficheiro
APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def load_env():
    """Lê o ficheiro .env (ao lado da app) sem precisar de nenhuma biblioteca extra."""
    f = APP_DIR / ".env"
    if not f.exists(): return
    for line in f.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1); k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'": v = v[1:-1]
        if v: os.environ.setdefault(k, v)  # variáveis já definidas no sistema têm prioridade


load_env()
DB = os.environ.get("UNIHELP_DB", str(APP_DIR / "unihelp_server.db"))
ADMIN_USER = "admin"
ADMIN_PASS = os.environ.get("ADMIN_PASSWORD", "admin1234")
DAY = 24 * 3600
LOCK = threading.RLock()
USER_RX = re.compile(r"^[A-Za-z0-9_]{3,20}$")
EMAIL_RX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
TRUST_PROXY = os.environ.get("TRUST_PROXY") == "1"
HITS = {}  # chave -> instantes recentes (limites de tentativas, em memória)
SYNC_MAIL = False  # True no WSGI: o PythonAnywhere não corre threads em segundo plano

con = sqlite3.connect(DB, check_same_thread=False)
con.row_factory = sqlite3.Row


def init():
    con.executescript("""
    create table if not exists users(id integer primary key, username text unique collate nocase, email text unique collate nocase,
      name text default '', surname text default '', bio text default '', avatar text, salt text, hash text,
      verified int default 0, role text default 'user', suspended int default 0, streak int default 0, created real);
    create table if not exists tokens(token text primary key, user int, created real);
    create table if not exists codes(user int, kind text, hash text, expires real, tries int default 0, primary key(user, kind));
    create table if not exists posts(id integer primary key, user int, body text, created real, hidden int default 0, deleted int default 0);
    create table if not exists likes(post int, user int, primary key(post, user));
    create table if not exists comments(id integer primary key, post int, user int, body text, created real,
      hidden int default 0, deleted int default 0);
    create table if not exists follows(a int, b int, created real, primary key(a, b));
    create table if not exists blocks(a int, b int, primary key(a, b));
    create table if not exists notifs(id integer primary key, user int, kind text, actor int, post int, created real, seen int default 0);
    create table if not exists reports(kind text, target int, reporter int, reason text, created real, primary key(kind, target, reporter));
    """)
    adm = one("select * from users where username=?", (ADMIN_USER,))
    if not adm:
        salt = secrets.token_hex(16)
        run("insert into users(username,email,name,salt,hash,verified,role,created) values(?,?,?,?,?,1,'admin',?)",
            (ADMIN_USER, "admin@unihelp.local", "Administrador", salt, hpw(ADMIN_PASS, salt), time.time()))
    elif not secrets.compare_digest(hpw(ADMIN_PASS, adm["salt"]), adm["hash"]):
        # a palavra-passe definida em ADMIN_PASSWORD mudou (ou a conta foi criada com outra): sincroniza
        salt = secrets.token_hex(16)
        run("update users set salt=?, hash=?, role='admin', verified=1, suspended=0 where id=?", (salt, hpw(ADMIN_PASS, salt), adm["id"]))
        run("delete from tokens where user=?", (adm["id"],))
        print("Palavra-passe da conta admin atualizada a partir de ADMIN_PASSWORD.", flush=True)
    run("delete from tokens where created<?", (now() - 90 * DAY,))
    if ADMIN_PASS == "admin1234":
        print("AVISO: a conta admin usa a palavra-passe por defeito. Define ADMIN_PASSWORD no .env antes de publicares o servidor.")


def check_config(host):
    """Recusa abrir o servidor à rede com a palavra-passe de admin por defeito."""
    if host not in LOCAL_HOSTS and (ADMIN_PASS == "admin1234" or len(ADMIN_PASS) < 12):
        sys.exit("ERRO: para aceitar ligações de outros computadores define ADMIN_PASSWORD (12+ caracteres) no .env.")


# utilitários 
def run(sql, a=()):
    cur = con.execute(sql, a); con.commit(); return cur
def rows(sql, a=()):
    return con.execute(sql, a).fetchall()
def one(sql, a=()):
    r = con.execute(sql, a).fetchone(); return r
def hpw(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 120_000).hex()
def sha(x):
    return hashlib.sha256(x.encode()).hexdigest()
def now():
    return time.time()
def recent(key, window):
    """Quantas vezes a chave foi registada nos últimos `window` segundos (cada chave usa sempre a mesma janela)."""
    h = [x for x in HITS.get(key, ()) if x > now() - window]; HITS[key] = h; return len(h)
def hit(key):
    if len(HITS) > 20_000:  # limpeza de chaves antigas
        for k in [k for k, h in HITS.items() if not h or h[-1] < now() - DAY]: del HITS[k]
    HITS.setdefault(key, []).append(now())


DUMMY_SALT = secrets.token_hex(16)
DUMMY_HASH = hpw("-", DUMMY_SALT)  # para um login com conta inexistente demorar o mesmo que um verdadeiro


class Err(Exception):
    def __init__(s, code, msg, **extra):
        s.code, s.msg, s.extra = code, msg, extra


def _smtp_send(to, subject, text):
    host = os.environ["SMTP_HOST"]; port = int(os.environ.get("SMTP_PORT", 587)); user = os.environ.get("SMTP_USER")
    try:
        m = EmailMessage(); m["To"], m["Subject"] = to, subject
        m["From"] = os.environ.get("SMTP_FROM") or user or "unihelp@localhost"; m.set_content(text)
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=15) as s:
                if user: s.login(user, os.environ.get("SMTP_PASS", ""))
                s.send_message(m)
        else:
            with smtplib.SMTP(host, port, timeout=15) as s:
                s.starttls()
                if user: s.login(user, os.environ.get("SMTP_PASS", ""))
                s.send_message(m)
        print(f"[EMAIL enviado] para {to}", flush=True)
    except Exception as e:
        print(f"\n[EMAIL FALHOU] {e!r}\nPara: {to}\nAssunto: {subject}\n{text}\n", flush=True)


def mail(to, subject, text):
    if not os.environ.get("SMTP_HOST"):
        print(f"\n[EMAIL simulado] Para: {to}\nAssunto: {subject}\n{text}\n", flush=True); return
    if SYNC_MAIL: return _smtp_send(to, subject, text)
    # em segundo plano, para um SMTP lento não bloquear o servidor todo
    threading.Thread(target=_smtp_send, args=(to, subject, text), daemon=True).start()


def send_code(c, uid, email, kind):
    """Envia um código, no máximo 1 por minuto e 5 por hora para cada email (e 20 por hora por IP).
    Devolve False quando o limite impediu o envio."""
    em = email.lower()
    if recent(("mail1", em), 60) or recent(("mail", em), 3600) >= 5 or recent(("mailip", c.ip), 3600) >= 20: return False
    hit(("mail1", em)); hit(("mail", em)); hit(("mailip", c.ip))
    code = f"{secrets.randbelow(10 ** 6):06d}"
    run("insert or replace into codes(user,kind,hash,expires,tries) values(?,?,?,?,0)", (uid, kind, sha(code), now() + 900))
    what = "confirmar o teu email" if kind == "verify" else "repor a tua palavra-passe"
    mail(email, "UniHelp - o teu código", f"O teu código para {what} é: {code}\nExpira em 15 minutos.\nSe não foste tu, ignora este email.")
    return True


def check_code(uid, kind, code):
    # o limite diário conta todas as tentativas falhadas, mesmo pedindo códigos novos
    if recent(("guess", uid, kind), DAY) >= 20: raise Err(429, "Demasiadas tentativas hoje. Tenta novamente amanhã.")
    r = one("select * from codes where user=? and kind=?", (uid, kind))
    if not r or r["expires"] < now(): raise Err(400, "Código expirado ou inexistente. Pede um novo.")
    if r["tries"] >= 5: raise Err(429, "Demasiadas tentativas. Pede um novo código.")
    if not secrets.compare_digest(sha(str(code).strip()), r["hash"]):
        hit(("guess", uid, kind))
        run("update codes set tries=tries+1 where user=? and kind=?", (uid, kind)); raise Err(400, "Código incorreto.")
    run("delete from codes where user=? and kind=?", (uid, kind))


def new_token(uid):
    t = secrets.token_urlsafe(32); run("insert into tokens values(?,?,?)", (sha(t), uid, now())); return t


def user_by_login(login):
    login = (login or "").strip()
    return one("select * from users where username=? or email=?", (login, login))


def by_name(username):
    u = one("select * from users where username=?", ((username or "").strip().lstrip("@"),))
    if not u: raise Err(404, "Utilizador não encontrado.")
    return u


def blocked(a, b):
    return bool(one("select 1 from blocks where (a=? and b=?) or (a=? and b=?)", (a, b, b, a)))


def pub(u):
    return {"username": u["username"], "name": u["name"], "surname": u["surname"]}
def mine(u):
    return {**pub(u), "email": u["email"], "bio": u["bio"], "streak": u["streak"], "role": u["role"], "avatar": u["avatar"]}
def avs(users):
    return {u["username"]: u["avatar"] for u in users if u["avatar"]}


def check_avatar(a):
    """Só aceita PNG pequenos (a app envia 64x64): evita imagens gigantes que rebentam a memória dos outros."""
    if a is None: return None
    try: raw = base64.b64decode(a, validate=True) if isinstance(a, str) and len(a) <= 150_000 else b""
    except (binascii.Error, ValueError): raw = b""
    if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR": raise Err(400, "Foto inválida.")
    w, h = struct.unpack(">II", raw[16:24])
    if not (0 < w <= 256 and 0 < h <= 256): raise Err(400, "Foto demasiado grande.")
    return a


def text(b, key, lo, hi, what):
    v = (b.get(key) or "").strip()
    if not lo <= len(v) <= hi: raise Err(400, f"{what}: entre {lo} e {hi} caracteres.")
    return v


#  roteador
ROUTES = []
def route(method, path, auth="user"):
    rx = re.compile("^" + re.sub(r"<(\w+)>", r"(?P<\1>[^/]+)", path) + "$")
    def deco(fn): ROUTES.append((method, rx, auth, fn)); return fn
    return deco


class Ctx:
    def __init__(s, me, body, q, tok="", ip=""): s.me, s.body, s.q, s.tok, s.ip = me, body, q, tok, ip
    def arg(s, k, d=""): return (s.q.get(k) or [d])[0]


def client_ip(forwarded, direct):
    if TRUST_PROXY and forwarded.strip(): return forwarded.split(",")[-1].strip()  # o proxy acrescenta o IP real no fim
    return direct


def process(method, path, header, length, read, ip):
    """Trata um pedido e devolve (código, dados). Usado pelo servidor normal e pelo WSGI (PythonAnywhere)."""
    try:
        try: n = int(length or 0)
        except ValueError: raise Err(400, "Pedido inválido.")
        if n < 0: raise Err(400, "Pedido inválido.")
        if n > 400_000: raise Err(413, "Pedido demasiado grande.")
        try: body = json.loads(read(n)) if n else {}
        except ValueError: raise Err(400, "JSON inválido.")
        if not isinstance(body, dict): raise Err(400, "JSON inválido.")
        u = urlparse(path)
        with LOCK:
            for m, rx, auth, fn in ROUTES:
                mt = rx.match(u.path)
                if m != method or not mt: continue
                me, tok = None, ""
                if auth:
                    tok = (header("Authorization") or "").replace("Bearer ", "").strip()
                    me = one("select u.* from tokens t join users u on u.id=t.user where t.token=? and t.created>?",
                             (sha(tok), now() - 90 * DAY)) if tok else None
                    if not me or me["suspended"]: raise Err(401, "Sessão inválida. Entra novamente.")
                    if auth == "admin" and me["role"] != "admin": raise Err(403, "Sem permissões de administrador.")
                return 200, fn(Ctx(me, body, parse_qs(u.query), tok if auth else "", ip), **mt.groupdict())
        raise Err(404, "Endereço inexistente.")
    except Err as e:
        return e.code, {"error": e.msg, **e.extra}
    except Exception as e:
        print("Erro interno:", repr(e), flush=True); return 500, {"error": "Erro interno do servidor."}


class Handler(BaseHTTPRequestHandler):
    timeout = 20  # liga lenta ou parada não prende o servidor
    def log_message(s, *a): pass

    def dispatch(s, method):
        code, data = process(method, s.path, s.headers.get, s.headers.get("Content-Length"), s.rfile.read,
                             client_ip(s.headers.get("X-Forwarded-For", ""), s.client_address[0]))
        raw = json.dumps(data).encode()
        s.send_response(code); s.send_header("Content-Type", "application/json; charset=utf-8")
        s.send_header("Content-Length", str(len(raw))); s.end_headers(); s.wfile.write(raw)

    def do_GET(s): s.dispatch("GET")
    def do_POST(s): s.dispatch("POST")
    def do_PUT(s): s.dispatch("PUT")
    def do_DELETE(s): s.dispatch("DELETE")


@route("GET", "/health", None)
def health(c):
    return {"ok": True}


#  contas
@route("POST", "/register", None)
def register(c):
    if recent(("reg", c.ip), 3600) >= 10: raise Err(429, "Demasiadas contas criadas a partir desta ligação. Tenta mais tarde.")
    hit(("reg", c.ip))
    b = c.body; un = (b.get("username") or "").strip(); em = (b.get("email") or "").strip().lower(); pw = b.get("password") or ""
    if not USER_RX.match(un): raise Err(400, "Nome de utilizador: 3 a 20 letras, números ou _.")
    if not EMAIL_RX.match(em): raise Err(400, "Email inválido.")
    if len(pw) < 6: raise Err(400, "A palavra-passe precisa de 6+ caracteres.")
    olds = rows("select * from users where username=? or email=?", (un, em))
    if any(o["verified"] for o in olds): raise Err(409, "Esse nome de utilizador ou email já está em uso.")
    for o in olds:
        run("delete from codes where user=?", (o["id"],)); run("delete from users where id=?", (o["id"],))
    salt = secrets.token_hex(16)
    uid = run("insert into users(username,email,name,surname,salt,hash,created) values(?,?,?,?,?,?,?)",
              (un, em, (b.get("name") or "").strip()[:40], (b.get("surname") or "").strip()[:40], salt, hpw(pw, salt), now())).lastrowid
    send_code(c, uid, em, "verify"); return {"ok": True}


@route("POST", "/resend", None)
def resend(c):
    u = user_by_login(c.body.get("login"))
    if u and not u["verified"] and not send_code(c, u["id"], u["email"], "verify"):
        raise Err(429, "Espera um minuto antes de pedires outro código.")
    return {"ok": True}


@route("POST", "/verify", None)
def verify(c):
    u = user_by_login(c.body.get("login"))
    if not u or u["verified"]: raise Err(400, "Pedido inválido.")
    check_code(u["id"], "verify", c.body.get("code"))
    run("update users set verified=1 where id=?", (u["id"],))
    return {"token": new_token(u["id"]), "me": mine(one("select * from users where id=?", (u["id"],)))}


@route("POST", "/login", None)
def login(c):
    key = (c.body.get("login") or "").strip().lower(); u = user_by_login(key)
    # o limite é por conta (nome e email contam juntos) e por IP
    lk = ("login", u["id"]) if u else ("login", key)
    if recent(lk, 900) >= 10 or recent(("loginip", c.ip), 900) >= 30:
        raise Err(429, "Demasiadas tentativas. Tenta novamente daqui a alguns minutos.")
    ok = secrets.compare_digest(hpw(c.body.get("password") or "", u["salt"] if u else DUMMY_SALT), u["hash"] if u else DUMMY_HASH)
    if not u or not ok:
        hit(lk); hit(("loginip", c.ip))
        raise Err(401, "Nome/email ou palavra-passe incorretos.")
    if u["suspended"]: raise Err(403, "Esta conta está suspensa.")
    if not u["verified"]:
        sent = send_code(c, u["id"], u["email"], "verify")
        raise Err(403, "Confirma primeiro o teu email. " + ("Enviámos-te um novo código." if sent else "Usa o último código que recebeste."),
                  unverified=True)
    return {"token": new_token(u["id"]), "me": mine(u)}


@route("POST", "/logout")
def logout(c):
    run("delete from tokens where token=?", (sha(c.tok),)); return {"ok": True}


@route("POST", "/forgot", None)
def forgot(c):
    u = one("select * from users where email=? and verified=1 and role='user'", ((c.body.get("email") or "").strip().lower(),))
    if u: send_code(c, u["id"], u["email"], "reset")
    return {"ok": True}  # resposta igual exista ou não, para não revelar emails


@route("POST", "/reset", None)
def reset(c):
    u = one("select * from users where email=? and verified=1 and role='user'", ((c.body.get("email") or "").strip().lower(),))
    if not u: raise Err(400, "Código incorreto.")
    check_code(u["id"], "reset", c.body.get("code"))
    pw = c.body.get("password") or ""
    if len(pw) < 6: raise Err(400, "A palavra-passe precisa de 6+ caracteres.")
    salt = secrets.token_hex(16)
    run("update users set salt=?, hash=? where id=?", (salt, hpw(pw, salt), u["id"])); run("delete from tokens where user=?", (u["id"],))
    return {"ok": True}


@route("GET", "/me")
def get_me(c):
    return {"me": mine(c.me)}


@route("PUT", "/me")
def put_me(c):
    b = c.body; sets, args = [], []
    for k, n in (("name", 40), ("surname", 40), ("bio", 200)):
        if k in b: sets.append(f"{k}=?"); args.append((b[k] or "").strip()[:n])
    if "avatar" in b:
        a = b["avatar"]
        sets.append("avatar=?"); args.append(check_avatar(a))
    if sets: run(f"update users set {', '.join(sets)} where id=?", args + [c.me["id"]])
    return {"me": mine(one("select * from users where id=?", (c.me["id"],)))}


@route("POST", "/me/password")
def change_pw(c):
    if not secrets.compare_digest(hpw(c.body.get("old") or "", c.me["salt"]), c.me["hash"]): raise Err(403, "A palavra-passe atual não está correta.")
    new = c.body.get("new") or ""
    if len(new) < 6: raise Err(400, "A palavra-passe precisa de 6+ caracteres.")
    salt = secrets.token_hex(16); run("update users set salt=?, hash=? where id=?", (salt, hpw(new, salt), c.me["id"]))
    # termina as sessões noutros computadores (fica só esta)
    run("delete from tokens where user=? and token!=?", (c.me["id"], sha(c.tok))); return {"ok": True}


@route("POST", "/me/streak")
def streak(c):
    try: n = max(0, min(int(c.body.get("streak", 0)), 10_000))
    except (TypeError, ValueError): n = 0
    run("update users set streak=? where id=?", (n, c.me["id"])); return {"ok": True}


@route("POST", "/me/delete")
def delete_me(c):
    u = c.me
    if u["role"] == "admin": raise Err(403, "A conta de administrador não pode ser apagada.")
    if not secrets.compare_digest(hpw(c.body.get("password") or "", u["salt"]), u["hash"]): raise Err(403, "Palavra-passe incorreta.")
    i = u["id"]
    run("delete from likes where user=? or post in (select id from posts where user=?)", (i, i))
    run("delete from comments where user=? or post in (select id from posts where user=?)", (i, i))
    run("delete from reports where reporter=? or (kind='user' and target=?)", (i, i))
    run("delete from notifs where user=? or actor=?", (i, i))
    for sql in ("delete from posts where user=?", "delete from follows where a=? or b=?", "delete from blocks where a=? or b=?",
                "delete from tokens where user=?", "delete from codes where user=?", "delete from users where id=?"):
        run(sql, (i,) * sql.count("?"))
    return {"ok": True}


#  publicações
POST_SQL = """select p.id, p.body, p.created, p.user uid, u.username, u.name, u.surname, u.avatar,
  (select count(*) from likes where post=p.id) likes,
  (select count(*) from likes where post=p.id and user=?) liked,
  (select count(*) from comments k where k.post=p.id and k.hidden=0 and k.deleted=0) ncom
  from posts p join users u on u.id=p.user
  where p.hidden=0 and p.deleted=0 and u.suspended=0
  and not exists(select 1 from blocks where (a=? and b=p.user) or (a=p.user and b=?)) """


def post_out(r):
    d = {k: r[k] for k in ("id", "body", "username", "name", "surname", "likes", "ncom")}
    d["liked"] = bool(r["liked"]); d["ago"] = int(now() - r["created"]); return d


def post_ok(c, pid):
    r = one(POST_SQL + " and p.id=?", (c.me["id"],) * 3 + (pid,))
    if not r: raise Err(404, "Publicação inexistente.")
    return r


def notify(user, kind, actor, post=None):
    if user != actor: run("insert into notifs(user,kind,actor,post,created) values(?,?,?,?,?)", (user, kind, actor, post, now()))


@route("GET", "/feed")
def feed(c):
    me = c.me["id"]; cond, extra = "", ()
    if c.arg("mode") == "friends":
        cond = (" and (p.user=? or (exists(select 1 from follows where a=? and b=p.user)"
                " and exists(select 1 from follows where a=p.user and b=?)))"); extra = (me, me, me)
    rs = rows(POST_SQL + " and p.created>? " + cond + " order by likes desc, p.created desc limit 100",
              (me, me, me) + (now() - DAY,) + extra)
    return {"posts": [post_out(r) for r in rs], "av": avs(rs)}


@route("POST", "/posts")
def new_post(c):
    body = text(c.body, "body", 1, 500, "Publicação")
    pid = run("insert into posts(user,body,created) values(?,?,?)", (c.me["id"], body, now())).lastrowid
    return {"id": pid}


@route("DELETE", "/posts/<pid>")
def del_post(c, pid):
    r = one("select * from posts where id=? and user=? and deleted=0", (pid, c.me["id"]))
    if not r: raise Err(404, "Só podes apagar as tuas publicações.")
    run("update posts set deleted=1 where id=?", (pid,)); return {"ok": True}


@route("POST", "/posts/<pid>/like")
def like(c, pid):
    p = post_ok(c, pid); me = c.me["id"]
    if one("select 1 from likes where post=? and user=?", (pid, me)): run("delete from likes where post=? and user=?", (pid, me)); liked = False
    else: run("insert into likes values(?,?)", (pid, me)); liked = True; notify(p["uid"], "like", me, int(pid))
    return {"liked": liked, "likes": one("select count(*) from likes where post=?", (pid,))[0]}


COM_SQL = """select k.id, k.body, k.created, k.user uid, u.username, u.name, u.surname, u.avatar from comments k join users u on u.id=k.user
  where k.post=? and k.hidden=0 and k.deleted=0 and u.suspended=0
  and not exists(select 1 from blocks where (a=? and b=k.user) or (a=k.user and b=?)) order by k.created"""


@route("GET", "/posts/<pid>/comments")
def comments(c, pid):
    p = post_ok(c, pid); rs = rows(COM_SQL, (pid, c.me["id"], c.me["id"]))
    return {"comments": [{**{k: r[k] for k in ("id", "body", "username", "name", "surname")}, "ago": int(now() - r["created"]),
                          "can_delete": r["uid"] == c.me["id"] or p["uid"] == c.me["id"]} for r in rs], "av": avs(rs)}


@route("POST", "/posts/<pid>/comments")
def add_comment(c, pid):
    p = post_ok(c, pid); body = text(c.body, "body", 1, 300, "Comentário")
    run("insert into comments(post,user,body,created) values(?,?,?,?)", (pid, c.me["id"], body, now()))
    notify(p["uid"], "comment", c.me["id"], int(pid)); return {"ok": True}


@route("DELETE", "/comments/<cid>")
def del_comment(c, cid):
    r = one("select k.*, p.user owner from comments k join posts p on p.id=k.post where k.id=? and k.deleted=0", (cid,))
    if not r or c.me["id"] not in (r["user"], r["owner"]): raise Err(404, "Não podes apagar este comentário.")
    run("update comments set deleted=1 where id=?", (cid,)); return {"ok": True}


# pessoas
def follow_state(me, u):
    i = bool(one("select 1 from follows where a=? and b=?", (me, u["id"])))
    h = bool(one("select 1 from follows where a=? and b=?", (u["id"], me)))
    return {**pub(u), "i_follow": i, "follows_me": h}


@route("POST", "/follow/<username>")
def follow(c, username):
    u = by_name(username); me = c.me["id"]
    if u["id"] == me: raise Err(400, "Não podes seguir-te a ti próprio.")
    if blocked(me, u["id"]) or u["suspended"]: raise Err(404, "Utilizador não encontrado.")
    if not one("select 1 from follows where a=? and b=?", (me, u["id"])):
        run("insert into follows values(?,?,?)", (me, u["id"], now()))
        if one("select 1 from follows where a=? and b=?", (u["id"], me)):
            notify(u["id"], "friend", me); notify(me, "friend", u["id"])
        else: notify(u["id"], "follow", me)
    return {"ok": True}


@route("DELETE", "/follow/<username>")
def unfollow(c, username):
    run("delete from follows where a=? and b=?", (c.me["id"], by_name(username)["id"])); return {"ok": True}


@route("GET", "/users/<username>")
def profile(c, username):
    u = by_name(username); me = c.me["id"]
    if u["id"] != me and (blocked(me, u["id"]) or u["suspended"]): raise Err(404, "Utilizador não encontrado.")
    cnt = lambda sql: one(sql, (u["id"],))[0]
    ps = rows(POST_SQL + " and p.user=? order by p.created desc limit 20", (me, me, me, u["id"]))
    d = {**follow_state(me, u), "bio": u["bio"], "streak": u["streak"], "is_me": u["id"] == me,
         "followers": cnt("select count(*) from follows where b=?"), "following": cnt("select count(*) from follows where a=?"),
         "n_posts": cnt("select count(*) from posts where user=? and hidden=0 and deleted=0"),
         "posts": [post_out(r) for r in ps], "av": {}}
    d["friends"] = d["i_follow"] and d["follows_me"]
    if u["avatar"]: d["av"][u["username"]] = u["avatar"]
    return d


def user_list(c, sql, uid):
    me = c.me["id"]
    rs = rows(sql + " and not exists(select 1 from blocks where (a=? and b=u.id) or (a=u.id and b=?)) limit 200", (uid, me, me))
    return {"users": [follow_state(me, r) for r in rs], "av": avs(rs)}


@route("GET", "/users/<username>/followers")
def followers(c, username):
    return user_list(c, "select u.* from follows f join users u on u.id=f.a where f.b=? and u.suspended=0", by_name(username)["id"])


@route("GET", "/users/<username>/following")
def following(c, username):
    return user_list(c, "select u.* from follows f join users u on u.id=f.b where f.a=? and u.suspended=0", by_name(username)["id"])


@route("GET", "/suggestions")
def suggestions(c):
    me = c.me["id"]
    rs = rows("""select u.*, (select count(*) from follows f1 join follows f2 on f1.b=f2.a where f1.a=? and f2.b=u.id) score
      from users u where u.id!=? and u.verified=1 and u.suspended=0 and u.role='user'
      and u.id not in (select b from follows where a=?)
      and not exists(select 1 from blocks where (a=? and b=u.id) or (a=u.id and b=?)) order by score desc, random() limit 8""",
              (me, me, me, me, me))
    return {"users": [follow_state(me, r) for r in rs], "av": avs(rs)}


@route("GET", "/search")
def search(c):
    q = c.arg("q").strip().lstrip("@")
    if not q: return {"users": [], "av": {}}
    k = f"%{q}%"; me = c.me["id"]
    rs = rows("""select * from users u where u.id!=? and u.verified=1 and u.suspended=0 and u.role='user'
      and (u.username like ? or u.name like ? or u.surname like ? or (u.name||' '||u.surname) like ?)
      and not exists(select 1 from blocks where (a=? and b=u.id) or (a=u.id and b=?)) order by u.username limit 30""",
              (me, k, k, k, k, me, me))
    return {"users": [follow_state(me, r) for r in rs], "av": avs(rs)}


@route("GET", "/blocks")
def my_blocks(c):
    rs = rows("select u.* from blocks b join users u on u.id=b.b where b.a=?", (c.me["id"],))
    return {"users": [pub(r) for r in rs], "av": avs(rs)}


@route("POST", "/block/<username>")
def block(c, username):
    u = by_name(username); me = c.me["id"]
    if u["id"] == me or u["role"] == "admin": raise Err(400, "Não podes bloquear esta conta.")
    run("insert or ignore into blocks values(?,?)", (me, u["id"]))
    run("delete from follows where (a=? and b=?) or (a=? and b=?)", (me, u["id"], u["id"], me)); return {"ok": True}


@route("DELETE", "/block/<username>")
def unblock(c, username):
    run("delete from blocks where a=? and b=?", (c.me["id"], by_name(username)["id"])); return {"ok": True}


#  notificações
@route("GET", "/notifs")
def notifs(c):
    rs = rows("""select n.*, u.username, u.name, u.surname, u.avatar, p.body pbody from notifs n join users u on u.id=n.actor
      left join posts p on p.id=n.post where n.user=? order by n.created desc limit 60""", (c.me["id"],))
    return {"items": [{**pub(r), "kind": r["kind"], "seen": bool(r["seen"]), "ago": int(now() - r["created"]),
                       "post": (r["pbody"] or "")[:80]} for r in rs], "av": avs(rs)}


@route("POST", "/notifs/seen")
def notifs_seen(c):
    run("update notifs set seen=1 where user=?", (c.me["id"],)); return {"ok": True}


@route("GET", "/notifs/count")
def notifs_count(c):
    return {"count": one("select count(*) from notifs where user=? and seen=0", (c.me["id"],))[0]}


#  moderação
@route("POST", "/report")
def report(c):
    kind, me = c.body.get("kind"), c.me["id"]; reason = (c.body.get("reason") or "").strip()[:200]
    if kind == "user": target = by_name(str(c.body.get("target")))["id"]
    elif kind in ("post", "comment"):
        try: target = int(c.body.get("target"))
        except (TypeError, ValueError): raise Err(400, "Alvo inválido.")
        r = one("select user from posts where id=?" if kind == "post" else "select user from comments where id=?", (target,))
        if not r: raise Err(404, "Conteúdo inexistente.")
    else: raise Err(400, "Tipo de denúncia inválido.")
    owner = target if kind == "user" else r["user"]
    if owner == me: raise Err(400, "Não podes denunciar o teu próprio conteúdo.")
    run("insert or ignore into reports values(?,?,?,?,?)", (kind, target, me, reason, now()))
    if kind != "user" and one("select count(distinct reporter) from reports where kind=? and target=?", (kind, target))[0] >= 3:
        run(f"update {'posts' if kind == 'post' else 'comments'} set hidden=1 where id=?", (target,))
    return {"ok": True}


#  administração
@route("GET", "/admin/summary", "admin")
def a_summary(c):
    n = lambda sql: one(sql)[0]
    return {"users": n("select count(*) from users where role='user' and verified=1"), "posts": n("select count(*) from posts where deleted=0"),
            "comments": n("select count(*) from comments where deleted=0"), "reports": n("select count(distinct kind||target) from reports"),
            "suspended": n("select count(*) from users where suspended=1"), "hidden": n("select count(*) from posts where hidden=1 and deleted=0")}


@route("GET", "/admin/reports", "admin")
def a_reports(c):
    out = []
    for g in rows("select kind, target, count(*) n, group_concat(reason, ' | ') reasons from reports group by kind, target order by n desc, max(created) desc"):
        d = {"kind": g["kind"], "target": g["target"], "n": g["n"], "reasons": g["reasons"] or ""}
        if g["kind"] == "user":
            u = one("select * from users where id=?", (g["target"],))
            if not u: continue
            d.update(author=u["username"], content=(u["bio"] or "(sem bio)"), suspended=bool(u["suspended"]), hidden=False)
        else:
            t = "posts" if g["kind"] == "post" else "comments"
            r = one(f"select x.body, x.hidden, x.deleted, u.username, u.suspended from {t} x join users u on u.id=x.user where x.id=?", (g["target"],))
            if not r or r["deleted"]: continue
            d.update(author=r["username"], content=r["body"], suspended=bool(r["suspended"]), hidden=bool(r["hidden"]))
        out.append(d)
    return {"reports": out}


@route("POST", "/admin/remove", "admin")
def a_remove(c):
    kind = c.body.get("kind")
    if kind not in ("post", "comment"): raise Err(400, "Tipo inválido.")
    run(f"update {'posts' if kind == 'post' else 'comments'} set deleted=1 where id=?", (c.body.get("target"),))
    run("delete from reports where kind=? and target=?", (kind, c.body.get("target"))); return {"ok": True}


@route("POST", "/admin/dismiss", "admin")
def a_dismiss(c):
    kind, t = c.body.get("kind"), c.body.get("target")
    if kind in ("post", "comment"): run(f"update {'posts' if kind == 'post' else 'comments'} set hidden=0 where id=?", (t,))
    run("delete from reports where kind=? and target=?", (kind, t)); return {"ok": True}


@route("POST", "/admin/suspend", "admin")
def a_suspend(c):
    u = by_name(c.body.get("username"))
    if u["role"] == "admin": raise Err(400, "Não podes suspender o administrador.")
    on = 1 if c.body.get("on") else 0
    run("update users set suspended=? where id=?", (on, u["id"]))
    if on: run("delete from tokens where user=?", (u["id"],))
    return {"ok": True}


@route("GET", "/admin/users", "admin")
def a_users(c):
    k = f"%{c.arg('q').strip().lstrip('@')}%"
    rs = rows("""select u.*, (select count(*) from posts where user=u.id and deleted=0) np from users u
      where u.role='user' and u.verified=1 and (u.username like ? or u.email like ? or u.name like ?) order by u.created desc limit 100""", (k, k, k))
    return {"users": [{**pub(r), "email": r["email"], "suspended": bool(r["suspended"]), "posts": r["np"]} for r in rs]}


REASONS = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 409: "Conflict",
           413: "Payload Too Large", 429: "Too Many Requests", 500: "Internal Server Error"}


def application(env, start_response):
    """Entrada WSGI (PythonAnywhere e outros alojamentos que correm Python por WSGI)."""
    path = env.get("PATH_INFO", "/") + ("?" + env["QUERY_STRING"] if env.get("QUERY_STRING") else "")
    header = lambda k: env.get("HTTP_" + k.upper().replace("-", "_"))
    code, data = process(env["REQUEST_METHOD"], path, header, env.get("CONTENT_LENGTH"), env["wsgi.input"].read,
                         client_ip(env.get("HTTP_X_FORWARDED_FOR", ""), env.get("REMOTE_ADDR", "")))
    raw = json.dumps(data).encode()
    start_response(f"{code} {REASONS.get(code, '')}", [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(raw)))])
    return [raw]


def start_background(port=None):
    """Arranca o servidor numa thread (usado pelo UniHelp.exe, que traz o servidor lá dentro)."""
    init()
    port = int(port or os.environ.get("PORT", 8765))
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)  # o servidor embutido na app nunca fica exposto à rede
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    host, port = os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", 8765))
    check_config(host); init()
    print(f"UniHelp servidor a correr em http://{host}:{port}   (Ctrl+C para parar)", flush=True)
    try: ThreadingHTTPServer((host, port), Handler).serve_forever()
    except KeyboardInterrupt: print("A parar.")