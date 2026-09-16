-- ============================================================
-- Script de criação do schema FIV no Supabase (PostgreSQL)
-- Sistema FATS - Ficha de Avaliação Técnica e Socioeconômica
--
-- COMO USAR:
-- 1. Entre no seu projeto em https://supabase.com/dashboard
-- 2. Vá em "SQL Editor" (menu lateral esquerdo)
-- 3. Clique em "New query", cole todo este conteúdo e clique em "Run"
-- ============================================================

-- 1. Cria o schema (o "esquema") chamado FIV (maiusculo)
-- IMPORTANTE: usamos aspas em "FIV" porque no Postgres, sem aspas, todo
-- nome vira minusculo automaticamente. Como o schema foi criado como FIV
-- (maiusculo), toda referencia a ele precisa vir entre aspas duplas.
CREATE SCHEMA IF NOT EXISTS "FIV";

-- 2. Cria a tabela produtores dentro do schema FIV
CREATE TABLE IF NOT EXISTS "FIV".produtores (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- 1. Identificação geral
    nome_produtor       TEXT,
    cpf                 TEXT,
    data_nascimento     TEXT,
    telefone            TEXT,
    dap_caf             TEXT,
    nome_propriedade    TEXT,
    municipio           TEXT,
    comunidade          TEXT,
    car                 TEXT,
    latitude            TEXT,
    longitude           TEXT,
    assistido_ateg      TEXT,
    tecnico_responsavel TEXT,
    foto_produtor       TEXT,

    -- 2. Perfil socioeconômico e segurança hídrica
    membros_residentes  TEXT,
    sucessao_familiar   TEXT,
    mao_obra            TEXT,
    fonte_renda         TEXT,
    fonte_agua          TEXT,
    seguranca_hidrica   TEXT,

    -- 3. Rebanho e produção leiteira
    area_leite           TEXT,
    volume_diario        TEXT,
    vacas_lactacao       TEXT,
    vacas_secas          TEXT,
    novilhas             TEXT,
    touros               TEXT,
    produtividade_media  TEXT,
    destino_producao     TEXT,
    composicao_genetica  TEXT,
    grau_girolando       TEXT,

    -- 4. Infraestrutura e instalações
    curral_ordenha       TEXT,
    tipo_ordenha         TEXT,
    higiene_ordenha      TEXT,
    refrigeracao_leite   TEXT,
    capacidade_tanque    TEXT,
    tronco_contencao     TEXT,
    obs_infraestrutura   TEXT,

    -- 5. Manejo alimentar e suporte forrageiro
    silagem              TEXT,
    estoque_seca         TEXT,
    palma_forrageira     TEXT,
    area_palma           TEXT,
    capineira            TEXT,
    area_capineira       TEXT,
    suplementacao        TEXT,
    sal_mineral          TEXT,
    agua_bebedouros      TEXT,

    -- 6. Triagem FIV Ceará
    aptidao_receptoras   TEXT,
    qtd_receptoras       TEXT,
    ecc                  TEXT,
    vacinacao_dia        TEXT,
    acompanhamento_vet   TEXT,

    -- 7. Parecer técnico
    parecer_matrizes     TEXT,
    parecer_fiv          TEXT,
    observacoes_tecnico  TEXT,
    nome_tecnico         TEXT,
    cpf_tecnico          TEXT,

    -- Etapa/rodada de visitas ATUAL deste produtor (cada produtor tem a
    -- sua própria contagem independente - ver tabela visitas mais abaixo).
    etapa_atual   INTEGER NOT NULL DEFAULT 1,

    criado_em     TIMESTAMP WITH TIME ZONE DEFAULT now(),
    atualizado_em TIMESTAMP WITH TIME ZONE DEFAULT now()
);

-- 3. Índices para acelerar a busca usada na tela inicial
CREATE INDEX IF NOT EXISTS idx_produtores_nome       ON "FIV".produtores (nome_produtor);
CREATE INDEX IF NOT EXISTS idx_produtores_cpf         ON "FIV".produtores (cpf);
CREATE INDEX IF NOT EXISTS idx_produtores_municipio   ON "FIV".produtores (municipio);
CREATE INDEX IF NOT EXISTS idx_produtores_propriedade ON "FIV".produtores (nome_propriedade);

