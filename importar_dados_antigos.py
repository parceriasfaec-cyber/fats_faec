"""
Importa os cadastros e as fotos do banco antigo (fats.db, SQLite) para o
Supabase (schema "FIV", tabela produtores).

COMO USAR:
    1. Coloque este arquivo dentro da pasta do projeto novo
       (a mesma pasta do app.py, database.py, .env etc.)
    2. Ajuste as 2 linhas abaixo, em "CONFIGURACOES", com os caminhos
       corretos do SEU computador.
    3. Rode:  python importar_dados_antigos.py
"""

import shutil
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import psycopg2
import psycopg2.extras

from database import DATABASE_URL, _base_dir

# =====================================================================
# CONFIGURACOES - ajuste estes 2 caminhos antes de rodar
# =====================================================================

# Caminho completo do banco antigo (fats.db). Exemplo:
# CAMINHO_DB_ANTIGO = r"C:\CIIAGRO2\FIV_SUPABASE_ANTIGO\fats.db"
CAMINHO_DB_ANTIGO = r"fats.db"

# Pasta onde estao as fotos antigas. Exemplo:
# PASTA_FOTOS_ANTIGAS = r"C:\CIIAGRO2\FIV_SUPABASE_ANTIGO\fotos"
PASTA_FOTOS_ANTIGAS = r"fotos"

# =====================================================================

# Campos da tabela NOVA, na ordem que serao inseridos (sem o "id", que e
# gerado automaticamente pelo Supabase)
CAMPOS_NOVOS = [
    "nome_produtor", "cpf", "data_nascimento", "telefone", "dap_caf",
    "nome_propriedade", "municipio", "comunidade", "car", "latitude",
    "longitude", "assistido_ateg", "tecnico_responsavel", "foto_produtor",
    "membros_residentes", "sucessao_familiar", "mao_obra", "fonte_renda",
    "fonte_agua", "seguranca_hidrica", "area_leite", "volume_diario",
    "vacas_lactacao", "vacas_secas", "novilhas", "touros",
    "produtividade_media", "destino_producao", "composicao_genetica",
    "grau_girolando", "curral_ordenha", "tipo_ordenha", "higiene_ordenha",
    "refrigeracao_leite", "capacidade_tanque", "tronco_contencao",
    "obs_infraestrutura", "silagem", "estoque_seca", "palma_forrageira",
    "area_palma", "capineira", "area_capineira", "suplementacao",
    "sal_mineral", "agua_bebedouros", "aptidao_receptoras",
    "qtd_receptoras", "ecc", "vacinacao_dia", "acompanhamento_vet",
    "parecer_matrizes", "parecer_fiv", "observacoes_tecnico",
    "nome_tecnico", "cpf_tecnico",
]


def _linha_para_dict(row_antiga: dict) -> dict:
    """Converte uma linha do banco antigo (que tinha 'car_coordenadas' em
    vez de 'car' / 'latitude' / 'longitude') para o formato novo."""
    dados = {}
    for campo in CAMPOS_NOVOS:
        if campo in row_antiga:
            dados[campo] = row_antiga[campo]
        elif campo == "car" and "car_coordenadas" in row_antiga:
            # Banco antigo nao tinha CAR separado de coordenadas: joga tudo
            # que tinha no campo antigo dentro do novo campo "car", e voce
            # ajusta manualmente latitude/longitude depois se precisar.
            dados[campo] = row_antiga.get("car_coordenadas") or ""
        else:
            dados[campo] = ""
    return dados


def main():
    if not DATABASE_URL:
        raise SystemExit(
            "DATABASE_URL nao encontrada. Confira se o .env esta "
            "preenchido nesta mesma pasta."
        )

    caminho_db = Path(CAMINHO_DB_ANTIGO)
    if not caminho_db.exists():
        raise SystemExit(
            f"Nao encontrei o banco antigo em: {caminho_db.resolve()}\n"
            "Ajuste a variavel CAMINHO_DB_ANTIGO no topo deste arquivo."
        )

    # -------- 1. Le os dados do banco antigo (SQLite) --------
    conn_antigo = sqlite3.connect(caminho_db)
    conn_antigo.row_factory = sqlite3.Row
    linhas = conn_antigo.execute("SELECT * FROM produtores").fetchall()
    linhas = [dict(r) for r in linhas]
    conn_antigo.close()
    print(f"Encontrados {len(linhas)} cadastro(s) no banco antigo.")

    if not linhas:
        print("Nada para importar.")
        return

    # -------- 2. Insere no Supabase --------
    conn_novo = psycopg2.connect(DATABASE_URL)
    with conn_novo.cursor() as cur:
        cur.execute('SET search_path TO "FIV", public')

    cols_sql = ", ".join(CAMPOS_NOVOS)
    placeholders = ", ".join(["%s"] * len(CAMPOS_NOVOS))
    sql_insert = f'INSERT INTO produtores ({cols_sql}) VALUES ({placeholders})'

    inseridos = 0
    with conn_novo.cursor() as cur:
        for linha in linhas:
            dados = _linha_para_dict(linha)
            valores = [dados[c] for c in CAMPOS_NOVOS]
            cur.execute(sql_insert, valores)
            inseridos += 1
    conn_novo.commit()
    conn_novo.close()
    print(f"[OK] {inseridos} cadastro(s) inserido(s) no Supabase.")

    # -------- 3. Copia as fotos --------
    pasta_fotos_antigas = Path(PASTA_FOTOS_ANTIGAS)
    pasta_fotos_novas = _base_dir() / "fotos"
    pasta_fotos_novas.mkdir(exist_ok=True)

    if not pasta_fotos_antigas.exists():
        print(f"[AVISO] Pasta de fotos antigas nao encontrada: "
              f"{pasta_fotos_antigas.resolve()} - fotos nao foram copiadas.")
        return

    copiadas = 0
    nomes_usados = {
        (linha.get("foto_produtor") or "").strip()
        for linha in linhas
        if (linha.get("foto_produtor") or "").strip()
    }
    for nome_arquivo in nomes_usados:
        origem = pasta_fotos_antigas / nome_arquivo
        destino = pasta_fotos_novas / nome_arquivo
        if origem.exists():
            shutil.copy2(origem, destino)
            copiadas += 1
        else:
            print(f"[AVISO] Foto nao encontrada: {origem}")
    print(f"[OK] {copiadas} foto(s) copiada(s) para {pasta_fotos_novas}")

    print()
    print("Importacao concluida! Abra o sistema (python app.py) e confira.")


if __name__ == "__main__":
    main()
