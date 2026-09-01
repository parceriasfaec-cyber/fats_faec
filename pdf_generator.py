import re
from io import BytesIO
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, Image
)
from reportlab.lib.enums import TA_CENTER

from database import _base_dir

PASTA_FOTOS = _base_dir() / "fotos"

styles = getSampleStyleSheet()

title_style = ParagraphStyle(
    "TitleFATS", parent=styles["Title"], fontSize=13, alignment=TA_CENTER, spaceAfter=2
)
subtitle_style = ParagraphStyle(
    "SubtitleFATS", parent=styles["Normal"], fontSize=9, alignment=TA_CENTER,
    textColor=colors.HexColor("#444444"), spaceAfter=1
)
section_style = ParagraphStyle(
    "Section", parent=styles["Heading2"], fontSize=10.5, spaceBefore=10, spaceAfter=4,
    textColor=colors.white, backColor=colors.HexColor("#2f5233"),
    leftIndent=4, borderPadding=(4, 4, 4, 4)
)
label_style = ParagraphStyle("Label", parent=styles["Normal"], fontSize=8.5, textColor=colors.HexColor("#555555"))
value_style = ParagraphStyle("Value", parent=styles["Normal"], fontSize=9.5, leading=12)
obs_style = ParagraphStyle("Obs", parent=styles["Normal"], fontSize=9, leading=12)


def _v(row, key):
    val = row[key] if key in row.keys() else None
    return val if val not in (None, "", "None") else "-"


def _v_data(row, key):
    """Mostra a data de nascimento sempre em dd/mm/aaaa, mesmo em registros
    antigos que foram salvos no formato aaaa-mm-dd."""
    val = _v(row, key)
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", val)
    if m:
        aaaa, mm, dd = m.groups()
        return f"{dd}/{mm}/{aaaa}"
    return val


def _v_cpf(row, key):
    """Mostra o CPF sempre com a mascara 000.000.000-00, mesmo em registros
    antigos salvos so com os digitos."""
    val = _v(row, key)
    digitos = re.sub(r"\D", "", val)
    if not digitos:
        return val
    if len(digitos) <= 3:
        return digitos
    if len(digitos) <= 6:
        return f"{digitos[0:3]}.{digitos[3:]}"
    if len(digitos) <= 9:
        return f"{digitos[0:3]}.{digitos[3:6]}.{digitos[6:]}"
    return f"{digitos[0:3]}.{digitos[3:6]}.{digitos[6:9]}-{digitos[9:11]}"


def _v_telefone(row, key):
    """Mostra o telefone sempre com mascara (00) 00000-0000 / (00) 0000-0000,
    mesmo em registros antigos salvos so com os digitos."""
    val = _v(row, key)
    digitos = re.sub(r"\D", "", val)
    if not digitos:
        return val
    if len(digitos) <= 2:
        return digitos
    if len(digitos) <= 6:
        return f"({digitos[0:2]}) {digitos[2:]}"
    if len(digitos) <= 10:
        return f"({digitos[0:2]}) {digitos[2:6]}-{digitos[6:]}"
    return f"({digitos[0:2]}) {digitos[2:7]}-{digitos[7:11]}"


def _field(label, value):
    return Table(
        [[Paragraph(label, label_style)], [Paragraph(str(value), value_style)]],
        colWidths=[None],
        style=TableStyle([
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
        ]),
    )


def _grid(pairs, cols=2):
    """pairs: list of (label, value). Lays out in a grid table with `cols` columns."""
    cells = [_field(l, v) for l, v in pairs]
    rows = []
    for i in range(0, len(cells), cols):
        row = cells[i:i + cols]
        while len(row) < cols:
            row.append("")
        rows.append(row)
    t = Table(rows, colWidths=[(17 * cm) / cols] * cols)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#dddddd")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _section(title):
    return Paragraph(title, section_style)


