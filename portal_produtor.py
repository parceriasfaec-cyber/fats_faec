"""
Portal do produtor: o produtor registra IATF/TETF dos PRÓPRIOS animais pelo
celular, por um link só dele (sem e-mail, sem senha), e não vê mais nada do
sistema.

  /p/<codigo>                     tela do produtor (registrar + meus registros)
  /p/<codigo>/api/salvar          envio dos registros (também da fila offline)
  /p/<codigo>/excluir/<id>        apagar um registro que ele fez por engano
  /manejo/links                   (equipe) gera e envia os links por WhatsApp

Portal do TÉCNICO (cadastra no lugar do produtor, pelo celular):

  /t/<codigo>                     o técnico digita o CPF do produtor
  /t/<codigo>/p/<produtor>        ficha completa só dos animais daquele produtor
  /manejo/tecnicos                (equipe) cadastra técnicos e envia o link de cada um

Regras de segurança:
  - o código do link é aleatório (não dá para adivinhar) e fica em
    produtores.token_acesso; "Gerar novo link" invalida o antigo;
  - o produtor só consegue registrar brincos dos animais ALOCADOS a ele, e a
    página só leva ao celular dele os brincos dele (nunca a lista geral);
  - só vê os registros dos animais dele.

Segunda cópia só para produtores: publique o mesmo repositório em outro projeto
do Vercel com a variável de ambiente MODO_PRODUTOR=1. Nessa cópia, qualquer
endereço que não seja do portal do produtor responde "não encontrado" — então
o endereço do sistema principal nunca precisa ser dado a um produtor.

Como ligar no app.py — antes do `if __name__ == "__main__":`:

    from portal_produtor import registrar_portal_produtor
    registrar_portal_produtor(app, get_connection, _erro_de_conexao)
"""

import json
import os
import re
import secrets
from datetime import timedelta

from flask import abort, flash, jsonify, redirect, render_template, request, url_for

from manejo_reprodutivo import (
    CAMPOS, achar_animal, contexto_formulario, datas_previstas, gravar_registro,
    hoje, ler_formulario, situacao, validar, valores_para_form,
)

MODO_PRODUTOR = os.environ.get("MODO_PRODUTOR", "").strip().lower() in ("1", "true", "sim", "yes")

MAX_REGISTROS_NA_TELA = 40
# O produtor usa a ficha completa (as 3 abas): preenche tudo o que a equipe preenche.
CAMPOS_DO_PRODUTOR = list(CAMPOS)


def _novo_token() -> str:
    return secrets.token_urlsafe(9)


def _so_digitos(texto) -> str:
    return re.sub(r"\D", "", texto or "")


