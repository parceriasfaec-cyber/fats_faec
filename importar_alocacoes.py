"""
Importação de alocações de animais a partir de uma planilha do Excel (.xlsx).

A planilha diz QUAL animal (Brinco FAEC) fica com QUAL produtor (ID Produtor).
Colunas reconhecidas (pelo nome do cabeçalho, em qualquer ordem):
    ID Produtor  (obrigatória)  -> id do produtor no sistema
    Brinco FAEC  (obrigatória)  -> brinco FAEC do animal
    CPF          (opcional)     -> se vier, é conferido com o do produtor
    Produtor     (opcional)     -> só aparece na prévia

Só mexe em produtor_id / status / atualizado_em. Não altera brincos nem fotos.

Fluxo em duas etapas (nada é gravado na primeira):
    1) POST /animais/importar-alocacoes            -> lê, valida e mostra a prévia
    2) POST /animais/importar-alocacoes/confirmar  -> valida de novo e grava

Como ligar no sistema (app.py) — duas linhas, antes do
`if __name__ == "__main__":`:

    from importar_alocacoes import registrar_importacao_alocacoes
    registrar_importacao_alocacoes(app, get_connection, _erro_de_conexao, LIMITE_ANIMAIS_POR_PRODUTOR)
"""

import io
import json
import re
import unicodedata

_MAX_LINHAS_PROCURANDO_CABECALHO = 20

# nome do campo -> variações aceitas no cabeçalho (sem acento, minúsculas)
_CABECALHOS = {
    "pid": {"id produtor", "id do produtor", "produtor id", "produtor_id", "id_produtor"},
    "brinco": {"brinco faec", "brinco_faec", "brinco"},
    "cpf": {"cpf"},
    "nome": {"produtor", "nome", "nome do produtor", "nome produtor"},
}


def _sem_acento(texto):
    texto = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in texto if not unicodedata.combining(c))


def _chave_cabecalho(valor):
    if valor is None:
        return None
    limpo = re.sub(r"\s+", " ", _sem_acento(valor).strip().lower())
    for campo, variacoes in _CABECALHOS.items():
        if limpo in variacoes:
            return campo
    return None


def _normalizar_brinco(valor):
    """Texto do brinco sem espaços nas pontas. Se o Excel guardou como número
    (18 em vez de "0018"), devolve com zeros à esquerda (4 dígitos)."""
    if valor is None:
        return ""
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    if isinstance(valor, int):
        return str(valor).zfill(4)
    return str(valor).strip()


def _chave_brinco(valor):
    """Chave de comparação: sem espaço/traço/ponto e em maiúsculas."""
    return re.sub(r"[\s\-./]", "", str(valor or "")).upper()


def _cpf_digitos(valor):
    return re.sub(r"\D", "", str(valor or ""))


def ler_planilha_alocacoes(conteudo_xlsx):
    """Lê o .xlsx e devolve (linhas, ignoradas_sem_produtor, erro).

    `linhas` = dicts {linha, pid, brinco, cpf, nome} só das linhas que têm
    ID Produtor. Linhas sem ID (anotações soltas, brincos "sobrando" numa
    coluna qualquer, linhas vazias) são só contadas em `ignoradas_sem_produtor`."""
    from openpyxl import load_workbook

    try:
        planilha = load_workbook(io.BytesIO(conteudo_xlsx), data_only=True, read_only=True).active
        todas = list(planilha.iter_rows(values_only=True))
    except Exception:
        return [], 0, "Não foi possível ler o arquivo. Confira se é uma planilha do Excel (.xlsx)."

    mapa, linha_cab = None, None
    for i, linha in enumerate(todas[:_MAX_LINHAS_PROCURANDO_CABECALHO]):
        achados = {}
        for j, celula in enumerate(linha):
            campo = _chave_cabecalho(celula)
            if campo and campo not in achados:
                achados[campo] = j
        if "pid" in achados and "brinco" in achados:
            mapa, linha_cab = achados, i
            break

    if mapa is None:
        return [], 0, (
            "Não encontrei o cabeçalho da planilha. Ela precisa ter as colunas "
            "\"ID Produtor\" e \"Brinco FAEC\" (opcionais: CPF e Produtor)."
        )

    def celula(linha, campo):
        j = mapa.get(campo)
        return linha[j] if j is not None and j < len(linha) else None

    linhas, ignoradas = [], 0
    for i, linha in enumerate(todas[linha_cab + 1:], start=linha_cab + 2):
        bruto_pid = celula(linha, "pid")
        if bruto_pid is None or str(bruto_pid).strip() == "":
            if any(c is not None and str(c).strip() for c in linha):
                ignoradas += 1  # tem algo escrito, mas sem produtor: não é alocação
            continue
        linhas.append({
            "linha": i,
            "pid": bruto_pid,
            "brinco": _normalizar_brinco(celula(linha, "brinco")),
            "cpf": str(celula(linha, "cpf") or "").strip(),
            "nome": str(celula(linha, "nome") or "").strip(),
        })
    return linhas, ignoradas, None


