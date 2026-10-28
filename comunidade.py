"""UniHelp - módulo de contas, comunidade, moderação e ajuda (cliente).

não se executa sozinho: o unihelp.py importa-o no fim e chama install().
fala com servidor.py por HTTP (só biblioteca padrão). Reutiliza os componentes visuais do unihelp.py.
"""
import base64, io, json, time, tkinter as tk, urllib.parse, urllib.request, urllib.error
from tkinter import messagebox
from urllib.parse import quote
import customtkinter as ctk
from PIL import Image
import __main__ as M
from __main__ import (Page, card, label, button, header, clear, F, ACC, ACC_H, CARD, CARD2, TXT, TXT2,
                      RED, GREEN, AMBER, AVATAR, cfg, setcfg, form_dialog)

#  cliente HTTP
class ApiError(Exception):
    def __init__(s, code, msg, data=None):
        super().__init__(msg); s.code, s.msg, s.data = code, msg, data or {}


class Api:
    def __init__(s):
        s.base = cfg("server_url", M.DEFAULT_SERVER).rstrip("/")
        s.token = cfg("session_token", ""); s.me = None; s.av = {}; s.on_expired = None

    def call(s, method, path, body=None, auth=True, timeout=8):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(s.base + path, data=data, method=method, headers={"Content-Type": "application/json"})
        if auth and s.token: req.add_header("Authorization", "Bearer " + s.token)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r: out = json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try: d = json.loads(e.read() or b"{}")
            except ValueError: d = {}
            if e.code == 401 and auth and s.token and s.on_expired: s.on_expired()
            raise ApiError(e.code, d.get("error", "Erro do servidor."), d)
        except (urllib.error.URLError, OSError, ValueError):
            raise ApiError(0, f"Sem ligação ao servidor ({s.base}). Confirma que o servidor está a correr.")
        if isinstance(out, dict) and out.get("av"): s.av.update(out.pop("av"))
        return out

    def get(s, p, **k): return s.call("GET", p, **k)
    def post(s, p, b=None, **k): return s.call("POST", p, b, **k)
    def put(s, p, b=None, **k): return s.call("PUT", p, b, **k)
    def delete(s, p, **k): return s.call("DELETE", p, **k)

    def keep(s, me):
        s.me = me
        if me.get("avatar"): s.av[me["username"]] = me["avatar"]
        setcfg("session_me", json.dumps({k: v for k, v in me.items() if k != "avatar"})); return me

    def forget(s):
        s.token = ""; setcfg("session_token", ""); setcfg("session_me", "")

    def logout(s):
        try: s.post("/logout", timeout=4)
        except ApiError: pass
        s.forget()

    def resume(s):
        """Sessão guardada: valida o token; sem rede, entra com os dados guardados."""
        if not s.token: return None
        try: return s.keep(s.get("/me", timeout=4)["me"])
        except ApiError as e:
            if e.code != 0: s.forget(); return None
            try: me = json.loads(cfg("session_me", "")); s.me = me; return me
            except ValueError: return None

    def push_avatar(s):
        b64 = None
        if AVATAR.exists():
            try:
                im = Image.open(AVATAR).convert("RGBA").resize((64, 64), Image.LANCZOS); buf = io.BytesIO(); im.save(buf, "PNG")
                b64 = base64.b64encode(buf.getvalue()).decode()
            except Exception: b64 = None
        try: s.put("/me", {"avatar": b64})
        except ApiError as e: return messagebox.showwarning("Foto", f"A foto ficou só neste computador.\n{e.msg}")
        if b64: s.av[s.me["username"]] = b64
        else: s.av.pop(s.me["username"], None)

    def update_profile(s, **kw):
        try: s.me = s.keep(s.put("/me", kw)["me"])
        except ApiError as e: messagebox.showwarning("Perfil público", f"Não foi possível atualizar online.\n{e.msg}")

    def push_streak(s, n):
        try: s.post("/me/streak", {"streak": n}, timeout=4)
        except ApiError: pass


# ajudas visuais
def dname(u):
    return (f"{u.get('name', '')} {u.get('surname', '')}").strip() or u["username"]

def ago(sec):
    if sec < 60: return "agora mesmo"
    if sec < 3600: return f"há {sec // 60} min"
    if sec < 86400: return f"há {sec // 3600} h"
    return f"há {sec // 86400} d"

def warn(fn, *a):
    """Chama a API; em caso de erro mostra aviso e devolve None."""
    try: return fn(*a)
    except ApiError as e: messagebox.showwarning("UniHelp", e.msg)

def net_error(parent, e):
    label(parent, "⚠  " + e.msg, 13, color=RED, wraplength=560, justify="left").pack(padx=20, pady=30)

_IMG = {}
def user_avatar(parent, api, username, display, size=40):
    b = api.av.get(username)
    if b:
        key = (username, size, len(b), b[-12:])
        if key not in _IMG:
            try:
                im = Image.open(io.BytesIO(base64.b64decode(b)))
                if max(im.size) > 512: raise ValueError("foto demasiado grande")  # antes de descodificar
                im = im.convert("RGBA"); im.load()
                _IMG[key] = ctk.CTkImage(light_image=im, dark_image=im, size=(size, size))
            except Exception: _IMG[key] = None
        if _IMG[key]: return ctk.CTkLabel(parent, text="", image=_IMG[key], width=size, height=size)
    return ctk.CTkLabel(parent, text=(display[:1] or "?").upper(), width=size, height=size, corner_radius=size // 2,
                        fg_color=ACC, text_color="white", font=F(size * 0.4, "bold"))

def report(parent, api, kind, target, what):
    r = form_dialog(parent, f"Denunciar {what}", [("reason", "Motivo (opcional)", False)],
                    "Com 3 denúncias de pessoas diferentes o conteúdo fica escondido." if kind != "user" else "A moderação vai analisar este perfil.",
                    "Denunciar")
    if r and warn(api.post, "/report", {"kind": kind, "target": target, "reason": r["reason"]}) is not None:
        messagebox.showinfo("Obrigado", "Denúncia enviada.")

def user_row(parent, app, u, follow=True):
    api = app.api; f = ctk.CTkFrame(parent, fg_color=CARD2, corner_radius=12); f.pack(fill="x", padx=8, pady=4)
    user_avatar(f, api, u["username"], dname(u), 40).pack(side="left", padx=10, pady=8)
    t = ctk.CTkFrame(f, fg_color="transparent"); t.pack(side="left", fill="x", expand=True)
    nm = label(t, dname(u), 14, "bold"); nm.pack(anchor="w")
    nm.bind("<Button-1>", lambda e: ProfileWindow(parent, app, u["username"]))
    label(t, f"@{u['username']}" + ("  ·  🤝 amigos" if u.get("i_follow") and u.get("follows_me") else ""), 12, color=TXT2).pack(anchor="w")
    if follow and "i_follow" in u and u["username"] != api.me["username"]:
        st = {"on": u["i_follow"]}; b = button(f, "", None, width=112); b.pack(side="right", padx=10)
        def paint():
            if st["on"]: b.configure(text="A seguir ✓", fg_color=CARD, hover_color=RED, text_color=TXT)
            else: b.configure(text="Seguir", fg_color=ACC, hover_color=ACC_H, text_color="white")
        def toggle():
            if warn(api.delete if st["on"] else api.post, f"/follow/{u['username']}") is not None: st["on"] = not st["on"]; paint()
        b.configure(command=toggle); paint()
    return f

