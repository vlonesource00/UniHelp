"""UniHelp v3 - assistente pessoal universitário (customtkinter + sqlite3).
Instalar:  python -m pip install customtkinter pillow
Novidades v3: retoma de onde paraste, palavra-passe sempre pedida + recuperação por código,
perfil editável com foto, metas e conquistas, bloqueio automático e cópia de segurança.
"""
import sys, os, socket, sqlite3, random, hashlib, secrets, json, time, datetime as dt, tkinter as tk
from tkinter import messagebox, filedialog
from pathlib import Path
import customtkinter as ctk
from PIL import Image, ImageDraw

# base de dados
HOME = Path.home() / ".unihelp"
_OLD = Path.home() / ".unipilot"  # dados da versão anterior (UniPilot): passam para a pasta nova
if _OLD.exists() and not HOME.exists():
    try: _OLD.rename(HOME)
    except OSError: pass
HOME.mkdir(exist_ok=True)
if (HOME / "unipilot.db").exists() and not (HOME / "unihelp.db").exists():
    try: (HOME / "unipilot.db").rename(HOME / "unihelp.db")
    except OSError: pass
AVATAR = HOME / "avatar.png"
# servidor da comunidade por defeito: ficheiro servidor_url.txt ao lado da app (ex.: https://unihelp.exemplo.com), senão o local
_APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
try: DEFAULT_SERVER = (_APP_DIR / "servidor_url.txt").read_text(encoding="utf-8-sig").strip().rstrip("/") or "http://127.0.0.1:8765"
except OSError: DEFAULT_SERVER = "http://127.0.0.1:8765"
con =sqlite3.connect(HOME / "unihelp.db"); con.row_factory = sqlite3.Row
con.executescript("""
create table if not exists user(id integer primary key, name text, salt text, hash text, rec_salt text, rec_hash text);
create table if not exists classes(id integer primary key, subject text, weekday int, start text, end text);
create table if not exists tasks(id integer primary key, title text, subject text, kind text, due text,
  importance int, est int, spent int default 0, done int default 0, done_at text, parent int);
create table if not exists habits(id integer primary key, name text);
create table if not exists habit_log(habit int, day text, primary key(habit, day));
create table if not exists sessions(id integer primary key, day text, subject text, minutes int, goal text);
create table if not exists notes(id integer primary key, title text, subject text, kind text, tags text, body text, task int);
create table if not exists cards(id integer primary key, q text, a text, tag text);
create table if not exists reminded(task int, lvl int, primary key(task, lvl));
create table if not exists settings(key text primary key, value text);
""")
# migração de bases de dados antigas (v2 -> v3)
_cols = {r[1] for r in con.execute("pragma table_info(user)")}
for _c in ("rec_salt", "rec_hash"):
    if _c not in _cols: con.execute(f"alter table user add column {_c} text")
con.commit()

def run(sql, a=()):
    cur = con.execute(sql, a); con.commit(); return cur
def rows(sql, a=()):
    return con.execute(sql, a).fetchall()
def cfg(k, d=None):
    r = rows("select value from settings where key=?", (k,)); return r[0][0] if r else d
def setcfg(k, v):
    run("insert or replace into settings values(?,?)", (k, str(v)))
def seed_habits():
    if not rows("select 1 from habits"):
        con.executemany("insert into habits(name) values(?)", [("Meta diária de estudo",), ("Rever a matéria das aulas",),
                        ("Praticar programação",), ("Rever apontamentos",)]); con.commit()
seed_habits()

# segurança
def hash_pw(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 120_000).hex()
def norm(code):
    return code.replace("-", "").replace(" ", "").upper()
def new_code():
    c = secrets.token_hex(6).upper(); return "-".join(c[i:i + 4] for i in (0, 4, 8))
def set_recovery():
    """Gera novo código de recuperação (o anterior deixa de funcionar) e devolve-o."""
    code, salt = new_code(), secrets.token_hex(16)
    run("update user set rec_salt=?, rec_hash=?", (salt, hash_pw(norm(code), salt))); return code
def check_pw(pw):
    u = rows("select * from user")[0]
    return secrets.compare_digest(hash_pw(pw, u["salt"]), u["hash"])
def set_pw(pw):
    salt = secrets.token_hex(16); run("update user set salt=?, hash=?", (salt, hash_pw(pw, salt)))
def wipe_all():
    for t in ("user", "classes", "tasks", "habits", "habit_log", "sessions", "notes", "cards", "reminded", "settings"):
        run(f"delete from {t}")
    seed_habits(); AVATAR.unlink(missing_ok=True)

#  lógica
DAYS = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
KINDS = ["Trabalho", "Projeto", "Relatório", "Exame", "Teste", "Outro"]
EXAMS = ("Exame", "Teste")
BASE_SUBJ = ["Programação", "Matemática", "Outras"]

def pdate(s):
    for f in ("%Y-%m-%d", "%d/%m/%Y"):
        try: return dt.datetime.strptime(s.strip(), f).date()
        except ValueError: pass
    raise ValueError("data inválida")
def mins(s):
    t = dt.datetime.strptime(s.strip(), "%H:%M"); return t.hour * 60 + t.minute
def hhmm(m):
    return f"{m // 60:02d}:{m % 60:02d}"
def today():
    return dt.date.today()
def subjects():
    s = set(BASE_SUBJ)
    s |= {r[0] for r in rows("select distinct subject from classes") if r[0]}
    s |= {r[0] for r in rows("select distinct subject from tasks") if r[0]}
    return sorted(s)

def leaves():
    """Tarefas por fazer sem subtarefas pendentes (é nelas que se trabalha)."""
    return rows("select * from tasks where done=0 and id not in "
                "(select parent from tasks where parent is not null and done=0)")

def priority(t):
    days = (pdate(t["due"]) - today()).days
    score = t["importance"] * 10
    if days < 0: return score + 100
    return score + 60 / (days + 1) + (20 if t["kind"] in EXAMS and days <= 7 else 0)

def prog(t):
    kids = rows("select done from tasks where parent=?", (t["id"],))
    if kids: return sum(k[0] for k in kids) / len(kids)
    return 1.0 if t["done"] else (min(0.99, t["spent"] / t["est"]) if t["est"] else 0)

