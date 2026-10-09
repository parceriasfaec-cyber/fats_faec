"""
Manejo reprodutivo (IATF / TETF): registro em campo de cada procedimento feito
numa matriz (receptora), com as datas previstas de DG1 / DG2 / parto, o
diagnóstico de gestação, a sexagem fetal, o corpo lúteo, o ECC e os dados da
doadora/embrião e do touro/sêmen.

Telas:
  /manejo                 lista dos registros, com filtro e pendências de DG
  /manejo/novo            novo registro (botão "Confirmar e próximo animal")
  /manejo/<id>            editar (é aqui que se lança o DG1/DG2 dias depois)
  /manejo/<id>/excluir    excluir (POST)

Como ligar no app.py — antes do `if __name__ == "__main__":`:

    from manejo_reprodutivo import registrar_manejo_reprodutivo
    registrar_manejo_reprodutivo(app, get_connection, _erro_de_conexao)

A tabela "FIV".manejo_reprodutivo é criada pelo init_db() (database.py) e
também está em supabase_schema.sql.
"""

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import flash, redirect, render_template, request, session, url_for


# ------------------------------------------------------------------
# Regras de negócio (ajuste aqui se o protocolo mudar)
# ------------------------------------------------------------------
DIAS_DG1 = 30   # 1º diagnóstico de gestação: data do procedimento + 30 dias
DIAS_DG2 = 60   # 2º diagnóstico (confirmação + sexagem): + 60 dias
# Duração da gestação usada na previsão de parto, por tipo de manejo.
# (Na TETF alguns técnicos contam ~277 dias, pois o embrião é transferido ~7
# dias depois do cio. Aqui os dois usam 284 dias, como no protótipo.)
DIAS_PARTO = {"IATF": 284, "TETF": 284}

FUSO = ZoneInfo("America/Fortaleza")

# Valores aceitos em cada campo de escolha (qualquer outro valor é descartado)
OPCOES = {
    "tipo_manejo": ["IATF", "TETF"],
    "dg1_resultado": ["PRENHE", "VAZIA", "REAVALIAR"],
    "dg2_resultado": ["PRENHE", "PERDA", "A CONFIRMAR"],
    "sexagem": ["FEMEA", "MACHO", "INDETERMINADA"],
    "corpo_luteo": ["DIREITO", "ESQUERDO", "SEM CL"],
    "ecc": ["2.0", "2.5", "3.0", "3.5", "4.0", "4.5"],
    "doadora_beta_caseina": ["A2A2", "A1A2", "A1A1"],
    "embriao_grau": ["GRAU 1", "GRAU 2", "GRAU 3"],
    "embriao_conservacao": ["FRESCO", "VITRIFICADO"],
    "semen_tipo": ["SEXADO FEMEA", "SEXADO MACHO", "CONVENCIONAL"],
}

RACAS_DOADORA = ["Gir Leiteiro", "Holandês", "Girolando 1/2", "Girolando 3/4", "Guzerá Leiteiro"]
RACAS_TOURO = ["Holandês (PO)", "Gir Leiteiro", "Girolando 5/8", "Guzerá"]

# Campos de texto livre; os marcados como MAIUSCULOS são gravados em caixa alta
CAMPOS_TEXTO = [
    "brinco", "dg1_obs", "dg2_obs", "embriao_obs",
    "doadora_nome", "embriao_codigo",
    "touro_nome", "touro_central", "botijao", "caneca", "rack", "semen_partida",
]
MAIUSCULOS = {
    "brinco", "doadora_nome", "embriao_codigo", "touro_nome", "touro_central",
    "botijao", "caneca", "rack", "semen_partida",
}
CAMPOS_RACA = {"doadora_raca": RACAS_DOADORA, "touro_raca": RACAS_TOURO}
CAMPOS_DATA = ["data_procedimento", "embriao_data_opu"]

# Tudo o que é gravado na tabela (fora id / animal_id / carimbos de tempo)
CAMPOS = (
    ["tipo_manejo", "data_procedimento"]
    + CAMPOS_TEXTO
    + list(CAMPOS_RACA)
    + [c for c in OPCOES if c != "tipo_manejo"]
    + ["embriao_data_opu"]
)

