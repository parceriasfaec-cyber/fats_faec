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

    -- Numero da "etapa"/rodada de visitas ATUAL desse produtor especifico.
    -- Cada produtor tem a sua propria contagem, porque nem todo mundo
    -- avanca de etapa ao mesmo tempo (tecnicos diferentes, ritmos
    -- diferentes). So conta como "ja visitado" no mapa quem tiver uma
    -- visita registrada com esse mesmo numero de etapa.
    etapa_atual INTEGER NOT NULL DEFAULT 1,

    criado_em TIMESTAMP WITH TIME ZONE DEFAULT now(),
    atualizado_em TIMESTAMP WITH TIME ZONE DEFAULT now()
);

-- Cadastro dos animais (receptoras) do lote FIV. Cada animal e cadastrado
-- primeiro (com suas fotos), e so depois e vinculado a um produtor (quando
-- o sorteio/distribuicao entre produtores for feito).
CREATE TABLE IF NOT EXISTS "FIV".animais (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    brinco_faec TEXT,      -- numero sequencial do brinco FAEC (ex: "0001")
    brinco_fazenda TEXT,   -- identificacao do brinco na fazenda (ex: "V 1452")
    peso TEXT,             -- peso do animal em kg (ex: "320,5")

    foto_1 TEXT,
    foto_2 TEXT,
    foto_3 TEXT,

    -- Preenchido depois, quando o animal for sorteado/atribuido a um
    -- produtor. Fica NULL ate la.
    produtor_id BIGINT REFERENCES "FIV".produtores(id) ON DELETE SET NULL,

    -- "disponivel" (ainda sem produtor) ou "alocado" (ja distribuido)
    status TEXT NOT NULL DEFAULT 'disponivel',

    criado_em TIMESTAMP WITH TIME ZONE DEFAULT now(),
    atualizado_em TIMESTAMP WITH TIME ZONE DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_animais_produtor ON "FIV".animais(produtor_id);
CREATE INDEX IF NOT EXISTS idx_animais_status ON "FIV".animais(status);

-- Historico de visitas de cada produtor. Um mesmo produtor pode ter varias
-- visitas ao longo do tempo, cada uma com um motivo diferente (cadastro,
-- entrega de material, acompanhamento dos animais, etc).
CREATE TABLE IF NOT EXISTS "FIV".visitas (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    produtor_id BIGINT NOT NULL REFERENCES "FIV".produtores(id) ON DELETE CASCADE,

    data_visita TEXT,    -- data da visita, no formato dd/mm/aaaa
    tipo_visita TEXT,    -- motivo da visita (ver TIPOS_VISITA em campos.py)
    observacoes TEXT,

    -- Numero da etapa do PRODUTOR no momento em que essa visita foi
    -- registrada (copiado de produtores.etapa_atual na hora do insert).
    -- E assim que o mapa sabe se essa visita "ainda vale" pra marcar o
    -- pino de cinza, ou se ja e de uma etapa anterior daquele produtor.
    etapa INTEGER NOT NULL DEFAULT 1,

    criado_em TIMESTAMP WITH TIME ZONE DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_visitas_produtor ON "FIV".visitas(produtor_id);
CREATE INDEX IF NOT EXISTS idx_visitas_etapa ON "FIV".visitas(etapa);

-- Migracao: garante a coluna peso em bancos que ja tinham a tabela
-- animais criada antes desta mudanca (CREATE TABLE IF NOT EXISTS acima
-- nao adiciona coluna em tabela que ja existe).
ALTER TABLE "FIV".animais ADD COLUMN IF NOT EXISTS peso TEXT;

-- Migracao: garante a coluna etapa_atual em bancos que ja tinham a
-- tabela produtores criada antes desta mudanca.
ALTER TABLE "FIV".produtores ADD COLUMN IF NOT EXISTS etapa_atual INTEGER NOT NULL DEFAULT 1;

-- Migracao: garante a coluna etapa em bancos que ja tinham a tabela
-- visitas criada antes desta mudanca.
ALTER TABLE "FIV".visitas ADD COLUMN IF NOT EXISTS etapa INTEGER NOT NULL DEFAULT 1;

-- Se voce ja tinha rodado a versao anterior desta migracao (que criava
-- uma tabela "FIV".config com uma etapa global unica), ela nao e mais
-- usada e pode ser apagada - mas deixar ela existindo tambem nao faz
-- diferenca nenhuma, entao nao mexemos nela automaticamente aqui.
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