def validar_alocacoes(linhas, produtores, animais, limite, permitir_mover=False):
    """Lógica pura (sem banco), fácil de testar.

    linhas    : saída de ler_planilha_alocacoes (ou a lista salva na prévia)
    produtores: {id: {"nome": str, "cpf": str, "qtd": int}}  (qtd = animais que já tem)
    animais   : lista de {"id", "brinco_faec", "produtor_id"}
    Devolve {"ok": [...], "problemas": [...], "ja_estavam": [...]}."""
    por_brinco = {}
    for a in animais:
        por_brinco.setdefault(_chave_brinco(a["brinco_faec"]), []).append(a)

    problemas, candidatos = [], []

    def erro(l, motivo):
        problemas.append({
            "linha": l["linha"], "nome": l.get("nome") or "", "pid": l.get("pid"),
            "brinco": l.get("brinco") or "", "motivo": motivo,
        })

    # 1) validações linha a linha
    for l in linhas:
        try:
            pid = int(str(l["pid"]).strip().split(".")[0])
        except (TypeError, ValueError):
            erro(l, "ID Produtor inválido")
            continue
        l = dict(l, pid=pid)
        if not l["brinco"]:
            erro(l, "linha sem Brinco FAEC")
            continue
        prod = produtores.get(pid)
        if prod is None:
            erro(l, f"produtor com ID {pid} não existe no sistema")
            continue
        l["nome"] = l["nome"] or prod["nome"]
        cpf_planilha = _cpf_digitos(l.get("cpf"))
        if cpf_planilha and _cpf_digitos(prod["cpf"]) and cpf_planilha != _cpf_digitos(prod["cpf"]):
            erro(l, "CPF da planilha não confere com o do produtor de mesmo ID")
            continue
        candidatos.append(l)

    # 2) brinco repetido para produtores diferentes na planilha: ninguém recebe
    donos = {}
    for l in candidatos:
        donos.setdefault(_chave_brinco(l["brinco"]), set()).add(l["pid"])
    conflitantes = {k for k, v in donos.items() if len(v) > 1}
    validos, vistos = [], set()
    for l in candidatos:
        chave = _chave_brinco(l["brinco"])
        if chave in conflitantes:
            outros = sorted(donos[chave] - {l["pid"]})
            erro(l, f"o mesmo brinco aparece também para o produtor ID {', '.join(map(str, outros))} (resolva na planilha)")
        elif (l["pid"], chave) in vistos:
            continue  # mesma linha repetida para o mesmo produtor
        else:
            vistos.add((l["pid"], chave))
            validos.append(l)

    # 3) casa com o animal no banco
    com_animal, ja_estavam = [], []
    for l in validos:
        achados = por_brinco.get(_chave_brinco(l["brinco"]), [])
        if not achados:
            erro(l, "brinco não cadastrado em Animais")
            continue
        if len(achados) > 1:
            erro(l, f"{len(achados)} animais com esse Brinco FAEC (ambíguo)")
            continue
        animal = achados[0]
        if animal["produtor_id"] == l["pid"]:
            ja_estavam.append(dict(l, animal_id=animal["id"]))
            continue
        if animal["produtor_id"] is not None and not permitir_mover:
            dono = produtores.get(animal["produtor_id"], {}).get("nome") or f"ID {animal['produtor_id']}"
            erro(l, f"animal já está alocado a {dono} (marque \"permitir mover\" para trocar)")
            continue
        com_animal.append(dict(l, animal_id=animal["id"], de=animal["produtor_id"]))

    # 4) limite por produtor (tudo ou nada por produtor, para não escolher no escuro)
    novos_por_produtor = {}
    for l in com_animal:
        novos_por_produtor.setdefault(l["pid"], []).append(l)
    ok = []
    for pid, itens in novos_por_produtor.items():
        ja_tem = produtores[pid]["qtd"]
        if ja_tem + len(itens) > limite:
            for l in itens:
                erro(l, f"estouraria o limite de {limite} animais (já tem {ja_tem}, entrariam {len(itens)})")
        else:
            ok.extend(itens)

    ok.sort(key=lambda l: (l["pid"], l["brinco"]))
    problemas.sort(key=lambda p: p["linha"])
    return {"ok": ok, "problemas": problemas, "ja_estavam": ja_estavam}


def _carregar_contexto(conn):
    produtores = {}
    for r in conn.execute(
        "SELECT p.id, p.nome_produtor, p.cpf, COUNT(a.id) AS qtd "
        "FROM produtores p LEFT JOIN animais a ON a.produtor_id = p.id "
        "GROUP BY p.id, p.nome_produtor, p.cpf"
    ).fetchall():
        produtores[r["id"]] = {"nome": r["nome_produtor"], "cpf": r["cpf"] or "", "qtd": r["qtd"]}
    animais = [
        {"id": r["id"], "brinco_faec": r["brinco_faec"] or "", "produtor_id": r["produtor_id"]}
        for r in conn.execute("SELECT id, brinco_faec, produtor_id FROM animais").fetchall()
    ]
    return produtores, animais


