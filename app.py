import io
import json
import os
import re
import sys
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import psycopg2
import requests
from flask import (
    Flask, render_template, request, redirect, url_for, flash, send_file,
    send_from_directory, abort
)
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from database import get_connection, init_db, _base_dir
from pdf_generator import generate_pdf
from supabase_storage import enviar_bytes, excluir_arquivo, BUCKET_ANIMAIS
from campos import FIELDS, FIELD_LABELS, MUNICIPIOS_CEARA
from fila_offline import (
    adicionar_na_fila, listar_pendentes, contar_pendentes, remover_da_fila,
    FILA_FOTOS_DIR,
)


# Pasta onde as fotos dos produtores ficam salvas (ao lado do banco de
# dados, para nao se perder quando o programa roda como .exe)
# Pasta onde as fotos dos produtores ficam salvas. Em producao (Render),
# aponte a variavel de ambiente FOTOS_DIR para o disco persistente
# (ex: /var/data/fotos). Localmente, sem essa variavel, continua usando a
# pasta "fotos" do lado do proprio app.py (como sempre foi).
UPLOAD_DIR = Path(os.environ["FOTOS_DIR"]) if os.environ.get("FOTOS_DIR") else (_base_dir() / "fotos")
try:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    # Em hospedagens com disco somente-leitura (Vercel, por exemplo), essa
    # pasta local nao pode ser criada - sem problema, pois as fotos novas
    # vao direto para o Supabase Storage. Essa pasta so serve para exibir
    # fotos antigas, salvas localmente antes da migracao.
    pass
EXTENSOES_PERMITIDAS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


# Campos que devem ser sempre salvos em CAIXA ALTA
CAMPOS_MAIUSCULOS = {
    "nome_produtor", "nome_propriedade", "municipio",
    "tecnico_responsavel", "nome_tecnico",
}

# Campos que aceitam apenas numeros inteiros (sem letras nem simbolos)
CAMPOS_SOMENTE_NUMERO = {
    "cpf_tecnico",
    "membros_residentes", "vacas_lactacao", "vacas_secas",
    "novilhas", "touros", "qtd_receptoras",
}

# Campos numericos que podem ter casas decimais (usando virgula, padrao BR)
CAMPOS_DECIMAL = {
    "area_leite", "volume_diario", "produtividade_media",
    "capacidade_tanque", "estoque_seca", "area_palma", "area_capineira",
}


def _formatar_cpf(valor: str) -> str:
    """Aplica a mascara padrao de CPF (000.000.000-00), a partir de
    qualquer coisa que o usuario tenha digitado (com ou sem pontuacao)."""
    digitos = re.sub(r"\D", "", valor or "")[:11]
    if len(digitos) <= 3:
        return digitos
    if len(digitos) <= 6:
        return f"{digitos[0:3]}.{digitos[3:]}"
    if len(digitos) <= 9:
        return f"{digitos[0:3]}.{digitos[3:6]}.{digitos[6:]}"
    return f"{digitos[0:3]}.{digitos[3:6]}.{digitos[6:9]}-{digitos[9:]}"


def _formatar_cpf_ou_registro(valor: str) -> str:
    """Igual a _formatar_cpf, mas pro campo que aceita CPF OU registro
    profissional (que pode ter mais de 11 dígitos, ou um formato
    diferente). Se tiver até 11 dígitos, formata como CPF; se passar
    disso, mostra o valor exatamente como foi salvo (sem cortar nem
    forçar pontuação de CPF num número que não é CPF)."""
    digitos = re.sub(r"\D", "", valor or "")
    if not digitos or len(digitos) > 11:
        return valor or ""
    return _formatar_cpf(digitos)


def _formatar_telefone(valor: str) -> str:
    """Aplica mascara de telefone brasileiro: (00) 0000-0000 para fixo
    ou (00) 00000-0000 para celular, conforme a quantidade de digitos."""
    digitos = re.sub(r"\D", "", valor or "")[:11]
    if len(digitos) <= 2:
        return digitos
    if len(digitos) <= 6:
        return f"({digitos[0:2]}) {digitos[2:]}"
    if len(digitos) <= 10:
        return f"({digitos[0:2]}) {digitos[2:6]}-{digitos[6:]}"
    return f"({digitos[0:2]}) {digitos[2:7]}-{digitos[7:]}"


