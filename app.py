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
from exportar_alocacoes import montar_planilha
from exportar_animais import _filtros_produtores
from supabase_storage import enviar_bytes, excluir_arquivo, BUCKET_ANIMAIS
from campos import FIELDS, FIELD_LABELS, MUNICIPIOS_CEARA, TIPOS_VISITA
from fila_offline import (
    adicionar_na_fila, listar_pendentes, contar_pendentes, remover_da_fila,
    adicionar_visita_na_fila, listar_visitas_pendentes,
    contar_visitas_pendentes, remover_visita_da_fila,
    adicionar_animal_na_fila, listar_animais_pendentes,
    contar_animais_pendentes, remover_animal_da_fila,
    excluir_visita_pendente, excluir_animal_pendente, FILA_FOTOS_DIR,
)


# Pasta onde as fotos dos produtores ficam salvas (ao lado do banco de
# dados, para nao se perder quando o programa roda como .exe)
# Pasta onde as fotos dos produtores ficam salvas. Em producao (Render),
# aponte a variavel de ambiente FOTOS_DIR para o disco persistente
# (ex: /var/data/fotos). Localmente, sem essa variavel, continua usando a
# pasta "fotos" do lado do proprio app.py (como sempre foi).
UPLOAD_DIR = Path(os.environ["FOTOS_DIR"]) if os.environ.get("FOTOS_DIR") else (_base_dir() / "fotos")
MAPA_CACHE_PATH = _base_dir() / "mapa_offline.json"
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


@app.after_request
def _sem_cache_nas_paginas(resposta):
    """Evita que o navegador (principalmente no celular) guarde as páginas
    em cache e mostre dados desatualizados depois de salvar/excluir algo
    (ex: uma foto removida ainda aparecendo na lista até um refresh forçado).
    Não afeta imagens/arquivos, só páginas HTML geradas pelo Flask."""
    if resposta.content_type and resposta.content_type.startswith("text/html"):
        resposta.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resposta


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
    return dict(
        foto_url=foto_url,
        qtd_pendentes_offline=(contar_pendentes() + contar_visitas_pendentes() +
                       contar_animais_pendentes()),
    )

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


def _ultimas_visitas_ativas(conn, incluir_cadastro=True):
    """Devolve a visita ativa mais recente de cada produtor."""
    filtro_cadastro = ""
    if not incluir_cadastro:
        filtro_cadastro = "AND v.tipo_visita <> 'Cadastro / Ficha inicial'"
    rows = conn.execute(
        "SELECT DISTINCT ON (v.produtor_id) v.produtor_id, v.data_visita, v.tipo_visita "
        "FROM visitas v "
        "WHERE v.ativa = TRUE " + filtro_cadastro + " "
        "ORDER BY v.produtor_id, v.criado_em DESC, v.id DESC"
    ).fetchall()
    return {r["produtor_id"]: r for r in rows}


def _ultimas_visitas_da_etapa(conn):
    """Devolve visitas que concluem a atividade atual, sem o cadastro inicial."""
    return _ultimas_visitas_ativas(conn, incluir_cadastro=False)


