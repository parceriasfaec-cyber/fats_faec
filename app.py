import io
import os
import re
import sys
import uuid
from pathlib import Path
from flask import (
    Flask, render_template, request, redirect, url_for, flash, send_file,
    send_from_directory, abort
)
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from database import get_connection, init_db, _base_dir
from pdf_generator import generate_pdf


# Pasta onde as fotos dos produtores ficam salvas (ao lado do banco de
# dados, para nao se perder quando o programa roda como .exe)
# Pasta onde as fotos dos produtores ficam salvas. Em producao (Render),
# aponte a variavel de ambiente FOTOS_DIR para o disco persistente
# (ex: /var/data/fotos). Localmente, sem essa variavel, continua usando a
# pasta "fotos" do lado do proprio app.py (como sempre foi).
UPLOAD_DIR = Path(os.environ["FOTOS_DIR"]) if os.environ.get("FOTOS_DIR") else (_base_dir() / "fotos")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
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


def _salvar_foto(arquivo) -> str:
    """Salva o arquivo de foto enviado e devolve o nome gerado para ele."""
    ext = Path(arquivo.filename or "").suffix.lower()
    if ext not in EXTENSOES_PERMITIDAS:
        ext = ".jpg"
    nome = f"{uuid.uuid4().hex}{ext}"
    arquivo.save(UPLOAD_DIR / nome)
    return nome


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
app.jinja_env.filters["mascara_telefone"] = _formatar_telefone


@app.route("/fotos/<path:nome>")
def foto(nome):
    return send_from_directory(str(UPLOAD_DIR), nome)

# Todos os campos do formulario, na ordem em que aparecem na ficha
FIELDS = [
    "nome_produtor", "cpf", "data_nascimento", "telefone", "dap_caf",
    "nome_propriedade", "municipio", "comunidade", "car", "latitude", "longitude",
    "assistido_ateg", "tecnico_responsavel", "foto_produtor",

    "membros_residentes", "sucessao_familiar", "mao_obra", "fonte_renda",
    "fonte_agua", "seguranca_hidrica",

    "area_leite", "volume_diario", "vacas_lactacao", "vacas_secas",
    "novilhas", "touros", "produtividade_media", "destino_producao",
    "composicao_genetica", "grau_girolando",

    "curral_ordenha", "tipo_ordenha", "higiene_ordenha", "refrigeracao_leite",
    "capacidade_tanque", "tronco_contencao", "obs_infraestrutura",

    "silagem", "estoque_seca", "palma_forrageira", "area_palma",
    "capineira", "area_capineira", "suplementacao", "sal_mineral",
    "agua_bebedouros",

    "aptidao_receptoras", "qtd_receptoras", "ecc", "vacinacao_dia",
    "acompanhamento_vet",

    "parecer_matrizes", "parecer_fiv", "observacoes_tecnico",
    "nome_tecnico", "cpf_tecnico",
]

# Rotulos (cabecalhos) legiveis para cada campo, usados na exportacao em Excel
FIELD_LABELS = {
    "nome_produtor": "Nome do Produtor",
    "cpf": "CPF",
    "data_nascimento": "Data de Nascimento",
    "telefone": "Telefone / Zap",
    "dap_caf": "DAP / CAF",
    "nome_propriedade": "Nome da Propriedade",
    "municipio": "Município",
    "comunidade": "Comunidade / Distrito",
    "car": "CAR",
    "latitude": "Latitude",
    "longitude": "Longitude",
    "assistido_ateg": "Assistido ATeG?",
    "tecnico_responsavel": "Técnico Responsável / Sindicato",
    "foto_produtor": "Foto do Produtor (arquivo)",
    "membros_residentes": "Membros Residentes no Lote (pessoas)",
    "sucessao_familiar": "Sucessão Familiar Identificada?",
    "mao_obra": "Mão de Obra na Pecuária",
    "fonte_renda": "Principal Fonte de Renda",
    "fonte_agua": "Fonte de Água Primária",
    "seguranca_hidrica": "Segurança Hídrica (Estiagem)",
    "area_leite": "Área Destinada ao Leite (ha)",
    "volume_diario": "Volume Diário Total (L/dia)",
    "vacas_lactacao": "Vacas em Lactação",
    "vacas_secas": "Vacas Secas",
    "novilhas": "Novilhas/Bezerras",
    "touros": "Touros/Garrotes",
    "produtividade_media": "Produtividade Média (L/vaca/dia)",
    "destino_producao": "Destino da Produção",
    "composicao_genetica": "Composição Genética",
    "grau_girolando": "Grau Girolando Predominante",
    "curral_ordenha": "Curral / Ordenha",
    "tipo_ordenha": "Tipo de Ordenha",
    "higiene_ordenha": "Higiene / Sanitização",
    "refrigeracao_leite": "Refrigeração do Leite",
    "capacidade_tanque": "Capacidade do Tanque (L)",
    "tronco_contencao": "Tronco de Contenção / Brete",
    "obs_infraestrutura": "Observações / Ajustes Necessários",
    "silagem": "Silagem (Milho/Sorgo)",
    "estoque_seca": "Estoque Estimado p/ Seca (meses)",
    "palma_forrageira": "Palma Forrageira",
    "area_palma": "Área Cultivada de Palma (ha/tarefas)",
    "capineira": "Capineira (Capiaçu/Outros)",
    "area_capineira": "Área de Capineira/Pasto (ha)",
    "suplementacao": "Suplementação Concentrada",
    "sal_mineral": "Sal Mineral Específico p/ Leite?",
    "agua_bebedouros": "Água Limpa e Abundante nos Bebedouros?",
    "aptidao_receptoras": "Possui Fêmeas Aptas como Receptoras?",
    "qtd_receptoras": "Qtd. Receptoras Estimadas Aptas (cabeças)",
    "ecc": "Escore de Condição Corporal (ECC)",
    "vacinacao_dia": "Vacinação Brucelose/Tuberculose em Dia?",
    "acompanhamento_vet": "Acompanhamento Vet. Reprodutivo Regular?",
    "parecer_matrizes": "Programa Matrizes do Amanhã",
    "parecer_fiv": "Projeto FIV Ceará",
    "observacoes_tecnico": "Observações / Recomendações Prioritárias do Técnico",
    "nome_tecnico": "Técnico Avaliador (Nome)",
    "cpf_tecnico": "CPF / Registro Profissional do Técnico",
}