def stat(parent, n, text, cmd=None):
    f = ctk.CTkFrame(parent, fg_color=CARD2, corner_radius=12); f.pack(side="left", expand=True, fill="x", padx=4)
    a = label(f, str(n), 20, "bold", ACC); a.pack(pady=(8, 0)); b = label(f, text, 12, color=TXT2); b.pack(pady=(0, 8))
    if cmd:
        for w in (f, a, b): w.bind("<Button-1>", lambda e: cmd())


# janelas de perfil e listas
class UserListWindow(ctk.CTkToplevel):
    def __init__(s, parent, app, title, path):
        super().__init__(parent); s.title(title); s.geometry("440x560"); s.after(150, s.focus)
        sc = ctk.CTkScrollableFrame(s, fg_color="transparent"); sc.pack(fill="both", expand=True, padx=8, pady=8)
        label(sc, title, 20, "bold").pack(anchor="w", padx=8, pady=(4, 8))
        try: users = app.api.get(path)["users"]
        except ApiError as e: return net_error(sc, e)
        if not users: label(sc, "Ninguém por aqui ainda.", 13, color=TXT2).pack(pady=30)
        for u in users: user_row(sc, app, u)


class ProfileWindow(ctk.CTkToplevel):
    def __init__(s, parent, app, username):
        super().__init__(parent); s.app, s.u = app, username; s.title(f"@{username}"); s.geometry("560x740"); s.after(150, s.focus)
        s.sc = ctk.CTkScrollableFrame(s, fg_color="transparent"); s.sc.pack(fill="both", expand=True, padx=8, pady=8); s.load()

    def load(s):
        clear(s.sc); api = s.app.api
        try: d = api.get(f"/users/{s.u}")
        except ApiError as e: return net_error(s.sc, e)
        c = card(s.sc); c.pack(fill="x", pady=(0, 10)); top = ctk.CTkFrame(c, fg_color="transparent"); top.pack(fill="x", padx=18, pady=16)
        user_avatar(top, api, d["username"], dname(d), 84).pack(side="left")
        col = ctk.CTkFrame(top, fg_color="transparent"); col.pack(side="left", padx=16)
        label(col, dname(d), 22, "bold").pack(anchor="w"); label(col, f"@{d['username']}", 13, color=TXT2).pack(anchor="w")
        label(col, f"🔥 Sequência diária: {d['streak']} dia(s)" + ("   ·   🤝 Amigos" if d["friends"] else ""), 13).pack(anchor="w", pady=(4, 0))
        if d["bio"]: label(c, d["bio"], 13, wraplength=480, justify="left").pack(anchor="w", padx=18, pady=(0, 10))
        r = ctk.CTkFrame(c, fg_color="transparent"); r.pack(fill="x", padx=14, pady=(0, 12))
        stat(r, d["followers"], "Seguidores", lambda: UserListWindow(s, s.app, f"Seguidores de @{s.u}", f"/users/{s.u}/followers"))
        stat(r, d["following"], "A seguir", lambda: UserListWindow(s, s.app, f"@{s.u} segue", f"/users/{s.u}/following"))
        stat(r, d["n_posts"], "Publicações")
        if not d["is_me"]:
            b = ctk.CTkFrame(c, fg_color="transparent"); b.pack(fill="x", padx=18, pady=(0, 14))
            button(b, "A seguir ✓ (deixar)" if d["i_follow"] else "Seguir", s.toggle, "ghost" if d["i_follow"] else "primary").pack(side="left")
            button(b, "🚩 Denunciar", lambda: report(s, api, "user", s.u, "perfil"), "ghost").pack(side="left", padx=6)
            button(b, "⛔ Bloquear", s.block, "ghost").pack(side="left")
        label(s.sc, "Publicações (últimas 20)", 16, "bold").pack(anchor="w", padx=6, pady=(6, 4))
        if not d["posts"]: label(s.sc, "Ainda sem publicações.", 13, color=TXT2).pack(pady=12)
        for p in d["posts"]:
            pc = card(s.sc, CARD2); pc.pack(fill="x", pady=4)
            label(pc, p["body"], 13, wraplength=480, justify="left").pack(anchor="w", padx=14, pady=(10, 2))
            label(pc, f"♥ {p['likes']}  ·  💬 {p['ncom']}  ·  {ago(p['ago'])}", 12, color=TXT2).pack(anchor="w", padx=14, pady=(0, 10))

    def toggle(s):
        i = s.app.api.get(f"/users/{s.u}")["i_follow"]
        if warn(s.app.api.delete if i else s.app.api.post, f"/follow/{s.u}") is not None: s.load()

    def block(s):
        if messagebox.askyesno("Bloquear", f"Bloquear @{s.u}? Deixam de se ver e de se seguir.", parent=s) and warn(s.app.api.post, f"/block/{s.u}") is not None:
            s.destroy()


