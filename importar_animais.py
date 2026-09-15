# -*- coding: utf-8 -*-
"""
Script de importacao em massa dos animais (receptoras) do lote FIV.

O QUE ESSE SCRIPT FAZ
----------------------
1. Garante que a tabela "FIV".animais existe no Supabase (roda o schema).
2. Insere os 200 animais abaixo (brinco FAEC + brinco Fazenda), sem
   duplicar quem ja foi importado antes (pode rodar o script varias
   vezes sem problema).
3. Procura, numa pasta local de fotos, ate 3 fotos de cada animal
   (usando o numero do brinco FAEC no NOME do arquivo) e faz o upload
   delas (ja comprimidas) para o Supabase Storage, atualizando
   foto_1/foto_2/foto_3 do animal.

COMO USAR
---------
1. Baixe as fotos do Google Drive (ou sincronize com o Google Drive
   Desktop, que cria uma pasta local automaticamente) para o computador.

2. Renomeie cada foto para comecar com o numero do brinco FAEC do
   animal (4 digitos, com zero na frente), por exemplo:
       0001_1.jpg   0001_2.jpg   0001_3.jpg   -> as 3 fotos do animal 0001
       0002-a.jpg   0002-b.jpg               -> as 2 fotos do animal 0002
   O que vem DEPOIS do numero (o "_1", "-a" etc.) pode ser qualquer
   coisa - o que importa e o arquivo COMECAR com o numero de 4 digitos.
   Se um animal tiver so 1 ou 2 fotos, sem problema, o resto fica vazio.

3. Coloque todas as fotos dentro de uma pasta (por padrao, o script
   procura numa pasta chamada "fotos_animais" do lado deste arquivo -
   pode trocar o nome/caminho na variavel PASTA_FOTOS abaixo, ou
   definindo a variavel de ambiente FOTOS_ANIMAIS_DIR).

4. Rode:  python importar_animais.py

Pode rodar o script de novo depois de adicionar mais fotos - ele so
sobe as fotos que ainda estiverem faltando (nao sobe de novo o que ja
tem foto salva).
"""

import os
import re
import sys
from pathlib import Path

from database import get_connection, init_db
from supabase_storage import enviar_bytes, configurado, BUCKET_ANIMAIS


