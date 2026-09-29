"""
Painel de entrada do sistema (página inicial "/") com os números gerais:
produtores, fichas, animais, visitas e um resumo por município.

A lista de produtores continua existindo, agora em /produtores (o endpoint
`index` foi mantido, então todos os links e redirecionamentos antigos
continuam funcionando).

Como ligar no app.py — antes do `if __name__ == "__main__":`:

    from painel_inicial import registrar_painel
    registrar_painel(app, get_connection, _campos_faltando,
                     _ultimas_visitas_da_etapa, _erro_de_conexao,
                     LIMITE_ANIMAIS_POR_PRODUTOR)
"""

from flask import render_template


def calcular_painel(produtores, animais, ultimas_visitas, campos_faltando, limite_animais):
    """Recebe as linhas do banco (já lidas) e devolve os números do painel.

    produtores      : lista de dicts (linhas de `produtores`)
    animais         : lista de dicts com `status` e `produtor_id`
    ultimas_visitas : dict {produtor_id: visita} (só de quem já foi visitado)
    """
    total = len(produtores)

    animais_por_produtor = {}
    alocados = 0
    for a in animais:
        if a.get("produtor_id") is not None:
            animais_por_produtor[a["produtor_id"]] = animais_por_produtor.get(a["produtor_id"], 0) + 1
        if (a.get("status") or "") == "alocado":
            alocados += 1

    completos = ateg = sem_foto = visitados = em_meta = 0
    por_municipio = {}
    for p in produtores:
        municipio = (p.get("municipio") or "").strip().upper() or "SEM MUNICÍPIO"
        m = por_municipio.setdefault(
            municipio, {"nome": municipio, "produtores": 0, "animais": 0, "visitados": 0, "completos": 0}
        )
        m["produtores"] += 1

        n_animais = animais_por_produtor.get(p["id"], 0)
        m["animais"] += n_animais
        if n_animais >= limite_animais:
            em_meta += 1

        if not campos_faltando(p):
            completos += 1
            m["completos"] += 1
        if (p.get("assistido_ateg") or "") == "Sim":
            ateg += 1
        if not (p.get("foto_produtor") or "").strip():
            sem_foto += 1
        if p["id"] in ultimas_visitas:
            visitados += 1
            m["visitados"] += 1

    def pct(parte, todo):
        return round(parte / todo * 100) if todo else 0

    municipios = sorted(por_municipio.values(), key=lambda m: (-m["produtores"], m["nome"]))
    for m in municipios:
        m["pct_visitados"] = pct(m["visitados"], m["produtores"])

    return {
        "total_produtores": total,
        "qtd_completos": completos,
        "qtd_incompletos": total - completos,
        "pct_completos": pct(completos, total),
        "qtd_ateg": ateg,
        "pct_ateg": pct(ateg, total),
        "qtd_sem_foto": sem_foto,
        "qtd_visitados": visitados,
        "qtd_pendentes": total - visitados,
        "pct_visitados": pct(visitados, total),
        "total_animais": len(animais),
        "animais_alocados": alocados,
        "animais_disponiveis": len(animais) - alocados,
        "qtd_em_meta": em_meta,
        "pct_em_meta": pct(em_meta, total),
        "limite_animais": limite_animais,
        "municipios": municipios,
    }


def registrar_painel(app, get_connection, campos_faltando, ultimas_visitas_da_etapa,
                     erro_de_conexao, limite_animais):
    """Cria a rota GET / (endpoint `inicio`)."""

    def inicio():
        try:
            conn = get_connection()
        except Exception as erro:
            if not erro_de_conexao(erro) and not isinstance(erro, RuntimeError):
                raise
            return render_template("offline.html")

        try:
            produtores = [dict(r) for r in conn.execute("SELECT * FROM produtores").fetchall()]
            animais = [dict(r) for r in conn.execute("SELECT status, produtor_id FROM animais").fetchall()]
            visitas = ultimas_visitas_da_etapa(conn)
        finally:
            conn.close()

        dados = calcular_painel(produtores, animais, visitas, campos_faltando, limite_animais)
        return render_template("inicio.html", **dados)

    app.add_url_rule("/", "inicio", inicio, methods=["GET"])
