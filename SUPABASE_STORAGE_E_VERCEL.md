# Fotos no Supabase Storage + Deploy no Vercel

## Parte 1 — Criar o bucket de fotos no Supabase

1. No painel do Supabase, vá em **Storage** (menu lateral).
2. Clique em **New bucket**.
3. Nome do bucket: `fotos-produtores` (tem que ser exatamente esse nome,
   ou então ajuste a variável `SUPABASE_BUCKET` no `.env` para o nome que
   você escolher).
4. Marque a opção **Public bucket** (assim as fotos podem ser exibidas
   direto no navegador, sem precisar de login).
5. Clique em **Create bucket**.

## Parte 2 — Pegar a Service Role Key

1. Vá em **Project Settings** (engrenagem) → **API**.
2. Copie o **Project URL** (algo como `https://xxxxxxxx.supabase.co`).
3. Em **Project API keys**, copie a chave **service_role** (não é a
   "anon"/"public" — é a outra, marcada como secreta).

⚠️ **Essa chave dá acesso total ao seu banco e storage.** Nunca a coloque
em código que vai para o navegador, nem a publique em lugar nenhum além
do `.env` (que já está protegido pelo `.gitignore`).

## Parte 3 — Preencha o .env local

```
SUPABASE_URL=https://xxxxxxxx.supabase.co
SUPABASE_SERVICE_KEY=a-chave-service-role-que-voce-copiou
SUPABASE_BUCKET=fotos-produtores
```

Teste localmente (`python app.py`), cadastre um produtor com foto, e
confira: a foto deve aparecer, e se você for em **Storage** no painel do
Supabase, o arquivo deve estar lá dentro do bucket `fotos-produtores`.

## Parte 4 — Publicar no Vercel

1. Suba as mudanças pro GitHub:
   ```
   git add .
   git commit -m "Fotos no Supabase Storage + config Vercel"
   git push
   ```
2. Acesse https://vercel.com e crie uma conta (dá pra usar login do GitHub).
3. Clique em **Add New** → **Project**.
4. Escolha o repositório `fats_faec`.
5. Antes de clicar em Deploy, abra **Environment Variables** e adicione:
   - `DATABASE_URL` → a mesma string de conexão do Supabase
   - `SECRET_KEY` → qualquer texto aleatório
   - `SUPABASE_URL` → o Project URL do Supabase
   - `SUPABASE_SERVICE_KEY` → a service_role key
   - `SUPABASE_BUCKET` → `fotos-produtores`
6. Clique em **Deploy**.

## Um limite importante do Vercel (plano gratuito)

Cada requisição no plano gratuito tem um limite de tamanho (por volta de
4,5 MB) e um tempo máximo de execução (10 segundos). Fotos tiradas direto
do celular às vezes passam disso. Se der erro ao enviar foto (mas não em
outros cadastros), o problema provavelmente é o tamanho do arquivo — nesse
caso, me avise que a gente ajusta o sistema para reduzir a foto antes de
enviar.

## Sobre fotos antigas (já cadastradas antes dessa mudança)

Elas continuam salvas localmente no seu computador (na pasta `fotos/`) e
não aparecem automaticamente no Vercel, já que essa pasta não sobe para o
Git. Se quiser, depois a gente escreve um scriptzinho para subir essas
fotos antigas também para o Supabase Storage.
