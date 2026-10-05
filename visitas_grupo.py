"""
Registro de visita em GRUPO: escolhe vários produtores de uma vez, informa a
data e o motivo uma única vez, e o sistema cria uma visita para cada um.

Regras (as mesmas do registro individual):
  - cada motivo só pode ser registrado uma vez por produtor; quem já tem o
    motivo escolhido é ignorado (e aparece no aviso);
  - a visita fica na etapa atual de cada produtor e conta como "visitado".

Como ligar no app.py — antes do `if __name__ == "__main__":`:

    from visitas_grupo import registrar_visitas_grupo
    registrar_visitas_grupo(app, get_connection, _erro_de_conexao)
"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import flash, redirect, render_template, request, url_for

from campos import TIPOS_VISITA


# Todo produtor já nasce com uma visita "Cadastro / Ficha inicial" (criada no
# cadastro, com a data em que foi cadastrado no sistema). Na visita em grupo com
# esse motivo, em vez de ignorar quem já tem, ATUALIZAMOS a data (e a observação,
# se informada) da visita de cadastro existente.
MOTIVO_CADASTRO = "Cadastro / Ficha inicial"


def _hoje_iso():
    return datetime.now(ZoneInfo("America/Fortaleza")).strftime("%Y-%m-%d")


def registrar_visitas_grupo(app, get_connection, erro_de_conexao):
    """Cria a rota GET/POST /visitas/grupo (endpoint `visita_grupo`)."""

    def _tela(conn, valores, selecionados):
        produtores = [dict(r) for r in conn.execute(
            "SELECT id, nome_produtor, nome_propriedade, municipio "
            "FROM produtores ORDER BY nome_produtor"
        ).fetchall()]
        motivos_por_produtor = {}
        for v in conn.execute("SELECT produtor_id, tipo_visita FROM visitas WHERE ativa = TRUE").fetchall():
            motivos_por_produtor.setdefault(v["produtor_id"], []).append(v["tipo_visita"])
        for p in produtores:
            p["motivos_json"] = json.dumps(motivos_por_produtor.get(p["id"], []), ensure_ascii=False)
            p["marcado"] = p["id"] in selecionados
        municipios = sorted({(p["municipio"] or "").strip().upper() for p in produtores if p["municipio"]})
        return render_template(
            "visita_grupo.html", produtores=produtores, municipios=municipios,
            tipos_visita=TIPOS_VISITA, valores=valores,
        )

    def visita_grupo():
        try:
            conn = get_connection()
        except Exception as erro:
            if not erro_de_conexao(erro) and not isinstance(erro, RuntimeError):
                raise
            return render_template("offline.html")

        try:
            if request.method == "GET":
                return _tela(conn, {"data_visita": _hoje_iso(), "tipo_visita": "", "observacoes": ""}, set())

            # ---------------- POST ----------------
            data_iso = (request.form.get("data_visita") or "").strip()
            motivo = (request.form.get("tipo_visita") or "").strip()
            observacoes = (request.form.get("observacoes") or "").strip()
            ids = set()
            for bruto in request.form.getlist("produtor_id"):
                if bruto.isdigit():
                    ids.add(int(bruto))
            valores = {"data_visita": data_iso, "tipo_visita": motivo, "observacoes": observacoes}

            erro_form = None
            try:
                data_br = datetime.strptime(data_iso, "%Y-%m-%d").strftime("%d/%m/%Y")
            except ValueError:
                data_br, erro_form = "", "Informe a data da visita."
            if not erro_form and motivo not in TIPOS_VISITA:
                erro_form = "Escolha o motivo da visita."
            if not erro_form and not ids:
                erro_form = "Marque pelo menos um produtor."
            if erro_form:
                flash(erro_form, "error")
                return _tela(conn, valores, ids)

            lista = list(ids)
            existentes = conn.execute(
                "SELECT id, nome_produtor, etapa_atual FROM produtores WHERE id = ANY(?)", (lista,)
            ).fetchall()
            # produtor_id -> True se o motivo já existe E está ativa; False se existe mas foi desmarcada
            ja_tem = {}
            for r in conn.execute(
                "SELECT produtor_id, ativa FROM visitas WHERE tipo_visita = ? AND produtor_id = ANY(?)",
                (motivo, lista),
            ).fetchall():
                ja_tem[r["produtor_id"]] = ja_tem.get(r["produtor_id"], False) or bool(r["ativa"])

            registrados, atualizados, reativados, ignorados = 0, 0, 0, []
            for p in existentes:
                if p["id"] in ja_tem:
                    if motivo == MOTIVO_CADASTRO:
                        conn.execute(
                            "UPDATE visitas SET data_visita = ?, "
                            "observacoes = COALESCE(NULLIF(?, ''), 'Cadastro inicial realizado'), ativa = TRUE "
                            "WHERE produtor_id = ? AND tipo_visita = ?",
                            (data_br, observacoes, p["id"], motivo),
                        )
                        atualizados += 1
                    elif not ja_tem[p["id"]]:
                        # a visita com esse motivo tinha sido desmarcada: reativa com a nova data
                        conn.execute(
                            "UPDATE visitas SET data_visita = ?, observacoes = ?, etapa = ?, ativa = TRUE "
                            "WHERE produtor_id = ? AND tipo_visita = ?",
                            (data_br, observacoes, p["etapa_atual"] or 1, p["id"], motivo),
                        )
                        reativados += 1
                    else:
                        ignorados.append(p["nome_produtor"] or f"#{p['id']}")
                    continue
                conn.execute(
                    "INSERT INTO visitas (produtor_id, data_visita, tipo_visita, observacoes, etapa, ativa) "
                    "VALUES (?, ?, ?, ?, ?, TRUE)",
                    (p["id"], data_br, motivo, observacoes, p["etapa_atual"] or 1),
                )
                registrados += 1
            conn.commit()  # tudo ou nada
        except Exception as erro:
            if erro_de_conexao(erro):
                if request.method == "GET":
                    return render_template("offline.html")
                flash("Sem conexão com a internet — a visita em grupo precisa de internet. "
                      "Nada foi registrado.", "error")
                return redirect(url_for("visita_grupo"))
            app.logger.exception("Falha ao registrar visita em grupo")
            flash(f"Não foi possível registrar (nada foi gravado): {erro}", "error")
            return redirect(url_for("visita_grupo"))
        finally:
            conn.close()

        if registrados or atualizados or reativados:
            partes = []
            if registrados:
                partes.append(f"registrada para {registrados} produtor(es)")
            if reativados:
                partes.append(f"reativada para {reativados} produtor(es) que tinham a visita desmarcada")
            if atualizados:
                partes.append(f"data atualizada para {atualizados} produtor(es) que já tinham o cadastro")
            flash(f"Visita \"{motivo}\" em {data_br}: " + " e ".join(partes) + ".", "success")
        else:
            flash("Nenhuma visita foi registrada.", "aviso")
        if ignorados:
            resumo = ", ".join(ignorados[:6]) + (f" e mais {len(ignorados) - 6}" if len(ignorados) > 6 else "")
            flash(f"{len(ignorados)} produtor(es) já tinham o motivo \"{motivo}\" e foram ignorados: {resumo}.", "aviso")
        return redirect(url_for("painel_visitas"))

    app.add_url_rule("/visitas/grupo", "visita_grupo", visita_grupo, methods=["GET", "POST"])