def registrar_importacao_alocacoes(app, get_connection, erro_de_conexao, limite):
    """Cria as rotas /animais/importar-alocacoes (+ /confirmar)."""
    from flask import flash, redirect, render_template, request, url_for

    def _sem_conexao():
        flash(
            "Sem conexão com a internet no momento — a importação precisa de internet. "
            "Nada foi alterado; tente de novo quando voltar.",
            "error",
        )
        return redirect(url_for("importar_alocacoes"))

    def importar_alocacoes():
        if request.method == "GET":
            return render_template("importar_alocacoes.html", previa=None, limite=limite)

        arquivo = request.files.get("planilha")
        if not arquivo or not arquivo.filename:
            flash("Escolha a planilha (.xlsx) com as alocações.", "error")
            return redirect(url_for("importar_alocacoes"))
        if not arquivo.filename.lower().endswith(".xlsx"):
            flash("O arquivo precisa ser uma planilha do Excel (.xlsx).", "error")
            return redirect(url_for("importar_alocacoes"))

        linhas, ignoradas, erro_leitura = ler_planilha_alocacoes(arquivo.read())
        if erro_leitura:
            flash(erro_leitura, "error")
            return redirect(url_for("importar_alocacoes"))
        if not linhas:
            flash("A planilha não tem nenhuma linha com ID Produtor.", "error")
            return redirect(url_for("importar_alocacoes"))

        permitir_mover = request.form.get("permitir_mover") == "1"
        conn = None
        try:
            conn = get_connection()
            produtores, animais = _carregar_contexto(conn)
        except Exception as e:
            if erro_de_conexao(e):
                return _sem_conexao()
            app.logger.exception("Falha ao validar planilha de alocações")
            flash(f"Não foi possível validar a planilha: {e}", "error")
            return redirect(url_for("importar_alocacoes"))
        finally:
            if conn is not None:
                conn.close()

        resultado = validar_alocacoes(linhas, produtores, animais, limite, permitir_mover)
        # O que vai para a etapa de confirmação (revalidado lá, nada é confiado).
        pares = [
            {"linha": l["linha"], "pid": l["pid"], "brinco": l["brinco"], "cpf": l.get("cpf", ""), "nome": l.get("nome", "")}
            for l in linhas
        ]
        return render_template(
            "importar_alocacoes.html",
            previa=resultado, ignoradas=ignoradas, permitir_mover=permitir_mover,
            pares_json=json.dumps(pares), limite=limite,
            produtores=produtores,
        )

    def confirmar_importar_alocacoes():
        try:
            linhas = json.loads(request.form.get("pares") or "[]")
        except ValueError:
            linhas = []
        if not linhas:
            flash("Nada para importar. Envie a planilha de novo.", "error")
            return redirect(url_for("importar_alocacoes"))
        permitir_mover = request.form.get("permitir_mover") == "1"

        conn = None
        try:
            conn = get_connection()
            produtores, animais = _carregar_contexto(conn)  # estado atual, não o da prévia
            resultado = validar_alocacoes(linhas, produtores, animais, limite, permitir_mover)
            for l in resultado["ok"]:
                condicao = "" if permitir_mover else " AND status = 'disponivel'"
                conn.execute(
                    "UPDATE animais SET produtor_id = ?, status = 'alocado', atualizado_em = now() "
                    f"WHERE id = ?{condicao}",
                    (l["pid"], l["animal_id"]),
                )
            conn.commit()  # tudo ou nada
        except Exception as e:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            if erro_de_conexao(e):
                return _sem_conexao()
            app.logger.exception("Falha ao importar alocações")
            flash(f"Não foi possível importar (nada foi gravado): {e}", "error")
            return redirect(url_for("importar_alocacoes"))
        finally:
            if conn is not None:
                conn.close()

        qtd = len(resultado["ok"])
        if qtd:
            flash(f"{qtd} animal(is) alocado(s) com sucesso.", "success")
        else:
            flash("Nenhum animal foi alocado.", "aviso")
        if resultado["ja_estavam"]:
            flash(f"{len(resultado['ja_estavam'])} já estavam com o produtor certo (nada a fazer).", "aviso")
        if resultado["problemas"]:
            flash(f"{len(resultado['problemas'])} linha(s) não foram importadas (revise a planilha).", "error")
        return redirect(url_for("animais"))

    app.add_url_rule(
        "/animais/importar-alocacoes", "importar_alocacoes", importar_alocacoes,
        methods=["GET", "POST"],
    )
    app.add_url_rule(
        "/animais/importar-alocacoes/confirmar", "confirmar_importar_alocacoes",
        confirmar_importar_alocacoes, methods=["POST"],
    )