# ------------------------------------------------------------------
# Lista dos 200 animais: (brinco_faec, brinco_fazenda)
# ------------------------------------------------------------------
ANIMAIS = [
    ("0001", "0973 CW"),
    ("0002", "V 1452"),
    ("0003", "V 1458"),
    ("0004", "X 0043"),
    ("0005", "V 1953"),
    ("0006", "X 0264"),
    ("0007", "V 1778"),
    ("0008", "V 1891"),
    ("0009", "1049 CW"),
    ("0010", "CW"),
    ("0011", "V 1958"),
    ("0012", "X 0195"),
    ("0013", "V 1845"),
    ("0014", "X 0182"),
    ("0015", "V 1841"),
    ("0016", "V 1732"),
    ("0017", "V 1533"),
    ("0018", "V 1821"),
    ("0019", "V 1930"),
    ("0020", "V 1980"),
    ("0021", "V 1449"),
    ("0022", "V 1548"),
    ("0023", "V 1993"),
    ("0024", "V 1812"),
    ("0025", "SEM"),
    ("0026", "V 1921"),
    ("0027", "V 1863"),
    ("0028", "V 1955"),
    ("0029", "V 1957"),
    ("0030", "V 1851"),
    ("0031", "X 0096"),
    ("0032", "V 1697"),
    ("0033", "X 0180"),
    ("0034", "V 1892"),
    ("0035", "V 1464"),
    ("0036", "V 1956"),
    ("0037", "V 1962"),
    ("0038", "V 1831"),
    ("0039", "V 1864"),
    ("0040", "V 1834"),
    ("0041", "X 0038"),
    ("0042", "X 0138"),
    ("0043", "V 1805"),
    ("0044", "V 1969"),
    ("0045", "V 1996"),
    ("0046", "X 0209"),
    ("0047", "V 1977"),
    ("0048", "V 1908"),
    ("0049", "V 1799"),
    ("0050", "V 1374"),
    ("0051", "V 1507"),
    ("0052", "V 1694"),
    ("0053", "V 1789"),
    ("0054", "V 1848"),
    ("0055", "V 1303"),
    ("0056", "V 1860"),
    ("0057", "V 1320"),
    ("0058", "V 1865"),
    ("0059", "V 1476"),
    ("0060", "V 1938"),
    ("0061", "V 1419"),
    ("0062", "V 1911"),
    ("0063", "V 1130"),
    ("0064", "V 1846"),
    ("0065", "X 0088"),
    ("0066", "V 1896"),
    ("0067", "V 1696"),
    ("0068", "X 0164"),
    ("0069", "X 0190"),
    ("0070", "V 1668"),
    ("0071", "V 1746"),
    ("0072", "V 1868"),
    ("0073", "V 1756"),
    ("0074", "X 0042"),
    ("0075", "X 0085"),
    ("0076", "V 1939"),
    ("0077", "V 1889"),
    ("0078", "V 1673"),
    ("0079", "V 1828"),
    ("0080", "V 1504"),
    ("0081", "U 1551"),
    ("0082", "V 1903"),
    ("0083", "V 1829"),
    ("0084", "V 1988"),
    ("0085", "V 1739"),
    ("0086", "V 1559"),
    ("0087", "V 1444"),
    ("0088", "V 1531"),
    ("0089", "V 1771"),
    ("0090", "V 1588"),
    ("0091", "V 1628"),
    ("0092", "V 1395"),
    ("0093", "V 1394"),
    ("0094", "V 1975"),
    ("0095", "V 1744"),
    ("0096", "V 1197"),
    ("0097", "V 1640"),
    ("0098", "V 1852"),
    ("0099", "X 0050"),
    ("0100", "V 1913"),
    ("0101", "X 0335"),
    ("0102", "X 0308"),
    ("0103", "X 0333"),
    ("0104", "X 0262"),
    ("0105", "X 0323"),
    ("0106", "X 0168"),
    ("0107", "X 0241"),
    ("0108", "X 0154"),
    ("0109", "X 0081"),
    ("0110", "X 0307"),
    ("0111", "V 1816"),
    ("0112", "X 0273"),
    ("0113", "X 0107"),
    ("0114", "X 0325"),
    ("0115", "X 0040"),
    ("0116", "X 0328"),
    ("0117", "X 0255"),
    ("0118", "X 0131"),
    ("0119", "X 0271"),
    ("0120", "X 0259"),
    ("0121", "X 0215"),
    ("0122", "X 0144"),
    ("0123", "X 0266"),
    ("0124", "X 0242"),
    ("0125", "X 0306"),
    ("0126", "X 0090"),
    ("0127", "X 0156"),
    ("0128", "V 1999"),
    ("0129", "X 0339"),
    ("0130", "X 0130"),
    ("0131", "V 1960"),
    ("0132", "X 0203"),
    ("0133", "X 0243"),
    ("0134", "X 0265"),
    ("0135", "V 1881"),
    ("0136", "X 0045"),
    ("0137", "X 0152"),
    ("0138", "X 0247"),
    ("0139", "X 0261"),
    ("0140", "X 0258"),
    ("0141", "X 0324"),
    ("0142", "V 1970"),
    ("0143", "V 1830"),
    ("0144", "X 0317"),
    ("0145", "X 0142"),
    ("0146", "V 1882"),
    ("0147", "V 1874"),
    ("0148", "X 0267"),
    ("0149", "X 0149"),
    ("0150", "X 0148"),
    ("0151", "V 1965"),
    ("0152", "X 0127"),
    ("0153", "X 0301"),
    ("0154", "SEM"),
    ("0155", "X 0114"),
    ("0156", "X 0222"),
    ("0157", "V 1867"),
    ("0158", "X 0028"),
    ("0159", "V 1850"),
    ("0160", "X 0162"),
    ("0161", "X 0158"),
    ("0162", "X 0268"),
    ("0163", "V 1967"),
    ("0164", "V 1991"),
    ("0165", "V 1935"),
    ("0166", "X 0221"),
    ("0167", "X 0136"),
    ("0168", "X 0126"),
    ("0169", "SEM 201"),
    ("0170", "X 0269"),
    ("0171", "X 0223"),
    ("0172", "X 0194"),
    ("0173", "X 0237"),
    ("0174", "X 0204"),
    ("0175", "X 0212"),
    ("0176", "X 0077"),
    ("0177", "X 0340"),
    ("0178", "X 0193"),
    ("0179", "V 1944"),
    ("0180", "X 0322"),
    ("0181", "X 0031"),
    ("0182", "X 0211"),
    ("0183", "X 0046"),
    ("0184", "X 0315"),
    ("0185", "X 0030"),
    ("0186", "X 0250"),
    ("0187", "X 0087"),
    ("0188", "X 0157"),
    ("0189", "X 0272"),
    ("0190", "X 0260"),
    ("0191", "X 0274"),
    ("0192", "X 0101"),
    ("0193", "X 0256"),
    ("0194", "X 0080"),
    ("0195", "X 0312"),
    ("0196", "X 0270"),
    ("0197", "X 0208"),
    ("0198", "X 0084"),
    ("0199", "X 0320"),
    ("0200", "X 0179"),
]


