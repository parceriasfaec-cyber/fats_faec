# -*- coding: utf-8 -*-
"""
Planilha de PRODUTORES x ANIMAIS ALOCADOS (lote FIV).

Gera um arquivo .xlsx com 2 abas:

  1. "Animais por produtor"  - 1 linha por animal alocado, com os dados
                               do produtor ao lado (ordenado por produtor).
  2. "Resumo por produtor"   - 1 linha por produtor (inclusive quem ainda
                               nao recebeu nenhum animal), com a quantidade
                               e a lista de brincos FAEC.

DUAS FORMAS DE USAR
-------------------
* Pelo sistema: tela "Animais" -> botao "Exportar Excel (alocados)".
* Direto pelo terminal (na pasta do projeto, com o .env configurado):

      python exportar_alocacoes.py
      python exportar_alocacoes.py meu_arquivo.xlsx

  Sem nome de arquivo, salva como "produtores_e_animais.xlsx".
"""

import io
import re
import sys
from collections import defaultdict

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

COR_CABECALHO = "1F4E2C"  # mesmo verde da exportacao de produtores
FONTE = "Arial"

SQL_ALOCADOS = """
    SELECT p.id AS produtor_id, p.nome_produtor, p.cpf, p.municipio,
           p.nome_propriedade, p.tecnico_responsavel,
           a.brinco_faec, a.brinco_fazenda, a.peso
    FROM animais a
    JOIN produtores p ON p.id = a.produtor_id
    ORDER BY p.nome_produtor, a.brinco_faec
"""

SQL_PRODUTORES = """
    SELECT p.id AS produtor_id, p.nome_produtor, p.cpf, p.municipio,
           p.nome_propriedade, p.tecnico_responsavel
    FROM produtores p
    ORDER BY p.nome_produtor
"""


def _cpf(valor):
    """Mascara 000.000.000-00 (igual a usada no resto do sistema)."""
    d = re.sub(r"\D", "", valor or "")[:11]
    if len(d) <= 3:
        return d
    if len(d) <= 6:
        return f"{d[0:3]}.{d[3:]}"
    if len(d) <= 9:
        return f"{d[0:3]}.{d[3:6]}.{d[6:]}"
    return f"{d[0:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"


def _maiusculo(valor):
    return (valor or "").upper()


def _estilizar_aba(ws, headers, linhas_dados):
    """Cabecalho verde, congela a 1a linha, filtro e larguras automaticas."""
    fill = PatternFill(start_color=COR_CABECALHO, end_color=COR_CABECALHO,
                       fill_type="solid")
    for c in ws[1]:
        c.fill = fill
        c.font = Font(name=FONTE, size=10, bold=True, color="FFFFFF")
        c.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"

    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.font = Font(name=FONTE, size=10)
            c.alignment = Alignment(vertical="top")

    for i, header in enumerate(headers, start=1):
        largura = len(str(header))
        for row_cells in ws.iter_rows(min_col=i, max_col=i, min_row=2):
            v = row_cells[0].value
            if v is not None and not str(v).startswith("="):
                largura = max(largura, len(str(v)))
        ws.column_dimensions[get_column_letter(i)].width = min(max(largura + 2, 10), 55)

    if linhas_dados:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{linhas_dados + 1}"


def montar_planilha(conn):
    """Recebe uma conexao (a do database.get_connection()) e devolve um
    io.BytesIO com o .xlsx pronto."""
    alocados = conn.execute(SQL_ALOCADOS).fetchall()
    produtores = conn.execute(SQL_PRODUTORES).fetchall()

    wb = Workbook()

    # ---------------- Aba 1: 1 linha por animal alocado ----------------
    ws1 = wb.active
    ws1.title = "Animais por produtor"
    h1 = ["Produtor", "CPF", "Município", "Propriedade", "Técnico responsável",
          "Brinco FAEC", "Brinco Fazenda", "Peso (kg)"]
    ws1.append(h1)

    brincos_por_produtor = defaultdict(list)
    for r in alocados:
        ws1.append([
            _maiusculo(r["nome_produtor"]), _cpf(r["cpf"]),
            _maiusculo(r["municipio"]), _maiusculo(r["nome_propriedade"]),
            _maiusculo(r["tecnico_responsavel"]),
            r["brinco_faec"], r["brinco_fazenda"], r["peso"],
        ])
        brincos_por_produtor[r["produtor_id"]].append(r["brinco_faec"] or "")
    _estilizar_aba(ws1, h1, len(alocados))

    # ---------------- Aba 2: 1 linha por produtor ----------------
    ws2 = wb.create_sheet("Resumo por produtor")
    h2 = ["Produtor", "CPF", "Município", "Propriedade", "Técnico responsável",
          "Qtd. animais", "Brincos FAEC"]
    ws2.append(h2)
    for r in produtores:
        brincos = brincos_por_produtor.get(r["produtor_id"], [])
        ws2.append([
            _maiusculo(r["nome_produtor"]), _cpf(r["cpf"]),
            _maiusculo(r["municipio"]), _maiusculo(r["nome_propriedade"]),
            _maiusculo(r["tecnico_responsavel"]),
            len(brincos), ", ".join(brincos),
        ])
    _estilizar_aba(ws2, h2, len(produtores))
    ws2.column_dimensions["G"].width = 55
    for row in ws2.iter_rows(min_row=2, min_col=7, max_col=7):
        row[0].alignment = Alignment(vertical="top", wrap_text=True)

    # Linha de total (formula, para acompanhar se voce filtrar/editar)
    if produtores:
        ultima = len(produtores) + 1
        linha_total = ultima + 2  # deixa 1 linha em branco antes do total
        ws2.cell(row=linha_total, column=1, value="TOTAL DE ANIMAIS ALOCADOS")
        ws2.cell(row=linha_total, column=6, value=f"=SUM(F2:F{ultima})")
        for col in (1, 6):
            ws2.cell(row=linha_total, column=col).font = Font(name=FONTE, size=10, bold=True)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


if __name__ == "__main__":
    from database import get_connection

    destino = sys.argv[1] if len(sys.argv) > 1 else "produtores_e_animais.xlsx"
    conn = get_connection()
    try:
        arquivo = montar_planilha(conn)
    finally:
        conn.close()
    with open(destino, "wb") as f:
        f.write(arquivo.getvalue())
    print(f"Planilha salva em: {destino}")
