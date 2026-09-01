# Publicando o Sistema FATS no Render

O projeto já vem com 3 arquivos prontos pra isso:
- `Procfile` — diz ao Render como rodar o app (usando gunicorn)
- `render.yaml` — descreve o serviço, o disco persistente e as variáveis
- `requirements.txt` — já com o `gunicorn` incluso

## Passo 1 — Suba essas mudanças pro GitHub

No terminal, dentro da pasta do projeto:
```
git add .
git commit -m "Preparando deploy no Render"
git push
```

## Passo 2 — Crie a conta no Render

1. Acesse https://render.com e crie uma conta (dá pra usar login do GitHub).

## Passo 3 — Crie o serviço a partir do render.yaml

1. No painel, clique em **New +** → **Blueprint**.
2. Escolha o repositório `parceriasfaec-cyber/fats_faec`.
3. O Render vai ler o `render.yaml` sozinho e mostrar um serviço chamado
   `fats-faec`, já com o disco `fotos-produtores` (1 GB, montado em
   `/var/data`).
4. Ele vai pedir pra preencher 2 variáveis que ficaram em branco de
   propósito (por segurança, não vão pro GitHub):
   - **DATABASE_URL** → cole a mesma string de conexão do Supabase que
     você usa no `.env` local.
   - **SECRET_KEY** → qualquer texto aleatório (pode ser o mesmo do seu
     `.env` local).
5. Clique em **Apply** / **Create**.

## Passo 4 — Escolha o plano

Quando ele perguntar o plano do serviço `fats-faec`, escolha **Starter**
(pago) — é o que garante o disco persistente e o app sempre ligado (sem
"dormir").

## Passo 5 — Aguarde o build

O Render vai instalar as dependências (`pip install -r requirements.txt`) e
rodar `gunicorn app:app`. Isso leva de 2 a 5 minutos na primeira vez.

## Passo 6 — Teste

Quando terminar, o Render mostra uma URL tipo:
```
https://fats-faec.onrender.com
```
Abra ela, cadastre um produtor de teste com foto, e confira se:
- O cadastro aparece na lista (prova que o Supabase está conectado certo)
- A foto aparece certinha (prova que o disco persistente está funcionando)

## Sobre atualizações futuras

Toda vez que você der `git push` pro GitHub, o Render detecta sozinho e já
refaz o deploy automaticamente com a versão nova — não precisa repetir
esses passos.