# Pasta com as fotos ja baixadas do Google Drive (ou de onde estiverem).
# Pode trocar via variavel de ambiente FOTOS_ANIMAIS_DIR, sem precisar
# editar o codigo.
PASTA_FOTOS = Path(
    os.environ.get("FOTOS_ANIMAIS_DIR")
    or (Path(__file__).parent / "fotos_animais")
)

EXTENSOES_IMAGEM = {".jpg", ".jpeg", ".png", ".webp"}


def inserir_animais():
    """Insere na tabela animais quem ainda nao existe (compara pelo
    brinco_faec). Pode rodar varias vezes sem duplicar."""
    conn = get_connection()
    ja_existentes = {
        row["brinco_faec"]
        for row in conn.execute("SELECT brinco_faec FROM animais").fetchall()
    }

    novos = [a for a in ANIMAIS if a[0] not in ja_existentes]
    for brinco_faec, brinco_fazenda in novos:
        conn.execute(
            "INSERT INTO animais (brinco_faec, brinco_fazenda) VALUES (?, ?)",
            (brinco_faec, brinco_fazenda),
        )
    conn.commit()
    conn.close()

    print(f"Animais ja cadastrados antes: {len(ja_existentes)}")
    print(f"Animais novos inseridos agora: {len(novos)}")


def _fotos_do_animal(brinco_faec: str):
    """Procura na PASTA_FOTOS os arquivos que comecam com esse numero de
    brinco (ex: '0001_1.jpg', '0001_2.jpg'...), em ordem, e devolve os
    caminhos (no maximo 3)."""
    if not PASTA_FOTOS.exists():
        return []
    encontrados = []
    for caminho in sorted(PASTA_FOTOS.iterdir()):
        if not caminho.is_file():
            continue
        if caminho.suffix.lower() not in EXTENSOES_IMAGEM:
            continue
        if re.match(rf"^{re.escape(brinco_faec)}([^0-9]|$)", caminho.name):
            encontrados.append(caminho)
    return encontrados[:3]


def enviar_fotos():
    """Para cada animal que ainda tem alguma foto vazia, procura na pasta
    local e sobe (comprimida) o que encontrar para o Supabase Storage."""
    if not configurado():
        print(
            "AVISO: SUPABASE_URL / SUPABASE_SERVICE_KEY nao configurados - "
            "pulando envio de fotos (só os dados foram importados)."
        )
        return

    if not PASTA_FOTOS.exists():
        print(
            f"AVISO: pasta de fotos não encontrada em '{PASTA_FOTOS}'. "
            "Baixe as fotos do Google Drive para essa pasta (ou aponte "
            "FOTOS_ANIMAIS_DIR para o lugar certo) e rode o script de novo."
        )
        return

    conn = get_connection()
    rows = conn.execute(
        "SELECT id, brinco_faec, foto_1, foto_2, foto_3 FROM animais ORDER BY brinco_faec"
    ).fetchall()

    enviados = 0
    sem_foto_na_pasta = []

    for row in rows:
        fotos_atuais = [row["foto_1"] or "", row["foto_2"] or "", row["foto_3"] or ""]
        if all(fotos_atuais):
            continue  # ja tem as 3 fotos, nada a fazer

        caminhos = _fotos_do_animal(row["brinco_faec"])
        if not caminhos:
            sem_foto_na_pasta.append(row["brinco_faec"])
            continue

        novas_urls = list(fotos_atuais)
        indice_caminho = 0
        for i in range(3):
            if novas_urls[i]:
                continue  # essa posicao ja tem foto, nao mexe
            if indice_caminho >= len(caminhos):
                break
            caminho = caminhos[indice_caminho]
            indice_caminho += 1
            conteudo = caminho.read_bytes()
            url = enviar_bytes(conteudo, caminho.name, bucket=BUCKET_ANIMAIS)
            novas_urls[i] = url
            enviados += 1
            print(f"  animal {row['brinco_faec']}: enviada {caminho.name}")

        conn.execute(
            "UPDATE animais SET foto_1 = ?, foto_2 = ?, foto_3 = ?, atualizado_em = now() WHERE id = ?",
            (novas_urls[0], novas_urls[1], novas_urls[2], row["id"]),
        )
        conn.commit()

    conn.close()

    print(f"\nTotal de fotos enviadas nesta execução: {enviados}")
    if sem_foto_na_pasta:
        print(
            f"Animais sem nenhuma foto encontrada na pasta "
            f"({len(sem_foto_na_pasta)}): {', '.join(sem_foto_na_pasta)}"
        )


if __name__ == "__main__":
    print("Verificando schema do banco (cria a tabela 'animais' se não existir)...")
    init_db()

    print("\n--- Importando cadastro dos animais ---")
    inserir_animais()

    print(f"\n--- Enviando fotos (pasta: {PASTA_FOTOS}) ---")
    enviar_fotos()

    print("\nConcluído.")