# comunidade
class Community(Page):
    def __init__(s, shell):
        super().__init__(shell); s.mode = "public"
        header(s, "Comunidade", "Desabafos dos estudantes · últimas 24 horas, ordenados por gostos")
        s.seg = ctk.CTkSegmentedButton(s, values=["🌍 Público", "🤝 Amigos"], command=s.set_mode); s.seg.set("🌍 Público"); s.seg.pack(anchor="w", pady=(0, 10))
        c = card(s); c.pack(fill="x", pady=(0, 6))
        s.box = ctk.CTkTextbox(c, height=70, corner_radius=10, fg_color=CARD2, font=F(14)); s.box.pack(fill="x", padx=14, pady=(14, 6))
        r = ctk.CTkFrame(c, fg_color="transparent"); r.pack(fill="x", padx=14, pady=(0, 12))
        s.count = label(r, "0/500", 12, color=TXT2); s.count.pack(side="left"); button(r, "Publicar", s.publish).pack(side="right")
        s.box.bind("<KeyRelease>", lambda e: s.count.configure(text=f"{len(s.box.get('1.0', 'end').strip())}/500"))
        s.note = label(s, "", 12, color=TXT2); s.note.pack(anchor="w", pady=(0, 4))
        s.lst = ctk.CTkScrollableFrame(s, fg_color="transparent"); s.lst.pack(fill="both", expand=True)

    def set_mode(s, v):
        s.mode = "friends" if "Amigos" in v else "public"; s.refresh()

    def publish(s):
        body = s.box.get("1.0", "end").strip()
        if not body: return messagebox.showwarning("Publicação vazia", "Escreve alguma coisa primeiro.")
        if len(body) > 500: return messagebox.showwarning("Demasiado longa", "O máximo são 500 caracteres.")
        if warn(s.shell.app.api.post, "/posts", {"body": body}) is not None:
            s.box.delete("1.0", "end"); s.count.configure(text="0/500"); s.refresh()

    def refresh(s):
        clear(s.lst)
        s.note.configure(text="Só vês publicações de quem segues e te segue de volta, mais as tuas." if s.mode == "friends" else "")
        try: posts = s.shell.app.api.get(f"/feed?mode={s.mode}")["posts"]
        except ApiError as e: return net_error(s.lst, e)
        if not posts:
            label(s.lst, "Ainda não há publicações nas últimas 24 h." if s.mode == "public"
                  else "Sem publicações de amigos. Segue pessoas que te sigam de volta 🤝", 14, color=TXT2).pack(pady=40)
        for p in posts: s.build(p)

    def build(s, p):
        app = s.shell.app; api = app.api; own = p["username"] == api.me["username"]
        c = card(s.lst); c.pack(fill="x", padx=4, pady=5); top = ctk.CTkFrame(c, fg_color="transparent"); top.pack(fill="x", padx=14, pady=(12, 0))
        user_avatar(top, api, p["username"], dname(p), 40).pack(side="left")
        col = ctk.CTkFrame(top, fg_color="transparent"); col.pack(side="left", padx=10)
        nm = label(col, dname(p), 14, "bold"); nm.pack(anchor="w")
        nm.bind("<Button-1>", lambda e: ProfileWindow(s, app, p["username"]))
        label(col, f"@{p['username']} · {ago(p['ago'])}", 12, color=TXT2).pack(anchor="w")
        label(c, p["body"], 14, wraplength=640, justify="left").pack(anchor="w", padx=16, pady=10)
        bar = ctk.CTkFrame(c, fg_color="transparent"); bar.pack(fill="x", padx=8, pady=(0, 8)); st = {"box": None}
        like = ctk.CTkButton(bar, width=64, height=30, fg_color="transparent", hover_color=CARD2, font=F(14, "bold"), text_color=TXT2)
        def paint():
            like.configure(text=f"{'♥' if p['liked'] else '♡'} {p['likes']}", text_color=RED if p["liked"] else TXT2)
        def toggle():
            r = warn(api.post, f"/posts/{p['id']}/like")
            if r is not None: p["liked"], p["likes"] = r["liked"], r["likes"]; paint()
        like.configure(command=toggle); paint(); like.pack(side="left")
        com = ctk.CTkButton(bar, text=f"💬 {p['ncom']}", width=64, height=30, fg_color="transparent", hover_color=CARD2, font=F(14, "bold"), text_color=TXT2)
        com.configure(command=lambda: s.toggle_comments(p, c, com, st)); com.pack(side="left")
        if own: button(bar, "🗑", lambda: s.remove(p), "ghost", 40).pack(side="right", padx=4)
        else: button(bar, "🚩", lambda: report(s, api, "post", p["id"], "publicação"), "ghost", 40).pack(side="right", padx=4)

    def remove(s, p):
        if messagebox.askyesno("Apagar", "Apagar esta publicação?") and warn(s.shell.app.api.delete, f"/posts/{p['id']}") is not None: s.refresh()

    def toggle_comments(s, p, c, btn, st):
        if st["box"]: st["box"].destroy(); st["box"] = None; return
        st["box"] = ctk.CTkFrame(c, fg_color=CARD2, corner_radius=12); st["box"].pack(fill="x", padx=12, pady=(0, 12)); s.load_comments(p, st["box"], btn)

    def load_comments(s, p, box, btn):
        clear(box); app = s.shell.app; api = app.api
        try: cs = api.get(f"/posts/{p['id']}/comments")["comments"]
        except ApiError as e: return net_error(box, e)
        p["ncom"] = len(cs); btn.configure(text=f"💬 {len(cs)}")
        for k in cs:
            r = ctk.CTkFrame(box, fg_color="transparent"); r.pack(fill="x", padx=10, pady=4)
            user_avatar(r, api, k["username"], dname(k), 28).pack(side="left", anchor="n")
            col = ctk.CTkFrame(r, fg_color="transparent"); col.pack(side="left", padx=8, fill="x", expand=True)
            label(col, f"{dname(k)} · {ago(k['ago'])}", 12, "bold").pack(anchor="w")
            label(col, k["body"], 13, wraplength=500, justify="left").pack(anchor="w")
            if k["can_delete"]:
                ctk.CTkButton(r, text="✕", width=26, height=26, fg_color="transparent", hover_color=RED, text_color=TXT2,
                              command=lambda i=k["id"]: s.del_comment(i, p, box, btn)).pack(side="right")
            if k["username"] != api.me["username"]:
                ctk.CTkButton(r, text="🚩", width=26, height=26, fg_color="transparent", hover_color=AMBER, text_color=TXT2,
                              command=lambda i=k["id"]: report(s, api, "comment", i, "comentário")).pack(side="right")
        r = ctk.CTkFrame(box, fg_color="transparent"); r.pack(fill="x", padx=10, pady=(4, 10))
        e = ctk.CTkEntry(r, placeholder_text="Escreve um comentário…", height=34, corner_radius=10); e.pack(side="left", fill="x", expand=True)
        def send(*_):
            t = e.get().strip()
            if t and warn(api.post, f"/posts/{p['id']}/comments", {"body": t}) is not None: s.load_comments(p, box, btn)
        e.bind("<Return>", send); button(r, "Enviar", send, width=70).pack(side="left", padx=6)

    def del_comment(s, i, p, box, btn):
        if warn(s.shell.app.api.delete, f"/comments/{i}") is not None: s.load_comments(p, box, btn)


class People(Page):
    TABS = ["🔎 Descobrir", "👥 Seguidores", "➡ A seguir", "⛔ Bloqueados"]
    def __init__(s, shell):
        super().__init__(shell); header(s, "Pessoas", "Pesquisa por nome ou @utilizador, segue e descobre colegas")
        s.seg = ctk.CTkSegmentedButton(s, values=s.TABS, command=lambda v: s.refresh()); s.seg.set(s.TABS[0]); s.seg.pack(anchor="w", pady=(0, 8))
        r = ctk.CTkFrame(s, fg_color="transparent"); r.pack(fill="x", pady=(0, 8))
        s.q = ctk.CTkEntry(r, placeholder_text="🔍 Nome ou @utilizador…", height=38, corner_radius=10); s.q.pack(side="left", fill="x", expand=True)
        s.q.bind("<Return>", lambda e: s.search()); button(r, "Pesquisar", s.search).pack(side="left", padx=6)
        s.lst = ctk.CTkScrollableFrame(s, fg_color="transparent"); s.lst.pack(fill="both", expand=True)

    def search(s):
        s.seg.set(s.TABS[0]); s.refresh()

    def refresh(s):
        clear(s.lst); app = s.shell.app; api = app.api; tab = s.seg.get(); me = api.me["username"]
        try:
            if tab == s.TABS[0]:
                q = s.q.get().strip()
                users = api.get("/search?q=" + quote(q))["users"] if q else api.get("/suggestions")["users"]
                title = f"Resultados para «{q}»" if q else "Sugestões para ti"
            elif tab == s.TABS[1]: users, title = api.get(f"/users/{me}/followers")["users"], "Quem te segue"
            elif tab == s.TABS[2]: users, title = api.get(f"/users/{me}/following")["users"], "Quem segues"
            else: users, title = api.get("/blocks")["users"], "Pessoas bloqueadas"
        except ApiError as e: return net_error(s.lst, e)
        label(s.lst, title, 16, "bold").pack(anchor="w", padx=8, pady=(4, 4))
        if not users: label(s.lst, "Nada para mostrar.", 13, color=TXT2).pack(pady=30)
        for u in users:
            f = user_row(s.lst, app, u, follow=tab != s.TABS[3])
            if tab == s.TABS[3]: button(f, "Desbloquear", lambda n=u["username"]: s.unblock(n), "ghost", 110).pack(side="right", padx=10)

    def unblock(s, n):
        if warn(s.shell.app.api.delete, f"/block/{n}") is not None: s.refresh()