def free_blocks(day, start=9 * 60, end=21 * 60):
    if day == today():
        n = dt.datetime.now(); start = max(start, (n.hour * 60 + n.minute + 14) // 15 * 15)
    busy = [(mins(c["start"]), mins(c["end"])) for c in rows("select * from classes where weekday=?", (day.weekday(),))]
    busy.append((13 * 60, 14 * 60))  # almoço
    blocks, cur = [], start
    for s, e in sorted(busy):
        if s > cur: blocks.append([cur, min(s, end)])
        cur = max(cur, e)
    if cur < end: blocks.append([cur, end])
    return [b for b in blocks if b[1] - b[0] >= 15]

def build_plan(days=7, daily_cap=240):
    """Distribui tarefas pelos blocos livres. O limite diário evita ocupar todas as horas livres;
    exames/testes ganham sessões de revisão repartidas pelos dias anteriores."""
    tasks = sorted(leaves(), key=priority, reverse=True)
    need = {t["id"]: max(15, t["est"] - t["spent"]) for t in tasks}
    plan = []
    for i in range(days):
        day = today() + dt.timedelta(i)
        blocks, used = free_blocks(day), 0
        for t in tasks:
            due = pdate(t["due"])
            if (due < day and due >= today()) or (t["kind"] in EXAMS and (due - day).days > 7): continue
            cap, got = (90 if t["kind"] in EXAMS else 120), 0
            while need[t["id"]] > 0 and got < cap and used < daily_cap and blocks:
                b = blocks[0]
                length = min(50, need[t["id"]], cap - got, daily_cap - used, b[1] - b[0])
                plan.append((day, b[0], length, t))
                b[0] += length + 10; need[t["id"]] -= length; got += length; used += length
                if b[1] - b[0] < 15: blocks.pop(0)
    return sorted(plan, key=lambda p: (p[0], p[1]))

def day_data():
    n = len(rows("select 1 from classes where weekday=?", (today().weekday(),)))
    pend = leaves(); plan = [p for p in build_plan(1) if p[0] == today()]
    urgent = [t for t in pend if (pdate(t["due"]) - today()).days <= 2 or t["importance"] >= 5]
    return n, pend, plan, urgent

def summary():
    n, pend, plan, _ = day_data(); top = sorted(pend, key=priority, reverse=True)[:3]
    txt = f"Aulas hoje: {n}\nTarefas pendentes: {len(pend)}\nEstudo planeado: {sum(p[2] for p in plan)} min\n"
    return txt + ("\nPrioridades:\n" + "\n".join(f"• {t['title']} ({t['due']})" for t in top) if top else "\nSem tarefas pendentes 🎉")

def ask_ai(prompt):
    """Ponto de integração para um modelo de IA (quizzes, explicações, decompor projetos)."""
    raise NotImplementedError("Liga aqui a API do modelo que preferires.")

#  hábitos, metas e conquistas
def habit_streak():
    d, n = today(), 0
    if not rows("select 1 from habit_log where day=?", (str(d),)): d -= dt.timedelta(1)
    while rows("select 1 from habit_log where day=?", (str(d),)): n += 1; d -= dt.timedelta(1)
    return n
def week_start(d=None):
    d = d or today(); return d - dt.timedelta(d.weekday())
def study_min(a, b):
    return rows("select coalesce(sum(minutes),0) from sessions where day between ? and ?", (str(a), str(b)))[0][0]
def tasks_done(a, b):
    return rows("select count(*) from tasks where done=1 and done_at between ? and ? and id not in "
                "(select parent from tasks where parent is not null)", (str(a), str(b)))[0][0]
def goals():
    return int(cfg("goal_day", 120)), int(cfg("goal_week", 600)), int(cfg("goal_tasks", 5))
def week_met(ws):
    _, gw, gt = goals(); e = ws + dt.timedelta(6)
    return study_min(ws, e) >= gw and tasks_done(ws, e) >= gt
def achievements():
    lo, hi = "0000-01-01", "9999-12-31"
    sess = rows("select count(*) from sessions")[0][0]; notes = rows("select count(*) from notes")[0][0]
    perfect = any(week_met(week_start() - dt.timedelta(7 * k)) for k in range(12))
    return [("🎯", "Primeiro foco", "Completa a tua primeira sessão de estudo", sess >= 1),
            ("✅", "Dez concluídas", "Conclui 10 tarefas", tasks_done(lo, hi) >= 10),
            ("📚", "10 horas", "Soma 10 horas de estudo", study_min(lo, hi) >= 600),
            ("🔥", "Semana de fogo", "7 dias seguidos de hábitos", habit_streak() >= 7),
            ("🏆", "Semana perfeita", "Cumpre as metas semanais", perfect),
            ("🗒", "Segundo cérebro", "Guarda 5 notas no CodeVault", notes >= 5)]

# retoma de sessão de foco interrompida (fecho inesperado)
def recover_focus():
    raw = cfg("focus_state")
    if not raw: return 0
    setcfg("focus_state", "")
    try:
        st = json.loads(raw); m = min(int((st["last"] - st["start"]) // 60), st["total"] // 60)
    except (ValueError, KeyError): return 0
    if m < 1: return 0
    run("insert into sessions(day,subject,minutes,goal) values(?,?,?,?)",
        (str(dt.date.fromtimestamp(st["start"])), st["subj"], m, st["goal"]))
    if st.get("task"): run("update tasks set spent=spent+? where id=?", (m, st["task"]))
    return m

#foto de perfil
def save_avatar(src):
    im = Image.open(src).convert("RGBA"); w, h = im.size; m = min(w, h)
    im = im.crop(((w - m) // 2, (h - m) // 2, (w + m) // 2, (h + m) // 2)).resize((256, 256), Image.LANCZOS)
    mask = Image.new("L", (256, 256), 0); ImageDraw.Draw(mask).ellipse((0, 0, 255, 255), fill=255)
    out = Image.new("RGBA", (256, 256), (0, 0, 0, 0)); out.paste(im, (0, 0), mask); out.save(AVATAR)

def load_avatar(size):
    if not AVATAR.exists(): return None
    try:
        im = Image.open(AVATAR).convert("RGBA"); im.load()
        return ctk.CTkImage(light_image=im, dark_image=im, size=(size, size))
    except Exception: return None

# estilo e componentes
WIN = sys.platform.startswith("win")
FONT = "Segoe UI" if WIN else "Helvetica Neue"
MONO = "Consolas" if WIN else "Menlo"
ACC, ACC_H = "#6366F1", "#4F46E5"
BG, CARD, CARD2 = ("#F3F4F8", "#0F111A"), ("#FFFFFF", "#1A1D2B"), ("#EEF0F6", "#242839")
TXT, TXT2 = ("#111827", "#F3F4F6"), ("#6B7280", "#9CA3AF")
RED, GREEN, AMBER = "#EF4444", "#22C55E", "#F59E0B"

def F(size=13, w="normal", **kw):
    return ctk.CTkFont(family=FONT, size=int(size), weight=w, **kw)
def label(p, text, size=13, w="normal", color=None, **kw):
    return ctk.CTkLabel(p, text=text, font=F(size, w), text_color=color, **kw)
def card(p, fg=CARD, **kw):
    return ctk.CTkFrame(p, corner_radius=16, fg_color=fg, **kw)
def clear(w):
    for c in w.winfo_children(): c.destroy()
def button(p, text, cmd, kind="primary", width=None):
    fg, hv, tc = {"primary": (ACC, ACC_H, "white"), "ghost": (CARD2, ("#E1E4EE", "#2F344B"), TXT),
                  "danger": (RED, "#DC2626", "white")}[kind]
    kw = {"width": width} if width else {}
    return ctk.CTkButton(p, text=text, command=cmd, fg_color=fg, hover_color=hv, text_color=tc,
                         corner_radius=10, height=36, font=F(13, "bold"), **kw)
def header(p, text, sub=""):
    h = ctk.CTkFrame(p, fg_color="transparent"); h.pack(fill="x", pady=(2, 12))
    label(h, text, 26, "bold").pack(anchor="w")
    if sub: label(h, sub, 13, color=TXT2).pack(anchor="w")
    return h
def fld(parent, name, values=None, default="", width=130, ph=""):
    f = ctk.CTkFrame(parent, fg_color="transparent"); f.pack(side="left", padx=4)
    label(f, name, 11, color=TXT2).pack(anchor="w")
    if values is not None:
        w = ctk.CTkComboBox(f, values=values or [""], width=width, height=34, corner_radius=10); w.set(default)
    else:
        w = ctk.CTkEntry(f, width=width, height=34, corner_radius=10, placeholder_text=ph)
        if default: w.insert(0, default)
    w.pack(); return w
def setv(w, v):
    if isinstance(w, ctk.CTkComboBox): w.set(v)
    else: w.delete(0, "end"); w.insert(0, v)
def item(parent, big, small, chip=None, color=ACC):
    f = ctk.CTkFrame(parent, fg_color=CARD2, corner_radius=12); f.pack(fill="x", padx=14, pady=4)
    ctk.CTkFrame(f, width=5, height=34, corner_radius=3, fg_color=color).pack(side="left", padx=(10, 8), pady=10)
    t = ctk.CTkFrame(f, fg_color="transparent"); t.pack(side="left", fill="x", expand=True)
    label(t, big, 14, "bold").pack(anchor="w"); label(t, small, 12, color=TXT2).pack(anchor="w")
    if chip: label(f, chip, 12, "bold", color).pack(side="right", padx=14)
def goal_bar(p, text, val, goal, padx=18):
    ok = val >= goal; r = ctk.CTkFrame(p, fg_color="transparent"); r.pack(fill="x", padx=padx, pady=4)
    top = ctk.CTkFrame(r, fg_color="transparent"); top.pack(fill="x")
    label(top, text, 13).pack(side="left")
    label(top, f"{val}/{goal}" + (" ✅" if ok else ""), 12, "bold", GREEN if ok else TXT2).pack(side="right")
    b = ctk.CTkProgressBar(r, height=10, progress_color=GREEN if ok else ACC)
    b.set(min(1, val / goal) if goal else 0); b.pack(fill="x", pady=(4, 0))

def form_dialog(parent, title, fields, note="", ok="Confirmar"):
    """Janela modal simples. fields = [(chave, rótulo, é_segredo)]. Devolve dict ou None."""
    w = ctk.CTkToplevel(parent); w.title(title); w.resizable(False, False)
    w.geometry(f"400x{150 + 74 * len(fields) + (50 if note else 0)}")
    out, ents = {}, {}
    if note: label(w, note, 12, color=TXT2, wraplength=350, justify="left").pack(padx=24, pady=(20, 0), anchor="w")
    for k, name, secret in fields:
        label(w, name, 12, color=TXT2).pack(anchor="w", padx=24, pady=(14, 2))
        e = ctk.CTkEntry(w, height=38, corner_radius=10, show="•" if secret else ""); e.pack(fill="x", padx=24); ents[k] = e
    def done(*_):
        out.update({k: e.get() for k, e in ents.items()}, ok=True); w.destroy()
    r = ctk.CTkFrame(w, fg_color="transparent"); r.pack(pady=22)
    button(r, ok, done).pack(side="left", padx=6); button(r, "Cancelar", w.destroy, "ghost").pack(side="left", padx=6)
    for e in ents.values(): e.bind("<Return>", done)
    def grab():
        try: w.grab_set(); next(iter(ents.values())).focus()
        except tk.TclError: pass
    w.after(150, grab); w.wait_window()
    return out if out.get("ok") else None

def show_code(root, code, reason):
    root.clipboard_clear(); root.clipboard_append(code)
    messagebox.showinfo("Código de recuperação", f"{reason}\n\n{code}\n\nGuarda-o num sítio seguro (já foi copiado para a "
                        "área de transferência). Serve para repor a palavra-passe e só pode ser usado uma vez.")

class Avatar(ctk.CTkFrame):
    """Foto de perfil redonda; sem foto mostra a inicial do nome."""
    def __init__(s, parent, size=40, name=""):
        super().__init__(parent, fg_color="transparent", width=size, height=size)
        s.size, s.name = size, name; s.reload()
    def reload(s, name=None):
        if name is not None: s.name = name
        clear(s); s.img = load_avatar(s.size)
        if s.img: lbl = ctk.CTkLabel(s, text="", image=s.img, width=s.size, height=s.size)
        else: lbl = ctk.CTkLabel(s, text=(s.name[:1] or "?").upper(), width=s.size, height=s.size, corner_radius=s.size // 2,
                                 fg_color=ACC, text_color="white", font=F(s.size * 0.4, "bold"))
        lbl.pack()

class Page(ctk.CTkFrame):
    def __init__(s, shell):
        super().__init__(shell.body, fg_color="transparent"); s.shell = shell
    def refresh(s): pass

#  Painel
class Dashboard(Page):
    def __init__(s, shell):
        super().__init__(shell)
        s.sc = ctk.CTkScrollableFrame(s, fg_color="transparent"); s.sc.pack(fill="both", expand=True)

    def refresh(s):
        sc = s.sc; clear(sc); h = dt.datetime.now().hour; user = s.shell.app.user
        top = ctk.CTkFrame(sc, fg_color="transparent"); top.pack(fill="x", padx=8, pady=(0, 14))
        Avatar(top, 56, user).pack(side="left", padx=(0, 14))
        col = ctk.CTkFrame(top, fg_color="transparent"); col.pack(side="left")
        label(col, f"{'Bom dia' if h < 12 else 'Boa tarde' if h < 20 else 'Boa noite'}, {user}! 👋", 28, "bold").pack(anchor="w")
        label(col, f"Aqui está o teu dia · {DAYS[today().weekday()]}, {today():%d/%m/%Y}", 14, color=TXT2).pack(anchor="w")
        n, pend, plan, urgent = day_data()
        row = ctk.CTkFrame(sc, fg_color="transparent"); row.pack(fill="x")
        for i, (name, val, col) in enumerate([("Aulas hoje", n, ACC), ("Tarefas pendentes", len(pend), AMBER),
                ("Estudo planeado", f"{sum(p[2] for p in plan) / 60:.1f} h", GREEN), ("Prioridade máxima", len(urgent), RED)]):
            row.columnconfigure(i, weight=1, uniform="s")
            c = card(row); c.grid(row=0, column=i, padx=6, sticky="ew")
            label(c, str(val), 34, "bold", col).pack(anchor="w", padx=18, pady=(14, 0))
            label(c, name, 13, color=TXT2).pack(anchor="w", padx=18, pady=(0, 14))
        cols = ctk.CTkFrame(sc, fg_color="transparent"); cols.pack(fill="both", expand=True, pady=10)
        cols.columnconfigure(0, weight=3, uniform="c"); cols.columnconfigure(1, weight=2, uniform="c")
        L, R = card(cols), card(cols); L.grid(row=0, column=0, padx=6, sticky="nsew"); R.grid(row=0, column=1, padx=6, sticky="nsew")
        label(L, "A tua lista de prioridades", 17, "bold").pack(anchor="w", padx=18, pady=(16, 8))
        if plan:
            for d, st, ln, t in plan[:6]: item(L, t["title"], t["subject"] or "—", f"{hhmm(st)} · {ln} min")
        else:
            for t in sorted(pend, key=priority, reverse=True)[:5]: item(L, t["title"], t["subject"] or "—", f"prazo {t['due']}")
        if not pend: label(L, "Sem tarefas por fazer. Aproveita o dia 🎉", 13, color=TXT2).pack(padx=18, pady=10)
        ctk.CTkFrame(L, height=8, fg_color="transparent").pack()
        label(R, "Avisos de prazos", 17, "bold").pack(anchor="w", padx=18, pady=(16, 8))
        warned = False
        for t in rows("select * from tasks where parent is null and done=0 order by due"):
            d = (pdate(t["due"]) - today()).days
            if d <= 7:
                warned = True
                item(R, t["title"], "ATRASADO" if d < 0 else f"faltam {d} dia(s)", None, RED if d <= 1 else AMBER)
        if not warned: label(R, "Nenhum prazo nos próximos 7 dias ✅", 13, color=TXT2).pack(padx=18, pady=6)
        gd, gw, gt = goals(); ws = week_start(); we = ws + dt.timedelta(6)
        label(R, "Metas da semana", 17, "bold").pack(anchor="w", padx=18, pady=(16, 4))
        goal_bar(R, "Estudo (min)", study_min(ws, we), gw); goal_bar(R, "Tarefas concluídas", tasks_done(ws, we), gt)
        label(R, "Concluídas (7 dias)", 17, "bold").pack(anchor="w", padx=18, pady=(16, 6))
        data = [(today() - dt.timedelta(i), rows("select count(*) from tasks where done=1 and done_at=?", (str(today() - dt.timedelta(i)),))[0][0]) for i in range(6, -1, -1)]
        mx = max([c for _, c in data] + [1])
        for d, c in data:
            r = ctk.CTkFrame(R, fg_color="transparent"); r.pack(fill="x", padx=18, pady=2)
            label(r, DAYS[d.weekday()][:3], 12, color=TXT2, width=34).pack(side="left")
            b = ctk.CTkProgressBar(r, height=10, progress_color=ACC); b.set(c / mx); b.pack(side="left", fill="x", expand=True, padx=8)
            label(r, str(c), 12, "bold", width=20).pack(side="left")
        ctk.CTkFrame(R, height=10, fg_color="transparent").pack()

#  Horário
class Schedule(Page):
    def __init__(s, shell):
        super().__init__(shell); header(s, "Horário", "As tuas aulas da semana")
        f = card(s); f.pack(fill="x", pady=(0, 12)); r = ctk.CTkFrame(f, fg_color="transparent"); r.pack(padx=14, pady=12, anchor="w")
        s.subj = fld(r, "Disciplina", width=210, ph="ex.: Algoritmos"); s.day = fld(r, "Dia", DAYS, "Segunda", 120)
        s.a = fld(r, "Início", default="09:00", width=80); s.b = fld(r, "Fim", default="10:30", width=80)
        button(r, "＋ Adicionar", s.add).pack(side="left", padx=10, pady=(18, 0))
        s.week = ctk.CTkFrame(s, fg_color="transparent"); s.week.pack(fill="both", expand=True)

    def add(s):
        try: mins(s.a.get()); mins(s.b.get())
        except ValueError: return messagebox.showwarning("Hora inválida", "Usa o formato HH:MM.")
        if not s.subj.get().strip(): return messagebox.showwarning("Falta a disciplina", "Escreve o nome da disciplina.")
        run("insert into classes(subject,weekday,start,end) values(?,?,?,?)", (s.subj.get().strip(), DAYS.index(s.day.get()), s.a.get(), s.b.get()))
        s.refresh()

    def remove(s, i):
        run("delete from classes where id=?", (i,)); s.refresh()

    def refresh(s):
        clear(s.week)
        for i in range(7):
            s.week.columnconfigure(i, weight=1, uniform="d")
            col = card(s.week); col.grid(row=0, column=i, padx=4, sticky="nsew")
            label(col, DAYS[i], 14, "bold", ACC if i == today().weekday() else None).pack(pady=(12, 6))
            for c in rows("select * from classes where weekday=? order by start", (i,)):
                f = ctk.CTkFrame(col, fg_color=CARD2, corner_radius=10); f.pack(fill="x", padx=8, pady=4)
                ctk.CTkButton(f, text="✕", width=24, height=24, fg_color="transparent", hover_color=RED, text_color=TXT2,
                              command=lambda i=c["id"]: s.remove(i)).pack(anchor="e", padx=2, pady=(2, 0))
                label(f, f"{c['start']}–{c['end']}", 12, "bold", ACC).pack(anchor="w", padx=10)
                label(f, c["subject"], 12, wraplength=110, justify="left").pack(anchor="w", padx=10, pady=(0, 10))

# Tarefas / Deadline Guardian
class Tasks(Page):
    def __init__(s, shell):
        super().__init__(shell); header(s, "Tarefas e prazos", "Trabalhos, projetos e exames num só sítio")
        f = card(s); f.pack(fill="x", pady=(0, 10)); r = ctk.CTkFrame(f, fg_color="transparent"); r.pack(padx=14, pady=12, anchor="w")
        s.ttl = fld(r, "Título", width=220, ph="ex.: Relatório de Redes"); s.subj = fld(r, "Disciplina", subjects(), "", 140)
        s.kind = fld(r, "Tipo", KINDS, "Trabalho", 110); s.due = fld(r, "Prazo", default=str(today() + dt.timedelta(7)), width=100)
        s.imp = fld(r, "Import. 1-5", ["1", "2", "3", "4", "5"], "3", 70); s.est = fld(r, "Estimativa (min)", default="60", width=90)
        button(r, "＋ Adicionar", s.add).pack(side="left", padx=8, pady=(18, 0))
        button(r, "Reorganizar atrasadas", s.reorganize, "ghost").pack(side="left", padx=4, pady=(18, 0))
        s.lst = ctk.CTkScrollableFrame(s, fg_color="transparent"); s.lst.pack(fill="both", expand=True)

    def add(s):
        try:
            d = pdate(s.due.get()); imp = int(s.imp.get()); est = int(s.est.get())
            if not s.ttl.get().strip() or not 1 <= imp <= 5: raise ValueError
        except ValueError: return messagebox.showwarning("Dados inválidos", "Confirma título, prazo, importância (1-5) e estimativa.")
        run("insert into tasks(title,subject,kind,due,importance,est) values(?,?,?,?,?,?)",
            (s.ttl.get().strip(), s.subj.get(), s.kind.get(), str(d), imp, est))
        s.ttl.delete(0, "end"); s.refresh()

    def finish(s, i):
        run("update tasks set done=1, done_at=? where id=?", (str(today()), i))
        p = rows("select parent from tasks where id=?", (i,))[0][0]
        if p and not rows("select 1 from tasks where parent=? and done=0", (p,)):
            run("update tasks set done=1, done_at=? where id=?", (str(today()), p))
        s.refresh()

    def divide(s, i):
        txt = ctk.CTkInputDialog(text="Subtarefas separadas por ';'\nex.: Pesquisa; Código; Relatório; Revisão", title="Dividir tarefa").get_input()
        parts = [p.strip() for p in (txt or "").split(";") if p.strip()]
        if not parts: return
        t = rows("select * from tasks where id=?", (i,))[0]
        for p in parts:
            run("insert into tasks(title,subject,kind,due,importance,est,parent) values(?,?,?,?,?,?,?)",
                (p, t["subject"], t["kind"], t["due"], t["importance"], max(15, t["est"] // len(parts)), i))
        s.refresh()

    def reorganize(s):
        """Atrasadas: re-datadas por importância nos próximos dias, sem mexer no resto do plano."""
        late = sorted([t for t in leaves() if pdate(t["due"]) < today()], key=lambda t: -t["importance"])
        if not late: return messagebox.showinfo("Tudo em dia", "Não há tarefas atrasadas.")
        for k, t in enumerate(late):
            run("update tasks set due=? where id=?", (str(today() + dt.timedelta(1 + k // 2)), t["id"]))
            if t["parent"]: run("update tasks set due=(select max(due) from tasks where parent=?) where id=?", (t["parent"], t["parent"]))
        messagebox.showinfo("Reorganizado", f"{len(late)} tarefa(s) atrasada(s) redistribuída(s) pelos próximos dias.")
        s.refresh()

    def remove(s, i):
        run("delete from tasks where id=? or parent=?", (i, i)); s.refresh()

    def row(s, t, sub=False):
        late = not t["done"] and pdate(t["due"]) < today(); p = prog(t)
        f = card(s.lst, CARD2 if sub else CARD); f.pack(fill="x", padx=(40 if sub else 4, 4), pady=4)
        for txt, cmd, kind, w in [("✕", lambda: s.remove(t["id"]), "ghost", 38)] + \
                ([("✔", lambda: s.finish(t["id"]), "primary", 38)] if not t["done"] else []) + \
                ([("Dividir", lambda: s.divide(t["id"]), "ghost", 70)] if not sub and not t["done"] and not rows("select 1 from tasks where parent=?", (t["id"],)) else []):
            button(f, txt, cmd, kind, w).pack(side="right", padx=(0, 8), pady=10)
        label(f, f"{int(p * 100)}%", 13, "bold", GREEN if t["done"] else ACC, width=44).pack(side="right", padx=6)
        left = ctk.CTkFrame(f, fg_color="transparent"); left.pack(side="left", fill="x", expand=True, padx=14, pady=10)
        ctk.CTkLabel(left, text=t["title"], text_color=TXT2 if t["done"] else TXT,
                     font=ctk.CTkFont(family=FONT, size=14, weight="bold", overstrike=bool(t["done"]))).pack(anchor="w")
        label(left, f"{t['subject'] or '—'} · {t['kind']} · prazo {t['due']}" + ("  ·  ATRASADO" if late else ""), 12, color=RED if late else TXT2).pack(anchor="w")
        b = ctk.CTkProgressBar(left, height=8, progress_color=GREEN if t["done"] else ACC); b.set(p); b.pack(fill="x", pady=(6, 0))

    def refresh(s):
        clear(s.lst); s.subj.configure(values=subjects())
        for t in rows("select * from tasks where parent is null order by done, due"):
            s.row(t)
            for c in rows("select * from tasks where parent=? order by id", (t["id"],)): s.row(c, True)
        if not rows("select 1 from tasks"): label(s.lst, "Ainda não tens tarefas. Adiciona a primeira acima ✨", 14, color=TXT2).pack(pady=40)

# Foco (Pomodoro)
class Focus(Page):
    def __init__(s, shell):
        super().__init__(shell); s.job = None; s.running = False; s.mode = "work"; s.task = None; s.state = {}
        header(s, "Foco", "Começa a estudar sem perder tempo a decidir por onde começar")
        body = ctk.CTkFrame(s, fg_color="transparent"); body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=3, uniform="f"); body.columnconfigure(1, weight=2, uniform="f"); body.rowconfigure(0, weight=1)
        L, R = card(body), card(body); L.grid(row=0, column=0, sticky="nsew", padx=6); R.grid(row=0, column=1, sticky="nsew", padx=6)
        s.status = label(L, "Pronto para começar", 15, color=TXT2); s.status.pack(pady=(28, 0))
        s.clock = label(L, "25:00", 88, "bold"); s.clock.pack(pady=4)
        s.bar = ctk.CTkProgressBar(L, height=10, progress_color=ACC); s.bar.set(0); s.bar.pack(fill="x", padx=60, pady=8)
        r = ctk.CTkFrame(L, fg_color="transparent"); r.pack(pady=10)
        # os valores escolhidos ficam guardados para a próxima vez
        s.dur = fld(r, "Sessão (min)", ["25", "45", "50"], cfg("f_dur", "25"), 100)
        s.brk = fld(r, "Pausa (min)", ["5", "10", "15"], cfg("f_brk", "5"), 100)
        s.subj = fld(r, "Disciplina", BASE_SUBJ, cfg("f_subj", "Programação"), 170)
        s.goal = ctk.CTkEntry(L, placeholder_text="Objetivo desta sessão (ex.: resolver 5 exercícios)", height=40, corner_radius=10)
        s.goal.pack(fill="x", padx=60, pady=8)
        bb = ctk.CTkFrame(L, fg_color="transparent"); bb.pack(pady=10)
        ctk.CTkButton(bb, text="▶  Começar a estudar", height=50, width=240, corner_radius=14, fg_color=ACC, hover_color=ACC_H,
                      font=F(16, "bold"), command=s.study_now).pack(side="left", padx=6)
        button(bb, "Iniciar manual", lambda: s.start(None), "ghost").pack(side="left", padx=4)
        button(bb, "■ Parar", s.stop, "ghost").pack(side="left", padx=4)
        label(R, "Hoje", 17, "bold").pack(anchor="w", padx=18, pady=(16, 6)); s.sbox = ctk.CTkFrame(R, fg_color="transparent"); s.sbox.pack(fill="x", padx=18)

    def show(s):
        s.clock.configure(text=f"{s.left // 60:02d}:{s.left % 60:02d}"); s.bar.set(1 - s.left / s.total if s.total else 0)

    def beat(s):
        """Batimento: permite recuperar o tempo estudado se a aplicação fechar de repente."""
        if s.state: s.state["last"] = time.time(); setcfg("focus_state", json.dumps(s.state))

    def start(s, task):
        if s.running: return
        try: s.m, s.bm = int(s.dur.get()), int(s.brk.get())
        except ValueError: return messagebox.showwarning("Valores inválidos", "Duração e pausa têm de ser números.")
        setcfg("f_dur", s.m); setcfg("f_brk", s.bm); setcfg("f_subj", s.subj.get())
        s.task, s.mode, s.total, s.running = task, "work", s.m * 60, True
        s.left = s.total; s.status.configure(text="A estudar… 🎯"); s.show()
        now = time.time(); s.state = {"start": now, "last": now, "total": s.total, "task": task, "subj": s.subj.get(), "goal": s.goal.get()}
        s.beat(); s.job = s.after(1000, s.tick)

    def study_now(s):
        ts = sorted(leaves(), key=priority, reverse=True)
        if not ts: return messagebox.showinfo("Sem tarefas", "Adiciona primeiro uma tarefa no separador Tarefas.")
        t = ts[0]; setv(s.subj, t["subject"] or "Outras"); setv(s.goal, t["title"]); s.start(t["id"])

    def tick(s):
        s.left -= 1; s.show()
        if s.mode == "work" and s.left % 20 == 0: s.beat()
        if s.left <= 0: s.finish()
        else: s.job = s.after(1000, s.tick)

    def log(s, secs):
        m = secs // 60
        if m < 1: return
        run("insert into sessions(day,subject,minutes,goal) values(?,?,?,?)", (str(today()), s.subj.get(), m, s.goal.get()))
        if s.task: run("update tasks set spent=spent+? where id=?", (m, s.task))

    def finish(s):
        s.winfo_toplevel().bell()
        if s.mode == "work":
            s.log(s.total); s.state = {}; setcfg("focus_state", "")
            s.mode, s.total, s.left = "break", s.bm * 60, s.bm * 60
            s.status.configure(text="Pausa ☕"); s.show(); s.refresh()
            messagebox.showinfo("Sessão concluída", f"Boa! Faz uma pausa de {s.bm} minutos.")
            s.job = s.after(1000, s.tick)
        else:
            s.running, s.mode = False, "work"; s.status.configure(text="Pausa terminada"); s.total = s.left = s.m * 60; s.show()
            if messagebox.askyesno("Próxima sessão", "A pausa acabou. Começar a próxima sessão?"): s.start(s.task)

    def stop(s):
        if s.job: s.after_cancel(s.job); s.job = None
        if s.running and s.mode == "work": s.log(s.total - s.left)
        s.state = {}; setcfg("focus_state", "")
        s.running = False; s.status.configure(text="Parado"); s.refresh()

    def refresh(s):
        s.subj.configure(values=subjects()); clear(s.sbox)
        r = rows("select subject, sum(minutes) m from sessions where day=? group by subject", (str(today()),))
        mx = max([x["m"] for x in r] + [1])
        for x in r:
            label(s.sbox, f"{x['subject']} · {x['m']} min", 13).pack(anchor="w", pady=(6, 0))
            b = ctk.CTkProgressBar(s.sbox, height=10, progress_color=GREEN); b.set(x["m"] / mx); b.pack(fill="x")
        if not r: label(s.sbox, "Ainda nada — bora começar!", 13, color=TXT2).pack(anchor="w")
        else: label(s.sbox, f"Total: {sum(x['m'] for x in r)} min", 14, "bold", ACC).pack(anchor="w", pady=(12, 0))
        gd = goals()[0]; label(s.sbox, f"Meta diária: {gd} min", 12, color=TXT2).pack(anchor="w", pady=(10, 0))
        goal_bar(s.sbox, "Progresso do dia", study_min(today(), today()), gd, padx=0)
        if not s.running:
            try: s.total = s.left = int(s.dur.get()) * 60; s.show()
            except ValueError: pass

#  Hábitos / Momentum + relatório semanal
class Habits(Page):
    def __init__(s, shell):
        super().__init__(shell); header(s, "Momentum", "Rotina consistente, sem depender da motivação")
        b = card(s, ("#E0E7FF", "#252A4A")); b.pack(fill="x", pady=(0, 12))
        s.msg = label(b, "", 14, wraplength=820, justify="left"); s.msg.pack(anchor="w", padx=18, pady=14)
        cols = ctk.CTkFrame(s, fg_color="transparent"); cols.pack(fill="both", expand=True)
        cols.columnconfigure(0, weight=1, uniform="h"); cols.columnconfigure(1, weight=1, uniform="h"); cols.rowconfigure(0, weight=1)
        L, R = card(cols), card(cols); L.grid(row=0, column=0, sticky="nsew", padx=6); R.grid(row=0, column=1, sticky="nsew", padx=6)
        label(L, "Hábitos de hoje", 17, "bold").pack(anchor="w", padx=18, pady=(16, 8))
        s.box = ctk.CTkFrame(L, fg_color="transparent"); s.box.pack(fill="x", padx=18)
        add = ctk.CTkFrame(L, fg_color="transparent"); add.pack(fill="x", padx=18, pady=14)
        s.new = ctk.CTkEntry(add, placeholder_text="Novo hábito…", height=36, corner_radius=10); s.new.pack(side="left", fill="x", expand=True)
        button(add, "＋", s.add_habit, width=40).pack(side="left", padx=6)
        label(R, "Resumo semanal", 17, "bold").pack(anchor="w", padx=18, pady=(16, 8))
        s.rep = ctk.CTkTextbox(R, font=(FONT, 13), fg_color=CARD2, corner_radius=12); s.rep.pack(fill="both", expand=True, padx=14, pady=(0, 14))

    def add_habit(s):
        if s.new.get().strip(): run("insert into habits(name) values(?)", (s.new.get().strip(),)); s.new.delete(0, "end"); s.refresh()

    def del_habit(s, i):
        run("delete from habits where id=?", (i,)); run("delete from habit_log where habit=?", (i,)); s.refresh()

    def toggle(s, h, v):
        if v.get(): run("insert or ignore into habit_log values(?,?)", (h, str(today())))
        else: run("delete from habit_log where habit=? and day=?", (h, str(today())))
        s.refresh()

    def streak(s):
        return habit_streak()

    def refresh(s):
        clear(s.box); undone = []
        for h in rows("select * from habits"):
            done = bool(rows("select 1 from habit_log where habit=? and day=?", (h["id"], str(today()))))
            v = tk.BooleanVar(value=done); r = ctk.CTkFrame(s.box, fg_color="transparent"); r.pack(fill="x", pady=4)
            ctk.CTkButton(r, text="✕", width=24, height=24, fg_color="transparent", hover_color=RED, text_color=TXT2,
                          command=lambda i=h["id"]: s.del_habit(i)).pack(side="right")
            for k in range(6, -1, -1):
                d = str(today() - dt.timedelta(k)); ok = rows("select 1 from habit_log where habit=? and day=?", (h["id"], d))
                ctk.CTkFrame(r, width=14, height=14, corner_radius=4, fg_color=ACC if ok else CARD2).pack(side="right", padx=1)
            ctk.CTkCheckBox(r, text=h["name"], variable=v, font=F(14), fg_color=ACC, hover_color=ACC_H,
                            command=lambda i=h["id"], v=v: s.toggle(i, v)).pack(side="left")
            if not done: undone.append(h["name"])
        y = str(today() - dt.timedelta(1))
        if not rows("select 1 from habit_log where day=?", (y,)) and undone:
            s.msg.configure(text=f"Ontem ficou em branco — acontece a todos. Hoje não precisas de compensar: faz só 10 minutos de «{undone[0]}» e a rotina retoma.")
        else:
            s.msg.configure(text=f"🔥 Sequência atual: {s.streak()} dia(s). Um hábito pequeno por dia vale mais do que um grande por semana.")
        s.rep.configure(state="normal"); s.rep.delete("1.0", "end"); s.rep.insert("end", s.report()); s.rep.configure(state="disabled")

    def report(s):
        ini = str(today() - dt.timedelta(6)); out = [f"Semana de {ini} a {today()}", "", "Hábitos"]
        for h in rows("select * from habits"):
            out.append(f"  {h['name']}: {rows('select count(*) from habit_log where habit=? and day>=?', (h['id'], ini))[0][0]}/7 dias")
        out += ["", "Tempo de estudo"]
        r = rows("select subject, sum(minutes) m from sessions where day>=? group by subject", (ini,))
        out += [f"  {x['subject']}: {x['m']} min" for x in r] or ["  sem sessões registadas"]
        out += ["", f"Tarefas concluídas: {rows('select count(*) from tasks where done=1 and done_at>=?', (ini,))[0][0]}", "", "Sugestões para os próximos dias"]
        seen = []
        for d, st, ln, t in build_plan(3):
            if t["title"] not in seen: seen.append(t["title"]); out.append(f"  • {t['title']} ({d:%d/%m}, {ln} min)")
        return "\n".join(out)

#CodeVault
class Vault(Page):
    def __init__(s, shell):
        super().__init__(shell); s.cur = None; s.ids = []; s.restored = False; s.base = None
        header(s, "CodeVault", "O teu segundo cérebro: notas, código, fórmulas e flashcards")
        body = ctk.CTkFrame(s, fg_color="transparent"); body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=2, uniform="v"); body.columnconfigure(1, weight=5, uniform="v"); body.rowconfigure(0, weight=1)
        L, R = card(body), card(body); L.grid(row=0, column=0, sticky="nsew", padx=6); R.grid(row=0, column=1, sticky="nsew", padx=6)
        s.q = ctk.CTkEntry(L, placeholder_text="🔍 Pesquisar palavra, etiqueta, disciplina…", height=36, corner_radius=10)
        s.q.pack(fill="x", padx=12, pady=(14, 6)); s.q.bind("<KeyRelease>", lambda e: s.refresh_list())
        button(L, "＋ Nova nota", s.new).pack(fill="x", padx=12, pady=(0, 6))
        s.lst = ctk.CTkScrollableFrame(L, fg_color="transparent"); s.lst.pack(fill="both", expand=True, padx=4, pady=(0, 8))
        r = ctk.CTkFrame(R, fg_color="transparent"); r.pack(fill="x", padx=12, pady=(12, 4))
        s.ttl = fld(r, "Título", width=170); s.subj = fld(r, "Disciplina", subjects(), "", 120)
        s.kind = fld(r, "Tipo", ["Nota", "Código", "Fórmula", "Conceito"], "Nota", 100)
        s.tags = fld(r, "Etiquetas", width=150, ph="Python, redes…"); s.proj = fld(r, "Projeto ligado", [""], "", 140)
        s.text = ctk.CTkTextbox(R, font=(MONO, 13), fg_color=CARD2, corner_radius=12); s.text.pack(fill="both", expand=True, padx=14, pady=8)
        bb = ctk.CTkFrame(R, fg_color="transparent"); bb.pack(pady=(0, 12))
        for txt, cmd, k in [("Guardar", s.save, "primary"), ("Apagar", s.delete, "ghost"), ("Criar flashcard", s.card, "ghost"), ("Rever flashcards", s.review, "ghost")]:
            button(bb, txt, cmd, k).pack(side="left", padx=4)
        s.base = s.snap()

    # verificação de alterações por guardar
    def snap(s):
        return (s.ttl.get().strip(), s.subj.get(), s.kind.get(), s.tags.get().strip(), s.proj.get(), s.text.get("1.0", "end").strip())

    def dirty(s):
        return s.snap() != s.base and bool(s.ttl.get().strip() or s.text.get("1.0", "end").strip())

    def can_leave(s):
        """Pergunta se queres guardar alterações pendentes. False = cancelar a ação."""
        if not s.dirty(): return True
        a = messagebox.askyesnocancel("Alterações por guardar", "Tens alterações por guardar na nota. Queres guardá-las?")
        if a is None: return False
        if a: return s.save()
        s._load(s.cur) if s.cur else s._blank(); return True

    def autosave(s):
        return (not s.dirty()) or (bool(s.ttl.get().strip()) and s.save())

    def _blank(s):
        s.cur = None; setcfg("vault_note", "")
        for w in (s.ttl, s.tags): w.delete(0, "end")
        setv(s.proj, ""); s.text.delete("1.0", "end"); s.base = s.snap(); s.refresh_list()

    def new(s):
        if s.can_leave(): s._blank()

    def save(s):
        if not s.ttl.get().strip():
            messagebox.showwarning("Falta o título", "Dá um título à nota."); return False
        p = s.proj.get().split(" · ")[0]; task = int(p) if p.isdigit() else None
        a = (s.ttl.get().strip(), s.subj.get(), s.kind.get(), s.tags.get(), s.text.get("1.0", "end").strip(), task)
        if s.cur: run("update notes set title=?,subject=?,kind=?,tags=?,body=?,task=? where id=?", a + (s.cur,))
        else: s.cur = run("insert into notes(title,subject,kind,tags,body,task) values(?,?,?,?,?,?)", a).lastrowid
        setcfg("vault_note", s.cur); s.base = s.snap(); s.refresh_list(); return True

    def _load(s, i):
        n = rows("select * from notes where id=?", (i,))[0]; s.cur = i; setcfg("vault_note", i)
        setv(s.ttl, n["title"]); setv(s.subj, n["subject"] or ""); setv(s.kind, n["kind"]); setv(s.tags, n["tags"] or "")
        p = rows("select id,title from tasks where id=?", (n["task"],)) if n["task"] else []
        setv(s.proj, f"{p[0]['id']} · {p[0]['title']}" if p else "")
        s.text.delete("1.0", "end"); s.text.insert("end", n["body"] or ""); s.base = s.snap(); s.refresh_list()

    def load(s, i):
        if i != s.cur and not s.can_leave(): return
        s._load(i)

    def delete(s):
        if s.cur: run("delete from notes where id=?", (s.cur,)); s._blank()

    def card(s):
        body = s.text.get("1.0", "end").strip()
        if s.cur and body:
            run("insert into cards(q,a,tag) values(?,?,?)", (s.ttl.get(), body, s.tags.get()))
            messagebox.showinfo("Flashcard criado", "Pergunta = título, resposta = conteúdo da nota.")

    def review(s):
        cards = rows("select * from cards")
        if not cards: return messagebox.showinfo("Sem flashcards", "Cria flashcards a partir das notas.")
        w = ctk.CTkToplevel(s); w.title("Flashcards"); w.geometry("580x380"); w.after(200, w.focus)
        q = label(w, "", 20, "bold", wraplength=520); q.pack(pady=(30, 10))
        a = label(w, "", 14, color=TXT2, wraplength=520, justify="left"); a.pack(pady=10, padx=20)
        cur = {}
        def nxt(): cur["c"] = random.choice(cards); q.configure(text=cur["c"]["q"]); a.configure(text="")
        button(w, "Mostrar resposta", lambda: a.configure(text=cur["c"]["a"])).pack(pady=6)
        button(w, "Próximo", nxt, "ghost").pack(); nxt()

    def refresh_list(s):
        k = f"%{s.q.get()}%"
        r = rows("select * from notes where title like ? or body like ? or tags like ? or subject like ? order by title", (k, k, k, k))
        clear(s.lst)
        for n in r:
            ctk.CTkButton(s.lst, text=f"[{n['kind']}] {n['title']}", anchor="w", height=34, corner_radius=8, font=F(13),
                          fg_color=ACC if n["id"] == s.cur else "transparent", text_color="white" if n["id"] == s.cur else TXT,
                          hover_color=CARD2, command=lambda i=n["id"]: s.load(i)).pack(fill="x", pady=1)

    def refresh(s):
        s.subj.configure(values=subjects())
        s.proj.configure(values=[""] + [f"{t['id']} · {t['title']}" for t in rows("select id,title from tasks where parent is null")])
        s.refresh_list()
        if not s.restored:  # retoma a última nota aberta
            s.restored = True; n = cfg("vault_note", "")
            if n.isdigit() and rows("select 1 from notes where id=?", (int(n),)): s._load(int(n))

#  Planeador
class Planner(Page):
    def __init__(s, shell):
        super().__init__(shell)
        h = header(s, "Planeador inteligente", "Blocos livres da semana, com prioridade a prazos e exames próximos")
        button(h, "Não consegui cumprir — reorganizar plano", s.refresh, "ghost").pack(anchor="w", pady=(8, 0))
        s.lst = ctk.CTkScrollableFrame(s, fg_color="transparent"); s.lst.pack(fill="both", expand=True)

    def refresh(s):
        clear(s.lst); plan = build_plan(7); days = {}
        for d, st, ln, t in plan: days.setdefault(d, []).append((st, ln, t))
        for d, items in days.items():
            label(s.lst, f"{DAYS[d.weekday()]} · {d:%d/%m}" + ("  (hoje)" if d == today() else ""), 15, "bold", ACC if d == today() else None).pack(anchor="w", padx=8, pady=(10, 4))
            c = card(s.lst); c.pack(fill="x", padx=4, pady=(0, 4))
            for st, ln, t in items: item(c, t["title"], t["subject"] or "—", f"{hhmm(st)} · {ln} min", AMBER if t["kind"] in EXAMS else ACC)
            ctk.CTkFrame(c, height=6, fg_color="transparent").pack()
        if not plan: label(s.lst, "Sem nada para planear. Adiciona tarefas e aulas primeiro ✨", 14, color=TXT2).pack(pady=40)

# Perfil, metas e conquistas
class Profile(Page):
    def __init__(s, shell):
        super().__init__(shell); header(s, "Perfil", "A tua conta, metas cumpridas e conquistas")
        s.sc = ctk.CTkScrollableFrame(s, fg_color="transparent"); s.sc.pack(fill="both", expand=True)

    def refresh(s):
        sc = s.sc; clear(sc); user = rows("select name from user")[0][0]
        course, uni, bio = cfg("prof_course", ""), cfg("prof_uni", ""), cfg("prof_bio", "")
        gd, gw, gt = goals(); ws = week_start(); we = ws + dt.timedelta(6)

        #  cabeçalho com foto
        c = card(sc); c.pack(fill="x", padx=4, pady=(0, 10))
        r = ctk.CTkFrame(c, fg_color="transparent"); r.pack(fill="x", padx=20, pady=18)
        Avatar(r, 96, user).pack(side="left")
        info = ctk.CTkFrame(r, fg_color="transparent"); info.pack(side="left", padx=18)
        label(info, user, 24, "bold").pack(anchor="w")
        label(info, " · ".join(x for x in (course, uni) if x) or "Completa o teu perfil abaixo", 13, color=TXT2).pack(anchor="w")
        if bio: label(info, bio, 13, wraplength=520, justify="left").pack(anchor="w", pady=(4, 0))
        bb = ctk.CTkFrame(info, fg_color="transparent"); bb.pack(anchor="w", pady=(10, 0))
        button(bb, "📷 Alterar foto", s.pick_photo).pack(side="left", padx=(0, 6))
        button(bb, "Remover foto", s.remove_photo, "ghost").pack(side="left")

        #  editar perfil
        c = card(sc); c.pack(fill="x", padx=4, pady=(0, 10))
        label(c, "Editar perfil", 17, "bold").pack(anchor="w", padx=18, pady=(16, 6))
        r = ctk.CTkFrame(c, fg_color="transparent"); r.pack(anchor="w", padx=14)
        s.e_name = fld(r, "Nome", default=user, width=200); s.e_course = fld(r, "Curso", default=course, width=220, ph="ex.: Eng. Informática")
        s.e_uni = fld(r, "Universidade", default=uni, width=220)
        r = ctk.CTkFrame(c, fg_color="transparent"); r.pack(anchor="w", padx=14, pady=(8, 0))
        s.e_bio = fld(r, "Sobre mim", default=bio, width=660, ph="Uma frase sobre ti ou o teu objetivo")
        button(c, "Guardar perfil", s.save_profile).pack(anchor="w", padx=18, pady=14)

        # metas
        c = card(sc); c.pack(fill="x", padx=4, pady=(0, 10))
        label(c, "Metas", 17, "bold").pack(anchor="w", padx=18, pady=(16, 6))
        r = ctk.CTkFrame(c, fg_color="transparent"); r.pack(anchor="w", padx=14)
        s.g_day = fld(r, "Estudo diário (min)", default=str(gd), width=130)
        s.g_week = fld(r, "Estudo semanal (min)", default=str(gw), width=130)
        s.g_tasks = fld(r, "Tarefas por semana", default=str(gt), width=130)
        button(r, "Guardar metas", s.save_goals).pack(side="left", padx=10, pady=(18, 0))
        label(c, "Esta semana", 14, "bold").pack(anchor="w", padx=18, pady=(14, 2))
        goal_bar(c, "Estudo hoje (min)", study_min(today(), today()), gd)
        goal_bar(c, "Estudo da semana (min)", study_min(ws, we), gw)
        goal_bar(c, "Tarefas concluídas na semana", tasks_done(ws, we), gt)
        n = (today() - ws).days + 1; met = sum(1 for k in range(n) if study_min(ws + dt.timedelta(k), ws + dt.timedelta(k)) >= gd)
        label(c, f"Dias com a meta diária cumprida: {met}/{n}", 12, color=TXT2).pack(anchor="w", padx=18, pady=(4, 0))
        label(c, "Histórico de semanas", 14, "bold").pack(anchor="w", padx=18, pady=(14, 4))
        for k in range(3, -1, -1):
            w0 = ws - dt.timedelta(7 * k); w1 = w0 + dt.timedelta(6); m, t = study_min(w0, w1), tasks_done(w0, w1)
            ok = m >= gw and t >= gt
            item(c, f"Semana de {w0:%d/%m}" + ("  (esta semana)" if k == 0 else ""), f"{m} min de estudo · {t} tarefa(s)",
                 "✅ cumprida" if ok else ("em curso" if k == 0 else "não cumprida"), GREEN if ok else AMBER)
        total = sum(week_met(ws - dt.timedelta(7 * k)) for k in range(12))
        label(c, f"Semanas com metas cumpridas (últimas 12): {total}", 13, "bold", ACC).pack(anchor="w", padx=18, pady=(8, 16))

        # conquistas
        c = card(sc); c.pack(fill="x", padx=4, pady=(0, 10))
        ach = achievements()
        label(c, f"Conquistas · {sum(1 for a in ach if a[3])}/{len(ach)}", 17, "bold").pack(anchor="w", padx=18, pady=(16, 6))
        g = ctk.CTkFrame(c, fg_color="transparent"); g.pack(fill="x", padx=10, pady=(0, 14))
        for i, (ic, t, d, ok) in enumerate(ach):
            g.columnconfigure(i % 3, weight=1, uniform="a")
            f = ctk.CTkFrame(g, fg_color=CARD2, corner_radius=12); f.grid(row=i // 3, column=i % 3, padx=4, pady=4, sticky="nsew")
            label(f, ic if ok else "🔒", 26).pack(pady=(10, 0))
            label(f, t, 13, "bold", None if ok else TXT2).pack()
            label(f, d, 11, color=TXT2, wraplength=170).pack(padx=8, pady=(0, 10))

        #  segurança e dados
        c = card(sc); c.pack(fill="x", padx=4, pady=(0, 10))
        label(c, "Segurança e dados", 17, "bold").pack(anchor="w", padx=18, pady=(16, 6))
        r = ctk.CTkFrame(c, fg_color="transparent"); r.pack(anchor="w", padx=14)
        lm = cfg("lock_min", "15"); box = fld(r, "Bloqueio automático (min)", ["Nunca", "5", "10", "15", "30", "60"], "Nunca" if lm == "0" else lm, 150)
        box.configure(command=lambda v: setcfg("lock_min", 0 if v == "Nunca" else v))
        r = ctk.CTkFrame(c, fg_color="transparent"); r.pack(anchor="w", padx=18, pady=(10, 16))
        button(r, "Alterar palavra-passe", s.change_pw, "ghost").pack(side="left", padx=(0, 6))
        button(r, "Novo código de recuperação", s.new_recovery, "ghost").pack(side="left", padx=6)
        button(r, "Cópia de segurança", s.backup, "ghost").pack(side="left", padx=6)

    # ações
    def pick_photo(s):
        p = filedialog.askopenfilename(title="Escolhe uma foto", filetypes=[("Imagens", "*.png *.jpg *.jpeg *.gif *.bmp *.webp")])
        if not p: return
        try: save_avatar(p)
        except Exception as e: return messagebox.showerror("Foto inválida", f"Não foi possível usar esta imagem.\n{e}")
        s.shell.update_user(); s.refresh()

    def remove_photo(s):
        AVATAR.unlink(missing_ok=True); s.shell.update_user(); s.refresh()

    def save_profile(s):
        name = s.e_name.get().strip()
        if not name: return messagebox.showwarning("Falta o nome", "O nome não pode ficar vazio.")
        run("update user set name=?", (name,)); s.shell.app.user = name
        setcfg("prof_course", s.e_course.get().strip()); setcfg("prof_uni", s.e_uni.get().strip()); setcfg("prof_bio", s.e_bio.get().strip())
        s.shell.update_user(); s.refresh(); messagebox.showinfo("Perfil", "Perfil guardado.")

    def save_goals(s):
        try: v = [int(s.g_day.get()), int(s.g_week.get()), int(s.g_tasks.get())]
        except ValueError: return messagebox.showwarning("Valores inválidos", "As metas têm de ser números inteiros.")
        if min(v) < 1: return messagebox.showwarning("Valores inválidos", "As metas têm de ser pelo menos 1.")
        for k, x in zip(("goal_day", "goal_week", "goal_tasks"), v): setcfg(k, x)
        s.refresh()

    def change_pw(s):
        app = s.shell.app; r = form_dialog(s, "Alterar palavra-passe", [("old", "Palavra-passe atual", True),
                    ("new", "Nova palavra-passe", True), ("new2", "Confirmar nova palavra-passe", True)])
        if not r or app.locked(): return
        if not check_pw(r["old"]): app.fail(); return messagebox.showerror("Incorreta", "A palavra-passe atual não está correta.")
        app.good()
        if len(r["new"]) < 6 or r["new"] != r["new2"]:
            return messagebox.showwarning("Dados inválidos", "A nova palavra-passe precisa de 6+ caracteres e de coincidir com a confirmação.")
        set_pw(r["new"]); messagebox.showinfo("Palavra-passe", "Palavra-passe alterada.")

    def new_recovery(s):
        app = s.shell.app; r = form_dialog(s, "Novo código de recuperação", [("pw", "Palavra-passe atual", True)],
                    "O código atual deixará de funcionar.")
        if not r or app.locked(): return
        if not check_pw(r["pw"]): app.fail(); return messagebox.showerror("Incorreta", "A palavra-passe não está correta.")
        app.good(); show_code(app.root, set_recovery(), "Novo código gerado.")

    def backup(s):
        p = filedialog.asksaveasfilename(defaultextension=".db", initialfile=f"unihelp-backup-{today()}.db", filetypes=[("Base de dados", "*.db")])
        if not p: return
        try:
            dest = sqlite3.connect(p); con.backup(dest); dest.close()
        except Exception as e: return messagebox.showerror("Erro", str(e))
        messagebox.showinfo("Cópia de segurança", "Cópia guardada.\nAtenção: inclui a tua conta (palavra-passe cifrada) e todos os dados.")

# ecrãs principais
class Login(ctk.CTkFrame):
    def __init__(s, app):
        super().__init__(app.root, fg_color="transparent"); s.app = app; s.new = not rows("select 1 from user")
        box = ctk.CTkFrame(s, width=420, height=650, corner_radius=22, fg_color=CARD, border_width=3, border_color=ACC)
        box.place(relx=.5, rely=.5, anchor="center"); box.pack_propagate(False)
        if not s.new: Avatar(box, 72, rows("select name from user")[0][0]).pack(pady=(34, 0))
        label(box, "Bem-vindo(a)", 32, "bold").pack(anchor="w", padx=40, pady=(10 if not s.new else 50, 2))
        label(box, "Cria a tua conta para começar" if s.new else "Introduz a palavra-passe para entrar", 13, color=TXT2).pack(anchor="w", padx=40)
        s.name = ctk.CTkEntry(box, placeholder_text="Nome", height=48, corner_radius=12); s.name.pack(fill="x", padx=40, pady=(30, 12))
        s.pw = ctk.CTkEntry(box, placeholder_text="Palavra-passe", show="•", height=48, corner_radius=12); s.pw.pack(fill="x", padx=40)
        s.pw2 = None
        if s.new:
            s.pw2 = ctk.CTkEntry(box, placeholder_text="Confirmar palavra-passe", show="•", height=48, corner_radius=12); s.pw2.pack(fill="x", padx=40, pady=(12, 0))
        s.err = label(box, "", 12, color=RED, wraplength=340, justify="left"); s.err.pack(anchor="w", padx=40, pady=(6, 0))
        ctk.CTkButton(box, text="Criar conta" if s.new else "Entrar", height=50, corner_radius=12, fg_color=ACC, hover_color=ACC_H,
                      font=F(15, "bold"), command=s.go).pack(fill="x", padx=40, pady=(8, 0))
        if not s.new:
            s.name.insert(0, rows("select name from user")[0][0])
            ctk.CTkButton(box, text="Esqueci-me da palavra-passe", fg_color="transparent", hover=False, text_color=TXT2,
                          font=F(12), command=s.forgot).pack(pady=(10, 0))
            ctk.CTkButton(box, text="Perdi o código — apagar conta e dados", fg_color="transparent", hover=False, text_color=RED,
                          font=F(11), command=s.nuke).pack()
        for w in (s.name, s.pw, s.pw2):
            if w: w.bind("<Return>", lambda e: s.go())
        (s.pw if not s.new else s.name).focus()

    def go(s):
        if s.app.locked(): return
        name, pw = s.name.get().strip(), s.pw.get()
        if s.new:
            if not name or len(pw) < 6: return s.err.configure(text="Escolhe um nome e uma palavra-passe com 6+ caracteres.")
            if pw != s.pw2.get(): return s.err.configure(text="As palavras-passe não coincidem.")
            salt = secrets.token_hex(16); run("insert into user(name,salt,hash) values(?,?,?)", (name, salt, hash_pw(pw, salt)))
            show_code(s.app.root, set_recovery(), "Conta criada! Este é o teu código de recuperação.")
        else:
            u = rows("select * from user")[0]
            if name.lower() != u["name"].lower() or not secrets.compare_digest(hash_pw(pw, u["salt"]), u["hash"]):
                s.app.fail(); return s.err.configure(text="Nome ou palavra-passe incorretos.")
            s.app.good(); name = u["name"]
        s.app.login(name)

    def forgot(s):
        if s.app.locked(): return
        r = form_dialog(s, "Repor palavra-passe", [("code", "Código de recuperação", False), ("pw", "Nova palavra-passe", True),
                        ("pw2", "Confirmar palavra-passe", True)], "Introduz o código de recuperação que recebeste ao criar a conta.", "Repor")
        if not r: return
        u = rows("select * from user")[0]
        if not u["rec_hash"] or not secrets.compare_digest(hash_pw(norm(r["code"]), u["rec_salt"]), u["rec_hash"]):
            s.app.fail(); return messagebox.showerror("Código inválido", "O código de recuperação não está correto.")
        if len(r["pw"]) < 6 or r["pw"] != r["pw2"]:
            return messagebox.showwarning("Dados inválidos", "A nova palavra-passe precisa de 6+ caracteres e de coincidir com a confirmação.")
        s.app.good(); set_pw(r["pw"])
        show_code(s.app.root, set_recovery(), "Palavra-passe alterada. Este é o teu novo código de recuperação (o anterior deixou de funcionar).")
        s.err.configure(text="")

    def nuke(s):
        if messagebox.askyesno("Apagar tudo", "ATENÇÃO: isto apaga a conta E todos os dados (aulas, tarefas, notas, hábitos, foto…).\n"
                               "Só deves fazer isto se perdeste a palavra-passe e o código.\nContinuar?", icon="warning"):
            wipe_all(); s.app.show_login()

class Shell(ctk.CTkFrame):
    def __init__(s, app):
        super().__init__(app.root, fg_color="transparent"); s.app = app; s.current = None
        side = ctk.CTkFrame(s, width=230, corner_radius=0, fg_color=CARD); side.pack(side="left", fill="y"); side.pack_propagate(False)
        label(side, "UniHelp", 22, "bold", ACC).pack(anchor="w", padx=22, pady=(26, 22))
        s.body = ctk.CTkFrame(s, fg_color="transparent"); s.body.pack(side="left", fill="both", expand=True, padx=22, pady=18)
        s.pages, s.nav = {}, {}
        for name, icon, cls in [("Painel", "🏠", Dashboard), ("Horário", "🗓", Schedule), ("Tarefas", "✅", Tasks), ("Foco", "🎯", Focus),
                                ("Hábitos", "🔥", Habits), ("CodeVault", "📚", Vault), ("Planeador", "🧠", Planner), ("Perfil", "👤", Profile)]:
            s.pages[name] = cls(s)
            s.nav[name] = ctk.CTkButton(side, text=f"  {icon}   {name}", anchor="w", height=42, corner_radius=10, fg_color="transparent",
                                        text_color=TXT, hover_color=CARD2, font=F(14, "bold"), command=lambda n=name: s.show(n))
            s.nav[name].pack(fill="x", padx=14, pady=2)
        bot = ctk.CTkFrame(side, fg_color="transparent"); bot.pack(side="bottom", fill="x", padx=14, pady=16)
        chip = ctk.CTkFrame(bot, fg_color=CARD2, corner_radius=12); chip.pack(fill="x", pady=(0, 8))
        s.avatar = Avatar(chip, 36, app.user); s.avatar.pack(side="left", padx=8, pady=8)
        s.chip_name = label(chip, app.user, 14, "bold"); s.chip_name.pack(side="left")
        for w in (chip, s.chip_name): w.bind("<Button-1>", lambda e: s.show("Perfil"))

        def mode():
            m = "dark" if sw.get() else "light"; ctk.set_appearance_mode(m); setcfg("mode", m)
        sw = ctk.CTkSwitch(bot, text="Modo escuro", progress_color=ACC, font=F(12), command=mode); sw.pack(anchor="w", pady=6, padx=4)
        if ctk.get_appearance_mode() == "Dark": sw.select()
        button(bot, "Terminar sessão", app.logout, "ghost").pack(fill="x")
        last = cfg("last_page", "Painel")  # retoma onde ficaste
        s.show(last if last in s.pages else "Painel")

    def update_user(s):
        s.avatar.reload(s.app.user); s.chip_name.configure(text=s.app.user)

    def can_leave(s):
        return s.pages["CodeVault"].can_leave()

    def show(s, name):
        if s.current == "CodeVault" and name != "CodeVault" and not s.can_leave(): return
        s.current = name; setcfg("last_page", name)
        for p in s.pages.values(): p.pack_forget()
        s.pages[name].pack(fill="both", expand=True); s.pages[name].refresh()
        for n, b in s.nav.items():
            b.configure(fg_color=ACC if n == name else "transparent", text_color="white" if n == name else TXT)

class App:
    def __init__(s):
        ctk.set_appearance_mode(cfg("mode", "dark")); ctk.set_default_color_theme("blue")
        s.root = ctk.CTk(fg_color=BG); s.root.title("UniHelp"); s.root.geometry("1240x780"); s.root.minsize(1080, 700)
        s.user = None; s.cur = None; s.rem = False; s.fails = 0; s.lock_until = 0.0; s.last = time.time()
        s.root.protocol("WM_DELETE_WINDOW", s.close)
        for ev in ("<Motion>", "<Key>", "<Button>"): s.root.bind_all(ev, s.touch, add="+")
        s.show_login(); s.root.after(30000, s.autolock)

    #  limite de tentativas de palavra-passe / código
    def locked(s):
        left = int(s.lock_until - time.time())
        if left > 0: messagebox.showwarning("Aguarda", f"Demasiadas tentativas falhadas. Tenta novamente em {left}s."); return True
        return False
    def fail(s):
        s.fails += 1
        if s.fails >= 5: s.fails = 0; s.lock_until = time.time() + 30
    def good(s):
        s.fails = 0

    def touch(s, e=None):
        s.last = time.time()

    def autolock(s):
        """Bloqueia (volta ao ecrã de palavra-passe) após inatividade, sem perder trabalho."""
        try: m = int(cfg("lock_min", 15))
        except ValueError: m = 15
        if s.user and m and isinstance(s.cur, Shell) and time.time() - s.last > m * 60:
            if not s.cur.pages["Foco"].running and s.cur.pages["CodeVault"].autosave(): s.show_login()
        s.root.after(30000, s.autolock)

    def swap(s, new):
        if isinstance(s.cur, Shell): s.cur.pages["Foco"].stop()
        if s.cur: s.cur.destroy()
        s.cur = new; new.pack(fill="both", expand=True)

    def show_login(s):
        s.user = None; s.swap(Login(s))

    def logout(s):
        if isinstance(s.cur, Shell) and not s.cur.can_leave(): return
        s.show_login()

    def close(s):
        if isinstance(s.cur, Shell):
            if not s.cur.can_leave(): return
            s.cur.pages["Foco"].stop()
        s.root.destroy()

    def login(s, name):
        s.user = name; s.last = time.time(); s.swap(Shell(s))
        if not s.rem: s.rem = True; s.root.after(600, s.startup)

    def startup(s):
        u = rows("select * from user")[0]
        if not u["rec_hash"]:  # contas criadas na v2 não tinham código de recuperação
            show_code(s.root, set_recovery(), "Passámos a ter recuperação de palavra-passe. Este é o teu código.")
        m = recover_focus()
        if m: messagebox.showinfo("Sessão recuperada", f"A última sessão de foco foi interrompida. Registámos {m} min de estudo.")
        messagebox.showinfo("UniHelp — o teu dia", summary()); s.reminders()

    def reminders(s):
        msgs = []
        if s.user:
            for t in rows("select * from tasks where parent is null and done=0"):
                d = (pdate(t["due"]) - today()).days; lvl = next((l for l in (1, 3, 7) if 0 <= d <= l), None)
                if lvl and not rows("select 1 from reminded where task=? and lvl=?", (t["id"], lvl)):
                    run("insert into reminded values(?,?)", (t["id"], lvl)); msgs.append(f"{t['title']} — faltam {d} dia(s)")
        if msgs: s.root.bell(); messagebox.showwarning("Prazos a aproximar-se", "\n".join(msgs))
        s.root.after(30 * 60 * 1000, s.reminders)

# comunidade, contas online, admin e ajuda
# (tudo isto está no ficheiro comunidade.py; o servidor.py tem de estar a correr)
import comunidade; comunidade.install()

def start_local_server():
    """Se o servidor configurado for local e ainda não estiver a correr, arranca-o dentro da própria app."""
    from urllib.parse import urlparse
    u = urlparse(cfg("server_url", DEFAULT_SERVER)); host, port = u.hostname or "127.0.0.1", u.port or 8765
    if host not in ("127.0.0.1", "localhost"): return  # servidor remoto: nada a arrancar
    try: socket.create_connection((host, port), timeout=0.5).close(); return  # já está a correr
    except OSError: pass
    try:
        import servidor
        servidor.start_background(port)
    except Exception as e:
        messagebox.showwarning("UniHelp", f"Não foi possível arrancar o servidor local.\n{e}")

if __name__ == "__main__":
    if sys.stdout is None:  # sem consola (.exe ou pythonw): regista mensagens do servidor num ficheiro
        _d = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
        sys.stdout = sys.stderr = open(_d / "unihelp.log", "a", encoding="utf-8", buffering=1)
    start_local_server()
    App().root.mainloop()