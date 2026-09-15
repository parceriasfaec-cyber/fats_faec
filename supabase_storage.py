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

import io
import os
import uuid

import requests

try:
    from PIL import Image, ImageOps
    _PIL_DISPONIVEL = True
except ImportError:
    # Sem Pillow instalado, o app continua funcionando, so nao comprime
    # as fotos antes de subir (nao ideal no plano gratuito, mas nao quebra).
    _PIL_DISPONIVEL = False

SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
BUCKET = os.environ.get("SUPABASE_BUCKET", "fotos-produtores")
# Bucket separado para as fotos dos animais (crie-o no painel do Supabase:
# Storage -> New bucket -> "fotos-animais", marcado como "Public bucket").
BUCKET_ANIMAIS = os.environ.get("SUPABASE_BUCKET_ANIMAIS", "fotos-animais")

EXTENSOES_PERMITIDAS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

# Lado maior da imagem, em pixels, depois de redimensionada. 1280px e mais
# que suficiente para visualizar no sistema e imprimir na ficha em PDF,
# e reduz MUITO o tamanho do arquivo (fotos de celular costumam ter
# 3000-4000px de lado, varios MB cada).
TAMANHO_MAXIMO_PX = 1280
QUALIDADE_JPEG = 75


def configurado() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)


def _comprimir_imagem(conteudo: bytes) -> tuple[bytes, str]:
    """Redimensiona (no maximo TAMANHO_MAXIMO_PX no lado maior) e recomprime
    a imagem como JPEG, para economizar espaco no Supabase Storage (crucial
    no plano gratuito, que tem so 1GB no total).

    Devolve (novos_bytes, nova_extensao). Se a imagem nao puder ser lida
    (arquivo corrompido, formato nao suportado) ou o Pillow nao estiver
    instalado, devolve o conteudo original sem alterar - e melhor subir a
    foto do jeito que veio do que travar o cadastro por causa disso."""
    if not _PIL_DISPONIVEL:
        return conteudo, ""
    try:
        img = Image.open(io.BytesIO(conteudo))
        # Corrige a rotacao de fotos tiradas na vertical pelo celular
        # (metadado EXIF de orientacao), senao a foto comprimida pode
        # aparecer "deitada".
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")  # remove canal alfa/paleta, exigido p/ JPEG

        largura, altura = img.size
        maior_lado = max(largura, altura)
        if maior_lado > TAMANHO_MAXIMO_PX:
            escala = TAMANHO_MAXIMO_PX / maior_lado
            img = img.resize(
                (max(1, round(largura * escala)), max(1, round(altura * escala))),
                Image.LANCZOS,
            )

        saida = io.BytesIO()
        img.save(saida, format="JPEG", quality=QUALIDADE_JPEG, optimize=True)
        return saida.getvalue(), ".jpg"
    except Exception:
        # Nao e uma imagem valida (ou formato que o Pillow nao abre) -
        # sobe do jeito que veio, sem travar o cadastro.
        return conteudo, ""


def enviar_bytes(conteudo: bytes, nome_original: str, mimetype: str = "", bucket: str = None) -> str:
    """Comprime (quando possivel) e envia os bytes de uma foto (ja lida em
    memoria) para o Supabase Storage, devolvendo a URL publica dela. Usado
    tanto para uploads normais quanto para sincronizar fotos que ficaram na
    fila offline.

    Por padrao usa o bucket dos produtores (BUCKET); passe `bucket=` para
    enviar a outro bucket (ex: fotos dos animais, que ficam separadas)."""
    if not configurado():
        raise RuntimeError(
            "SUPABASE_URL e SUPABASE_SERVICE_KEY nao estao configurados. "
            "Preencha essas variaveis no .env (veja o topo deste arquivo)."
        )

    bucket_final = bucket or BUCKET

    ext = os.path.splitext(nome_original or "")[1].lower()
    if ext not in EXTENSOES_PERMITIDAS:
        ext = ".jpg"

    mimetype_final = mimetype or "application/octet-stream"
    if ext in {".jpg", ".jpeg", ".png"}:
        # So tenta comprimir formatos foto comuns (nao mexe em .gif/.webp,
        # que sao mais raros nesse sistema e podem ser animados).
        conteudo, nova_ext = _comprimir_imagem(conteudo)
        if nova_ext:
            ext = nova_ext
            mimetype_final = "image/jpeg"

    nome_arquivo = f"{uuid.uuid4().hex}{ext}"

    resp = requests.post(
        f"{SUPABASE_URL}/storage/v1/object/{bucket_final}/{nome_arquivo}",
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": mimetype_final,
        },
        data=conteudo,
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Supabase Storage recusou o upload (HTTP {resp.status_code}): {resp.text}"
        )

    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket_final}/{nome_arquivo}"


def enviar_foto(arquivo) -> str:
    """Recebe o arquivo enviado pelo formulario (Flask FileStorage) e
    devolve a URL publica dele no Supabase Storage."""
    return enviar_bytes(arquivo.read(), arquivo.filename or "", arquivo.mimetype or "")