class Notifications(Page):
    def __init__(s, shell):
        super().__init__(shell); header(s, "Notificações", "Gostos, comentários, novos seguidores e amizades")
        s.lst = ctk.CTkScrollableFrame(s, fg_color="transparent"); s.lst.pack(fill="both", expand=True)

    def refresh(s):
        clear(s.lst); app = s.shell.app; api = app.api
        try: items = api.get("/notifs")["items"]
        except ApiError as e: return net_error(s.lst, e)
        if not items: label(s.lst, "Sem notificações por agora 🔔", 14, color=TXT2).pack(pady=40)
        for n in items:
            txt = {"like": "gostou da tua publicação", "comment": "comentou a tua publicação", "follow": "começou a seguir-te"}.get(n["kind"])
            txt = f"{dname(n)} {txt}" if txt else f"Tu e {dname(n)} são agora amigos 🤝"
            f = ctk.CTkFrame(s.lst, fg_color=CARD, corner_radius=12); f.pack(fill="x", padx=4, pady=4)
            ctk.CTkFrame(f, width=5, height=34, corner_radius=3, fg_color=RED if not n["seen"] else CARD2).pack(side="left", padx=(10, 8), pady=10)
            user_avatar(f, api, n["username"], dname(n), 36).pack(side="left", padx=(0, 10))
            t = ctk.CTkFrame(f, fg_color="transparent"); t.pack(side="left", fill="x", expand=True)
            label(t, txt, 14, "bold" if not n["seen"] else "normal").pack(anchor="w")
            label(t, (f"«{n['post']}»  ·  " if n["post"] else "") + ago(n["ago"]), 12, color=TXT2, wraplength=520, justify="left").pack(anchor="w")
            for w in (f, t): w.bind("<Button-1>", lambda e, u=n["username"]: ProfileWindow(s, app, u))
        try: api.post("/notifs/seen"); s.shell.set_badge(0)
        except ApiError: pass


