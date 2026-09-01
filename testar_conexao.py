"""
Script simples para testar se a conexao com o Supabase esta funcionando.

Como usar:
    1. Coloque este arquivo dentro da pasta do projeto (FAT_FIV_supabase),
       ao lado do app.py e do .env
    2. Rode no terminal:  python testar_conexao.py
"""

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("Aviso: pacote python-dotenv nao encontrado. Rode:")
    print("    pip install -r requirements.txt")
    print()

import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL")

print("=" * 60)
print("TESTE DE CONEXAO COM O SUPABASE")
print("=" * 60)

if not DATABASE_URL:
    print("[ERRO] A variavel DATABASE_URL nao foi encontrada.")
    print("       Confira se o arquivo .env existe nesta mesma pasta")
    print("       e se a linha DATABASE_URL=... esta preenchida.")
    raise SystemExit(1)

# Mostra a string escondendo a senha, so pra conferencia visual
if "@" in DATABASE_URL:
    partes = DATABASE_URL.split("@")
    usuario_oculto = partes[0].split("//")[-1].split(":")[0]
    print(f"Conectando como usuario: {usuario_oculto}")
    print(f"Host: {partes[1].split('/')[0]}")

try:
    conn = psycopg2.connect(DATABASE_URL)
    print("[OK] Conexao com o banco Supabase aberta com sucesso!")

    with conn.cursor() as cur:
        cur.execute('SET search_path TO "FIV", public')

        # Confere se o schema FIV existe
        cur.execute("""
            SELECT schema_name FROM information_schema.schemata
            WHERE schema_name = 'FIV'
        """)
        if cur.fetchone():
            print("[OK] Schema \"FIV\" encontrado.")
        else:
            print("[AVISO] Schema \"FIV\" NAO foi encontrado. "
                  "Rode o supabase_schema.sql no SQL Editor do Supabase.")

        # Confere se a tabela produtores existe dentro do schema FIV
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'FIV' AND table_name = 'produtores'
        """)
        if cur.fetchone():
            print("[OK] Tabela \"FIV\".produtores encontrada.")

            cur.execute('SELECT COUNT(*) FROM "FIV".produtores')
            total = cur.fetchone()[0]
            print(f"[OK] Consulta de teste funcionou. "
                  f"Registros na tabela hoje: {total}")
        else:
            print("[AVISO] Tabela \"FIV\".produtores NAO foi encontrada. "
                  "Rode o supabase_schema.sql no SQL Editor do Supabase.")

    conn.close()
    print()
    print("Tudo certo! O sistema esta pronto para conectar no Supabase.")

except Exception as e:
    print("[ERRO] Nao foi possivel conectar ao banco.")
    print(f"       Detalhes: {e}")
    print()
    print("Coisas comuns que causam esse erro:")
    print(" - Senha errada no DATABASE_URL (o [YOUR-PASSWORD] nao foi trocado)")
    print(" - Copiou a string de conexao errada (confira em Project Settings")
    print("   -> Database -> Connection string -> URI)")
    print(" - Seu computador/rede esta bloqueando a porta 5432/6543")
    raise SystemExit(1)