# O que é lembrado de um animal para o próximo (o técnico aplica o mesmo
# sêmen/doadora em vários animais seguidos). Brinco, DG, CL, ECC e o código
# do embrião começam em branco a cada animal.
CARREGAR_PARA_PROXIMO = [
    "tipo_manejo", "data_procedimento",
    "doadora_nome", "doadora_raca", "doadora_beta_caseina", "embriao_data_opu",
    "touro_nome", "touro_raca", "touro_central", "semen_tipo",
    "botijao", "caneca", "rack", "semen_partida",
]

MAX_LISTA = 300


# ------------------------------------------------------------------
# Funções puras (sem banco) — fáceis de testar
# ------------------------------------------------------------------
def hoje() -> date:
    return datetime.now(FUSO).date()


def datas_previstas(data_proc: date, tipo_manejo: str = "") -> dict:
    """DG1, DG2 e parto previstos a partir da data do procedimento."""
    if not data_proc:
        return {"dg1": None, "dg2": None, "parto": None}
    return {
        "dg1": data_proc + timedelta(days=DIAS_DG1),
        "dg2": data_proc + timedelta(days=DIAS_DG2),
        "parto": data_proc + timedelta(days=DIAS_PARTO.get(tipo_manejo, 284)),
    }


def situacao(reg: dict, ref: date):
    """Resume em que pé está o registro. Devolve (texto, classe_css)."""
    prev = datas_previstas(reg.get("data_procedimento"), reg.get("tipo_manejo"))
    dg1, dg2 = reg.get("dg1_resultado"), reg.get("dg2_resultado")

    if dg1 == "VAZIA":
        return "Vazia no DG1", "erro"
    if dg2 == "PERDA":
        return "Perda gestacional", "erro"
    if dg2 == "PRENHE":
        return "Prenhe confirmada", "ok"

    if dg1 in (None, "", "REAVALIAR"):
        quando, rotulo = prev["dg1"], "DG1"
        if dg1 == "REAVALIAR":
            rotulo = "Reavaliar DG1"
    else:  # DG1 prenhe, falta confirmar no DG2
        quando, rotulo = prev["dg2"], "DG2"
    if not quando:
        return "—", "neutro"
    if quando < ref:
        atraso = (ref - quando).days
        return f"{rotulo} atrasado {atraso}d", "erro"
    if quando == ref:
        return f"{rotulo} hoje", "aviso"
    return f"{rotulo} em {quando.strftime('%d/%m')}", "neutro"


def _data_ou_none(valor):
    valor = (valor or "").strip()
    if not valor:
        return None
    try:
        return datetime.strptime(valor, "%Y-%m-%d").date()
    except ValueError:
        return None


def ler_formulario(form) -> dict:
    """Lê o POST e devolve um dict com todos os CAMPOS já normalizados
    (vazio vira None; valores fora das opções são descartados)."""
    dados = {}
    for c in CAMPOS_TEXTO:
        v = (form.get(c) or "").strip()
        dados[c] = (v.upper() if c in MAIUSCULOS else v) or None
    for c, validos in CAMPOS_RACA.items():
        v = (form.get(c) or "").strip()
        dados[c] = v if v in validos else None
    for c, validos in OPCOES.items():
        v = (form.get(c) or "").strip()
        dados[c] = v if v in validos else None
    for c in CAMPOS_DATA:
        dados[c] = _data_ou_none(form.get(c))
    return dados


def validar(dados: dict):
    """Devolve a mensagem de erro, ou None se estiver tudo certo."""
    if not dados.get("brinco"):
        return "Informe o brinco da matriz."
    if not dados.get("tipo_manejo"):
        return "Escolha o tipo de manejo (IATF ou TETF)."
    if not dados.get("data_procedimento"):
        return "Informe a data do procedimento."
    return None


def _normalizar_brinco(valor) -> str:
    return "".join((valor or "").upper().split())


def _chaves_brinco(valor) -> set:
    """Formas equivalentes de um brinco ('0001' = '1', 'V 1452' = 'V1452')."""
    base = _normalizar_brinco(valor)
    if not base:
        return set()
    chaves = {base}
    if base.isdigit():
        chaves.add(str(int(base)))
    return chaves


