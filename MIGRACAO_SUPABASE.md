# Migração do Sistema FATS para o Supabase

O que mudou: o sistema deixou de usar o arquivo `fats.db` (SQLite) e passou a
salvar tudo em um banco PostgreSQL no Supabase, dentro de um schema chamado
**`fiv`**, na tabela **`fiv.produtores`**.

## Passo 1 — Criar o schema e a tabela no Supabase

1. Acesse https://supabase.com/dashboard e entre no seu projeto (ou crie um
   novo, se ainda não tiver).
2. No menu lateral, clique em **SQL Editor** → **New query**.
3. Abra o arquivo `supabase_schema.sql` (está nesta mesma pasta), copie todo
   o conteúdo, cole no editor e clique em **Run**.
4. Confirme que deu certo: no menu lateral vá em **Table Editor**, troque o
   schema selecionado (canto superior, onde geralmente está "public") para
   **fiv** e veja a tabela `produtores` lá, vazia.

## Passo 2 — Pegar a string de conexão

1. No painel do projeto, vá em **Project Settings** (ícone de engrenagem) →
   **Database**.
2. Em **Connection string**, escolha a aba **URI**.
3. Copie a string (algo como
   `postgresql://postgres.xxxx:[YOUR-PASSWORD]@aws-0-sa-east-1.pooler.supabase.com:6543/postgres`).
4. Troque `[YOUR-PASSWORD]` pela senha do banco (a que você definiu quando
   criou o projeto — se não lembrar, dá pra resetar em **Database → Reset
   database password**).

## Passo 3 — Configurar o projeto localmente

1. Copie o arquivo `.env.example` e renomeie a cópia para `.env`.
2. Abra o `.env` e cole a string de conexão na linha `DATABASE_URL=`.
3. Instale as dependências (inclui o driver do Postgres):
   ```
   pip install -r requirements.txt
   ```

## Passo 4 — Rodar o sistema

```
python app.py
```

Se a variável `DATABASE_URL` estiver certa, o sistema abre normalmente em
`http://127.0.0.1:5000`, só que agora lendo e gravando direto no Supabase.

## Sobre as fotos dos produtores

Por enquanto as fotos continuam sendo salvas na pasta `fotos/`, no mesmo
computador que roda o sistema (igual funcionava antes) — só os dados da
ficha (texto) é que foram para o Supabase. Se no futuro você quiser que as
fotos também fiquem na nuvem (Supabase Storage), é só pedir que a gente
adapta.

## Sobre os dados que já existiam (fats.db)

Você optou por começar com o banco vazio no Supabase. Quando tiver a
planilha para importar os cadastros antigos, me envie o arquivo (.xlsx ou
.csv) que eu preparo a importação direto para a tabela `fiv.produtores`.

## Rodando como .exe (PyInstaller)

Se depois quiser gerar o `.exe` de novo (`build_exe.bat`), lembre-se de que
o `.env` também precisa estar na pasta do `.exe`, ou de configurar a
variável `DATABASE_URL` no Windows antes de abrir o programa — senão ele
não vai saber como se conectar ao Supabase.