# Lista dos 184 municípios do Ceará (ordem alfabética), usada no menu
# suspenso do campo "Município" para evitar erros de digitação.
MUNICIPIOS_CEARA = [
    "Abaiara", "Acarapé", "Acaraú", "Acopiara", "Aiuaba", "Alcântaras",
    "Altaneira", "Alto Santo", "Amontada", "Antonina do Norte", "Apuiarés",
    "Aquiraz", "Aracati", "Aracoiaba", "Ararendá", "Araripe", "Aratuba",
    "Arneiroz", "Assaré", "Aurora", "Baixio", "Banabuiú", "Barbalha",
    "Barreira", "Barro", "Barroquinha", "Baturité", "Beberibe", "Bela Cruz",
    "Boa Viagem", "Brejo Santo", "Camocim", "Campos Sales", "Canindé",
    "Capistrano", "Caridade", "Cariré", "Caririaçu", "Cariús", "Carnaubal",
    "Cascavel", "Catarina", "Catunda", "Caucaia", "Cedro", "Chaval",
    "Choró", "Chorozinho", "Coreaú", "Crateús", "Crato", "Croatá", "Cruz",
    "Deputado Irapuan Pinheiro", "Ererê", "Eusébio", "Farias Brito",
    "Forquilha", "Fortaleza", "Fortim", "Frecheirinha", "General Sampaio",
    "Graça", "Granja", "Granjeiro", "Groaíras", "Guaiúba",
    "Guaraciaba do Norte", "Guaramiranga", "Hidrolândia", "Horizonte",
    "Ibaretama", "Ibiapina", "Ibicuitinga", "Icapuí", "Icó", "Iguatu",
    "Independência", "Ipaporanga", "Ipaumirim", "Ipu", "Ipueiras",
    "Iracema", "Irauçuba", "Itaiçaba", "Itaitinga", "Itapajé", "Itapipoca",
    "Itapiúna", "Itarema", "Itatira", "Jaguaretama", "Jaguaribara",
    "Jaguaribe", "Jaguaruana", "Jardim", "Jati", "Jijoca de Jericoacoara",
    "Juazeiro do Norte", "Jucás", "Lavras da Mangabeira",
    "Limoeiro do Norte", "Madalena", "Maracanaú", "Maranguape", "Marco",
    "Martinópole", "Massapê", "Mauriti", "Meruoca", "Milagres", "Milhã",
    "Miraíma", "Missão Velha", "Mombaça", "Monsenhor Tabosa",
    "Morada Nova", "Moraújo", "Morrinhos", "Mucambo", "Mulungu",
    "Nova Olinda", "Nova Russas", "Novo Oriente", "Ocara", "Orós",
    "Pacajus", "Pacatuba", "Pacoti", "Pacujá", "Palhano", "Palmácia",
    "Paracuru", "Paraipaba", "Parambu", "Paramoti", "Pedra Branca",
    "Penaforte", "Pentecoste", "Pereiro", "Pindoretama", "Piquet Carneiro",
    "Pires Ferreira", "Poranga", "Porteiras", "Potengi", "Potiretama",
    "Quiterianópolis", "Quixadá", "Quixelô", "Quixeramobim", "Quixeré",
    "Redenção", "Reriutaba", "Russas", "Saboeiro", "Salitre",
    "Santana do Acaraú", "Santana do Cariri", "Santa Quitéria",
    "São Benedito", "São Gonçalo do Amarante", "São João do Jaguaribe",
    "São Luís do Curu", "Senador Pompeu", "Senador Sá", "Sobral",
    "Solonópole", "Tabuleiro do Norte", "Tamboril", "Tarrafas", "Tauá",
    "Tejuçuoca", "Tianguá", "Trairi", "Tururu", "Ubajara", "Umari",
    "Umirim", "Uruburetama", "Uruoca", "Varjota", "Várzea Alegre",
    "Viçosa do Ceará",
]

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
    return render_template("list.html", produtores=rows, busca=busca)


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


def _salvar(pid):
    dados = {}
    for f in FIELDS:
        if f == "foto_produtor":
            arquivo = request.files.get("foto_produtor")
            if arquivo and arquivo.filename:
                dados[f] = _salvar_foto(arquivo)
            else:
                dados[f] = _foto_atual(pid)
            continue
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


@app.route("/excluir/<int:pid>", methods=["POST"])
def excluir(pid):
    conn = get_connection()
    conn.execute("DELETE FROM produtores WHERE id = ?", (pid,))
    conn.commit()
    conn.close()
    flash("Cadastro excluído.", "success")
    return redirect(url_for("index"))


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
            elif f == "telefone":
                v = _formatar_telefone(v)
            valores.append(v)
        linha = [row["id"]] + valores + [row["criado_em"], row["atualizado_em"]]
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
