"""
Fila local de cadastros feitos sem internet.

Quando o tecnico esta em campo sem sinal e tenta cadastrar um produtor, o
sistema nao consegue falar com o Supabase - nesse caso, em vez de perder o
que foi digitado, o cadastro fica guardado aqui, num banquinho SQLite local
(no mesmo computador/notebook), ate que o usuario clique em "Sincronizar
agora" com a internet de volta.

Isso so funciona rodando o sistema localmente (python app.py ou o .exe) -
em hospedagens na nuvem (Vercel, Render) nao faz sentido, pois la sempre
ha internet do lado do servidor.
"""

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from database import _base_dir
from campos import FIELDS

FILA_DB_PATH = _base_dir() / "fila_offline.db"
FILA_FOTOS_DIR = _base_dir() / "fila_fotos"

EXTENSOES_PERMITIDAS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

# Todos os campos do cadastro, exceto a foto (que e tratada a parte,
# guardada como arquivo local em vez de texto)
_CAMPOS_TEXTO = [f for f in FIELDS if f != "foto_produtor"]


def _conectar():
    conn = sqlite3.connect(str(FILA_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_fila():
    try:
        FILA_FOTOS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    colunas_sql = ", ".join(f'"{c}" TEXT' for c in _CAMPOS_TEXTO)
    conn = _conectar()
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS fila_produtores (
            id_local INTEGER PRIMARY KEY AUTOINCREMENT,
            criado_em_local TEXT,
            foto_local_arquivo TEXT,
            {colunas_sql}
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fila_visitas (
            id_local INTEGER PRIMARY KEY AUTOINCREMENT,
            produtor_id INTEGER NOT NULL,
            data_visita TEXT,
            tipo_visita TEXT,
            observacoes TEXT,
            criado_em_local TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fila_animais (
            id_local INTEGER PRIMARY KEY AUTOINCREMENT,
            brinco_faec TEXT NOT NULL,
            brinco_fazenda TEXT,
            peso TEXT,
            criado_em_local TEXT
        )
    """)
    conn.commit()
    conn.close()


def adicionar_na_fila(dados: dict, foto_bytes=None, foto_nome_original="", foto_mimetype="") -> int:
    """Guarda um cadastro novo na fila local (dados + foto, se tiver).
    Devolve o id local gerado (so para referencia na tela)."""
    init_fila()

    foto_local_arquivo = ""
    if foto_bytes:
        ext = Path(foto_nome_original or "").suffix.lower()
        if ext not in EXTENSOES_PERMITIDAS:
            ext = ".jpg"
        foto_local_arquivo = f"{uuid.uuid4().hex}{ext}"
        (FILA_FOTOS_DIR / foto_local_arquivo).write_bytes(foto_bytes)

    colunas = ["criado_em_local", "foto_local_arquivo"] + _CAMPOS_TEXTO
    colunas_sql = ", ".join(f'"{c}"' for c in colunas)
    placeholders = ", ".join(["?"] * len(colunas))
    valores = [datetime.now().strftime("%d/%m/%Y %H:%M"), foto_local_arquivo]
    valores += [dados.get(f, "") for f in _CAMPOS_TEXTO]

    conn = _conectar()
    cur = conn.execute(
        f"INSERT INTO fila_produtores ({colunas_sql}) VALUES ({placeholders})",
        valores,
    )
    conn.commit()
    id_local = cur.lastrowid
    conn.close()
    return id_local


def listar_pendentes() -> list:
    init_fila()
    conn = _conectar()
    rows = conn.execute("SELECT * FROM fila_produtores ORDER BY id_local").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def contar_pendentes() -> int:
    try:
        init_fila()
        conn = _conectar()
        n = conn.execute("SELECT COUNT(*) AS n FROM fila_produtores").fetchone()["n"]
        conn.close()
        return n
    except Exception:
        return 0


def remover_da_fila(id_local: int):
    conn = _conectar()
    row = conn.execute(
        "SELECT foto_local_arquivo FROM fila_produtores WHERE id_local = ?",
        (id_local,),
    ).fetchone()
    conn.execute("DELETE FROM fila_produtores WHERE id_local = ?", (id_local,))
    conn.commit()
    conn.close()
    if row and row["foto_local_arquivo"]:
        try:
            (FILA_FOTOS_DIR / row["foto_local_arquivo"]).unlink(missing_ok=True)
        except OSError:
            pass


def adicionar_visita_na_fila(produtor_id: int, data_visita: str, tipo_visita: str, observacoes: str) -> int:
    init_fila()
    conn = _conectar()
    cur = conn.execute(
        "INSERT INTO fila_visitas "
        "(produtor_id, data_visita, tipo_visita, observacoes, criado_em_local) "
        "VALUES (?, ?, ?, ?, ?)",
        (produtor_id, data_visita, tipo_visita, observacoes,
         datetime.now().strftime("%d/%m/%Y %H:%M")),
    )
    conn.commit()
    id_local = cur.lastrowid
    conn.close()
    return id_local


def listar_visitas_pendentes() -> list:
    init_fila()
    conn = _conectar()
    rows = conn.execute("SELECT * FROM fila_visitas ORDER BY id_local").fetchall()
    conn.close()
    return [dict(row) for row in rows]


def contar_visitas_pendentes() -> int:
    try:
        init_fila()
        conn = _conectar()
        total = conn.execute("SELECT COUNT(*) AS n FROM fila_visitas").fetchone()["n"]
        conn.close()
        return total
    except Exception:
        return 0


def remover_visita_da_fila(id_local: int):
    conn = _conectar()
    conn.execute("DELETE FROM fila_visitas WHERE id_local = ?", (id_local,))
    conn.commit()
    conn.close()


def excluir_visita_pendente(id_local: int):
    remover_visita_da_fila(id_local)


def adicionar_animal_na_fila(brinco_faec: str, brinco_fazenda: str, peso: str) -> int:
    init_fila()
    conn = _conectar()
    cur = conn.execute(
        "INSERT INTO fila_animais "
        "(brinco_faec, brinco_fazenda, peso, criado_em_local) VALUES (?, ?, ?, ?)",
        (brinco_faec, brinco_fazenda or None, peso or None,
         datetime.now().strftime("%d/%m/%Y %H:%M")),
    )
    conn.commit()
    id_local = cur.lastrowid
    conn.close()
    return id_local


def listar_animais_pendentes() -> list:
    init_fila()
    conn = _conectar()
    rows = conn.execute("SELECT * FROM fila_animais ORDER BY id_local").fetchall()
    conn.close()
    return [dict(row) for row in rows]


def contar_animais_pendentes() -> int:
    try:
        init_fila()
        conn = _conectar()
        total = conn.execute("SELECT COUNT(*) AS n FROM fila_animais").fetchone()["n"]
        conn.close()
        return total
    except Exception:
        return 0


def remover_animal_da_fila(id_local: int):
    conn = _conectar()
    conn.execute("DELETE FROM fila_animais WHERE id_local = ?", (id_local,))
    conn.commit()
    conn.close()


def excluir_animal_pendente(id_local: int):
    remover_animal_da_fila(id_local)