def registrar_portal_produtor(app, get_connection, erro_de_conexao):

    # ---- na cópia só para produtores/técnicos, o resto do sistema vira 404 ----
    if MODO_PRODUTOR:
        liberados = {"static", "manejo_service_worker"}

        @app.before_request
        def _so_portal_do_produtor():
            ep = request.endpoint or ""
            if ep in liberados or ep.startswith("produtor_") or ep.startswith("tecnico_"):
                return None
            abort(404)

    def _conectar():
        try:
            return get_connection()
        except Exception as erro:
            if not erro_de_conexao(erro) and not isinstance(erro, RuntimeError):
                raise
            return None

    def _token_ok(token):
        return bool(token) and len(token) <= 64 and re.fullmatch(r"[A-Za-z0-9_-]+", token)

    def _produtor(conn, token):
        if not _token_ok(token):
            return None
        return conn.execute(
            "SELECT id, nome_produtor, nome_propriedade FROM produtores WHERE token_acesso = ?",
            (token,),
        ).fetchone()

    def _tecnico(conn, token):
        if not _token_ok(token):
            return None
        return conn.execute(
            "SELECT id, nome FROM tecnicos WHERE token_acesso = ? AND ativo",
            (token,),
        ).fetchone()

    # ---- "contexto": quem está usando e para onde cada botão leva ----------
    def _ctx_produtor(conn, token):
        prod = _produtor(conn, token)
        if not prod:
            return None
        return dict(
            quem="produtor", prod=prod, origem="produtor", registrado_por=None,
            dono="seus animais", rotulo_meus="Meus registros",
            u=dict(
                home=url_for("produtor_home", token=token),
                api=url_for("produtor_api_salvar", token=token),
                manifest=url_for("produtor_manifest", token=token),
                editar=lambda rid: url_for("produtor_editar", token=token, rid=rid),
                excluir=lambda rid: url_for("produtor_excluir", token=token, rid=rid),
                trocar=None,
            ),
        )

    def _ctx_tecnico(conn, token, pid):
        tec = _tecnico(conn, token)
        if not tec:
            return None
        prod = conn.execute(
            "SELECT id, nome_produtor, nome_propriedade FROM produtores WHERE id = ?", (pid,),
        ).fetchone()
        if not prod:
            return None
        return dict(
            quem="tecnico", prod=prod, tecnico=tec, origem="tecnico", registrado_por=tec["nome"],
            dono="animais deste produtor", rotulo_meus="Registros do produtor",
            u=dict(
                home=url_for("tecnico_produtor", token=token, pid=pid),
                api=url_for("tecnico_api_salvar", token=token, pid=pid),
                manifest=url_for("tecnico_manifest", token=token),
                editar=lambda rid: url_for("tecnico_editar", token=token, pid=pid, rid=rid),
                excluir=lambda rid: url_for("tecnico_excluir", token=token, pid=pid, rid=rid),
                trocar=url_for("tecnico_home", token=token),
            ),
        )

    # =================== ficha (igual para produtor e técnico) ===================
    def _animais_do_produtor(conn, pid):
        return [dict(r) for r in conn.execute(
            "SELECT id, brinco_faec, brinco_fazenda, peso FROM animais WHERE produtor_id = ? ORDER BY brinco_faec",
            (pid,),
        ).fetchall()]

    def _animais_js(animais):
        """Só os animais DO PRODUTOR seguem para o celular (para conferir mesmo sem sinal)."""
        return [
            {"id": a["id"], "faec": a["brinco_faec"] or "", "fazenda": a["brinco_fazenda"] or "",
             "peso": a["peso"] or "", "produtor": "", "municipio": ""}
            for a in animais
        ]

    def _tela(ctx, reg, animais, registros=None, registro_id=None):
        return render_template(
            "produtor_portal.html", ctx=ctx, u=ctx["u"], quem=ctx["quem"], produtor=ctx["prod"],
            tecnico=ctx.get("tecnico"), dono=ctx["dono"], rotulo_meus=ctx["rotulo_meus"],
            reg=reg, registro_id=registro_id, registros=registros or [],
            animais=_animais_js(animais), total_animais=len(animais), modo="produtor",
            **contexto_formulario(),
        )

    SEM_SINAL = ("Sem conexão com a internet. Tente de novo quando o sinal voltar.", 503)

    def _home(resolver):
        conn = _conectar()
        if conn is None:
            return SEM_SINAL
        try:
            ctx = resolver(conn)
            if not ctx:
                abort(404)
            pid = ctx["prod"]["id"]
            animais = _animais_do_produtor(conn, pid)
            linhas = conn.execute(
                "SELECT m.* FROM manejo_reprodutivo m JOIN animais a ON a.id = m.animal_id "
                "WHERE a.produtor_id = ? ORDER BY m.data_procedimento DESC, m.id DESC LIMIT ?",
                (pid, MAX_REGISTROS_NA_TELA),
            ).fetchall()
        except Exception as erro:
            if erro_de_conexao(erro):
                return SEM_SINAL
            raise
        finally:
            conn.close()

        ref = hoje()
        registros = []
        for r in linhas:
            r = dict(r)
            r["sit_texto"], r["sit_classe"] = situacao(r, ref)
            r["prev"] = datas_previstas(r["data_procedimento"], r["tipo_manejo"])
            r["pode_excluir"] = not r.get("dg1_resultado") and not r.get("dg2_resultado")
            registros.append(r)
        return _tela(ctx, {c: None for c in CAMPOS}, animais, registros=registros)

    def _editar(resolver, rid):
        """Abre um registro do produtor (ex.: para lançar o DG1/DG2 dias depois)."""
        conn = _conectar()
        if conn is None:
            return SEM_SINAL
        try:
            ctx = resolver(conn)
            if not ctx:
                abort(404)
            pid = ctx["prod"]["id"]
            animais = _animais_do_produtor(conn, pid)
            atual = conn.execute(
                "SELECT m.* FROM manejo_reprodutivo m JOIN animais a ON a.id = m.animal_id "
                "WHERE m.id = ? AND a.produtor_id = ?",
                (rid, pid),
            ).fetchone()
            if atual is None:
                abort(404)

            if request.method == "GET":
                return _tela(ctx, valores_para_form(dict(atual)), animais, registro_id=rid)

            dados = ler_formulario(request.form)
            erro_form = validar(dados)
            animal = achar_animal(dados["brinco"], animais) if not erro_form else None
            if not erro_form and not animal:
                erro_form = f"O brinco {dados['brinco']} não está entre os {ctx['dono']}."
            if erro_form:
                flash(erro_form, "error")
                return _tela(ctx, valores_para_form(dados), animais, registro_id=rid)

            dados["brinco"] = (animal.get("brinco_faec") or dados["brinco"]).strip().upper()
            sets = ", ".join(f"{c} = ?" for c in ["animal_id"] + CAMPOS_DO_PRODUTOR)
            conn.execute(
                f"UPDATE manejo_reprodutivo SET {sets}, atualizado_em = now() WHERE id = ?",
                [animal["id"]] + [dados[c] for c in CAMPOS_DO_PRODUTOR] + [rid],
            )
            conn.commit()
            destino = ctx["u"]["home"]
        except Exception as erro:
            if erro_de_conexao(erro):
                return "Sem conexão com a internet — as alterações não foram salvas.", 503
            raise
        finally:
            conn.close()
        return redirect(destino)

    def _api(resolver):
        corpo = request.get_json(silent=True) or {}
        itens = corpo.get("registros")
        if not isinstance(itens, list) or not itens or len(itens) > 100:
            return jsonify({"ok": False, "erro": "Envie de 1 a 100 registros."}), 400
        conn = _conectar()
        if conn is None:
            return jsonify({"ok": False, "erro": "Sem conexão com o banco."}), 503
        try:
            ctx = resolver(conn)
            if not ctx:
                return jsonify({"ok": False, "erro": "Link inválido."}), 404
            animais = _animais_do_produtor(conn, ctx["prod"]["id"])
            resultados = []
            for item in itens:
                bruto = item if isinstance(item, dict) else {}
                dados = ler_formulario({k: bruto.get(k) for k in CAMPOS_DO_PRODUTOR})
                erro_item = validar(dados)
                if not erro_item and dados["data_procedimento"] > hoje() + timedelta(days=1):
                    erro_item = "A data do procedimento não pode ser no futuro."
                if erro_item:
                    resultados.append({"status": "invalido", "mensagem": erro_item})
                    continue
                if not achar_animal(dados["brinco"], animais):
                    resultados.append({
                        "status": "nao_encontrado", "brinco": dados["brinco"],
                        "mensagem": f"O brinco {dados['brinco']} não está entre os {ctx['dono']}.",
                    })
                    continue
                status, existente_id, _ = gravar_registro(
                    conn, dados, animais, origem=ctx["origem"], registrado_por=ctx["registrado_por"])
                resultados.append({"status": status, "brinco": dados["brinco"], "id": existente_id})
            conn.commit()
        except Exception as erro:
            app.logger.exception("Falha ao gravar manejo (portal)")
            codigo = 503 if erro_de_conexao(erro) else 500
            return jsonify({"ok": False, "erro": "Não foi possível gravar agora."}), codigo
        finally:
            conn.close()
        return jsonify({"ok": True, "resultados": resultados})

    def _excluir(resolver, rid):
        conn = _conectar()
        if conn is None:
            return SEM_SINAL
        try:
            ctx = resolver(conn)
            if not ctx:
                abort(404)
            # só apaga registro de animal DO PRODUTOR e que ainda não tem diagnóstico
            conn.execute(
                "DELETE FROM manejo_reprodutivo WHERE id = ? AND dg1_resultado IS NULL AND dg2_resultado IS NULL "
                "AND animal_id IN (SELECT id FROM animais WHERE produtor_id = ?)",
                (rid, ctx["prod"]["id"]),
            )
            conn.commit()
            destino = ctx["u"]["home"]
        finally:
            conn.close()
        return redirect(destino)

    def _manifesto(inicio):
        manifesto = {
            "name": "Manejo reprodutivo",
            "short_name": "Manejo",
            "start_url": inicio,
            "scope": inicio,
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

    # =========================== lado do produtor ===========================
    def produtor_home(token):
        return _home(lambda conn: _ctx_produtor(conn, token))

    def produtor_editar(token, rid):
        return _editar(lambda conn: _ctx_produtor(conn, token), rid)

    def produtor_api_salvar(token):
        return _api(lambda conn: _ctx_produtor(conn, token))

    def produtor_excluir(token, rid):
        return _excluir(lambda conn: _ctx_produtor(conn, token), rid)

    def produtor_manifest(token):
        base = url_for("produtor_home", token=token) + "/"
        return _manifesto(base)

    app.add_url_rule("/p/<token>", "produtor_home", produtor_home, methods=["GET"], strict_slashes=False)
    app.add_url_rule("/p/<token>/registro/<int:rid>", "produtor_editar", produtor_editar, methods=["GET", "POST"])
    app.add_url_rule("/p/<token>/api/salvar", "produtor_api_salvar", produtor_api_salvar, methods=["POST"])
    app.add_url_rule("/p/<token>/excluir/<int:rid>", "produtor_excluir", produtor_excluir, methods=["POST"])
    app.add_url_rule("/p/<token>/manifest.webmanifest", "produtor_manifest", produtor_manifest, methods=["GET"])

    # =========================== lado do técnico ============================
    def tecnico_home(token):
        """Tela do CPF: o técnico digita o CPF do produtor e abre a ficha dele."""
        conn = _conectar()
        if conn is None:
            return SEM_SINAL
        escolha, erro_cpf, cpf_digitado = [], None, ""
        try:
            tec = _tecnico(conn, token)
            if not tec:
                abort(404)
            if request.method == "POST":
                cpf_digitado = (request.form.get("cpf") or "").strip()
                digitos = _so_digitos(cpf_digitado)
                if len(digitos) != 11:
                    erro_cpf = "O CPF precisa ter 11 números."
                else:
                    achados = conn.execute(
                        "SELECT id, nome_produtor, nome_propriedade, municipio FROM produtores "
                        "WHERE lpad(regexp_replace(coalesce(cpf, ''), '[^0-9]', '', 'g'), 11, '0') = ? "
                        "ORDER BY nome_produtor",
                        (digitos,),
                    ).fetchall()
                    if not achados:
                        erro_cpf = "Nenhum produtor encontrado com esse CPF."
                    elif len(achados) == 1:
                        return redirect(url_for("tecnico_produtor", token=token, pid=achados[0]["id"]))
                    else:
                        escolha = [dict(a) for a in achados]
        except Exception as erro:
            if erro_de_conexao(erro):
                return SEM_SINAL
            raise
        finally:
            conn.close()
        return render_template(
            "tecnico_cpf.html", token=token, tecnico=tec, erro_cpf=erro_cpf,
            cpf=cpf_digitado, escolha=escolha,
            manifest_url=url_for("tecnico_manifest", token=token),
        )

    def tecnico_produtor(token, pid):
        return _home(lambda conn: _ctx_tecnico(conn, token, pid))

    def tecnico_editar(token, pid, rid):
        return _editar(lambda conn: _ctx_tecnico(conn, token, pid), rid)

    def tecnico_api_salvar(token, pid):
        return _api(lambda conn: _ctx_tecnico(conn, token, pid))

    def tecnico_excluir(token, pid, rid):
        return _excluir(lambda conn: _ctx_tecnico(conn, token, pid), rid)

    def tecnico_manifest(token):
        return _manifesto(url_for("tecnico_home", token=token) + "/")

    app.add_url_rule("/t/<token>", "tecnico_home", tecnico_home, methods=["GET", "POST"], strict_slashes=False)
    app.add_url_rule("/t/<token>/p/<int:pid>", "tecnico_produtor", tecnico_produtor, methods=["GET"], strict_slashes=False)
    app.add_url_rule("/t/<token>/p/<int:pid>/registro/<int:rid>", "tecnico_editar", tecnico_editar, methods=["GET", "POST"])
    app.add_url_rule("/t/<token>/p/<int:pid>/api/salvar", "tecnico_api_salvar", tecnico_api_salvar, methods=["POST"])
    app.add_url_rule("/t/<token>/p/<int:pid>/excluir/<int:rid>", "tecnico_excluir", tecnico_excluir, methods=["POST"])
    app.add_url_rule("/t/<token>/manifest.webmanifest", "tecnico_manifest", tecnico_manifest, methods=["GET"])

    # =========================== lado da equipe ===========================
    if MODO_PRODUTOR:
        return

    def manejo_links():
        try:
            conn = get_connection()
        except Exception as erro:
            if not erro_de_conexao(erro) and not isinstance(erro, RuntimeError):
                raise
            return render_template("offline.html")
        try:
            busca = (request.args.get("q") or "").strip()
            params, filtro = [], ""
            if busca:
                filtro = "WHERE (p.nome_produtor ILIKE ? OR p.nome_propriedade ILIKE ? OR p.municipio ILIKE ?)"
                params = [f"%{busca}%"] * 3
            linhas = conn.execute(
                "SELECT p.id, p.nome_produtor, p.nome_propriedade, p.municipio, p.telefone, p.token_acesso, "
                "  (SELECT COUNT(*) FROM animais a WHERE a.produtor_id = p.id) AS qtd_animais, "
                "  (SELECT COUNT(*) FROM manejo_reprodutivo m JOIN animais a ON a.id = m.animal_id "
                "     WHERE a.produtor_id = p.id AND m.origem = 'produtor') AS qtd_registros "
                f"FROM produtores p {filtro} ORDER BY p.nome_produtor",
                params,
            ).fetchall()
        except Exception as erro:
            if erro_de_conexao(erro):
                return render_template("offline.html")
            raise
        finally:
            conn.close()
        produtores = []
        for r in linhas:
            r = dict(r)
            digitos = re.sub(r"\D", "", r.get("telefone") or "")
            if len(digitos) in (10, 11):
                digitos = "55" + digitos
            r["whatsapp"] = digitos if len(digitos) in (12, 13) else ""
            produtores.append(r)
        return render_template(
            "manejo_links.html", produtores=produtores, busca=busca,
            sem_link=sum(1 for r in produtores if not r["token_acesso"]),
        )

    def manejo_links_gerar():
        conn = get_connection()
        try:
            faltam = conn.execute("SELECT id FROM produtores WHERE token_acesso IS NULL").fetchall()
            for r in faltam:
                conn.execute("UPDATE produtores SET token_acesso = ? WHERE id = ?", (_novo_token(), r["id"]))
            conn.commit()
        finally:
            conn.close()
        flash(f"{len(faltam)} link(s) gerado(s).", "success")
        return redirect(url_for("manejo_links"))

    def manejo_links_renovar(pid):
        conn = get_connection()
        try:
            conn.execute("UPDATE produtores SET token_acesso = ? WHERE id = ?", (_novo_token(), pid))
            conn.commit()
        finally:
            conn.close()
        flash("Novo link gerado. O link antigo deixou de funcionar.", "success")
        return redirect(url_for("manejo_links"))

    app.add_url_rule("/manejo/links", "manejo_links", manejo_links, methods=["GET"])
    app.add_url_rule("/manejo/links/gerar", "manejo_links_gerar", manejo_links_gerar, methods=["POST"])
    app.add_url_rule("/manejo/links/<int:pid>/renovar", "manejo_links_renovar", manejo_links_renovar, methods=["POST"])

    # ---------------------- técnicos (cadastro e links) ----------------------
    def manejo_tecnicos():
        try:
            conn = get_connection()
        except Exception as erro:
            if not erro_de_conexao(erro) and not isinstance(erro, RuntimeError):
                raise
            return render_template("offline.html")
        try:
            linhas = conn.execute(
                "SELECT t.id, t.nome, t.telefone, t.token_acesso, t.ativo, "
                "  (SELECT COUNT(*) FROM manejo_reprodutivo m WHERE m.origem = 'tecnico' AND m.registrado_por = t.nome) AS qtd_registros "
                "FROM tecnicos t ORDER BY t.ativo DESC, t.nome"
            ).fetchall()
        except Exception as erro:
            if erro_de_conexao(erro):
                return render_template("offline.html")
            raise
        finally:
            conn.close()
        tecnicos = []
        for r in linhas:
            r = dict(r)
            digitos = _so_digitos(r.get("telefone"))
            if len(digitos) in (10, 11):
                digitos = "55" + digitos
            r["whatsapp"] = digitos if len(digitos) in (12, 13) else ""
            tecnicos.append(r)
        return render_template("manejo_tecnicos.html", tecnicos=tecnicos)

    def manejo_tecnicos_novo():
        nome = " ".join((request.form.get("nome") or "").split())[:80].title()
        telefone = (request.form.get("telefone") or "").strip()[:30] or None
        if not nome:
            flash("Informe o nome do técnico.", "error")
            return redirect(url_for("manejo_tecnicos"))
        conn = get_connection()
        try:
            igual = conn.execute("SELECT id FROM tecnicos WHERE lower(nome) = lower(?)", (nome,)).fetchone()
            if igual:
                flash("Já existe um técnico com esse nome. Use um nome diferente (ex.: com o sobrenome).", "error")
            else:
                conn.execute(
                    "INSERT INTO tecnicos (nome, telefone, token_acesso) VALUES (?, ?, ?)",
                    (nome, telefone, _novo_token()),
                )
                conn.commit()
                flash(f"Técnico {nome} cadastrado. O link dele está na lista.", "success")
        finally:
            conn.close()
        return redirect(url_for("manejo_tecnicos"))

    def manejo_tecnicos_renovar(tid):
        conn = get_connection()
        try:
            conn.execute("UPDATE tecnicos SET token_acesso = ? WHERE id = ?", (_novo_token(), tid))
            conn.commit()
        finally:
            conn.close()
        flash("Novo link gerado. O link antigo deixou de funcionar.", "success")
        return redirect(url_for("manejo_tecnicos"))

    def manejo_tecnicos_ativar(tid):
        conn = get_connection()
        try:
            conn.execute("UPDATE tecnicos SET ativo = NOT ativo WHERE id = ?", (tid,))
            conn.commit()
        finally:
            conn.close()
        return redirect(url_for("manejo_tecnicos"))

    app.add_url_rule("/manejo/tecnicos", "manejo_tecnicos", manejo_tecnicos, methods=["GET"])
    app.add_url_rule("/manejo/tecnicos/novo", "manejo_tecnicos_novo", manejo_tecnicos_novo, methods=["POST"])
    app.add_url_rule("/manejo/tecnicos/<int:tid>/renovar", "manejo_tecnicos_renovar", manejo_tecnicos_renovar, methods=["POST"])
    app.add_url_rule("/manejo/tecnicos/<int:tid>/ativar", "manejo_tecnicos_ativar", manejo_tecnicos_ativar, methods=["POST"])