def generate_pdf(row) -> BytesIO:
    """row: sqlite3.Row (or dict-like) with all produtor fields."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
    )
    story = []

    titulo_paragrafos = [
        Paragraph("FICHA DE AVALIAÇÃO TÉCNICO-SOCIAL (FATS)", title_style),
        Paragraph("PROGRAMA MATRIZES DO AMANHÃ &amp; INTERFACE FIV CEARÁ", subtitle_style),
        Paragraph("SISTEMA FAEC / SENAR-CE — SEBRAE / GOVERNO DO CEARÁ", subtitle_style),
    ]

    foto_nome = _v(row, "foto_produtor")
    foto_imagem = None
    if foto_nome and foto_nome != "-":
        caminho_foto = PASTA_FOTOS / foto_nome
        if caminho_foto.exists():
            try:
                foto_imagem = Image(str(caminho_foto), width=2.6 * cm, height=2.6 * cm)
            except Exception:
                foto_imagem = None

    if foto_imagem:
        cabecalho = Table(
            [[foto_imagem, titulo_paragrafos]],
            colWidths=[3 * cm, 14 * cm],
        )
        cabecalho.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (0, 0), "CENTER"),
            ("ALIGN", (1, 0), (1, 0), "CENTER"),
            ("LEFTPADDING", (0, 0), (0, 0), 0),
            ("RIGHTPADDING", (0, 0), (0, 0), 8),
        ]))
        story.append(cabecalho)
    else:
        story.extend(titulo_paragrafos)

    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#2f5233"), spaceBefore=6, spaceAfter=6))

    # 1. Identificacao
    story.append(_section("1. IDENTIFICAÇÃO GERAL DO PRODUTOR E DA PROPRIEDADE"))
    story.append(_grid([
        ("Nome do Produtor", _v(row, "nome_produtor").upper()),
        ("CPF", _v_cpf(row, "cpf")),
        ("Data de Nascimento", _v_data(row, "data_nascimento")),
        ("Telefone / Zap", _v_telefone(row, "telefone")),
        ("DAP / CAF", _v(row, "dap_caf")),
        ("Nome da Propriedade", _v(row, "nome_propriedade").upper()),
        ("Município", _v(row, "municipio").upper()),
        ("Comunidade / Distrito", _v(row, "comunidade")),
        ("CAR", _v(row, "car")),
        ("Latitude", _v(row, "latitude")),
        ("Longitude", _v(row, "longitude")),
        ("Assistido ATeG?", _v(row, "assistido_ateg")),
        ("Técnico Responsável / Sindicato", _v(row, "tecnico_responsavel").upper()),
    ], cols=2))

    # 2. Perfil socioeconomico
    story.append(_section("2. PERFIL SOCIOECONÔMICO MÍNIMO E SEGURANÇA HÍDRICA"))
    story.append(_grid([
        ("Membros Residentes no Lote", _v(row, "membros_residentes")),
        ("Sucessão Familiar Identificada?", _v(row, "sucessao_familiar")),
        ("Mão de Obra na Pecuária", _v(row, "mao_obra")),
        ("Principal Fonte de Renda", _v(row, "fonte_renda")),
        ("Fonte de Água Primária", _v(row, "fonte_agua")),
        ("Segurança Hídrica (Estiagem)", _v(row, "seguranca_hidrica")),
    ], cols=2))

    # 3. Rebanho
    story.append(_section("3. PERFIL DO REBANHO E PRODUÇÃO LEITEIRA ATUAL"))
    story.append(_grid([
        ("Área Destinada ao Leite (ha)", _v(row, "area_leite")),
        ("Volume Diário Total (L/dia)", _v(row, "volume_diario")),
        ("Vacas em Lactação", _v(row, "vacas_lactacao")),
        ("Vacas Secas", _v(row, "vacas_secas")),
        ("Novilhas/Bezerras", _v(row, "novilhas")),
        ("Touros/Garrotes", _v(row, "touros")),
        ("Produtividade Média (L/vaca/dia)", _v(row, "produtividade_media")),
        ("Destino da Produção", _v(row, "destino_producao")),
        ("Composição Genética", _v(row, "composicao_genetica")),
        ("Grau Girolando Predominante", _v(row, "grau_girolando")),
    ], cols=2))

    # 4. Infraestrutura
    story.append(_section("4. INFRAESTRUTURA E INSTALAÇÕES DA PROPRIEDADE"))
    story.append(_grid([
        ("Curral / Ordenha", _v(row, "curral_ordenha")),
        ("Tipo de Ordenha", _v(row, "tipo_ordenha")),
        ("Higiene / Sanitização", _v(row, "higiene_ordenha")),
        ("Refrigeração do Leite", _v(row, "refrigeracao_leite")),
        ("Capacidade do Tanque (L)", _v(row, "capacidade_tanque")),
        ("Tronco de Contenção / Brete", _v(row, "tronco_contencao")),
    ], cols=2))
    story.append(_field("Observações / Ajustes Necessários", _v(row, "obs_infraestrutura")))
    story.append(Spacer(1, 8))

    # 5. Manejo alimentar
    story.append(_section("5. MANEJO ALIMENTAR E SUPORTE FORRAGEIRO"))
    story.append(_grid([
        ("Silagem (Milho/Sorgo)", _v(row, "silagem")),
        ("Estoque Estimado p/ Seca (meses)", _v(row, "estoque_seca")),
        ("Palma Forrageira", _v(row, "palma_forrageira")),
        ("Área Cultivada de Palma (ha)", _v(row, "area_palma")),
        ("Capineira (Capiaçu/Outros)", _v(row, "capineira")),
        ("Área de Capineira/Pasto (ha)", _v(row, "area_capineira")),
        ("Suplementação Concentrada", _v(row, "suplementacao")),
        ("Sal Mineral Específico p/ Leite?", _v(row, "sal_mineral")),
        ("Água Limpa e Abundante?", _v(row, "agua_bebedouros")),
    ], cols=2))

    # 6. Triagem FIV
    story.append(_section("6. TRIAGEM DE ELEGIBILIDADE E POTENCIAL PARA O PROJETO FIV CEARÁ"))
    story.append(_grid([
        ("Possui Fêmeas Aptas como Receptoras?", _v(row, "aptidao_receptoras")),
        ("Qtd. Receptoras Estimadas Aptas", _v(row, "qtd_receptoras")),
        ("Escore de Condição Corporal (ECC)", _v(row, "ecc")),
        ("Vacinação Brucelose/Tuberculose em Dia?", _v(row, "vacinacao_dia")),
        ("Acompanhamento Vet. Reprodutivo Regular?", _v(row, "acompanhamento_vet")),
    ], cols=2))

    # 7. Parecer
    story.append(_section("7. PARECER TÉCNICO DE ENQUADRAMENTO E RECOMENDAÇÕES"))
    story.append(_grid([
        ("Programa Matrizes do Amanhã", _v(row, "parecer_matrizes")),
        ("Projeto FIV Ceará", _v(row, "parecer_fiv")),
    ], cols=2))
    story.append(_field("Observações / Recomendações Prioritárias do Técnico", _v(row, "observacoes_tecnico")))
    story.append(Spacer(1, 14))

    story.append(_grid([
        ("Técnico Avaliador (FAEC/SENAR/SEBRAE)", _v(row, "nome_tecnico").upper()),
        ("CPF / Registro Profissional", _v(row, "cpf_tecnico")),
    ], cols=2))

    story.append(Spacer(1, 30))
    assinaturas = Table(
        [["_" * 40, "_" * 40], ["Assinatura do Técnico Avaliador", "Assinatura do Produtor (Beneficiário)"]],
        colWidths=[8.5 * cm, 8.5 * cm],
        style=TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTSIZE", (0, 1), (-1, 1), 8),
            ("TEXTCOLOR", (0, 1), (-1, 1), colors.HexColor("#555555")),
        ]),
    )
    story.append(assinaturas)

    doc.build(story)
    buf.seek(0)
    return buf
