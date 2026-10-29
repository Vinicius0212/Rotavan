# -*- coding: utf-8 -*-
"""Cadastro rápido de uma família (sem planilhas) e configuração portátil.

Na nuvem gratuita o disco é apagado quando o servidor reinicia. Para que os
links já entregues continuem valendo, a configuração da linha (escola, ponto de
casa, horários e os códigos de acesso) pode ser guardada num "segredo" do
serviço de hospedagem (variável de ambiente ROTAVAN_CONFIG) e reaplicada a cada
inicialização. Nela não há nome de criança nem endereço: só códigos e o
cruzamento mais próximo de casa."""
from __future__ import annotations

import json

from ..cenario import Cenario, Crianca, Escola, Parametros, Veiculo, hhmm_para_s
from .servico import Central, ErroRastreio, cenario_de_dict, cenario_para_dict


def montar_cenario_familia(dados: dict, G) -> Cenario:
    """dados = {"criancas": [{"codigo", "lat", "lon", "escola": {"nome", "lat", "lon", "entrada", "saida"}}],
                "garagem": {"lat", "lon"} | None, "capacidade": 15}"""
    criancas_in = dados.get("criancas") or []
    if not criancas_in:
        raise ErroRastreio("Informe pelo menos uma criança.")
    escolas: dict[str, Escola] = {}
    criancas = []
    for i, c in enumerate(criancas_in):
        cod = str(c.get("codigo") or f"C{i + 1:02d}").strip().upper()[:12]
        if not cod.replace("_", "").isalnum():
            raise ErroRastreio("O código da criança deve ter só letras e números (ex.: C01). Não use o nome.")
        e = c.get("escola") or {}
        try:
            chave = e["nome"].strip()
            if chave not in escolas:
                escolas[chave] = Escola(id=f"E{len(escolas) + 1}", nome=chave, lat=float(e["lat"]), lon=float(e["lon"]),
                                        entrada=hhmm_para_s(e["entrada"]), saida=hhmm_para_s(e["saida"]))
            criancas.append(Crianca(id=cod, lat=float(c["lat"]), lon=float(c["lon"]), escola=escolas[chave].id))
        except (KeyError, ValueError, TypeError):
            raise ErroRastreio(f"Dados incompletos para {cod}: marque a casa no mapa e escolha a escola e os horários.")
    g = dados.get("garagem") or {"lat": criancas[0].lat, "lon": criancas[0].lon}
    veic = [Veiculo(id="V1", capacidade=int(dados.get("capacidade") or 15), lat=float(g["lat"]), lon=float(g["lon"]))]
    cen = Cenario(list(escolas.values()), criancas, veic, Parametros(tempo_limite_s=5), descricao="cadastro rápido")
    if G is not None:
        cen.ajustar_a_malha(G)          # casa vira o cruzamento mais próximo (LGPD)
    for x in [*cen.escolas, *cen.criancas, *cen.veiculos]:
        x.no = None                     # o nó é recalculado quando precisar
    return cen


def exportar(central: Central, linha_id: str) -> dict:
    linha = central._linha(linha_id)
    if linha is None:
        raise ErroRastreio("Linha não encontrada.", 404)
    acessos = central.links(linha_id)
    tokens = {f"{a['papel']}:{a['ref']}": a["token"] for a in acessos}
    # o aceite do termo também é guardado, para a família não precisar aceitar de novo após um reinício
    aceites = {f"{a['papel']}:{a['ref']}": a["consentimento_em"] for a in acessos if a["consentimento_em"]}
    return {"linha_id": linha_id, "nome": linha["nome"], "cenario": json.loads(linha["cenario"]),
            "opcoes": central.opcoes(linha_id), "tokens": tokens, "consentimentos": aceites}


def aplicar(central: Central, cfg: dict) -> dict:
    cen = cenario_de_dict(cfg["cenario"])
    for x in [*cen.escolas, *cen.criancas, *cen.veiculos]:
        x.no = None
    pub = central.publicar_linha(cfg["nome"], cen, linha_id=cfg["linha_id"], tokens=cfg.get("tokens"),
                                 opcoes=cfg.get("opcoes"))
    for chave, quando in (cfg.get("consentimentos") or {}).items():
        tok = (cfg.get("tokens") or {}).get(chave)
        if tok:
            central.db.x("UPDATE acessos SET consentimento_em=COALESCE(consentimento_em, ?) WHERE token=?",
                         (quando, tok))
    return pub


def aplicar_do_ambiente(central: Central, texto: str | None) -> int:
    """ROTAVAN_CONFIG pode ter uma configuração ou uma lista delas."""
    if not texto:
        return 0
    cfgs = json.loads(texto)
    cfgs = cfgs if isinstance(cfgs, list) else [cfgs]
    for c in cfgs:
        aplicar(central, c)
    return len(cfgs)


def resumo_publico(cen_dict: dict) -> dict:
    """Para mostrar na tela de administração (sem nada além do necessário)."""
    return {"escolas": [{"nome": e["nome"], "entrada": e["entrada"], "saida": e["saida"]} for e in cen_dict["escolas"]],
            "criancas": [c["id"] for c in cen_dict["criancas"]]}


__all__ = ["montar_cenario_familia", "exportar", "aplicar", "aplicar_do_ambiente", "cenario_para_dict"]