-- 4. Histórico de visitas de cada produtor. Um mesmo produtor pode ter
-- várias visitas ao longo do tempo (cadastro, entrega de material,
-- acompanhamento dos animais, etc) - cada uma vira uma linha aqui.
CREATE TABLE IF NOT EXISTS "FIV".visitas (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    produtor_id BIGINT NOT NULL REFERENCES "FIV".produtores(id) ON DELETE CASCADE,

    data_visita TEXT,    -- data da visita, no formato dd/mm/aaaa
    tipo_visita TEXT,    -- motivo da visita
    observacoes TEXT,

    -- Etapa do PRODUTOR (produtores.etapa_atual) no momento em que essa
    -- visita foi registrada. Cada produtor avança de etapa no seu próprio
    -- ritmo - não é uma contagem única pro sistema inteiro.
    etapa INTEGER NOT NULL DEFAULT 1,

    criado_em TIMESTAMP WITH TIME ZONE DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_visitas_produtor ON "FIV".visitas (produtor_id);
CREATE INDEX IF NOT EXISTS idx_visitas_etapa ON "FIV".visitas (etapa);

-- ============================================================
-- MIGRAÇÃO (rode só se você já tinha criado a tabela ANTES desta
-- mudança, ou seja, se ela ainda tem a coluna antiga car_coordenadas
-- em vez de car / latitude / longitude). Se a tabela ainda não existia,
-- pode ignorar este bloco: o CREATE TABLE acima já cria do jeito certo.
-- ============================================================
ALTER TABLE "FIV".produtores DROP COLUMN IF EXISTS car_coordenadas;
ALTER TABLE "FIV".produtores ADD COLUMN IF NOT EXISTS car TEXT;
ALTER TABLE "FIV".produtores ADD COLUMN IF NOT EXISTS latitude TEXT;
ALTER TABLE "FIV".produtores ADD COLUMN IF NOT EXISTS longitude TEXT;

-- Adiciona o campo de peso na tabela de animais (rode este comando se
-- a tabela FIV.animais já existia antes desta mudança).
ALTER TABLE "FIV".animais ADD COLUMN IF NOT EXISTS peso TEXT;

-- Cria a tabela de histórico de visitas (rode este comando se o banco já
-- existia antes desta mudança - o CREATE TABLE lá em cima já resolve se o
-- banco for novo).
CREATE TABLE IF NOT EXISTS "FIV".visitas (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    produtor_id BIGINT NOT NULL REFERENCES "FIV".produtores(id) ON DELETE CASCADE,
    data_visita TEXT,
    tipo_visita TEXT,
    observacoes TEXT,
    etapa INTEGER NOT NULL DEFAULT 1,
    criado_em TIMESTAMP WITH TIME ZONE DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_visitas_produtor ON "FIV".visitas (produtor_id);
CREATE INDEX IF NOT EXISTS idx_visitas_etapa ON "FIV".visitas (etapa);

-- Adiciona a coluna de etapa_atual em produtores e a coluna de etapa em
-- visitas (rode este comando se essas tabelas já existiam antes desta
-- mudança, sem essas colunas). Cada produtor guarda a SUA própria etapa
-- atual, então times diferentes podem avançar em ritmos diferentes.
ALTER TABLE "FIV".produtores ADD COLUMN IF NOT EXISTS etapa_atual INTEGER NOT NULL DEFAULT 1;
ALTER TABLE "FIV".visitas ADD COLUMN IF NOT EXISTS etapa INTEGER NOT NULL DEFAULT 1;

-- Se você já tinha rodado uma versão anterior desta migração que criava
-- uma tabela "FIV".config com uma etapa global única, ela não é mais
-- usada pelo sistema. Não precisa apagar - só não faz mais diferença.

-- Pronto! A tabela FIV.produtores está criada e vazia, pronta para receber os dados.