def _normalizar_data(valor: str) -> str:
    """Garante que a data de nascimento fique salva no formato dd/mm/aaaa."""
    valor = (valor or "").strip()
    if not valor:
        return ""
    # Ja esta no formato dd/mm/aaaa
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", valor):
        return valor
    # Formato vindo de <input type="date"> (aaaa-mm-dd)
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", valor)
    if m:
        aaaa, mm, dd = m.groups()
        return f"{dd}/{mm}/{aaaa}"
    # Usuario digitou so os numeros (ddmmaaaa)
    digitos = re.sub(r"\D", "", valor)
    if len(digitos) == 8:
        return f"{digitos[0:2]}/{digitos[2:4]}/{digitos[4:8]}"
    return valor


def _exibir_data(valor: str) -> str:
    """Converte datas antigas (aaaa-mm-dd) para dd/mm/aaaa na hora de exibir no formulario."""
    valor = (valor or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", valor)
    if m:
        aaaa, mm, dd = m.groups()
        return f"{dd}/{mm}/{aaaa}"
    return valor


def _foto_atual(pid) -> str:
    """Retorna o nome da foto ja salva para esse produtor (para nao perder
    a foto quando o formulario e reenviado sem escolher um novo arquivo)."""
    if pid is None:
        return ""
    conn = get_connection()
    row = conn.execute(
        "SELECT foto_produtor FROM produtores WHERE id = ?", (pid,)
    ).fetchone()
    conn.close()
    return (row["foto_produtor"] if row else "") or ""


def _resource_dir() -> Path:
    """Pasta onde estao templates/static, tanto rodando com 'python app.py'
    quanto rodando como .exe gerado pelo PyInstaller (modo --onefile)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).parent


_BASE = _resource_dir()
app = Flask(
    __name__,
    template_folder=str(_BASE / "templates"),
    static_folder=str(_BASE / "static"),
)
app.secret_key = os.environ.get("SECRET_KEY", "troque-esta-chave-em-producao")
app.jinja_env.filters["mascara_cpf"] = _formatar_cpf
app.jinja_env.filters["mascara_cpf_ou_registro"] = _formatar_cpf_ou_registro
app.jinja_env.filters["mascara_telefone"] = _formatar_telefone


@app.route("/fotos/<path:nome>")
def foto(nome):
    return send_from_directory(str(UPLOAD_DIR), nome)


@app.context_processor
def _injetar_helpers():
    def foto_url(nome):
        """Resolve a URL da foto: se ja for uma URL do Supabase Storage
        (fotos novas), usa direto; se for so um nome de arquivo (fotos
        antigas, salvas localmente antes da migracao), busca na rota
        local /fotos/<nome>."""
        if not nome:
            return ""
        if nome.startswith("http://") or nome.startswith("https://"):
            return nome
        return url_for("foto", nome=nome)
    return dict(foto_url=foto_url, qtd_pendentes_offline=contar_pendentes())

# Campos considerados na checagem de "ficha completa". A foto fica de fora
# porque nem sempre é possível tirar foto do produtor na hora da visita.
FIELDS_OBRIGATORIOS = [f for f in FIELDS if f != "foto_produtor"]


def _campos_faltando(produtor) -> list:
    """Devolve a lista de campos obrigatorios que ainda estao vazios
    nesse cadastro."""
    faltando = []
    for f in FIELDS_OBRIGATORIOS:
        valor = produtor.get(f) or ""
        if not str(valor).strip():
            faltando.append(f)
    return faltando


def _parse_coordenada(valor: str):
    """Converte texto de latitude/longitude (que pode vir com virgula ou
    ponto) para float. Devolve None se nao for um numero valido."""
    valor = (valor or "").strip().replace(",", ".")
    if not valor:
        return None
    try:
        return float(valor)
    except ValueError:
        return None


@app.route("/mapa")
def mapa():
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM produtores ORDER BY nome_produtor"
    ).fetchall()
    conn.close()

    pontos = []
    sem_coordenadas = 0
    # Contagem por municipio, para os cartoes do topo acompanharem o filtro
    municipio_stats = {}
    for row in rows:
        produtor = dict(row)
        municipio = produtor.get("municipio") or "-"
        stats = municipio_stats.setdefault(
            municipio, {"total": 0, "no_mapa": 0, "sem_coordenadas": 0}
        )
        stats["total"] += 1

        lat = _parse_coordenada(produtor.get("latitude"))
        lon = _parse_coordenada(produtor.get("longitude"))
        if lat is None or lon is None:
            sem_coordenadas += 1
            stats["sem_coordenadas"] += 1
            continue
        stats["no_mapa"] += 1
        pontos.append({
            "id": produtor["id"],
            "nome_produtor": produtor.get("nome_produtor") or "-",
            "nome_propriedade": produtor.get("nome_propriedade") or "-",
            "municipio": municipio,
            "tecnico_responsavel": produtor.get("tecnico_responsavel") or "-",
            "lat": lat,
            "lon": lon,
        })

    return render_template(
        "mapa.html",
        pontos=pontos,
        pontos_json=json.dumps(pontos, ensure_ascii=False),
        total=len(rows),
        qtd_no_mapa=len(pontos),
        sem_coordenadas=sem_coordenadas,
        municipio_stats_json=json.dumps(municipio_stats, ensure_ascii=False),
    )


@app.route("/dashboard")
def dashboard():
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM produtores ORDER BY nome_produtor"
    ).fetchall()
    conn.close()

    total = len(rows)
    completos = []
    incompletos = []
    for row in rows:
        produtor = dict(row)
        faltando = _campos_faltando(produtor)
        if faltando:
            incompletos.append({
                "id": produtor["id"],
                "nome_produtor": produtor.get("nome_produtor") or "-",
                "municipio": produtor.get("municipio") or "-",
                "tecnico_responsavel": produtor.get("tecnico_responsavel") or "-",
                "qtd_faltando": len(faltando),
                "labels_faltando": [FIELD_LABELS.get(f, f) for f in faltando],
            })
        else:
            completos.append(produtor)

    # Ordena quem tem mais campos faltando primeiro
    incompletos.sort(key=lambda p: p["qtd_faltando"], reverse=True)

    qtd_completos = len(completos)
    qtd_incompletos = len(incompletos)
    pct_completos = round((qtd_completos / total) * 100) if total else 0

    return render_template(
        "dashboard.html",
        total=total,
        qtd_completos=qtd_completos,
        qtd_incompletos=qtd_incompletos,
        pct_completos=pct_completos,
        incompletos=incompletos,
    )


@app.route("/")
def index():
    busca = request.args.get("q", "").strip()
    municipio_filtro = request.args.get("municipio", "").strip()
    ateg_filtro = request.args.get("ateg", "").strip()  # "Sim", "Não" ou "" (todos)
    try:
        pagina = int(request.args.get("pagina", 1))
    except ValueError:
        pagina = 1
    pagina = max(1, pagina)
    POR_PAGINA = 20

    conn = get_connection()

    condicoes = []
    params = []
    if busca:
        like = f"%{busca}%"
        condicoes.append(
            "(p.nome_produtor ILIKE ? OR p.cpf ILIKE ? OR p.municipio ILIKE ? OR p.nome_propriedade ILIKE ?)"
        )
        params += [like, like, like, like]
    if municipio_filtro:
        condicoes.append("p.municipio = ?")
        params.append(municipio_filtro)
    if ateg_filtro in ("Sim", "Não"):
        condicoes.append("p.assistido_ateg = ?")
        params.append(ateg_filtro)
    where_sql = ("WHERE " + " AND ".join(condicoes)) if condicoes else ""

    todos = conn.execute(
        f"""SELECT p.*, COUNT(a.id) AS qtd_animais
           FROM produtores p
           LEFT JOIN animais a ON a.produtor_id = p.id
           {where_sql}
           GROUP BY p.id
           ORDER BY p.nome_produtor""",
        params,
    ).fetchall()

    # Cards de resumo — calculados sobre TODOS os produtores que batem com
    # a busca/filtro atual (não só os da página exibida na tabela).
    total_filtrado = len(todos)
    qtd_assistidos_ateg = sum(1 for p in todos if (p["assistido_ateg"] or "") == "Sim")
    pct_assistidos_ateg = round(qtd_assistidos_ateg / total_filtrado * 100) if total_filtrado else 0
    qtd_em_meta = sum(1 for p in todos if p["qtd_animais"] >= LIMITE_ANIMAIS_POR_PRODUTOR)
    pct_em_meta = round(qtd_em_meta / total_filtrado * 100) if total_filtrado else 0
    qtd_sem_foto = sum(1 for p in todos if not p["foto_produtor"])

    total_paginas = max(1, -(-total_filtrado // POR_PAGINA))  # arredonda pra cima
    pagina = min(pagina, total_paginas)
    inicio = (pagina - 1) * POR_PAGINA
    produtores_pagina = todos[inicio:inicio + POR_PAGINA]

    # Lista de municípios pro filtro: sempre todos os que já têm cadastro
    # (não só os da busca atual), pra dar pra trocar de filtro livremente.
    municipios_disponiveis = [
        m["municipio"] for m in conn.execute(
            "SELECT DISTINCT municipio FROM produtores WHERE municipio IS NOT NULL AND municipio <> '' ORDER BY municipio"
        ).fetchall()
    ]

    conn.close()
    return render_template(
        "list.html", produtores=produtores_pagina, busca=busca,
        limite_animais=LIMITE_ANIMAIS_POR_PRODUTOR,
        municipio_filtro=municipio_filtro, ateg_filtro=ateg_filtro,
        municipios_disponiveis=municipios_disponiveis,
        pagina=pagina, total_paginas=total_paginas, total_filtrado=total_filtrado,
        por_pagina=POR_PAGINA, inicio_pagina=inicio,
        qtd_assistidos_ateg=qtd_assistidos_ateg, pct_assistidos_ateg=pct_assistidos_ateg, pct_em_meta=pct_em_meta,
        qtd_sem_foto=qtd_sem_foto,
    )


@app.route("/ficha/<int:pid>")
def ficha(pid):
    conn = get_connection()
    row = conn.execute("SELECT * FROM produtores WHERE id = ?", (pid,)).fetchone()
    conn.close()
    if row is None:
        abort(404)
    produtor = dict(row)
    produtor["data_nascimento"] = _exibir_data(produtor.get("data_nascimento"))
    produtor["cpf"] = _formatar_cpf(produtor.get("cpf"))
    produtor["telefone"] = _formatar_telefone(produtor.get("telefone"))
    return render_template("ficha.html", produtor=produtor)


@app.route("/novo", methods=["GET", "POST"])
def novo():
    if request.method == "POST":
        ok, dados = _salvar(None)
        if ok:
            return redirect(url_for("index"))
        return render_template("form.html", produtor=dados, municipios=MUNICIPIOS_CEARA)
    return render_template("form.html", produtor=None, municipios=MUNICIPIOS_CEARA)


@app.route("/editar/<int:pid>", methods=["GET", "POST"])
def editar(pid):
    conn = get_connection()
    row = conn.execute("SELECT * FROM produtores WHERE id = ?", (pid,)).fetchone()
    conn.close()
    if row is None:
        abort(404)
    if request.method == "POST":
        ok, dados = _salvar(pid)
        if ok:
            return redirect(url_for("index"))
        return render_template("form.html", produtor=dados, municipios=MUNICIPIOS_CEARA)
    produtor = dict(row)
    produtor["data_nascimento"] = _exibir_data(produtor.get("data_nascimento"))
    produtor["cpf"] = _formatar_cpf(produtor.get("cpf"))
    produtor["telefone"] = _formatar_telefone(produtor.get("telefone"))
    return render_template("form.html", produtor=produtor, municipios=MUNICIPIOS_CEARA)


def _erro_de_conexao(e: Exception) -> bool:
    """Detecta se a excecao e por falta de internet/conexao com o Supabase
    (para diferenciar de outros bugs de verdade, que devem continuar
    aparecendo normalmente como erro)."""
    if isinstance(e, (psycopg2.OperationalError, requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
        return True
    # psycopg2 as vezes embrulha o erro de conexao dentro de outro tipo -
    # olhamos tambem o texto da mensagem, por seguranca.
    texto = str(e).lower()
    return any(p in texto for p in [
        "could not connect", "connection refused", "network is unreachable",
        "timeout expired", "temporary failure in name resolution",
        "failed to establish a new connection",
    ])


def _salvar(pid):
    # A foto e lida uma unica vez aqui (antes de mais nada), para podermos
    # tanto enviar ao Supabase Storage quanto, se precisar, guardar na fila
    # offline local.
    arquivo_foto = request.files.get("foto_produtor")
    foto_bytes = None
    foto_nome_original = ""
    foto_mimetype = ""
    if arquivo_foto and arquivo_foto.filename:
        foto_bytes = arquivo_foto.read()
        foto_nome_original = arquivo_foto.filename
        foto_mimetype = arquivo_foto.mimetype or ""

    dados = {}
    for f in FIELDS:
        if f == "foto_produtor":
            continue  # tratada a parte, acima
        valor = request.form.get(f, "").strip()
        if f in CAMPOS_MAIUSCULOS:
            valor = valor.upper()
        elif f == "cpf":
            valor = _formatar_cpf(valor)
        elif f == "telefone":
            valor = _formatar_telefone(valor)
        elif f in CAMPOS_SOMENTE_NUMERO:
            valor = re.sub(r"\D", "", valor)
        elif f in CAMPOS_DECIMAL:
            valor = re.sub(r"[^0-9,]", "", valor)
        elif f == "data_nascimento":
            valor = _normalizar_data(valor)
        dados[f] = valor

    # Volume Diario e Vacas em Lactacao sao obrigatorios
    if not dados.get("volume_diario") or not dados.get("vacas_lactacao"):
        flash(
            "Preencha \"Volume Diário Total (L/dia)\" e \"Vacas em Lactação\" "
            "- eles são obrigatórios e usados para calcular a produtividade.",
            "error",
        )
        dados["foto_produtor"] = _foto_atual(pid)
        return False, dados

    # Produtividade Media = Volume Diario Total / Vacas em Lactacao
    # (recalculado aqui no servidor, garantindo que fique sempre correta
    # mesmo que o calculo automatico da tela nao tenha rodado)
    try:
        volume = float(dados["volume_diario"].replace(",", "."))
        vacas = int(dados["vacas_lactacao"])
        dados["produtividade_media"] = f"{volume / vacas:.2f}".replace(".", ",")
    except (ValueError, ZeroDivisionError):
        dados["produtividade_media"] = ""

    # ---- Tenta salvar direto no Supabase (banco + foto) ----
    try:
        foto_final = _foto_atual(pid)
        if foto_bytes:
            foto_final = enviar_bytes(foto_bytes, foto_nome_original, foto_mimetype)
        dados["foto_produtor"] = foto_final

        conn = get_connection()
        if pid is None:
            cols = ", ".join(FIELDS)
            placeholders = ", ".join(["?"] * len(FIELDS))
            conn.execute(
                f"INSERT INTO produtores ({cols}) VALUES ({placeholders})",
                [dados[f] for f in FIELDS],
            )
            flash("Produtor cadastrado com sucesso.", "success")
        else:
            set_clause = ", ".join([f"{f} = ?" for f in FIELDS])
            conn.execute(
                f"UPDATE produtores SET {set_clause}, atualizado_em = now() WHERE id = ?",
                [dados[f] for f in FIELDS] + [pid],
            )
            flash("Cadastro atualizado com sucesso.", "success")
        conn.commit()
        conn.close()
        return True, dados

    except Exception as e:
        if not _erro_de_conexao(e):
            raise  # erro de verdade (bug) - nao esconde, deixa aparecer

        if pid is not None:
            # Editar um cadastro que ja existe no servidor exige internet
            # (nao da pra "mesclar" edicoes offline com seguranca aqui).
            flash(
                "Sem conexão com a internet no momento - não é possível "
                "editar um cadastro que já existe no servidor enquanto "
                "estiver offline. Tente novamente quando tiver internet.",
                "error",
            )
            dados["foto_produtor"] = _foto_atual(pid)
            return False, dados

        # Cadastro novo -> guarda na fila local para sincronizar depois
        dados["foto_produtor"] = ""
        id_local = adicionar_na_fila(dados, foto_bytes, foto_nome_original, foto_mimetype)
        flash(
            f"Sem internet no momento — o cadastro foi salvo aqui no "
            f"computador (pendência #{id_local}) e será enviado "
            f"automaticamente quando você sincronizar.",
            "aviso",
        )
        return True, dados


@app.route("/fila")
def fila():
    pendentes = listar_pendentes()
    return render_template("fila.html", pendentes=pendentes)


@app.route("/sincronizar", methods=["POST"])
def sincronizar():
    pendentes = listar_pendentes()
    sucesso = 0
    falha = 0
    for item in pendentes:
        try:
            foto_url = ""
            if item.get("foto_local_arquivo"):
                caminho = FILA_FOTOS_DIR / item["foto_local_arquivo"]
                if caminho.exists():
                    foto_url = enviar_bytes(
                        caminho.read_bytes(), item["foto_local_arquivo"], ""
                    )

            dados = {f: (item.get(f) or "") for f in FIELDS if f != "foto_produtor"}
            dados["foto_produtor"] = foto_url

            conn = get_connection()
            cols = ", ".join(FIELDS)
            placeholders = ", ".join(["?"] * len(FIELDS))
            conn.execute(
                f"INSERT INTO produtores ({cols}) VALUES ({placeholders})",
                [dados[f] for f in FIELDS],
            )
            conn.commit()
            conn.close()
            remover_da_fila(item["id_local"])
            sucesso += 1
        except Exception:
            falha += 1

    if sucesso:
        flash(f"{sucesso} cadastro(s) sincronizado(s) com sucesso!", "success")
    if falha:
        flash(
            f"{falha} cadastro(s) ainda não puderam ser enviados (sem "
            f"internet?). Eles continuam guardados aqui, tente de novo mais tarde.",
            "error",
        )
    if not sucesso and not falha:
        flash("Não há cadastros pendentes para sincronizar.", "success")
    return redirect(url_for("fila"))


@app.route("/fila/excluir/<int:id_local>", methods=["POST"])
def excluir_pendente(id_local):
    remover_da_fila(id_local)
    flash("Pendência removida da fila.", "success")
    return redirect(url_for("fila"))


@app.route("/excluir/<int:pid>", methods=["POST"])
def excluir(pid):
    conn = get_connection()
    conn.execute("DELETE FROM produtores WHERE id = ?", (pid,))
    conn.commit()
    conn.close()
    flash("Cadastro excluído.", "success")
    return redirect(url_for("index"))


# Quantidade máxima de animais que um produtor pode receber
LIMITE_ANIMAIS_POR_PRODUTOR = 5


@app.route("/produtores/<int:pid>/animais", methods=["GET", "POST"])
def animais_produtor(pid):
    conn = get_connection()
    produtor = conn.execute(
        "SELECT id, nome_produtor FROM produtores WHERE id = ?", (pid,)
    ).fetchone()
    if produtor is None:
        conn.close()
        abort(404)

    if request.method == "POST":
        ids_selecionados = [
            int(v) for v in request.form.getlist("animal_id") if v.strip()
        ]
        qtd_atual = conn.execute(
            "SELECT COUNT(*) AS n FROM animais WHERE produtor_id = ?", (pid,)
        ).fetchone()["n"]

        if not ids_selecionados:
            flash("Selecione ao menos um animal para atribuir.", "error")
        elif qtd_atual + len(ids_selecionados) > LIMITE_ANIMAIS_POR_PRODUTOR:
            vagas = max(0, LIMITE_ANIMAIS_POR_PRODUTOR - qtd_atual)
            flash(
                f"{produtor['nome_produtor']} já tem {qtd_atual} animal(is). "
                f"Você só pode adicionar mais {vagas} agora (limite de "
                f"{LIMITE_ANIMAIS_POR_PRODUTOR} por produtor).",
                "error",
            )
        else:
            # So atribui quem ainda estiver "disponivel" - protege contra
            # atribuir duas vezes o mesmo animal (ex: duas abas abertas).
            placeholders = ", ".join(["?"] * len(ids_selecionados))
            conn.execute(
                f"UPDATE animais SET produtor_id = ?, status = 'alocado', "
                f"atualizado_em = now() WHERE id IN ({placeholders}) AND status = 'disponivel'",
                [pid] + ids_selecionados,
            )
            conn.commit()
            flash(
                f"{len(ids_selecionados)} animal(is) atribuído(s) a {produtor['nome_produtor']}.",
                "success",
            )
        conn.close()
        return redirect(url_for("animais_produtor", pid=pid))

    atribuidos = conn.execute(
        "SELECT * FROM animais WHERE produtor_id = ? ORDER BY brinco_faec", (pid,)
    ).fetchall()
    disponiveis = conn.execute(
        "SELECT * FROM animais WHERE status = 'disponivel' ORDER BY brinco_faec"
    ).fetchall()
    conn.close()

    vagas = max(0, LIMITE_ANIMAIS_POR_PRODUTOR - len(atribuidos))
    return render_template(
        "atribuir_animais.html",
        produtor=produtor,
        atribuidos=atribuidos,
        disponiveis=disponiveis,
        vagas=vagas,
        limite=LIMITE_ANIMAIS_POR_PRODUTOR,
    )


@app.route("/animais/<int:aid>/desvincular", methods=["POST"])
def desvincular_animal(aid):
    conn = get_connection()
    row = conn.execute(
        "SELECT produtor_id FROM animais WHERE id = ?", (aid,)
    ).fetchone()
    if row is None:
        conn.close()
        abort(404)
    pid = row["produtor_id"]
    conn.execute(
        "UPDATE animais SET produtor_id = NULL, status = 'disponivel', "
        "atualizado_em = now() WHERE id = ?",
        (aid,),
    )
    conn.commit()
    conn.close()
    flash("Animal desvinculado do produtor.", "success")
    if pid:
        return redirect(url_for("animais_produtor", pid=pid))
    return redirect(url_for("index"))


@app.route("/animais/<int:aid>/fotos", methods=["GET", "POST"])
def fotos_animal(aid):
    conn = get_connection()
    animal = conn.execute("SELECT * FROM animais WHERE id = ?", (aid,)).fetchone()
    if animal is None:
        conn.close()
        abort(404)

    if request.method == "POST":
        novas_urls = {}
        urls_para_excluir = []
        for campo in ("foto_1", "foto_2", "foto_3"):
            arquivo = request.files.get(campo)
            remover = request.form.get(f"remover_{campo}") == "1"
            if arquivo and arquivo.filename:
                try:
                    url = enviar_bytes(
                        arquivo.read(), arquivo.filename, arquivo.mimetype or "",
                        bucket=BUCKET_ANIMAIS,
                    )
                    if animal[campo]:
                        urls_para_excluir.append(animal[campo])
                    novas_urls[campo] = url
                except Exception as e:
                    flash(f"Erro ao enviar a foto ({campo}): {e}", "error")
            elif remover and animal[campo]:
                urls_para_excluir.append(animal[campo])
                novas_urls[campo] = None

        if novas_urls:
            set_clause = ", ".join(f"{c} = ?" for c in novas_urls)
            conn.execute(
                f"UPDATE animais SET {set_clause}, atualizado_em = now() WHERE id = ?",
                list(novas_urls.values()) + [aid],
            )
            conn.commit()
            for url in urls_para_excluir:
                excluir_arquivo(url)
            flash("Foto(s) do animal atualizada(s) com sucesso.", "success")
        conn.close()
        return redirect(url_for("fotos_animal", aid=aid))

    conn.close()
    return render_template("fotos_animal.html", animal=animal)


@app.route("/animais")
def animais():
    busca = request.args.get("q", "").strip()
    conn = get_connection()
    if busca:
        like = f"%{busca}%"
        rows = conn.execute(
            """SELECT a.*, p.nome_produtor
               FROM animais a LEFT JOIN produtores p ON p.id = a.produtor_id
               WHERE a.brinco_faec ILIKE ? OR a.brinco_fazenda ILIKE ?
                  OR p.nome_produtor ILIKE ?
               ORDER BY a.brinco_faec""",
            (like, like, like),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT a.*, p.nome_produtor
               FROM animais a LEFT JOIN produtores p ON p.id = a.produtor_id
               ORDER BY a.brinco_faec"""
        ).fetchall()
    total = conn.execute("SELECT COUNT(*) AS n FROM animais").fetchone()["n"]
    disponiveis = conn.execute(
        "SELECT COUNT(*) AS n FROM animais WHERE status = 'disponivel'"
    ).fetchone()["n"]
    conn.close()
    return render_template(
        "animais.html", animais=rows, busca=busca, total=total,
        disponiveis=disponiveis, alocados=total - disponiveis,
    )