def achar_animal(brinco: str, animais: list):
    """Procura o brinco digitado entre os animais cadastrados (brinco FAEC ou
    brinco da fazenda). Devolve o animal ou None."""
    procurado = _chaves_brinco(brinco)
    if not procurado:
        return None
    for a in animais:
        if procurado & (_chaves_brinco(a.get("brinco_faec")) | _chaves_brinco(a.get("brinco_fazenda"))):
            return a
    return None


# ------------------------------------------------------------------
# Rotas
# ------------------------------------------------------------------
def registrar_manejo_reprodutivo(app, get_connection, erro_de_conexao):

    def _abrir_conexao():
        """Devolve (conn, None) ou (None, resposta_offline)."""
        try:
            return get_connection(), None
        except Exception as erro:
            if not erro_de_conexao(erro) and not isinstance(erro, RuntimeError):
                raise
            return None, render_template("offline.html")

    def _animais(conn):
        linhas = conn.execute(
            "SELECT a.id, a.brinco_faec, a.brinco_fazenda, a.peso, "
            "       p.nome_produtor, p.nome_propriedade, p.municipio "
            "FROM animais a LEFT JOIN produtores p ON p.id = a.produtor_id "
            "ORDER BY a.brinco_faec"
        ).fetchall()
        return [dict(r) for r in linhas]

    def _contar_hoje(conn) -> int:
        r = conn.execute(
            "SELECT COUNT(*) AS n FROM manejo_reprodutivo "
            "WHERE (criado_em AT TIME ZONE 'America/Fortaleza')::date = ?",
            (hoje(),),
        ).fetchone()
        return r["n"] if r else 0

    def _tela_formulario(conn, reg, registro_id=None):
        animais = _animais(conn)
        animais_js = [
            {
                "id": a["id"],
                "faec": a["brinco_faec"] or "",
                "fazenda": a["brinco_fazenda"] or "",
                "peso": a["peso"] or "",
                "produtor": a["nome_produtor"] or "",
                "propriedade": a["nome_propriedade"] or "",
                "municipio": a["municipio"] or "",
            }
            for a in animais
        ]
        return render_template(
            "manejo_form.html",
            reg=reg, registro_id=registro_id, animais=animais_js,
            opcoes=OPCOES, racas_doadora=RACAS_DOADORA, racas_touro=RACAS_TOURO,
            dias_dg1=DIAS_DG1, dias_dg2=DIAS_DG2, dias_parto=DIAS_PARTO,
            registrados_hoje=_contar_hoje(conn),
        )

    def _valores_para_form(dados: dict) -> dict:
        """Converte datas para texto ISO (o que o <input type=date> espera)."""
        v = dict(dados)
        for c in CAMPOS_DATA:
            if isinstance(v.get(c), date):
                v[c] = v[c].isoformat()
        return v

    def _resolver_animal(conn, brinco):
        return achar_animal(brinco, _animais(conn))

    # ---------------- lista ----------------
    def manejo_lista():
        conn, offline = _abrir_conexao()
        if offline:
            return offline
        try:
            busca = (request.args.get("q") or "").strip()
            tipo = request.args.get("tipo") or ""
            filtro = request.args.get("filtro") or ""

            where, params = [], []
            if busca:
                where.append(
                    "(m.brinco ILIKE ? OR p.nome_produtor ILIKE ? OR m.touro_nome ILIKE ? "
                    "OR m.doadora_nome ILIKE ?)"
                )
                params += [f"%{busca}%"] * 4
            if tipo in OPCOES["tipo_manejo"]:
                where.append("m.tipo_manejo = ?")
                params.append(tipo)
            if filtro == "dg1":
                where.append("(m.dg1_resultado IS NULL OR m.dg1_resultado = 'REAVALIAR')")
            elif filtro == "dg2":
                where.append(
                    "(m.dg1_resultado = 'PRENHE' "
                    "AND (m.dg2_resultado IS NULL OR m.dg2_resultado = 'A CONFIRMAR'))"
                )
            elif filtro == "vazias":
                where.append("(m.dg1_resultado = 'VAZIA' OR m.dg2_resultado = 'PERDA')")
            elif filtro == "prenhes":
                where.append(
                    "(m.dg2_resultado = 'PRENHE' "
                    "OR (m.dg1_resultado = 'PRENHE' AND m.dg2_resultado IS NULL))"
                )
            sql_where = ("WHERE " + " AND ".join(where)) if where else ""

            linhas = conn.execute(
                "SELECT m.*, p.nome_produtor, p.municipio "
                "FROM manejo_reprodutivo m "
                "LEFT JOIN animais a ON a.id = m.animal_id "
                "LEFT JOIN produtores p ON p.id = a.produtor_id "
                f"{sql_where} "
                "ORDER BY m.data_procedimento DESC, m.id DESC "
                f"LIMIT {MAX_LISTA + 1}",
                params,
            ).fetchall()
            total_hoje = _contar_hoje(conn)
        except Exception as erro:
            if erro_de_conexao(erro):
                return render_template("offline.html")
            raise
        finally:
            conn.close()

        ref = hoje()
        registros = []
        for r in linhas[:MAX_LISTA]:
            r = dict(r)
            r["sit_texto"], r["sit_classe"] = situacao(r, ref)
            r["prev"] = datas_previstas(r["data_procedimento"], r["tipo_manejo"])
            registros.append(r)
        return render_template(
            "manejo_lista.html", registros=registros, cortado=len(linhas) > MAX_LISTA,
            max_lista=MAX_LISTA, busca=busca, tipo=tipo, filtro=filtro,
            registrados_hoje=total_hoje,
        )

    # ---------------- novo ----------------
    def manejo_novo():
        conn, offline = _abrir_conexao()
        if offline:
            return offline
        try:
            if request.method == "GET":
                reg = {c: None for c in CAMPOS}
                reg.update(session.get("manejo_carregar") or {})
                reg["data_procedimento"] = reg.get("data_procedimento") or hoje().isoformat()
                # ?brinco=XXX (vindo de outra tela) já preenche o brinco
                if request.args.get("brinco"):
                    reg["brinco"] = request.args["brinco"].strip().upper()
                return _tela_formulario(conn, reg)

            dados = ler_formulario(request.form)
            erro_form = validar(dados)
            if erro_form:
                flash(erro_form, "error")
                return _tela_formulario(conn, _valores_para_form(dados))

            animal = _resolver_animal(conn, dados["brinco"])
            animal_id = animal["id"] if animal else None

            # Evita gravar duas vezes o mesmo procedimento (toque duplo no botão)
            if animal_id is not None:
                duplicado = conn.execute(
                    "SELECT id FROM manejo_reprodutivo WHERE animal_id = ? "
                    "AND tipo_manejo = ? AND data_procedimento = ? LIMIT 1",
                    (animal_id, dados["tipo_manejo"], dados["data_procedimento"]),
                ).fetchone()
            else:
                duplicado = conn.execute(
                    "SELECT id FROM manejo_reprodutivo WHERE animal_id IS NULL AND brinco = ? "
                    "AND tipo_manejo = ? AND data_procedimento = ? LIMIT 1",
                    (dados["brinco"], dados["tipo_manejo"], dados["data_procedimento"]),
                ).fetchone()
            if duplicado:
                flash(
                    f"A matriz {dados['brinco']} já tem {dados['tipo_manejo']} registrado nessa data. "
                    "Nada foi gravado de novo — para ajustar, edite o registro existente.",
                    "aviso",
                )
                return redirect(url_for("manejo_editar", rid=duplicado["id"]))

            colunas = ["animal_id"] + CAMPOS
            conn.execute(
                f"INSERT INTO manejo_reprodutivo ({', '.join(colunas)}) "
                f"VALUES ({', '.join(['?'] * len(colunas))})",
                [animal_id] + [dados[c] for c in CAMPOS],
            )
            conn.commit()
        except Exception as erro:
            if erro_de_conexao(erro):
                flash("Sem conexão com a internet — nada foi gravado. Tente de novo quando o sinal voltar.", "error")
                return redirect(url_for("manejo_novo"))
            app.logger.exception("Falha ao gravar manejo reprodutivo")
            flash(f"Não foi possível gravar (nada foi salvo): {erro}", "error")
            return redirect(url_for("manejo_novo"))
        finally:
            conn.close()

        # Guarda o que se repete de um animal para o outro (tipo, data, touro...)
        carregar = _valores_para_form(dados)
        session["manejo_carregar"] = {c: carregar[c] for c in CARREGAR_PARA_PROXIMO if carregar.get(c)}
        aviso = "" if animal else " (brinco não encontrado no cadastro de animais)"
        flash(f"Matriz {dados['brinco']} registrada{aviso}. Próximo animal!", "success")
        return redirect(url_for("manejo_novo"))

    # ---------------- editar ----------------
    def manejo_editar(rid):
        conn, offline = _abrir_conexao()
        if offline:
            return offline
        try:
            atual = conn.execute("SELECT * FROM manejo_reprodutivo WHERE id = ?", (rid,)).fetchone()
            if atual is None:
                flash("Registro não encontrado.", "error")
                return redirect(url_for("manejo_lista"))

            if request.method == "GET":
                return _tela_formulario(conn, _valores_para_form(dict(atual)), registro_id=rid)

            dados = ler_formulario(request.form)
            erro_form = validar(dados)
            if erro_form:
                flash(erro_form, "error")
                return _tela_formulario(conn, _valores_para_form(dados), registro_id=rid)

            animal = _resolver_animal(conn, dados["brinco"])
            sets = ", ".join(f"{c} = ?" for c in ["animal_id"] + CAMPOS)
            conn.execute(
                f"UPDATE manejo_reprodutivo SET {sets}, atualizado_em = now() WHERE id = ?",
                [animal["id"] if animal else None] + [dados[c] for c in CAMPOS] + [rid],
            )
            conn.commit()
        except Exception as erro:
            if erro_de_conexao(erro):
                flash("Sem conexão com a internet — as alterações não foram salvas.", "error")
                return redirect(url_for("manejo_editar", rid=rid))
            app.logger.exception("Falha ao atualizar manejo reprodutivo")
            flash(f"Não foi possível salvar (nada foi alterado): {erro}", "error")
            return redirect(url_for("manejo_editar", rid=rid))
        finally:
            conn.close()

        flash(f"Registro da matriz {dados['brinco']} atualizado.", "success")
        return redirect(url_for("manejo_lista"))

    # ---------------- excluir ----------------
    def manejo_excluir(rid):
        conn, offline = _abrir_conexao()
        if offline:
            return offline
        try:
            conn.execute("DELETE FROM manejo_reprodutivo WHERE id = ?", (rid,))
            conn.commit()
        except Exception as erro:
            if erro_de_conexao(erro):
                flash("Sem conexão com a internet — o registro não foi excluído.", "error")
                return redirect(url_for("manejo_lista"))
            raise
        finally:
            conn.close()
        flash("Registro excluído.", "success")
        return redirect(url_for("manejo_lista"))

    def manifest_pwa():
        """Permite "instalar" o sistema na tela inicial do celular (abre
        direto no registro de manejo, em tela cheia, sem a barra do navegador)."""
        manifesto = {
            "name": "FATS - Manejo reprodutivo",
            "short_name": "Manejo",
            "start_url": url_for("manejo_novo"),
            "scope": "/",
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#efe6d2",
            "theme_color": "#2b2015",
            "icons": [
                {"src": url_for("static", filename="icone-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
                {"src": url_for("static", filename="icone-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
            ],
        }
        return app.response_class(json.dumps(manifesto), mimetype="application/manifest+json")

    app.add_url_rule("/manifest.webmanifest", "manifest_pwa", manifest_pwa, methods=["GET"])
    app.add_url_rule("/manejo", "manejo_lista", manejo_lista, methods=["GET"])
    app.add_url_rule("/manejo/novo", "manejo_novo", manejo_novo, methods=["GET", "POST"])
    app.add_url_rule("/manejo/<int:rid>", "manejo_editar", manejo_editar, methods=["GET", "POST"])
    app.add_url_rule("/manejo/<int:rid>/excluir", "manejo_excluir", manejo_excluir, methods=["POST"])
