from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import date, timedelta, datetime
import sqlite3, os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "troque-esta-chave-em-producao")
# Em hospedagem com disco persistente no Render, configure DATABASE_PATH=/var/data/merenda.db.
DB = os.environ.get("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "merenda.db"))
os.makedirs(os.path.dirname(os.path.abspath(DB)), exist_ok=True)
PERFIS = ("Administrador", "Secretaria", "Cozinheira", "Aluno")

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con

def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS usuarios (
          id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL,
          login TEXT NOT NULL UNIQUE, senha TEXT NOT NULL,
          tipo TEXT NOT NULL, matricula TEXT DEFAULT '', turma TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS fornecedores (
          id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL, contato TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS alimentos (
          id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL, categoria TEXT DEFAULT '',
          quantidade REAL NOT NULL DEFAULT 0, unidade TEXT NOT NULL, data_entrada TEXT NOT NULL,
          data_validade TEXT NOT NULL, quantidade_minima REAL NOT NULL DEFAULT 0,
          fornecedor_id INTEGER, FOREIGN KEY(fornecedor_id) REFERENCES fornecedores(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS movimentacoes (
          id INTEGER PRIMARY KEY AUTOINCREMENT, alimento_id INTEGER NOT NULL,
          tipo TEXT NOT NULL, quantidade REAL NOT NULL, data TEXT NOT NULL,
          usuario_id INTEGER, observacao TEXT DEFAULT '',
          FOREIGN KEY(alimento_id) REFERENCES alimentos(id),
          FOREIGN KEY(usuario_id) REFERENCES usuarios(id)
        );
        CREATE TABLE IF NOT EXISTS consumos (
          id INTEGER PRIMARY KEY AUTOINCREMENT, alimento_id INTEGER NOT NULL,
          quantidade_utilizada REAL NOT NULL, data TEXT NOT NULL, refeicao TEXT NOT NULL,
          usuario_id INTEGER, FOREIGN KEY(alimento_id) REFERENCES alimentos(id),
          FOREIGN KEY(usuario_id) REFERENCES usuarios(id)
        );
        CREATE TABLE IF NOT EXISTS cardapios (
          id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL,
          refeicao TEXT NOT NULL, descricao TEXT NOT NULL
        );
        """)
        # Compatibilidade com bancos criados pela versão anterior
        cols = [r["name"] for r in con.execute("PRAGMA table_info(usuarios)").fetchall()]
        if "matricula" not in cols:
            con.execute("ALTER TABLE usuarios ADD COLUMN matricula TEXT DEFAULT ''")
        if "turma" not in cols:
            con.execute("ALTER TABLE usuarios ADD COLUMN turma TEXT DEFAULT ''")
        # Migra os perfis antigos para os três perfis atuais.
        con.execute("UPDATE usuarios SET tipo='Administrador' WHERE tipo IN ('Responsável pela merenda','Direção/Coordenação')")
        if con.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 0:
            con.execute("INSERT INTO usuarios(nome,login,senha,tipo) VALUES(?,?,?,?)",
              ("Administrador", "admin", generate_password_hash("Merenda@123"), "Administrador"))

def login_required(f):
    @wraps(f)
    def w(*a, **kw):
        if "usuario_id" not in session: return redirect(url_for("login"))
        return f(*a, **kw)
    return w

def roles(*allowed):
    def deco(f):
        @wraps(f)
        @login_required
        def w(*a, **kw):
            if session.get("tipo") not in allowed:
                flash("Seu perfil não tem permissão para esta área.", "error")
                return redirect(url_for("index"))
            return f(*a, **kw)
        return w
    return deco

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        with db() as con:
            u = con.execute("SELECT * FROM usuarios WHERE login=?", (request.form.get("login","").strip(),)).fetchone()
        if u and check_password_hash(u["senha"], request.form.get("senha","")):
            session.update(usuario_id=u["id"], nome=u["nome"], tipo=u["tipo"], turma=u["turma"] or "")
            return redirect(url_for("index"))
        flash("Login ou senha incorretos.", "error")
    return render_template("login.html")

@app.route("/sair")
def sair():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def index():
    if session["tipo"] == "Aluno":
        hoje = date.today()
        inicio = hoje - timedelta(days=hoje.weekday())
        fim = inicio + timedelta(days=6)
        with db() as con:
            menus = con.execute("""SELECT * FROM cardapios WHERE data BETWEEN ? AND ?
                ORDER BY data, CASE refeicao WHEN 'Café da manhã' THEN 1 WHEN 'Almoço' THEN 2 WHEN 'Café da tarde' THEN 3 ELSE 4 END""",
                                (inicio.isoformat(), fim.isoformat())).fetchall()
        dias_semana = {r["data"]: ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"][datetime.strptime(r["data"], "%Y-%m-%d").weekday()] for r in menus}
        return render_template("aluno.html", menus=menus, inicio=inicio, fim=fim, dias_semana=dias_semana)
    hoje = date.today()
    limite = (hoje + timedelta(days=30)).isoformat()
    with db() as con:
        total = con.execute("SELECT COUNT(*) FROM alimentos").fetchone()[0]
        vencidos = con.execute("SELECT * FROM alimentos WHERE data_validade<? ORDER BY data_validade", (hoje.isoformat(),)).fetchall()
        vencendo = con.execute("SELECT * FROM alimentos WHERE data_validade>=? AND data_validade<=? ORDER BY data_validade", (hoje.isoformat(),limite)).fetchall()
        baixos = con.execute("SELECT * FROM alimentos WHERE quantidade<=quantidade_minima ORDER BY nome").fetchall()
        consumo = con.execute("SELECT COALESCE(SUM(quantidade_utilizada),0) FROM consumos").fetchone()[0]
        recentes = con.execute("""SELECT m.*,a.nome alimento,u.nome usuario FROM movimentacoes m
          JOIN alimentos a ON a.id=m.alimento_id LEFT JOIN usuarios u ON u.id=m.usuario_id ORDER BY m.id DESC LIMIT 6""").fetchall()
    return render_template("index.html", total=total, vencidos=vencidos, vencendo=vencendo, baixos=baixos,
                           consumo=consumo, recentes=recentes)

@app.route("/alimentos", methods=["GET","POST"])
@roles("Administrador", "Secretaria", "Cozinheira")
def alimentos():
    with db() as con:
        fornecedores=con.execute("SELECT * FROM fornecedores ORDER BY nome").fetchall()
        if request.method=="POST":
            try:
                nome=request.form["nome"].strip(); qtd=float(request.form["quantidade"]); minimo=float(request.form.get("quantidade_minima") or 0)
                if not nome or qtd<0 or minimo<0: raise ValueError()
                cur=con.execute("""INSERT INTO alimentos(nome,categoria,quantidade,unidade,data_entrada,data_validade,quantidade_minima,fornecedor_id)
                  VALUES(?,?,?,?,?,?,?,?)""",(nome,request.form.get("categoria",""),qtd,request.form["unidade"],request.form["data_entrada"],request.form["data_validade"],minimo,request.form.get("fornecedor_id") or None))
                if qtd: con.execute("INSERT INTO movimentacoes(alimento_id,tipo,quantidade,data,usuario_id,observacao) VALUES(?,?,?,?,?,?)",
                  (cur.lastrowid,"Entrada",qtd,request.form["data_entrada"],session["usuario_id"],"Saldo inicial"))
                flash("Alimento cadastrado com sucesso!","success"); return redirect(url_for("alimentos"))
            except (ValueError,KeyError): flash("Confira os campos informados.","error")
        rows=con.execute("SELECT a.*,f.nome fornecedor FROM alimentos a LEFT JOIN fornecedores f ON f.id=a.fornecedor_id ORDER BY a.nome").fetchall()
    return render_template("alimentos.html",alimentos=rows,fornecedores=fornecedores)

@app.route("/alimentos/<int:alimento_id>/excluir", methods=["POST"])
@roles("Administrador", "Secretaria", "Cozinheira")
def excluir_alimento(alimento_id):
    with db() as con:
        item = con.execute("SELECT id, nome FROM alimentos WHERE id=?", (alimento_id,)).fetchone()
        if not item:
            flash("Alimento não encontrado.", "error")
        else:
            con.execute("DELETE FROM movimentacoes WHERE alimento_id=?", (alimento_id,))
            con.execute("DELETE FROM consumos WHERE alimento_id=?", (alimento_id,))
            con.execute("DELETE FROM alimentos WHERE id=?", (alimento_id,))
            flash(f"Alimento {item['nome']} excluído.", "success")
    return redirect(url_for("alimentos"))

@app.route("/fornecedores", methods=["GET","POST"])
@roles("Administrador")
def fornecedores():
    with db() as con:
        if request.method=="POST":
            nome=request.form.get("nome","").strip()
            if nome:
                con.execute("INSERT INTO fornecedores(nome,contato) VALUES(?,?)",(nome,request.form.get("contato","")))
                flash("Fornecedor cadastrado!","success"); return redirect(url_for("fornecedores"))
            flash("Informe o nome.","error")
        rows=con.execute("SELECT * FROM fornecedores ORDER BY nome").fetchall()
    return render_template("fornecedores.html",fornecedores=rows)

@app.route("/usuarios", methods=["GET","POST"])
@roles("Administrador", "Secretaria")
def usuarios():
    with db() as con:
        if request.method=="POST":
            nome=request.form.get("nome","").strip(); login=request.form.get("login","").strip()
            senha=request.form.get("senha",""); tipo=request.form.get("tipo","")
            matricula=request.form.get("matricula","").strip(); turma=request.form.get("turma","").strip()
            perfil_atual = session.get("tipo")
            tipos_permitidos = PERFIS if perfil_atual == "Administrador" else ("Cozinheira", "Aluno")
            if nome and login and len(senha)>=8 and tipo in tipos_permitidos:
                try:
                    con.execute("INSERT INTO usuarios(nome,login,senha,tipo,matricula,turma) VALUES(?,?,?,?,?,?)",
                      (nome,login,generate_password_hash(senha),tipo,matricula,turma))
                    flash("Usuário cadastrado!","success"); return redirect(url_for("usuarios"))
                except sqlite3.IntegrityError: flash("Este login já está em uso.","error")
            else: flash("Seu perfil não pode criar esse tipo de usuário ou os campos estão inválidos. A senha precisa ter pelo menos 8 caracteres.","error")
        busca=request.args.get("q", "").strip()
        filtro_perfil = "" if session.get("tipo") == "Administrador" else " AND tipo IN ('Cozinheira','Aluno')"
        if busca:
            termo=f"%{busca}%"
            rows=con.execute("SELECT id,nome,login,tipo,matricula,turma FROM usuarios WHERE (nome LIKE ? OR login LIKE ? OR matricula LIKE ? OR turma LIKE ?)" + filtro_perfil + " ORDER BY tipo,nome",(termo,termo,termo,termo)).fetchall()
        else:
            rows=con.execute("SELECT id,nome,login,tipo,matricula,turma FROM usuarios WHERE 1=1" + filtro_perfil + " ORDER BY tipo,nome").fetchall()
    return render_template("usuarios.html",usuarios=rows,perfis=(PERFIS if session.get("tipo") == "Administrador" else ("Cozinheira", "Aluno")), busca=busca)

@app.route("/usuarios/<int:usuario_id>/excluir", methods=["POST"])
@roles("Administrador", "Secretaria")
def excluir_usuario(usuario_id):
    if usuario_id == session.get("usuario_id"):
        flash("Você não pode excluir o próprio usuário enquanto estiver conectado.", "error")
        return redirect(url_for("usuarios"))
    with db() as con:
        alvo = con.execute("SELECT id, nome, tipo FROM usuarios WHERE id=?", (usuario_id,)).fetchone()
        if not alvo:
            flash("Usuário não encontrado.", "error")
            return redirect(url_for("usuarios"))
        if session.get("tipo") == "Secretaria" and alvo["tipo"] not in ("Cozinheira", "Aluno"):
            flash("A Secretaria só pode excluir usuários da Cozinha e Alunos.", "error")
            return redirect(url_for("usuarios"))
        if alvo["tipo"] == "Administrador":
            total_admins = con.execute("SELECT COUNT(*) FROM usuarios WHERE tipo='Administrador'").fetchone()[0]
            if total_admins <= 1:
                flash("Não é possível excluir o último administrador.", "error")
                return redirect(url_for("usuarios"))
        # Mantém o histórico de movimentações e consumos, removendo apenas a associação ao usuário.
        con.execute("UPDATE movimentacoes SET usuario_id=NULL WHERE usuario_id=?", (usuario_id,))
        con.execute("UPDATE consumos SET usuario_id=NULL WHERE usuario_id=?", (usuario_id,))
        con.execute("DELETE FROM usuarios WHERE id=?", (usuario_id,))
    flash(f"Usuário {alvo['nome']} excluído com sucesso.", "success")
    return redirect(url_for("usuarios"))

@app.route("/movimentacoes", methods=["GET","POST"])
@roles("Cozinheira","Administrador")
def movimentacoes():
    with db() as con:
        if request.method=="POST":
            try:
                aid=int(request.form["alimento_id"]); qtd=float(request.form["quantidade"]); tipo=request.form["tipo"]
                item=con.execute("SELECT * FROM alimentos WHERE id=?",(aid,)).fetchone()
                if not item or qtd<=0 or tipo not in ("Entrada","Saída") or (tipo=="Saída" and qtd>item["quantidade"]):
                    flash("Confira o alimento e a quantidade disponível.","error")
                else:
                    saldo=item["quantidade"]+qtd if tipo=="Entrada" else item["quantidade"]-qtd
                    con.execute("UPDATE alimentos SET quantidade=? WHERE id=?",(saldo,aid))
                    con.execute("INSERT INTO movimentacoes(alimento_id,tipo,quantidade,data,usuario_id,observacao) VALUES(?,?,?,?,?,?)",
                      (aid,tipo,qtd,request.form["data"],session["usuario_id"],request.form.get("observacao","")))
                    flash("Movimentação registrada!","success"); return redirect(url_for("movimentacoes"))
            except (ValueError,KeyError): flash("Preencha os campos corretamente.","error")
        itens=con.execute("SELECT * FROM alimentos ORDER BY nome").fetchall()
        rows=con.execute("""SELECT m.*,a.nome alimento,u.nome usuario FROM movimentacoes m JOIN alimentos a ON a.id=m.alimento_id
          LEFT JOIN usuarios u ON u.id=m.usuario_id ORDER BY m.id DESC LIMIT 100""").fetchall()
    return render_template("movimentacoes.html",alimentos=itens,movimentacoes=rows,hoje=date.today().isoformat())

@app.route("/consumo", methods=["GET","POST"])
@roles("Cozinheira","Administrador")
def consumo():
    with db() as con:
        if request.method=="POST":
            try:
                aid=int(request.form["alimento_id"]); qtd=float(request.form["quantidade"])
                item=con.execute("SELECT * FROM alimentos WHERE id=?",(aid,)).fetchone()
                refeicao=request.form["refeicao"].strip()
                if not item or qtd<=0 or qtd>item["quantidade"] or not refeicao: flash("Confira o alimento e a quantidade disponível.","error")
                else:
                    con.execute("UPDATE alimentos SET quantidade=quantidade-? WHERE id=?",(qtd,aid))
                    con.execute("INSERT INTO consumos(alimento_id,quantidade_utilizada,data,refeicao,usuario_id) VALUES(?,?,?,?,?)",
                      (aid,qtd,request.form["data"],refeicao,session["usuario_id"]))
                    con.execute("INSERT INTO movimentacoes(alimento_id,tipo,quantidade,data,usuario_id,observacao) VALUES(?,?,?,?,?,?)",
                      (aid,"Saída",qtd,request.form["data"],session["usuario_id"],"Consumo: "+refeicao))
                    flash("Consumo registrado e estoque atualizado!","success"); return redirect(url_for("consumo"))
            except (ValueError,KeyError): flash("Preencha os campos corretamente.","error")
        itens=con.execute("SELECT * FROM alimentos ORDER BY nome").fetchall()
        rows=con.execute("""SELECT c.*,a.nome alimento,u.nome usuario FROM consumos c JOIN alimentos a ON a.id=c.alimento_id
          LEFT JOIN usuarios u ON u.id=c.usuario_id ORDER BY c.data DESC,c.id DESC""").fetchall()
    return render_template("consumo.html",alimentos=itens,consumos=rows,hoje=date.today().isoformat())

@app.route("/cardapio", methods=["GET","POST"])
@roles("Administrador", "Secretaria", "Cozinheira")
def cardapio():
    with db() as con:
        if request.method=="POST":
            data=request.form.get("data"); refeicao=request.form.get("refeicao","").strip(); descricao=request.form.get("descricao","").strip()
            if data and refeicao and descricao:
                con.execute("INSERT INTO cardapios(data,refeicao,descricao) VALUES(?,?,?)",(data,refeicao,descricao))
                flash("Item do cardápio salvo!","success"); return redirect(url_for("cardapio"))
            flash("Preencha todos os campos.","error")
        rows=con.execute("""SELECT * FROM cardapios ORDER BY data DESC,
          CASE refeicao WHEN 'Café da manhã' THEN 1 WHEN 'Almoço' THEN 2 WHEN 'Café da tarde' THEN 3 ELSE 4 END""").fetchall()
    return render_template("cardapio.html",cardapios=rows,can_edit=session["tipo"] in ("Administrador", "Secretaria", "Cozinheira"))

@app.route("/cardapio/<int:cardapio_id>/excluir", methods=["POST"])
@roles("Administrador", "Secretaria", "Cozinheira")
def excluir_cardapio(cardapio_id):
    with db() as con:
        item = con.execute("SELECT id FROM cardapios WHERE id=?", (cardapio_id,)).fetchone()
        if item:
            con.execute("DELETE FROM cardapios WHERE id=?", (cardapio_id,))
            flash("Item do cardápio excluído.", "success")
        else:
            flash("Item do cardápio não encontrado.", "error")
    return redirect(url_for("cardapio"))

@app.route("/relatorios")
@roles("Administrador")
def relatorios():
    with db() as con:
        estoque=con.execute("SELECT * FROM alimentos ORDER BY nome").fetchall()
        consumo=con.execute("""SELECT c.*,a.nome alimento,a.unidade FROM consumos c JOIN alimentos a ON a.id=c.alimento_id ORDER BY c.data DESC""").fetchall()
        total=con.execute("SELECT COALESCE(SUM(quantidade_utilizada),0) FROM consumos").fetchone()[0]
    return render_template("relatorios.html",estoque=estoque,consumos=consumo,total=total)

if __name__=="__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