@app.route("/pdf/<int:pid>")
def pdf(pid):
    conn = get_connection()
    row = conn.execute("SELECT * FROM produtores WHERE id = ?", (pid,)).fetchone()
    conn.close()
    if row is None:
        abort(404)
    buf = generate_pdf(row)
    nome = (row["nome_produtor"] or "produtor").strip().replace(" ", "_")
    return send_file(
        buf, mimetype="application/pdf", as_attachment=False,
        download_name=f"FATS_{nome}.pdf",
    )


FUSO_BRASIL = ZoneInfo("America/Fortaleza")  # UTC-3, sem horário de verão


def _hora_brasil(valor):
    """Converte um datetime do banco (vem com fuso, geralmente UTC) pro
    horário do Brasil (América/Fortaleza) — e só DEPOIS remove o tzinfo,
    porque o Excel (openpyxl) não aceita datetime com fuso horário (dá
    TypeError na hora de salvar). Se o valor já vier sem fuso (naive),
    assume que já está em UTC (é o padrão do Postgres) antes de converter
    — sem isso, a hora ficaria errada (3h adiantada) em vez de simplesmente
    dar erro."""
    if not isinstance(valor, datetime):
        return valor
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=ZoneInfo("UTC"))
    return valor.astimezone(FUSO_BRASIL).replace(tzinfo=None)