# ajuda (explicação de cada função)
HELP = [
 ("🏠", "Painel", [
  ("Saudação e resumo", "Mostra o teu nome, a foto e a data. Quatro cartões: aulas de hoje, tarefas pendentes, horas de estudo planeadas para hoje e quantas tarefas são de prioridade máxima (prazo em 2 dias ou menos, ou importância 5)."),
  ("Lista de prioridades", "O que deves fazer hoje. Se o Planeador já reservou blocos para hoje, mostra a hora e a duração de cada um; senão mostra as tarefas mais urgentes."),
  ("Avisos de prazos", "Tarefas que terminam nos próximos 7 dias ou já estão atrasadas (a vermelho quando falta 1 dia ou menos)."),
  ("Metas da semana e gráfico", "Barras com o estudo e as tarefas concluídas desta semana face às tuas metas, e quantas tarefas concluíste em cada um dos últimos 7 dias.")]),
 ("🗓", "Horário", [
  ("Adicionar aula", "Escreve a disciplina, escolhe o dia e as horas (HH:MM) e carrega em «Adicionar»."),
  ("Vista semanal", "Uma coluna por dia, com o dia de hoje destacado. O ✕ remove a aula."),
  ("Para que serve", "O Planeador usa o horário para nunca marcar estudo durante as aulas.")]),
 ("✅", "Tarefas e prazos", [
  ("Adicionar tarefa", "Título, disciplina, tipo (trabalho, projeto, relatório, exame, teste), prazo (AAAA-MM-DD ou DD/MM/AAAA), importância de 1 a 5 e estimativa em minutos."),
  ("Concluir (✔)", "Marca como feita e regista o dia. Numa tarefa dividida, a tarefa-mãe conclui-se sozinha quando todas as subtarefas estiverem feitas."),
  ("Dividir", "Parte uma tarefa grande em subtarefas separadas por «;» (ex.: Pesquisa; Código; Relatório). A estimativa é repartida entre elas."),
  ("Reorganizar atrasadas", "Redistribui as tarefas atrasadas pelos próximos dias (duas por dia), começando pelas mais importantes."),
  ("Barra de progresso", "Em tarefas simples compara o tempo estudado com a estimativa; em tarefas divididas é a percentagem de subtarefas feitas."),
  ("Lembretes automáticos", "Ao abrir a app e de 30 em 30 minutos, avisa quando faltam 7, 3 e 1 dias para um prazo (cada aviso só aparece uma vez)."),
  ("Apagar (✕)", "Remove a tarefa e as suas subtarefas.")]),
 ("🎯", "Foco", [
  ("Começar a estudar", "Escolhe sozinho a tarefa mais prioritária, preenche a disciplina e o objetivo e arranca o cronómetro, sem teres de decidir."),
  ("Iniciar manual", "Começa uma sessão com a disciplina e o objetivo que escreveres."),
  ("Sessão e pausa", "A duração da sessão e da pausa (Pomodoro) ficam guardadas para a próxima vez. No fim da sessão há uma pausa automática."),
  ("Parar", "Interrompe e regista o tempo já estudado."),
  ("Registo do tempo", "Cada sessão fica guardada, soma ao tempo gasto da tarefa e conta para as metas."),
  ("Recuperação", "Se a app fechar a meio de uma sessão, ao reabrir regista automaticamente o tempo que já tinhas estudado."),
  ("Painel «Hoje»", "Minutos estudados por disciplina e progresso da meta diária.")]),
 ("🔥", "Hábitos (Momentum)", [
  ("Checklist diária", "Marca os hábitos de hoje. Os quadrados mostram os últimos 7 dias de cada hábito."),
  ("Novo hábito / apagar", "Escreve o nome e carrega em ＋; o ✕ remove o hábito e o seu histórico."),
  ("Sequência", "Dias seguidos com pelo menos um hábito cumprido. Se ontem ficou em branco, a app sugere só 10 minutos para retomares, sem culpa."),
  ("Resumo semanal", "Hábitos cumpridos, tempo de estudo por disciplina, tarefas concluídas e sugestões para os próximos dias.")]),
 ("📚", "CodeVault", [
  ("Notas", "Guarda notas, código, fórmulas e conceitos, com disciplina, etiquetas e (opcionalmente) o projeto a que pertencem."),
  ("Pesquisa", "Procura no título, conteúdo, etiquetas e disciplina enquanto escreves."),
  ("Flashcards", "«Criar flashcard» usa o título como pergunta e o conteúdo como resposta; «Rever flashcards» mostra cartões ao acaso."),
  ("Segurança do trabalho", "Se tentares sair com alterações por guardar, a app pergunta se queres guardar. Ao reabrir, volta à última nota que tinhas aberta.")]),
 ("🧠", "Planeador", [
  ("Plano da semana", "Distribui as tarefas pelos blocos livres das 9h às 21h, fora das aulas e do almoço (13h-14h), em sessões de até 50 minutos com 10 de intervalo."),
  ("Como decide a prioridade", "Importância × 10, mais urgência (cresce à medida que o prazo chega), muito mais se estiver atrasada e um extra para exames e testes a 7 dias ou menos."),
  ("Limites saudáveis", "No máximo 240 minutos de estudo por dia. Exames e testes ganham sessões repartidas pelos 7 dias anteriores."),
  ("Não consegui cumprir", "Recalcula o plano a partir de agora, sem castigos.")]),
 ("💬", "Comunidade", [
  ("Publicar", "Partilha um desabafo (até 500 caracteres). As publicações ficam visíveis durante 24 horas."),
  ("Feed público", "Todas as publicações das últimas 24 h, ordenadas por gostos."),
  ("Feed de amigos", "Só publicações de pessoas que segues e que te seguem de volta (seguimento mútuo), mais as tuas."),
  ("Gostos ♥", "Um por pessoa e por publicação; o coração fica vermelho. Carregar de novo retira o gosto."),
  ("Comentários 💬", "Abre os comentários da publicação e escreve o teu. Podes apagar os teus; o dono da publicação pode apagar qualquer comentário."),
  ("Apagar 🗑 / Denunciar 🚩", "Apagas as tuas publicações. Nas dos outros podes denunciar.")]),
 ("🔎", "Pessoas", [
  ("Pesquisa", "Por nome, sobrenome ou @utilizador."),
  ("Sugestões", "Pessoas que ainda não segues, priorizando as que são seguidas por quem tu segues."),
  ("Seguir / deixar de seguir", "Quando duas pessoas se seguem uma à outra tornam-se amigas 🤝."),
  ("Listas", "Quem te segue, quem segues e quem bloqueaste (podes desbloquear)."),
  ("Perfil de outras pessoas", "Clica num nome para veres foto, bio, seguidores, a seguir, publicações e a sequência diária. Aí também podes seguir, denunciar ou bloquear.")]),
 ("🔔", "Notificações", [
  ("Avisos", "Gostos, comentários, novos seguidores e novas amizades. As novas aparecem com uma barra vermelha."),
  ("Selo vermelho", "O número no menu mostra quantas ainda não viste; desaparece ao abrires a página.")]),
 ("🛡", "Moderação", [
  ("Denunciar", "Publicações (🚩), comentários (🚩) e perfis. Cada pessoa só conta uma vez por conteúdo."),
  ("Escondido às 3 denúncias", "Uma publicação ou comentário fica escondido assim que 3 pessoas diferentes o denunciam; a moderação decide depois."),
  ("Bloquear", "Deixam de se ver (feed, perfil, comentários) e de se seguir."),
  ("Administração", "Entrando com a conta de administrador abre-se uma sessão própria, onde se vêem as denúncias, se removem conteúdos, se suspendem ou reativam contas e se consulta o resumo.")]),
 ("👤", "Perfil e conta", [
  ("Foto, nome, sobrenome e bio", "Tudo o que os outros veem no teu perfil. A foto é recortada em círculo."),
  ("Metas e conquistas", "Define metas diárias e semanais; a app guarda o histórico de semanas e desbloqueia conquistas."),
  ("Bloqueio automático", "Depois de alguns minutos sem usar a app, pede a palavra-passe outra vez (sem perderes trabalho)."),
  ("Cópia de segurança", "Guarda um ficheiro com os dados guardados neste computador."),
  ("Criar conta", "Nome de utilizador único, email único e palavra-passe. Recebes um código de 6 dígitos no email para confirmar."),
  ("Entrar", "Com o nome de utilizador ou com o email. «Manter sessão iniciada» guarda a sessão neste computador."),
  ("Recuperar palavra-passe", "«Esqueci-me da palavra-passe» envia um código de 6 dígitos para o email."),
  ("Apagar conta", "Em Perfil → Conta. Apaga a conta, as publicações, comentários, seguidores e os dados guardados neste computador. Não tem volta.")]),
 ("🎨", "Aspeto", [
  ("Modo escuro/claro", "No interruptor do menu lateral."),
  ("Menu e janelas estreitas", "Em janelas largas o menu fica ao lado; em janelas estreitas passa a um botão ☰ no canto superior direito."),
  ("Retoma", "A app abre na última página onde estavas.")]),
]


class Help(Page):
    def __init__(s, shell):
        super().__init__(shell); header(s, "Ajuda", "O que faz cada parte do UniHelp (clica num módulo para abrir)")
        sc = ctk.CTkScrollableFrame(s, fg_color="transparent"); sc.pack(fill="both", expand=True)
        for i, (icon, title, fns) in enumerate(HELP):
            c = card(sc); c.pack(fill="x", padx=4, pady=4); body = ctk.CTkFrame(c, fg_color="transparent"); st = {"open": False}
            b = ctk.CTkButton(c, text=f"{icon}   {title}", anchor="w", height=40, fg_color="transparent", hover_color=CARD2, text_color=TXT, font=F(15, "bold"))
            b.pack(fill="x", padx=6, pady=4)
            for name, desc in fns:
                label(body, name, 13, "bold", ACC).pack(anchor="w", padx=18, pady=(8, 0))
                label(body, desc, 13, wraplength=640, justify="left").pack(anchor="w", padx=18)
            ctk.CTkFrame(body, height=10, fg_color="transparent").pack()
            def toggle(body=body, st=st, b=b, title=title, icon=icon):
                st["open"] = not st["open"]
                if st["open"]: body.pack(fill="x")
                else: body.pack_forget()
                b.configure(text=f"{icon}   {title}" + ("   ▾" if st["open"] else ""))
            b.configure(command=toggle)
            if i == 0: toggle()


