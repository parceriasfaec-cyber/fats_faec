-- Rode no Supabase (SQL Editor) ANTES de publicar o sistema com o RG.
-- Só acrescenta a coluna; não altera nem apaga nenhum dado existente.
alter table "FIV".produtores add column if not exists rg text;
