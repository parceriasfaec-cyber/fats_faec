import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras


def _base_dir() -> Path:
    """Pasta onde ficam os arquivos auxiliares do app (hoje, so a pasta
    de fotos - os dados em si agora vivem no Supabase, nao mais em um
    arquivo .db local).

    Quando o programa roda como .exe gerado pelo PyInstaller, os arquivos
    do app ficam numa pasta temporaria que e apagada ao fechar o programa.
    Por isso, a pasta de fotos fica ao lado do proprio .exe."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


# ------------------------------------------------------------------
# Conexão com o Supabase (PostgreSQL)
#
# A string de conexão fica na variável de ambiente DATABASE_URL.
# Você encontra ela no Supabase em:
#   Project Settings -> Database -> Connection string -> "URI"
# Ela se parece com:
#   postgresql://postgres.xxxxxxxx:SUA_SENHA@aws-0-xxxx.pooler.supabase.com:6543/postgres
#
# Como definir no Windows (PowerShell), antes de rodar o app:
#   $env:DATABASE_URL = "postgresql://postgres...sua-string-aqui"
#
# Ou crie um arquivo ".env" na pasta do projeto com:
#   DATABASE_URL=postgresql://postgres...sua-string-aqui
# (o app carrega esse arquivo automaticamente se o pacote python-dotenv
#  estiver instalado - veja requirements.txt)
# ------------------------------------------------------------------

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DATABASE_URL = os.environ.get("DATABASE_URL")

# Todas as tabelas do sistema vivem dentro do schema FIV (maiusculo) no
# Supabase. Como o nome tem letra maiuscula, o Postgres exige aspas duplas
# em toda referencia a ele (sem aspas, ele procuraria por "fiv" minusculo).
ESQUEMA = '"FIV"'


SCHEMA = """
CREATE SCHEMA IF NOT EXISTS "FIV";

CREATE TABLE IF NOT EXISTS "FIV".produtores (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    nome_produtor TEXT,
    cpf TEXT,
    data_nascimento TEXT,
    telefone TEXT,
    dap_caf TEXT,
    nome_propriedade TEXT,
    municipio TEXT,
    comunidade TEXT,
    car TEXT,
    latitude TEXT,
    longitude TEXT,
    assistido_ateg TEXT,
    tecnico_responsavel TEXT,
    foto_produtor TEXT,

    membros_residentes TEXT,
    sucessao_familiar TEXT,
    mao_obra TEXT,
    fonte_renda TEXT,
    fonte_agua TEXT,
    seguranca_hidrica TEXT,

    area_leite TEXT,
    volume_diario TEXT,
    vacas_lactacao TEXT,
    vacas_secas TEXT,
    novilhas TEXT,
    touros TEXT,
    produtividade_media TEXT,
    destino_producao TEXT,
    composicao_genetica TEXT,
    grau_girolando TEXT,

    curral_ordenha TEXT,
    tipo_ordenha TEXT,
    higiene_ordenha TEXT,
    refrigeracao_leite TEXT,
    capacidade_tanque TEXT,
    tronco_contencao TEXT,
    obs_infraestrutura TEXT,

    silagem TEXT,
    estoque_seca TEXT,
    palma_forrageira TEXT,
    area_palma TEXT,
    capineira TEXT,
    area_capineira TEXT,
    suplementacao TEXT,
    sal_mineral TEXT,
    agua_bebedouros TEXT,

    aptidao_receptoras TEXT,
    qtd_receptoras TEXT,
    ecc TEXT,
    vacinacao_dia TEXT,
    acompanhamento_vet TEXT,

    parecer_matrizes TEXT,
    parecer_fiv TEXT,
    observacoes_tecnico TEXT,
    nome_tecnico TEXT,
    cpf_tecnico TEXT,

    criado_em TIMESTAMP WITH TIME ZONE DEFAULT now(),
    atualizado_em TIMESTAMP WITH TIME ZONE DEFAULT now()
);
"""


class _CursorProxy:
    """Faz a conexão do psycopg2 se comportar como a conexão do sqlite3
    que o resto do app.py já sabia usar: conn.execute(sql, params) e
    depois .fetchone()/.fetchall() no valor retornado."""

    def __init__(self, conn):
        self._conn = conn
        self._cursor = None

    def execute(self, sql, params=()):
        # sqlite3 usa "?" como placeholder, psycopg2 usa "%s"
        sql_pg = sql.replace("?", "%s")
        self._cursor = self._conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        )
        self._cursor.execute(sql_pg, params)
        return self

    def executescript(self, sql):
        self._cursor = self._conn.cursor()
        self._cursor.execute(sql)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def commit(self):
        self._conn.commit()

    def close(self):
        if self._cursor is not None:
            self._cursor.close()
        self._conn.close()


def get_connection():
    if not DATABASE_URL:
        raise RuntimeError(
            "A variavel de ambiente DATABASE_URL nao foi definida. "
            "Configure a string de conexao do Supabase antes de rodar o app "
            "(veja as instrucoes no topo do arquivo database.py)."
        )
    conn = psycopg2.connect(DATABASE_URL)
    # Faz todas as consultas desta conexao olharem primeiro para o schema "fiv"
    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {ESQUEMA}, public")
    conn.commit()
    return _CursorProxy(conn)


def init_db():
    conn = psycopg2.connect(DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Schema 'fiv' e tabela 'produtores' verificados/criados no Supabase.")