# entrada / criação de conta
class AuthScreen(ctk.CTkFrame):
    FORMS = {
     "login": ("Bem-vindo(a)", "Entra com o teu nome de utilizador ou email",
               [("login", "Nome de utilizador ou email", False), ("password", "Palavra-passe", True)], "Entrar"),
     "register": ("Criar conta", "Um nome de utilizador e um email por pessoa",
                  [("username", "Nome de utilizador (3-20: letras, números, _)", False), ("email", "Email", False),
                   ("name", "Nome", False), ("surname", "Sobrenome", False),
                   ("password", "Palavra-passe (6+ caracteres)", True), ("password2", "Confirmar palavra-passe", True)], "Criar conta"),
     "verify": ("Confirma o teu email", "Escreve o código de 6 dígitos que enviámos para o teu email", [("code", "Código de 6 dígitos", False)], "Confirmar"),
     "forgot": ("Recuperar palavra-passe", "Enviamos um código de 6 dígitos para o email da conta", [("email", "Email da conta", False)], "Enviar código"),
     "reset": ("Nova palavra-passe", "Escreve o código recebido e a nova palavra-passe",
               [("code", "Código de 6 dígitos", False), ("password", "Nova palavra-passe", True), ("password2", "Confirmar", True)], "Repor palavra-passe"),
    }

    def __init__(s, app, prefill="", msg=""):
        super().__init__(app.root, fg_color="transparent"); s.app, s.prefill, s.pending = app, prefill, ""
        s.box = ctk.CTkScrollableFrame(s, width=400, corner_radius=22, fg_color=CARD, border_width=3, border_color=ACC)
        s.box.place(relx=.5, rely=.5, anchor="center", relheight=.95); s.show("login", msg)

    def say(s, text, ok=False):
        s.msg.configure(text=text, text_color=GREEN if ok else RED)

    def show(s, mode, msg="", ok=False):
        s.mode = mode; clear(s.box); title, sub, fields, btn = s.FORMS[mode]; b = s.box
        label(b, "UniHelp", 16, "bold", ACC).pack(anchor="w", padx=24, pady=(22, 0))
        label(b, title, 28, "bold").pack(anchor="w", padx=24, pady=(6, 2))
        label(b, sub, 13, color=TXT2, wraplength=330, justify="left").pack(anchor="w", padx=24)
        s.ent = {}
        for k, ph, sec in fields:
            e = ctk.CTkEntry(b, placeholder_text=ph, show="•" if sec else "", height=44, corner_radius=12); e.pack(fill="x", padx=24, pady=(12, 0))
            e.bind("<Return>", lambda ev: s.submit()); s.ent[k] = e
        s.keep = None
        if mode == "login":
            if s.prefill: s.ent["login"].insert(0, s.prefill)
            s.keep = ctk.CTkCheckBox(b, text="Manter sessão iniciada", font=F(12), fg_color=ACC, hover_color=ACC_H); s.keep.select()
            s.keep.pack(anchor="w", padx=24, pady=(12, 0))
        s.msg = label(b, "", 12, wraplength=330, justify="left"); s.msg.pack(anchor="w", padx=24, pady=(8, 0)); s.say(msg, ok)
        ctk.CTkButton(b, text=btn, height=48, corner_radius=12, fg_color=ACC, hover_color=ACC_H, font=F(15, "bold"), command=s.submit).pack(fill="x", padx=24, pady=(8, 0))
        links = {"login": [("Criar conta", lambda: s.show("register")), ("Esqueci-me da palavra-passe", lambda: s.show("forgot"))],
                 "register": [("Já tenho conta", lambda: s.show("login"))],
                 "verify": [("Reenviar código", s.resend), ("Voltar", lambda: s.show("login"))],
                 "forgot": [("Voltar", lambda: s.show("login"))], "reset": [("Voltar", lambda: s.show("login"))]}[mode]
        for t, cmd in links:
            ctk.CTkButton(b, text=t, fg_color="transparent", hover=False, text_color=TXT2, font=F(12), command=cmd).pack(pady=(4, 0))
        if mode == "login":
            ctk.CTkButton(b, text=f"Servidor: {s.app.api.base}", fg_color="transparent", hover=False, text_color=TXT2, font=F(10), command=s.set_server).pack(pady=(8, 12))
        (s.ent["password"] if mode == "login" and s.prefill else next(iter(s.ent.values()))).focus()

    def set_server(s):
        r = form_dialog(s, "Servidor", [("url", "Endereço do servidor", False)], "Ex.: http://127.0.0.1:8765 ou o endereço do teu servidor online.")
        url = (r or {}).get("url", "").strip().rstrip("/")
        if not url.startswith(("http://", "https://")): return
        host = urllib.parse.urlparse(url).hostname or ""
        if url.startswith("http://") and host not in ("127.0.0.1", "localhost") and not messagebox.askyesno(
                "Ligação não cifrada", "Este endereço usa http:// — a palavra-passe viaja sem cifra pela internet.\n"
                "Usa um endereço https:// sempre que possível. Continuar mesmo assim?", icon="warning"): return
        s.app.api.base = url; setcfg("server_url", url); s.show("login")

    def resend(s):
        try: s.app.api.post("/resend", {"login": s.pending}, auth=False); s.say("Código reenviado.", True)
        except ApiError as e: s.say(e.msg)

    def finish(s, r, keep):
        api = s.app.api; api.token = r["token"]; api.keep(r["me"]); setcfg("session_token", api.token if keep else "")
        s.app.enter(r["me"])

    def submit(s):
        v = {k: (e.get() if "password" in k else e.get().strip()) for k, e in s.ent.items()}; api = s.app.api; m = s.mode
        try:
            if m == "login":
                if not v["login"] or not v["password"]: return s.say("Preenche o nome/email e a palavra-passe.")
                try: r = api.post("/login", {"login": v["login"], "password": v["password"]}, auth=False)
                except ApiError as e:
                    if not e.data.get("unverified"): raise
                    s.pending = v["login"]; return s.show("verify", e.msg, True)
                s.finish(r, bool(s.keep.get()))
            elif m == "register":
                if v["password"] != v["password2"]: return s.say("As palavras-passe não coincidem.")
                if len(v["password"]) < 6: return s.say("A palavra-passe precisa de 6+ caracteres.")
                api.post("/register", {k: v[k] for k in ("username", "email", "name", "surname", "password")}, auth=False)
                s.pending = v["username"]; s.show("verify", f"Conta criada! Enviámos um código para {v['email']}.", True)
            elif m == "verify":
                s.finish(api.post("/verify", {"login": s.pending, "code": v["code"]}, auth=False), True)
            elif m == "forgot":
                api.post("/forgot", {"email": v["email"]}, auth=False); s.pending = v["email"]
                s.show("reset", "Se o email existir, enviámos um código.", True)
            elif m == "reset":
                if v["password"] != v["password2"] or len(v["password"]) < 6: return s.say("As palavras-passe têm de coincidir e ter 6+ caracteres.")
                api.post("/reset", {"email": s.pending, "code": v["code"], "password": v["password"]}, auth=False)
                s.show("login", "Palavra-passe alterada. Já podes entrar.", True)
        except ApiError as e:
            s.say(e.msg)


