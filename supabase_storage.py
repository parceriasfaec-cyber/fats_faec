"""
Envia as fotos dos produtores para o Supabase Storage (em vez de salvar
localmente), para funcionar em hospedagens sem disco permanente (Vercel,
Render no plano gratuito, etc.).

Variaveis de ambiente necessarias (no .env):
    SUPABASE_URL          -> ex: https://xxxxxxxx.supabase.co
    SUPABASE_SERVICE_KEY  -> a "service_role key" do projeto (NAO a anon)
    SUPABASE_BUCKET       -> opcional, padrao "fotos-produtores"

Onde encontrar:
    Painel do Supabase -> Project Settings -> API
    - "Project URL"       vira SUPABASE_URL
    - "service_role" key  vira SUPABASE_SERVICE_KEY (fica em "Project API keys")

IMPORTANTE: a service_role key da acesso total ao projeto - nunca a
compartilhe nem a exponha no navegador. Ela so deve existir no servidor
(no .env, que ja esta no .gitignore).
"""

import os
import uuid

import requests

SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
BUCKET = os.environ.get("SUPABASE_BUCKET", "fotos-produtores")

EXTENSOES_PERMITIDAS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def configurado() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)


def enviar_foto(arquivo) -> str:
    """Recebe o arquivo enviado pelo formulario (Flask FileStorage) e
    devolve a URL publica dele no Supabase Storage."""
    if not configurado():
        raise RuntimeError(
            "SUPABASE_URL e SUPABASE_SERVICE_KEY nao estao configurados. "
            "Preencha essas variaveis no .env (veja o topo deste arquivo)."
        )

    nome_original = arquivo.filename or ""
    ext = os.path.splitext(nome_original)[1].lower()
    if ext not in EXTENSOES_PERMITIDAS:
        ext = ".jpg"
    nome_arquivo = f"{uuid.uuid4().hex}{ext}"

    conteudo = arquivo.read()
    mimetype = arquivo.mimetype or "application/octet-stream"

    resp = requests.post(
        f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{nome_arquivo}",
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": mimetype,
        },
        data=conteudo,
        timeout=30,
    )
    resp.raise_for_status()

    return f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{nome_arquivo}"
