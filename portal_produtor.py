"""
Portal do produtor: o produtor registra IATF/TETF dos PRÓPRIOS animais pelo
celular, por um link só dele (sem e-mail, sem senha), e não vê mais nada do
sistema.

  /p/<codigo>                     tela do produtor (registrar + meus registros)
  /p/<codigo>/api/salvar          envio dos registros (também da fila offline)
  /p/<codigo>/excluir/<id>        apagar um registro que ele fez por engano
  /manejo/links                   (equipe) gera e envia os links por WhatsApp

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
    achar_animal, datas_previstas, gravar_registro, hoje, ler_formulario,
    situacao, validar,
)

MODO_PRODUTOR = os.environ.get("MODO_PRODUTOR", "").strip().lower() in ("1", "true", "sim", "yes")

# O produtor preenche só isto; o resto do registro fica em branco
CAMPOS_PRODUTOR = ["brinco", "tipo_manejo", "data_procedimento", "touro_nome"]
MAX_REGISTROS_NA_TELA = 40


def _novo_token() -> str:
    return secrets.token_urlsafe(9)


def registrar_portal_produtor(app, get_connection, erro_de_conexao):

    # ---- na cópia só para produtores, tudo que não for do portal vira 404 ----
    if MODO_PRODUTOR:
        liberados = {"static", "manejo_service_worker"}

        @app.before_request
        def _so_portal_do_produtor():
            ep = request.endpoint or ""
            if ep in liberados or ep.startswith("produtor_"):
                return None
            abort(404)

    def _conectar():
        try:
            return get_connection()
        except Exception as erro:
            if not erro_de_conexao(erro) and not isinstance(erro, RuntimeError):
                raise
            return None

    def _produtor(conn, token):
        if not token or len(token) > 64 or not re.fullmatch(r"[A-Za-z0-9_-]+", token):
            return None
        return conn.execute(
            "SELECT id, nome_produtor, nome_propriedade FROM produtores WHERE token_acesso = ?",
            (token,),
        ).fetchone()

    def _animais_do_produtor(conn, pid):
        return [dict(r) for r in conn.execute(
            "SELECT id, brinco_faec, brinco_fazenda FROM animais WHERE produtor_id = ? ORDER BY brinco_faec",
            (pid,),
        ).fetchall()]

    # =========================== lado do produtor ===========================
    def produtor_home(token):
        conn = _conectar()
        if conn is None:
            return "Sem conexão com a internet. Tente de novo quando o sinal voltar.", 503
        try:
            prod = _produtor(conn, token)
            if not prod:
                abort(404)
            animais = _animais_do_produtor(conn, prod["id"])
            linhas = conn.execute(
                "SELECT m.* FROM manejo_reprodutivo m JOIN animais a ON a.id = m.animal_id "
                "WHERE a.produtor_id = ? ORDER BY m.data_procedimento DESC, m.id DESC LIMIT ?",
                (prod["id"], MAX_REGISTROS_NA_TELA),
            ).fetchall()
        except Exception as erro:
            if erro_de_conexao(erro):
                return "Sem conexão com a internet. Tente de novo quando o sinal voltar.", 503
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
        # só os brincos DELE seguem para o celular (para conferir mesmo sem sinal)
        brincos = [
            {"faec": a["brinco_faec"] or "", "fazenda": a["brinco_fazenda"] or ""}
            for a in animais
        ]
        return render_template(
            "produtor_portal.html", token=token, produtor=prod, registros=registros,
            brincos=brincos, total_animais=len(animais),
        )

    def produtor_api_salvar(token):
        corpo = request.get_json(silent=True) or {}
        itens = corpo.get("registros")
        if not isinstance(itens, list) or not itens or len(itens) > 100:
            return jsonify({"ok": False, "erro": "Envie de 1 a 100 registros."}), 400
        conn = _conectar()
        if conn is None:
            return jsonify({"ok": False, "erro": "Sem conexão com o banco."}), 503
        try:
            prod = _produtor(conn, token)
            if not prod:
                return jsonify({"ok": False, "erro": "Link inválido."}), 404
            animais = _animais_do_produtor(conn, prod["id"])
            resultados = []
            for item in itens:
                bruto = item if isinstance(item, dict) else {}
                dados = ler_formulario({k: bruto.get(k) for k in CAMPOS_PRODUTOR})
                erro_item = validar(dados)
                if not erro_item and dados["data_procedimento"] > hoje() + timedelta(days=1):
                    erro_item = "A data do procedimento não pode ser no futuro."
                if erro_item:
                    resultados.append({"status": "invalido", "mensagem": erro_item})
                    continue
                if not achar_animal(dados["brinco"], animais):
                    resultados.append({
                        "status": "nao_encontrado", "brinco": dados["brinco"],
                        "mensagem": f"O brinco {dados['brinco']} não está entre os seus animais.",
                    })
                    continue
                status, existente_id, _ = gravar_registro(conn, dados, animais, origem="produtor")
                resultados.append({"status": status, "brinco": dados["brinco"], "id": existente_id})
            conn.commit()
        except Exception as erro:
            app.logger.exception("Falha ao gravar manejo do produtor")
            codigo = 503 if erro_de_conexao(erro) else 500
            return jsonify({"ok": False, "erro": "Não foi possível gravar agora."}), codigo
        finally:
            conn.close()
        return jsonify({"ok": True, "resultados": resultados})

    def produtor_excluir(token, rid):
        conn = _conectar()
        if conn is None:
            return "Sem conexão com a internet.", 503
        try:
            prod = _produtor(conn, token)
            if not prod:
                abort(404)
            # só apaga registro de animal DELE e que ainda não tem diagnóstico
            conn.execute(
                "DELETE FROM manejo_reprodutivo WHERE id = ? AND dg1_resultado IS NULL AND dg2_resultado IS NULL "
                "AND animal_id IN (SELECT id FROM animais WHERE produtor_id = ?)",
                (rid, prod["id"]),
            )
            conn.commit()
        finally:
            conn.close()
        return redirect(url_for("produtor_home", token=token))

    def produtor_manifest(token):
        manifesto = {
            "name": "Manejo reprodutivo",
            "short_name": "Manejo",
            "start_url": url_for("produtor_home", token=token) + "/",
            "scope": url_for("produtor_home", token=token) + "/",
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

    app.add_url_rule("/p/<token>", "produtor_home", produtor_home, methods=["GET"], strict_slashes=False)
    app.add_url_rule("/p/<token>/api/salvar", "produtor_api_salvar", produtor_api_salvar, methods=["POST"])
    app.add_url_rule("/p/<token>/excluir/<int:rid>", "produtor_excluir", produtor_excluir, methods=["POST"])
    app.add_url_rule("/p/<token>/manifest.webmanifest", "produtor_manifest", produtor_manifest, methods=["GET"])

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