# sessão de administração
class AdminShell(ctk.CTkFrame):
    KIND = {"post": "Publicação", "comment": "Comentário", "user": "Perfil"}

    def __init__(s, app):
        super().__init__(app.root, fg_color="transparent"); s.app = app
        top = ctk.CTkFrame(s, fg_color="transparent"); top.pack(fill="x", padx=24, pady=(18, 0))
        label(top, "🛡  Administração UniHelp", 26, "bold").pack(side="left"); button(top, "Terminar sessão", app.logout, "ghost").pack(side="right")
        label(s, "Sessão de administrador · moderação da comunidade", 13, color=TXT2).pack(anchor="w", padx=24)
        s.tv = ctk.CTkTabview(s, command=s.load); s.tv.pack(fill="both", expand=True, padx=20, pady=12)
        for n in ("Denúncias", "Utilizadores", "Resumo"): s.tv.add(n)
        s.q = ctk.CTkEntry(s.tv.tab("Utilizadores"), placeholder_text="🔍 Nome, @utilizador ou email (Enter)…", height=36, corner_radius=10)
        s.q.pack(fill="x", padx=6, pady=(4, 6)); s.q.bind("<Return>", lambda e: s.load())
        s.sc = {}
        for n in ("Denúncias", "Utilizadores", "Resumo"):
            s.sc[n] = ctk.CTkScrollableFrame(s.tv.tab(n), fg_color="transparent"); s.sc[n].pack(fill="both", expand=True)
        s.after(100, s.load)

    def load(s):
        n = s.tv.get(); clear(s.sc[n])
        try:
            if n == "Denúncias": s.reports(s.app.api.get("/admin/reports")["reports"])
            elif n == "Utilizadores": s.users(s.app.api.get("/admin/users?q=" + quote(s.q.get().strip()))["users"])
            else: s.summary(s.app.api.get("/admin/summary"))
        except ApiError as e: net_error(s.sc[n], e)

    def act(s, path, body, ask=None):
        if ask and not messagebox.askyesno("Confirmar", ask): return
        if warn(s.app.api.post, path, body) is not None: s.load()

    def reports(s, rs):
        p = s.sc["Denúncias"]
        if not rs: label(p, "Sem denúncias por tratar ✅", 14, color=TXT2).pack(pady=40)
        for r in rs:
            c = card(p); c.pack(fill="x", padx=4, pady=5); t = ctk.CTkFrame(c, fg_color="transparent"); t.pack(fill="x", padx=16, pady=(12, 0))
            label(t, f"{s.KIND[r['kind']]} · @{r['author']}", 15, "bold").pack(side="left")
            label(t, f"{r['n']} denúncia(s)", 12, "bold", RED if r["n"] >= 3 else AMBER).pack(side="right")
            estado = ("escondido (3+ denúncias)  " if r["hidden"] else "") + ("conta suspensa" if r["suspended"] else "")
            if estado.strip(): label(c, estado, 12, color=AMBER).pack(anchor="w", padx=16)
            label(c, r["content"], 14, wraplength=720, justify="left").pack(anchor="w", padx=16, pady=6)
            if r["reasons"].strip(" |"): label(c, "Motivos: " + r["reasons"], 12, color=TXT2, wraplength=720, justify="left").pack(anchor="w", padx=16)
            b = ctk.CTkFrame(c, fg_color="transparent"); b.pack(anchor="w", padx=14, pady=12); key = {"kind": r["kind"], "target": r["target"]}
            if r["kind"] != "user": button(b, "Remover conteúdo", lambda k=key: s.act("/admin/remove", k, "Remover este conteúdo definitivamente?"), "danger").pack(side="left", padx=2)
            button(b, "Ignorar denúncias" if r["kind"] == "user" or not r["hidden"] else "Repor e ignorar", lambda k=key: s.act("/admin/dismiss", k), "ghost").pack(side="left", padx=2)
            sus = r["suspended"]
            button(b, "Reativar conta" if sus else "Suspender conta", lambda a=r["author"], o=sus: s.act("/admin/suspend", {"username": a, "on": not o},
                   None if o else f"Suspender @{a}? Perde o acesso de imediato."), "ghost" if sus else "danger").pack(side="left", padx=2)

    def users(s, us):
        p = s.sc["Utilizadores"]
        if not us: label(p, "Nenhum utilizador encontrado.", 14, color=TXT2).pack(pady=40)
        for u in us:
            f = ctk.CTkFrame(p, fg_color=CARD, corner_radius=12); f.pack(fill="x", padx=4, pady=4); t = ctk.CTkFrame(f, fg_color="transparent")
            t.pack(side="left", fill="x", expand=True, padx=14, pady=10)
            label(t, f"{dname(u)}  ·  @{u['username']}", 14, "bold").pack(anchor="w")
            label(t, f"{u['email']}  ·  {u['posts']} publicação(ões)" + ("  ·  SUSPENSA" if u["suspended"] else ""), 12, color=RED if u["suspended"] else TXT2).pack(anchor="w")
            sus = u["suspended"]
            button(f, "Reativar" if sus else "Suspender", lambda n=u["username"], o=sus: s.act("/admin/suspend", {"username": n, "on": not o},
                   None if o else f"Suspender @{n}?"), "ghost" if sus else "danger", 110).pack(side="right", padx=12)

    def summary(s, d):
        p = s.sc["Resumo"]; r = ctk.CTkFrame(p, fg_color="transparent"); r.pack(fill="x", pady=10)
        for i, (k, name, col) in enumerate([("users", "Utilizadores", ACC), ("posts", "Publicações", GREEN), ("comments", "Comentários", GREEN),
                                            ("reports", "Denúncias por tratar", AMBER), ("hidden", "Escondidas", AMBER), ("suspended", "Contas suspensas", RED)]):
            r.columnconfigure(i % 3, weight=1, uniform="x"); c = card(r); c.grid(row=i // 3, column=i % 3, padx=6, pady=6, sticky="ew")
            label(c, str(d[k]), 34, "bold", col).pack(anchor="w", padx=18, pady=(14, 0)); label(c, name, 13, color=TXT2).pack(anchor="w", padx=18, pady=(0, 14))
        button(p, "Atualizar", s.load, "ghost").pack(anchor="w", padx=8)


# integração com o unihelp.py
def walk(w):
    for c in w.winfo_children():
        yield c; yield from walk(c)


def attach_badge(s):
    side = s.winfo_children()[0]; btn = s.nav["Notificações"]
    lab = ctk.CTkLabel(side, text="", width=22, height=22, corner_radius=11, fg_color=RED, text_color="white", font=F(11, "bold"))
    def setb(n):
        if n > 0: lab.configure(text=str(min(n, 99))); lab.place(in_=btn, relx=1, x=-12, rely=.5, anchor="e"); lab.lift()
        else: lab.place_forget()
    s.set_badge = setb
    def poll():
        try:
            if not s.winfo_exists(): return
            setb(s.app.api.get("/notifs/count")["count"])
        except ApiError: pass
        except tk.TclError: return
        s.after(45000, poll)
    s.after(1500, poll)


def attach_responsive(s):
    """Janela larga: menu lateral. Janela estreita: botão ☰ que abre o menu por cima."""
    side = s.winfo_children()[0]; st = {"narrow": False, "open": False}
    hamb = ctk.CTkButton(s, text="☰", width=40, height=36, fg_color=ACC, hover_color=ACC_H, font=F(18, "bold"))
    def toggle():
        if st["open"]: side.place_forget()
        else: side.place(x=0, y=0, relheight=1); side.lift()
        st["open"] = not st["open"]
    hamb.configure(command=toggle)
    def apply(_=None):
        if s.winfo_width() < 50: return
        narrow = s.winfo_width() < 900
        if narrow == st["narrow"]: return
        st["narrow"], st["open"] = narrow, False
        if narrow: side.pack_forget(); hamb.place(relx=1, x=-10, y=8, anchor="ne"); hamb.lift()
        else: side.place_forget(); hamb.place_forget(); side.pack(side="left", fill="y", before=s.body)
    orig = s.show
    def show(name):
        orig(name)
        if st["narrow"] and st["open"]: toggle()
    s.show = show; s.bind("<Configure>", apply); s.after(300, apply)


def sync_local_user(me):
    """O unihelp.py ainda lê o nome na tabela local 'user'; mantemo-la igual à conta online."""
    name = me.get("name") or me["username"]
    if M.rows("select 1 from user"): M.run("update user set name=?", (name,))
    else: M.run("insert into user(name,salt,hash,rec_salt,rec_hash) values(?,?,?,?,?)", (name, "00", "x", "00", "x"))


def install():
    A, S, P = M.App, M.Shell, M.Profile

    # App: contas online em vez da palavra-passe local
    def show_login(s, prefill="", msg=""):
        if not hasattr(s, "api"):
            s.api = Api(); s.me = None; me = s.api.resume(); s.api.on_expired = s.expired
            if me: return s.enter(me)
        if s.user and not prefill and s.me: prefill = s.me["username"]
        if s.user: s.api.forget()  # bloqueio automático / terminar sessão: pede a palavra-passe outra vez
        s.user = None; s.swap(AuthScreen(s, prefill, msg))

    def enter(s, me):
        s.me = me; sync_local_user(me); s.last = time.time()
        if me.get("avatar") and not AVATAR.exists():
            try: AVATAR.write_bytes(base64.b64decode(me["avatar"]))
            except Exception: pass
        if me["role"] == "admin": s.user = "admin"; return s.swap(AdminShell(s))
        s.user = me.get("name") or me["username"]; s.swap(S(s))
        if not s.rem: s.rem = True; s.root.after(600, s.startup)

    def expired(s):
        if not s.user or getattr(s, "_exp", False): return
        s._exp = True
        def go(): s._exp = False; s.user = None; s.api.forget(); s.show_login(msg="A tua sessão expirou. Entra novamente.")
        s.root.after(0, go)

    def logout(s):
        if isinstance(s.cur, S) and not s.cur.can_leave(): return
        s.api.logout(); s.show_login()

    def startup(s):
        m = M.recover_focus()
        if m: messagebox.showinfo("Sessão recuperada", f"A última sessão de foco foi interrompida. Registámos {m} min de estudo.")
        s.api.push_streak(M.habit_streak())
        messagebox.showinfo("UniHelp — o teu dia", M.summary()); s.reminders()

    init0 = A.__init__
    def app_init(s): init0(s); s.root.minsize(560, 560)
    A.__init__, A.show_login, A.enter, A.expired, A.logout, A.startup = app_init, show_login, enter, expired, logout, startup

    #  Menu lateral: novas páginas, selo vermelho e menu ☰
    shell0 = S.__init__
    def shell_init(s, app):
        last = cfg("last_page", "Painel"); shell0(s, app); side = s.winfo_children()[0]
        for b in s.nav.values(): b.configure(height=34); b.pack_configure(pady=1)
        for name, icon, cls in [("Comunidade", "💬", Community), ("Pessoas", "🔎", People), ("Notificações", "🔔", Notifications), ("Ajuda", "❓", Help)]:
            s.pages[name] = cls(s)
            s.nav[name] = ctk.CTkButton(side, text=f"  {icon}   {name}", anchor="w", height=34, corner_radius=10, fg_color="transparent", text_color=TXT,
                                        hover_color=CARD2, font=F(14, "bold"), command=lambda n=name: s.show(n))
            s.nav[name].pack(fill="x", padx=14, pady=1, before=s.nav["Perfil"])
        attach_badge(s); attach_responsive(s)
        if last in s.pages and last != s.current: s.show(last)
    S.__init__ = shell_init

    # Perfil: sobrenome, foto e dados públicos online, conta
    ref0, sp0, pick0, rem0 = P.refresh, P.save_profile, P.pick_photo, P.remove_photo
    def refresh(s):
        ref0(s); app = s.shell.app
        s.e_sur = M.fld(s.e_name.master.master, "Sobrenome", default=cfg("prof_surname", ""), width=150)
        for w in list(walk(s.sc)):
            if isinstance(w, ctk.CTkButton) and w.cget("text") == "Novo código de recuperação": w.destroy()
        c = card(s.sc); c.pack(fill="x", padx=4, pady=(0, 10)); me = app.me or {}
        label(c, "Conta", 17, "bold").pack(anchor="w", padx=18, pady=(16, 2))
        label(c, f"@{me.get('username', '')}  ·  {me.get('email', '')}", 13, color=TXT2).pack(anchor="w", padx=18)
        label(c, "A recuperação da palavra-passe é feita por email («Esqueci-me da palavra-passe» no ecrã de entrada).", 12, color=TXT2).pack(anchor="w", padx=18, pady=(4, 8))
        button(c, "Apagar a minha conta e todos os dados", s.delete_account, "danger").pack(anchor="w", padx=18, pady=(0, 16))
    def save_profile(s):
        name, sur, bio = s.e_name.get().strip(), s.e_sur.get().strip(), s.e_bio.get().strip(); setcfg("prof_surname", sur)
        sp0(s)
        if name: s.shell.app.api.update_profile(name=name, surname=sur, bio=bio)
    def pick_photo(s): pick0(s); s.shell.app.api.push_avatar()
    def remove_photo(s): rem0(s); s.shell.app.api.push_avatar()
    def change_pw(s):
        r = form_dialog(s, "Alterar palavra-passe", [("old", "Palavra-passe atual", True), ("new", "Nova palavra-passe", True), ("new2", "Confirmar nova palavra-passe", True)])
        if not r: return
        if len(r["new"]) < 6 or r["new"] != r["new2"]:
            return messagebox.showwarning("Dados inválidos", "A nova palavra-passe precisa de 6+ caracteres e de coincidir com a confirmação.")
        if warn(s.shell.app.api.post, "/me/password", {"old": r["old"], "new": r["new"]}) is not None: messagebox.showinfo("Palavra-passe", "Palavra-passe alterada.")
    def delete_account(s):
        app = s.shell.app
        r = form_dialog(s, "Apagar conta", [("pw", "Palavra-passe", True)], "Isto apaga a conta, as publicações, os comentários, os seguidores "
                        "e os dados guardados neste computador. Não pode ser desfeito.", "Apagar tudo")
        if not r or warn(app.api.post, "/me/delete", {"password": r["pw"]}) is None: return
        M.wipe_all(); app.api.forget(); app.user = None; app.me = None
        app.show_login(msg="Conta apagada. Até um dia!")
    P.refresh, P.save_profile, P.pick_photo, P.remove_photo, P.change_pw, P.delete_account = refresh, save_profile, pick_photo, remove_photo, change_pw, delete_account