@app.route("/mapa")
def mapa():
    offline = False
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM produtores ORDER BY nome_produtor"
        ).fetchall()
        ultimas_visitas = _ultimas_visitas_da_etapa(conn)
        conn.close()
    except Exception as erro:
        if not _erro_de_conexao(erro) and not isinstance(erro, RuntimeError):
            raise
        if not MAPA_CACHE_PATH.exists():
            return render_template(
                "mapa.html", pontos=[], pontos_json="[]",
                total=0, qtd_no_mapa=0, sem_coordenadas=0,
                municipio_stats_json="{}", tipos_visita_json=json.dumps(TIPOS_VISITA),
                offline=True,
            )
        cache = json.loads(MAPA_CACHE_PATH.read_text(encoding="utf-8"))
        return render_template(
            "mapa.html",
            pontos=cache["pontos"], pontos_json=json.dumps(cache["pontos"], ensure_ascii=False),
            total=cache["total"], qtd_no_mapa=cache["qtd_no_mapa"],
            sem_coordenadas=cache["sem_coordenadas"],
            municipio_stats_json=json.dumps(cache["municipio_stats"], ensure_ascii=False),
            tipos_visita_json=json.dumps(TIPOS_VISITA, ensure_ascii=False), offline=True,
        )

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
        ultima_visita = ultimas_visitas.get(produtor["id"])
        pontos.append({
            "id": produtor["id"],
            "nome_produtor": produtor.get("nome_produtor") or "-",
            "nome_propriedade": produtor.get("nome_propriedade") or "-",
            "municipio": municipio,
            "tecnico_responsavel": produtor.get("tecnico_responsavel") or "-",
            "lat": lat,
            "lon": lon,
            "etapa_atual": produtor.get("etapa_atual") or 1,
            # "visitado" agora e derivado do historico de visitas: basta ter
            # uma visita registrada NA ETAPA ATUAL DESTE produtor pra contar
            # como ja visitado (visitas de etapas anteriores dele nao
            # contam mais depois que ele avanca de etapa).
            "visitado": ultima_visita is not None,
            "data_visita": (ultima_visita["data_visita"] if ultima_visita else "") or "",
            "tipo_visita": (ultima_visita["tipo_visita"] if ultima_visita else "") or "",
        })

    try:
        MAPA_CACHE_PATH.write_text(json.dumps({
            "pontos": pontos, "total": len(rows), "qtd_no_mapa": len(pontos),
            "sem_coordenadas": sem_coordenadas, "municipio_stats": municipio_stats,
        }, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass

    return render_template(
        "mapa.html",
        pontos=pontos,
        pontos_json=json.dumps(pontos, ensure_ascii=False),
        total=len(rows),
        qtd_no_mapa=len(pontos),
        sem_coordenadas=sem_coordenadas,
        municipio_stats_json=json.dumps(municipio_stats, ensure_ascii=False),
        tipos_visita_json=json.dumps(TIPOS_VISITA, ensure_ascii=False),
        offline=offline,
    )


@app.route("/visitas")
def painel_visitas():
    """Visao geral, em forma de lista/tabela, de quem ja foi visitado na
    etapa atual (de cada um) e quem ainda falta - um jeito mais pratico de
    acompanhar isso do que ficar clicando pino por pino no mapa."""
    municipio_filtro = request.args.get("municipio", "").strip()
    status_filtro = request.args.get("status", "").strip()  # "", "visitado", "pendente"

    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM produtores ORDER BY nome_produtor"
    ).fetchall()
    ultimas_visitas = _ultimas_visitas_da_etapa(conn)
    conn.close()

    municipios = sorted(set((r.get("municipio") or "-") for r in rows))

    produtores = []
    total_visitados = 0
    for row in rows:
        produtor = dict(row)
        municipio = produtor.get("municipio") or "-"
        if municipio_filtro and municipio != municipio_filtro:
            continue

        ultima_visita = ultimas_visitas.get(produtor["id"])
        visitado = ultima_visita is not None
        if visitado:
            total_visitados += 1

        if status_filtro == "visitado" and not visitado:
            continue
        if status_filtro == "pendente" and visitado:
            continue

        produtores.append({
            "id": produtor["id"],
            "nome_produtor": produtor.get("nome_produtor") or "-",
            "nome_propriedade": produtor.get("nome_propriedade") or "-",
            "municipio": municipio,
            "tecnico_responsavel": produtor.get("tecnico_responsavel") or "-",
            "etapa_atual": produtor.get("etapa_atual") or 1,
            "visitado": visitado,
            "data_visita": (ultima_visita["data_visita"] if ultima_visita else "") or "",
            "tipo_visita": (ultima_visita["tipo_visita"] if ultima_visita else "") or "",
        })

    return render_template(
        "painel_visitas.html",
        produtores=produtores,
        municipios=municipios,
        municipio_filtro=municipio_filtro,
        status_filtro=status_filtro,
        total_geral=len(rows),
        total_visitados=total_visitados,
    )


@app.route("/relatorio")
def relatorio():
    municipio_filtro = request.args.get("municipio", "").strip()
    status_filtro = request.args.get("status", "").strip()
    motivo_filtro = request.args.get("motivo", "").strip()
    ordem_data = request.args.get("ordem_data", "").strip()

    conn = get_connection()
    rows = conn.execute(
        "SELECT p.id, p.nome_produtor, p.nome_propriedade, p.municipio, "
        "COUNT(a.id) AS qtd_animais "
        "FROM produtores p LEFT JOIN animais a ON a.produtor_id = p.id "
        "GROUP BY p.id, p.nome_produtor, p.nome_propriedade, p.municipio "
        "ORDER BY p.nome_produtor"
    ).fetchall()
    total_animais = conn.execute(
        "SELECT COUNT(*) AS total FROM animais"
    ).fetchone()["total"]
    total_atendimentos = conn.execute(
        "SELECT COUNT(*) AS total FROM visitas WHERE ativa = TRUE"
    ).fetchone()["total"]
    ultimas_visitas = _ultimas_visitas_ativas(conn)
    conn.close()

    municipios = sorted(set((row["municipio"] or "-") for row in rows))
    resumo_por_municipio = {}
    produtores = []
    total_visitados = 0

    for row in rows:
        municipio = row["municipio"] or "-"
        visita = ultimas_visitas.get(row["id"])
        visitado = visita is not None
        stats = resumo_por_municipio.setdefault(
            municipio, {"total": 0, "visitados": 0, "pendentes": 0, "animais": 0}
        )
        stats["total"] += 1
        stats["animais"] += row["qtd_animais"] or 0
        stats["visitados" if visitado else "pendentes"] += 1
        if visitado:
            total_visitados += 1

        if municipio_filtro and municipio != municipio_filtro:
            continue
        if status_filtro == "visitado" and not visitado:
            continue
        if status_filtro == "pendente" and visitado:
            continue
        motivo = (visita["tipo_visita"] if visita else "Cadastro / Ficha inicial") or "Cadastro / Ficha inicial"
        if motivo_filtro and motivo != motivo_filtro:
            continue

        produtores.append({
            "id": row["id"],
            "nome_produtor": row["nome_produtor"] or "-",
            "nome_propriedade": row["nome_propriedade"] or "-",
            "municipio": municipio,
            "qtd_animais": row["qtd_animais"] or 0,
            "visitado": visitado,
            "data_visita": (visita["data_visita"] if visita else "") or "",
            "tipo_visita": motivo,
        })

    if ordem_data in ("recente", "antiga"):
        def data_visita_chave(produtor):
            texto = produtor["data_visita"]
            if not texto:
                return datetime.min if ordem_data == "recente" else datetime.max
            try:
                data = datetime.strptime(texto, "%d/%m/%Y")
            except ValueError:
                return datetime.min if ordem_data == "recente" else datetime.max
            return data

        produtores.sort(
            key=data_visita_chave,
            reverse=ordem_data == "recente",
        )

    return render_template(
        "relatorio.html",
        produtores=produtores,
        municipios=municipios,
        municipio_filtro=municipio_filtro,
        status_filtro=status_filtro,
        motivo_filtro=motivo_filtro,
        ordem_data=ordem_data,
        motivos_visita=sorted(set(TIPOS_VISITA + ["Cadastro / Ficha inicial"])),
        total_geral=len(rows),
        total_visitados=total_visitados,
        total_atendimentos=total_atendimentos,
        total_animais=total_animais,
        resumo_por_municipio=sorted(resumo_por_municipio.items()),
    )


@app.route("/produtor/<int:pid>/nova_etapa", methods=["POST"])
def nova_etapa_produtor(pid):
    """Avanca a etapa de visitas SO DESSE produtor: ele volta a aparecer
    verde no mapa, sem mexer em nenhum outro (cada produtor tem seu
    proprio ritmo). O historico de visitas antigas dele continua salvo,
    so marcado com o numero da etapa anterior.

    So faz efeito se o produtor JA TIVER uma visita registrada na etapa
    atual dele - nao faz sentido "avancar de etapa" quem nunca foi
    visitado, isso so criaria um produtor pendente numa etapa mais alta
    sem nenhuma visita correspondente (o pino ficaria errado no mapa)."""
    eh_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    conn = get_connection()
    row = conn.execute(
        "SELECT etapa_atual FROM produtores WHERE id = ?", (pid,)
    ).fetchone()
    if row is None:
        conn.close()
        abort(404)

    etapa_atual = row["etapa_atual"] or 1
    ja_visitado = conn.execute(
        "SELECT 1 FROM visitas WHERE produtor_id = ? AND etapa = ? AND ativa = TRUE LIMIT 1",
        (pid, etapa_atual),
    ).fetchone()

    if not ja_visitado:
        conn.close()
        if eh_ajax:
            return {"ok": False, "motivo": "ainda_nao_visitado"}, 409
        flash("Esse produtor ainda não foi visitado nesta etapa.", "error")
        return redirect(url_for("mapa"))

    nova = etapa_atual + 1
    conn.execute(
        "UPDATE produtores SET etapa_atual = ?, atualizado_em = now() WHERE id = ?",
        (nova, pid),
    )
    conn.commit()
    conn.close()

    if eh_ajax:
        return {"ok": True, "produtor_id": pid, "etapa_atual": nova}

    flash("Nova etapa iniciada para este produtor.", "success")
    return redirect(url_for("mapa"))


@app.route("/etapas/nova", methods=["POST"])
def nova_etapa_lote():
    """Avanca a etapa de visitas dos produtores JA VISITADOS na etapa
    atual (opcionalmente filtrando por municipio, o mesmo filtro ja usado
    no mapa/painel) - pra um tecnico poder "zerar" so a area dele sem
    afetar quem ainda esta no meio de outra etapa em outro municipio.

    Quem ainda esta pendente (sem visita registrada na etapa atual) NAO
    avanca - continua na mesma etapa, aguardando ser visitado. Do
    contrario, um produtor nunca visitado poderia "pular" de etapa sem
    nenhuma visita correspondente, o que nao faz sentido nenhum."""
    municipio = (request.form.get("municipio") or "").strip()

    condicao_visitado = (
        "EXISTS (SELECT 1 FROM visitas v WHERE v.produtor_id = produtores.id "
        "AND v.etapa = produtores.etapa_atual AND v.ativa = TRUE)"
    )

    conn = get_connection()
    if municipio:
        conn.execute(
            "UPDATE produtores SET etapa_atual = etapa_atual + 1, "
            f"atualizado_em = now() WHERE municipio = ? AND {condicao_visitado}",
            (municipio,),
        )
        mensagem = (
            f"Nova etapa iniciada para os produtores já visitados em {municipio}. "
            f"Quem ainda está pendente continua na mesma etapa."
        )
    else:
        conn.execute(
            "UPDATE produtores SET etapa_atual = etapa_atual + 1, atualizado_em = now() "
            f"WHERE {condicao_visitado}"
        )
        mensagem = (
            "Nova etapa iniciada para todos os produtores já visitados. "
            "Quem ainda está pendente continua na mesma etapa."
        )
    conn.commit()
    conn.close()

    flash(mensagem, "success")
    return redirect(url_for("mapa"))




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

    try:
        conn = get_connection()
    except Exception as erro:
        if not _erro_de_conexao(erro) and not isinstance(erro, RuntimeError):
            raise
        return render_template("offline.html")

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


@app.route("/produtor/<int:pid>/visitas")
def visitas_produtor(pid):
    """Pagina dedicada ao historico de visitas do produtor: registrar uma
    visita nova (cadastro, entrega de material, acompanhamento etc) e ver
    as que ja foram feitas, sem misturar com a ficha tecnica completa."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM produtores WHERE id = ?", (pid,)).fetchone()
    if row is None:
        conn.close()
        abort(404)
    visitas = conn.execute(
        "SELECT * FROM visitas WHERE produtor_id = ? ORDER BY criado_em DESC, id DESC",
        (pid,),
    ).fetchall()
    conn.close()
    produtor = dict(row)
    return render_template(
        "visitas.html", produtor=produtor, visitas=visitas, tipos_visita=TIPOS_VISITA
    )


@app.route("/ficha/<int:pid>/visita", methods=["POST"])
def nova_visita(pid):
    """Registra uma nova visita para o produtor (cadastro, entrega de
    material, acompanhamento etc). Um mesmo produtor pode ter varias.

    Usada tanto pelo formulario normal da tela de Visitas (recarrega a
    pagina) quanto pelo botao rapido do mapa, que chama essa mesma rota
    via fetch() e so espera um JSON de volta (sem redirecionar)."""
    eh_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    data_visita = (request.form.get("data_visita") or "").strip()
    tipo_visita = (request.form.get("tipo_visita") or "").strip()
    observacoes = (request.form.get("observacoes") or "").strip()
    try:
        conn = get_connection()
        row = conn.execute("SELECT id, etapa_atual FROM produtores WHERE id = ?", (pid,)).fetchone()
        if row is None:
            conn.close()
            abort(404)

        motivo_existente = conn.execute(
            "SELECT 1 FROM visitas WHERE produtor_id = ? AND tipo_visita = ? LIMIT 1",
            (pid, tipo_visita),
        ).fetchone()
        if motivo_existente:
            conn.close()
            mensagem = "Este motivo de visita já foi registrado para este produtor."
            if eh_ajax:
                return {"ok": False, "motivo": "duplicado", "mensagem": mensagem}, 409
            flash(mensagem, "error")
            return redirect(url_for("visitas_produtor", pid=pid))

        conn.execute(
            "INSERT INTO visitas (produtor_id, data_visita, tipo_visita, observacoes, etapa) "
            "VALUES (?, ?, ?, ?, ?)",
            (pid, data_visita, tipo_visita, observacoes, row["etapa_atual"] or 1),
        )
        conn.commit()
        conn.close()
    except Exception as erro:
        if not _erro_de_conexao(erro) and not isinstance(erro, RuntimeError):
            raise
        id_local = adicionar_visita_na_fila(pid, data_visita, tipo_visita, observacoes)
        if eh_ajax:
            return {
                "ok": True,
                "offline": True,
                "fila_id": id_local,
                "produtor_id": pid,
                "data_visita": data_visita,
                "tipo_visita": tipo_visita,
            }
        flash("Sem internet: visita guardada na fila local para sincronizar depois.", "aviso")
        return redirect(url_for("visitas_produtor", pid=pid))

    if eh_ajax:
        return {
            "ok": True,
            "produtor_id": pid,
            "data_visita": data_visita,
            "tipo_visita": tipo_visita,
        }

    flash("Visita registrada com sucesso.")
    return redirect(url_for("visitas_produtor", pid=pid))


@app.route("/ficha/<int:pid>/visita/desmarcar", methods=["POST"])
def desmarcar_visita(pid):
    """Desfaz somente a visita mais recente da etapa atual do produtor."""
    eh_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    conn = get_connection()
    row = conn.execute(
        "SELECT etapa_atual FROM produtores WHERE id = ?", (pid,)
    ).fetchone()
    if row is None:
        conn.close()
        abort(404)

    visita = conn.execute(
        "SELECT id FROM visitas WHERE produtor_id = ? AND etapa = ? AND ativa = TRUE "
        "ORDER BY criado_em DESC, id DESC LIMIT 1",
        (pid, row["etapa_atual"] or 1),
    ).fetchone()
    if visita is None:
        conn.close()
        if eh_ajax:
            return {"ok": False, "motivo": "nao_visitado"}, 409
        flash("Essa propriedade não está marcada como visitada.", "error")
        return redirect(url_for("mapa"))

    conn.execute("UPDATE visitas SET ativa = FALSE WHERE id = ?", (visita["id"],))
    conn.commit()
    conn.close()

    if eh_ajax:
        return {"ok": True, "produtor_id": pid}
    flash("Visita desmarcada.", "success")
    return redirect(url_for("mapa"))


@app.route("/visita/<int:vid>/excluir", methods=["POST"])
def excluir_visita(vid):
    conn = get_connection()
    row = conn.execute("SELECT produtor_id FROM visitas WHERE id = ?", (vid,)).fetchone()
    if row is None:
        conn.close()
        abort(404)
    pid = row["produtor_id"]
    conn.execute("DELETE FROM visitas WHERE id = ?", (vid,))
    conn.commit()
    conn.close()
    flash("Visita excluída.")
    return redirect(url_for("visitas_produtor", pid=pid))


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
    if isinstance(e, (psycopg2.OperationalError, requests.exceptions.ConnectionError,
                      requests.exceptions.Timeout, UnicodeDecodeError)):
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
            novo_produtor = conn.execute(
                f"INSERT INTO produtores ({cols}) VALUES ({placeholders}) RETURNING id",
                [dados[f] for f in FIELDS],
            ).fetchone()
            conn.execute(
                "INSERT INTO visitas "
                "(produtor_id, data_visita, tipo_visita, observacoes, etapa, ativa) "
                "VALUES (?, ?, ?, ?, 1, TRUE)",
                (
                    novo_produtor["id"],
                    datetime.now(ZoneInfo("America/Fortaleza")).strftime("%d/%m/%Y"),
                    "Cadastro / Ficha inicial",
                    "Realizar cadastro inicial dos produtores",
                ),
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
    visitas_pendentes = listar_visitas_pendentes()
    animais_pendentes = listar_animais_pendentes()
    return render_template(
        "fila.html", pendentes=pendentes, visitas_pendentes=visitas_pendentes,
        animais_pendentes=animais_pendentes,
    )


@app.route("/modo-offline")
def modo_offline():
    return render_template("offline.html")


@app.route("/fila/visita/excluir/<int:id_local>", methods=["POST"])
def excluir_visita_pendente_rota(id_local):
    excluir_visita_pendente(id_local)
    flash("Visita removida da fila offline. Ela não será sincronizada.", "success")
    return redirect(url_for("fila"))


@app.route("/fila/animal/excluir/<int:id_local>", methods=["POST"])
def excluir_animal_pendente_rota(id_local):
    excluir_animal_pendente(id_local)
    flash("Animal removido da fila offline. Ele não será sincronizado.", "success")
    return redirect(url_for("fila"))


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
            novo_produtor = conn.execute(
                f"INSERT INTO produtores ({cols}) VALUES ({placeholders}) RETURNING id",
                [dados[f] for f in FIELDS],
            ).fetchone()
            conn.execute(
                "INSERT INTO visitas "
                "(produtor_id, data_visita, tipo_visita, observacoes, etapa, ativa) "
                "VALUES (?, ?, ?, ?, 1, TRUE)",
                (
                    novo_produtor["id"],
                    datetime.now(ZoneInfo("America/Fortaleza")).strftime("%d/%m/%Y"),
                    "Cadastro / Ficha inicial",
                    "Realizar cadastro inicial dos produtores",
                ),
            )
            conn.commit()
            conn.close()
            remover_da_fila(item["id_local"])
            sucesso += 1
        except Exception:
            falha += 1

    visitas_sucesso = 0
    visitas_falha = 0
    for visita in listar_visitas_pendentes():
        try:
            conn = get_connection()
            produtor = conn.execute(
                "SELECT etapa_atual FROM produtores WHERE id = ?",
                (visita["produtor_id"],),
            ).fetchone()
            if produtor is None:
                conn.close()
                raise RuntimeError("Produtor ainda não existe no servidor")
            duplicada = conn.execute(
                "SELECT 1 FROM visitas WHERE produtor_id = ? AND tipo_visita = ? LIMIT 1",
                (visita["produtor_id"], visita["tipo_visita"]),
            ).fetchone()
            if not duplicada:
                conn.execute(
                    "INSERT INTO visitas "
                    "(produtor_id, data_visita, tipo_visita, observacoes, etapa) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        visita["produtor_id"], visita["data_visita"],
                        visita["tipo_visita"], visita["observacoes"],
                        produtor["etapa_atual"] or 1,
                    ),
                )
                conn.commit()
            conn.close()
            remover_visita_da_fila(visita["id_local"])
            visitas_sucesso += 1
        except Exception:
            visitas_falha += 1

    animais_sucesso = 0
    animais_falha = 0
    for animal in listar_animais_pendentes():
        try:
            conn = get_connection()
            duplicado = conn.execute(
                "SELECT 1 FROM animais WHERE brinco_faec = ? LIMIT 1",
                (animal["brinco_faec"],),
            ).fetchone()
            if not duplicado:
                conn.execute(
                    "INSERT INTO animais (brinco_faec, brinco_fazenda, peso, status) "
                    "VALUES (?, ?, ?, 'disponivel')",
                    (animal["brinco_faec"], animal["brinco_fazenda"], animal["peso"]),
                )
                conn.commit()
            conn.close()
            remover_animal_da_fila(animal["id_local"])
            animais_sucesso += 1
        except Exception:
            animais_falha += 1

    if sucesso:
        flash(f"{sucesso} cadastro(s) sincronizado(s) com sucesso!", "success")
    if falha:
        flash(
            f"{falha} cadastro(s) ainda não puderam ser enviados (sem "
            f"internet?). Eles continuam guardados aqui, tente de novo mais tarde.",
            "error",
        )
    if visitas_sucesso:
        flash(f"{visitas_sucesso} visita(s) sincronizada(s) com sucesso!", "success")
    if visitas_falha:
        flash(
            f"{visitas_falha} visita(s) ainda aguardam internet ou o cadastro do produtor.",
            "error",
        )
    if animais_sucesso:
        flash(f"{animais_sucesso} animal(is) sincronizado(s) com sucesso!", "success")
    if animais_falha:
        flash(f"{animais_falha} animal(is) ainda aguardam internet.", "error")
    if not sucesso and not falha:
        if not visitas_sucesso and not visitas_falha and not animais_sucesso and not animais_falha:
            flash("Não há cadastros ou visitas pendentes para sincronizar.", "success")
    return redirect(url_for("modo_offline"))


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

    busca_disp = request.args.get("q_animal", "").strip()
    LIMITE_LISTAGEM_DISPONIVEIS = 50
    if busca_disp:
        like = f"%{busca_disp}%"
        disponiveis_todos = conn.execute(
            "SELECT * FROM animais WHERE status = 'disponivel' "
            "AND (brinco_faec ILIKE ? OR brinco_fazenda ILIKE ?) "
            "ORDER BY brinco_faec",
            (like, like),
        ).fetchall()
    else:
        disponiveis_todos = conn.execute(
            "SELECT * FROM animais WHERE status = 'disponivel' ORDER BY brinco_faec"
        ).fetchall()
    total_disponiveis = len(disponiveis_todos)
    disponiveis = disponiveis_todos[:LIMITE_LISTAGEM_DISPONIVEIS]
    conn.close()

    vagas = max(0, LIMITE_ANIMAIS_POR_PRODUTOR - len(atribuidos))
    return render_template(
        "atribuir_animais.html",
        produtor=produtor,
        atribuidos=atribuidos,
        disponiveis=disponiveis,
        total_disponiveis=total_disponiveis,
        busca_disp=busca_disp,
        limite_listagem=LIMITE_LISTAGEM_DISPONIVEIS,
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


@app.route("/animais/novo", methods=["GET", "POST"])
def novo_animal():
    """Cadastra um animal novo (antes disso, so era possivel cadastrar
    animais em massa pelo script de importacao)."""
    if request.method == "POST":
        brinco_faec = (request.form.get("brinco_faec") or "").strip()
        brinco_fazenda = (request.form.get("brinco_fazenda") or "").strip()
        peso = (request.form.get("peso") or "").strip()

        if not brinco_faec:
            flash("Informe o Brinco FAEC do animal.", "error")
            return render_template("novo_animal.html", animal={
                "brinco_faec": brinco_faec,
                "brinco_fazenda": brinco_fazenda,
                "peso": peso,
            })

        try:
            conn = get_connection()
            novo = conn.execute(
                "INSERT INTO animais (brinco_faec, brinco_fazenda, peso, status) "
                "VALUES (?, ?, ?, 'disponivel') RETURNING id",
                (brinco_faec, brinco_fazenda or None, peso or None),
            ).fetchone()
            conn.commit()
            conn.close()
            flash("Animal cadastrado com sucesso.", "success")
            return redirect(url_for("fotos_animal", aid=novo["id"]))
        except Exception as erro:
            if not _erro_de_conexao(erro) and not isinstance(erro, RuntimeError):
                raise
            adicionar_animal_na_fila(brinco_faec, brinco_fazenda, peso)
            flash("Sem internet: animal guardado na fila para sincronizar depois.", "aviso")
            return redirect(url_for("fila"))

    return render_template("novo_animal.html", animal=None)


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

        campos_para_salvar = dict(novas_urls)
        peso = request.form.get("peso", "").strip()
        if peso != (animal["peso"] or ""):
            campos_para_salvar["peso"] = peso or None
        brinco_faec = request.form.get("brinco_faec", "").strip()
        if brinco_faec and brinco_faec != (animal["brinco_faec"] or ""):
            campos_para_salvar["brinco_faec"] = brinco_faec
        brinco_fazenda = request.form.get("brinco_fazenda", "").strip()
        if brinco_fazenda != (animal["brinco_fazenda"] or ""):
            campos_para_salvar["brinco_fazenda"] = brinco_fazenda or None

        if campos_para_salvar:
            set_clause = ", ".join(f"{c} = ?" for c in campos_para_salvar)
            conn.execute(
                f"UPDATE animais SET {set_clause}, atualizado_em = now() WHERE id = ?",
                list(campos_para_salvar.values()) + [aid],
            )
            conn.commit()
            for url in urls_para_excluir:
                excluir_arquivo(url)
            flash("Dados do animal atualizados com sucesso.", "success")
        conn.close()
        return redirect(url_for("fotos_animal", aid=aid))

    conn.close()
    return render_template("fotos_animal.html", animal=animal)


def _normalizar_brinco(valor: str) -> str:
    """Deixa só letras e números maiúsculos, pra comparar brincos sem se
    importar com espaço, traço, ponto ou maiúscula/minúscula
    (ex: "V 1452", "v-1452" e "V1452" viram todas "V1452")."""
    return re.sub(r"[^A-Z0-9]", "", (valor or "").upper())


@app.route("/animais/importar-fotos", methods=["GET", "POST"])
def importar_fotos_lote():
    """
    Importação em lote de fotos de animais: o nome de cada arquivo enviado
    deve trazer o Brinco FAEC do animal (ex: "0001.jpg" vira a foto do
    animal de brinco 0001), com um sufixo opcional _1/_2/_3 pra escolher a
    posição (Foto 1/2/3) - sem sufixo, cai na primeira posição vazia.

    Feito pra importar de uma vez uma pasta cheia de fotos já renomeadas
    (ex: as ~200 fotos de uma visita a campo), em vez de subir animal por
    animal pela tela /animais/<id>/fotos.
    """
    if request.method == "GET":
        return render_template("importar_fotos_lote.html")

    arquivos = request.files.getlist("fotos")
    arquivos = [a for a in arquivos if a and a.filename]
    if not arquivos:
        flash("Selecione ao menos um arquivo antes de importar.", "error")
        return redirect(url_for("importar_fotos_lote"))

    conn = get_connection()
    animais = conn.execute(
        "SELECT id, brinco_faec, brinco_fazenda, foto_1, foto_2, foto_3 FROM animais"
    ).fetchall()

    # Indices de busca: brinco normalizado -> lista de animais que batem
    # (uma lista, e nao um so animal, porque brinco_fazenda pode se repetir
    # entre produtores diferentes - precisamos saber quando isso acontece
    # pra marcar como ambiguo em vez de arriscar salvar no animal errado).
    por_faec = {}
    por_fazenda = {}
    for a in animais:
        por_faec.setdefault(_normalizar_brinco(a["brinco_faec"]), []).append(a)
        if a["brinco_fazenda"]:
            por_fazenda.setdefault(_normalizar_brinco(a["brinco_fazenda"]), []).append(a)

    importados = []       # [{"arquivo":..., "brinco":..., "campo": "foto_1"}]
    sem_correspondencia = []
    ambiguos = []          # [{"arquivo":..., "brinco":..., "qtd": N}]
    sem_vaga = []          # [{"arquivo":..., "brinco":...}]
    erros = []             # [{"arquivo":..., "erro":...}]

    # Acumula as atualizacoes por animal (um mesmo animal pode receber mais
    # de uma foto nesta mesma importacao, ex: 0001_1.jpg e 0001_2.jpg).
    atualizacoes_por_animal = {}  # {animal_id: {"foto_1": url, ...}}
    slots_ja_usados = {}          # {animal_id: {"foto_1", "foto_2", ...} usados nesta importacao

    padrao_sufixo = re.compile(r"^(.+)[_\-]([123])$")

    for arquivo in arquivos:
        nome_arquivo = arquivo.filename
        base = os.path.splitext(nome_arquivo)[0]

        slot_forcado = None
        m = padrao_sufixo.match(base)
        if m:
            base, slot_forcado = m.group(1), int(m.group(2))

        chave = _normalizar_brinco(base)
        candidatos = por_faec.get(chave) or por_fazenda.get(chave) or []

        if len(candidatos) == 0:
            sem_correspondencia.append(nome_arquivo)
            continue
        if len(candidatos) > 1:
            ambiguos.append({"arquivo": nome_arquivo, "brinco": base, "qtd": len(candidatos)})
            continue

        animal = candidatos[0]
        ja_usados = slots_ja_usados.setdefault(animal["id"], set())
        estado_atual = {**dict(animal), **atualizacoes_por_animal.get(animal["id"], {})}

        if slot_forcado:
            campo = f"foto_{slot_forcado}"
        else:
            campo = next(
                (c for c in ("foto_1", "foto_2", "foto_3")
                 if not estado_atual.get(c) and c not in ja_usados),
                None,
            )

        if not campo or campo in ja_usados:
            sem_vaga.append({"arquivo": nome_arquivo, "brinco": animal["brinco_faec"]})
            continue

        try:
            url = enviar_bytes(
                arquivo.read(), nome_arquivo, arquivo.mimetype or "",
                bucket=BUCKET_ANIMAIS,
            )
        except Exception as e:
            erros.append({"arquivo": nome_arquivo, "erro": str(e)})
            continue

        atualizacoes_por_animal.setdefault(animal["id"], {})[campo] = url
        ja_usados.add(campo)
        importados.append({"arquivo": nome_arquivo, "brinco": animal["brinco_faec"], "campo": campo})

    for animal_id, campos in atualizacoes_por_animal.items():
        set_clause = ", ".join(f"{c} = ?" for c in campos)
        conn.execute(
            f"UPDATE animais SET {set_clause}, atualizado_em = now() WHERE id = ?",
            list(campos.values()) + [animal_id],
        )
    if atualizacoes_por_animal:
        conn.commit()
    conn.close()

    return render_template(
        "importar_fotos_lote.html",
        resultado={
            "importados": importados,
            "sem_correspondencia": sem_correspondencia,
            "ambiguos": ambiguos,
            "sem_vaga": sem_vaga,
            "erros": erros,
        },
    )


@app.route("/animais/<int:aid>/excluir", methods=["POST"])
def excluir_animal(aid):
    conn = get_connection()
    animal = conn.execute(
        "SELECT id, status, foto_1, foto_2, foto_3 FROM animais WHERE id = ?",
        (aid,),
    ).fetchone()
    if animal is None:
        conn.close()
        abort(404)
    if animal["status"] == "alocado":
        conn.close()
        flash("Desvincule o animal do produtor antes de excluí-lo.", "error")
        return redirect(url_for("animais"))

    conn.execute("DELETE FROM animais WHERE id = ?", (aid,))
    conn.commit()
    conn.close()
    for campo in ("foto_1", "foto_2", "foto_3"):
        if animal[campo]:
            try:
                excluir_arquivo(animal[campo])
            except Exception:
                pass
    flash("Animal excluído com sucesso.", "success")
    return redirect(url_for("animais"))


@app.route("/animais")
def animais():
    busca = request.args.get("q", "").strip()
    try:
        conn = get_connection()
    except Exception as erro:
        if not _erro_de_conexao(erro) and not isinstance(erro, RuntimeError):
            raise
        return render_template("offline.html")
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
    animais = conn.execute(
        "SELECT * FROM animais WHERE produtor_id = ? ORDER BY brinco_faec", (pid,)
    ).fetchall()
    conn.close()
    if row is None:
        abort(404)
    buf = generate_pdf(row, animais=animais)
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
    """Planilha com o cadastro completo dos produtores. Respeita os mesmos
    filtros da tela inicial (busca, município e ATEG)."""
    where_sql, params = _filtros_produtores(
        request.args.get("q", "").strip(),
        request.args.get("municipio", "").strip(),
        request.args.get("ateg", "").strip(),
    )
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT p.* FROM produtores p {where_sql} ORDER BY p.nome_produtor",
            params,
        ).fetchall()
    finally:
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


@app.route("/exportar-alocacoes-excel")
def exportar_alocacoes_excel():
    """Planilha com os produtores e os animais alocados a cada um."""
    conn = get_connection()
    try:
        buf = montar_planilha(conn)
    finally:
        conn.close()
    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="produtores_e_animais.xlsx",
    )


# Rota /exportar-animais-alocados (endpoint 'exportar_animais_alocados'),
# usada pelo botão da tela inicial (templates/list.html).
from exportar_animais import registrar_exportacao_animais
registrar_exportacao_animais(app)


@app.route("/debug-supabase")
def debug_supabase():
    """
    Rota TEMPORARIA de diagnostico: mostra, sem expor a chave inteira, se
    as variaveis de ambiente do Supabase Storage chegaram certas no
    ambiente publicado (Vercel/Render/etc). Protegida por um token simples
    (a propria SECRET_KEY do app) so pra nao ficar exposta a qualquer um
    que descubra a URL.

    Uso: /debug-supabase?token=<o valor da sua SECRET_KEY>

    IMPORTANTE: remova esta rota depois de terminar o diagnostico -- ela e
    so uma muleta temporaria, nao deve ficar num sistema publicado.
    """
    import supabase_storage as _ss

    token_esperado = os.environ.get("SECRET_KEY", "")
    if not token_esperado or request.args.get("token") != token_esperado:
        abort(404)

    def resumo_chave(valor):
        if not valor:
            return {"presente": False}
        partes = valor.split(".")
        return {
            "presente": True,
            "tamanho": len(valor),
            "numero_de_partes_separadas_por_ponto": len(partes),
            "partes_parecem_ok": len(partes) == 3 and all(partes),
            "comeca_com": valor[:6],
            "termina_com": valor[-6:],
            "tem_aspas_na_ponta": valor[:1] in ("'", '"') or valor[-1:] in ("'", '"'),
            "tem_espaco_ou_quebra_de_linha": any(c.isspace() for c in valor),
        }

    diagnostico = {
        "SUPABASE_URL": {
            "presente": bool(_ss.SUPABASE_URL),
            "valor": _ss.SUPABASE_URL or None,
        },
        "SUPABASE_SERVICE_KEY": resumo_chave(_ss.SUPABASE_SERVICE_KEY),
        "SUPABASE_BUCKET": _ss.BUCKET,
        "SUPABASE_BUCKET_ANIMAIS": _ss.BUCKET_ANIMAIS,
        "configurado()": _ss.configurado(),
    }
    return {"diagnostico_supabase": diagnostico}


if __name__ == "__main__":
    try:
        init_db()
        print(" Schema 'fiv' verificado no Supabase.")
    except Exception as erro:
        if not _erro_de_conexao(erro) and not isinstance(erro, RuntimeError):
            raise
        print(" Sem internet: iniciando em modo offline.")
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