@app.route("/exportar-excel")
def exportar_excel():
    busca = request.args.get("q", "").strip()
    conn = get_connection()
    if busca:
        like = f"%{busca}%"
        rows = conn.execute(
            """SELECT * FROM produtores
               WHERE nome_produtor ILIKE ? OR cpf ILIKE ? OR municipio ILIKE ?
                  OR nome_propriedade ILIKE ?
               ORDER BY nome_produtor""",
            (like, like, like, like),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM produtores ORDER BY nome_produtor"
        ).fetchall()
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Produtores"

    # Cabecalho: ID + todos os campos, na ordem da ficha
    headers = ["ID"] + [FIELD_LABELS.get(f, f) for f in FIELDS] + ["Criado em", "Atualizado em"]
    ws.append(headers)

    header_fill = PatternFill(start_color="1F4E2C", end_color="1F4E2C", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"

    for row in rows:
        valores = []
        for f in FIELDS:
            v = row[f]
            if f in CAMPOS_MAIUSCULOS and v:
                v = v.upper()
            elif f == "data_nascimento":
                v = _exibir_data(v)
            elif f == "cpf":
                v = _formatar_cpf(v)
            elif f == "cpf_tecnico":
                v = _formatar_cpf_ou_registro(v)
            elif f == "telefone":
                v = _formatar_telefone(v)
            valores.append(v)
        linha = [row["id"]] + valores + [_hora_brasil(row["criado_em"]), _hora_brasil(row["atualizado_em"])]
        ws.append(linha)

    # Largura automatica (aproximada) das colunas
    for i, header in enumerate(headers, start=1):
        col_letter = get_column_letter(i)
        max_len = len(str(header))
        for row_cells in ws.iter_rows(min_col=i, max_col=i, min_row=2):
            valor = row_cells[0].value
            if valor is not None:
                max_len = max(max_len, len(str(valor)))
        ws.column_dimensions[col_letter].width = min(max(max_len + 2, 10), 45)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="produtores_fats.xlsx",
    )


if __name__ == "__main__":
    init_db()
    if getattr(sys, "frozen", False):
        # Rodando como .exe: abre o navegador automaticamente e roda sem
        # o modo debug/reloader (que nao funciona dentro do PyInstaller).
        import threading
        import webbrowser

        url = "http://127.0.0.1:5000"
        print("=" * 50)
        print(" Sistema FATS iniciado!")
        print(f" Abrindo no navegador: {url}")
        print(" Para encerrar o programa, feche esta janela.")
        print("=" * 50)
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
        app.run(debug=False, host="127.0.0.1", port=5000)
    else:
        app.run(debug=True, host="0.0.0.0", port=5000)
