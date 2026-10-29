# -*- coding: utf-8 -*-
"""Heurísticas construtivas que imitam o planejamento intuitivo de um motorista.

Servem de linha de base para medir o ganho do otimizador quando não se tem a
rota real praticada. São deliberadamente simples e razoáveis — não espantalhos."""
from __future__ import annotations

from .avaliacao import Itinerario, simular
from .instancia import Instancia


def _agrupar_mesmo_no(inst: Instancia, kids, local_de):
    """Irmãos (ou vizinhos no mesmo cruzamento) viram uma parada só."""
    grupos: dict[int, list] = {}
    for k in kids:
        grupos.setdefault(inst.locais[local_de(k)].no, []).append(k)
    return list(grupos.values())


def _vizinho_mais_proximo(inst: Instancia, origem: int, grupos, local_de):
    """Ordena grupos pelo vizinho mais próximo (em tempo), a partir de `origem`."""
    ordem, cur, rest = [], origem, list(grupos)
    while rest:
        prox = min(rest, key=lambda g: inst.tempo(cur, local_de(g[0])))
        ordem.append(prox)
        cur = local_de(prox[0])
        rest.remove(prox)
    return ordem, cur


def vizinho_mais_proximo(inst: Instancia, veiculo: str | None = None) -> Itinerario:
    """Ida: busca todas as crianças sempre indo à casa mais próxima; depois
    passa nas escolas na ordem do horário de entrada.
    Volta: recolhe nas escolas pela ordem de saída; entrega pela casa mais próxima."""
    veiculo = veiculo or inst.cen.veiculos[0].id
    kids = [c.id for c in inst.cen.criancas]
    g = inst.loc_garagem[veiculo]
    escolas = sorted(inst.cen.escolas, key=lambda e: (e.entrada if inst.p.sentido == "ida" else e.saida))
    tokens = []
    if inst.p.sentido == "ida":
        grupos = _agrupar_mesmo_no(inst, kids, inst.local_embarque)
        ordem, _ = _vizinho_mais_proximo(inst, g, grupos, inst.local_embarque)
        tokens += [("embarque", inst.local_embarque(gr[0]), gr) for gr in ordem]
        for e in escolas:
            desce = [k for k in kids if inst.crianca(k).escola == e.id]
            if desce:
                tokens.append(("desembarque", inst.loc_escola[e.id], desce))
    else:
        cur = g
        for e in escolas:
            sobe = [k for k in kids if inst.crianca(k).escola == e.id]
            if sobe:
                tokens.append(("embarque", inst.loc_escola[e.id], sobe)); cur = inst.loc_escola[e.id]
        grupos = _agrupar_mesmo_no(inst, kids, inst.local_desembarque)
        ordem, _ = _vizinho_mais_proximo(inst, cur, grupos, inst.local_desembarque)
        tokens += [("desembarque", inst.local_desembarque(gr[0]), gr) for gr in ordem]
    return simular(inst, veiculo, tokens, "vizinho mais próximo")


def escola_por_escola(inst: Instancia, veiculo: str | None = None) -> Itinerario:
    """Ida: atende uma escola de cada vez, na ordem do horário de entrada —
    busca (pela casa mais próxima) só as crianças daquela escola e as deixa lá.
    Volta: recolhe uma escola e entrega todas as suas crianças antes da próxima."""
    veiculo = veiculo or inst.cen.veiculos[0].id
    g = inst.loc_garagem[veiculo]
    chave = (lambda e: e.entrada) if inst.p.sentido == "ida" else (lambda e: e.saida)
    tokens, cur = [], g
    for e in sorted(inst.cen.escolas, key=chave):
        kids = [c.id for c in inst.cen.criancas if c.escola == e.id]
        if not kids:
            continue
        le = inst.loc_escola[e.id]
        if inst.p.sentido == "ida":
            grupos = _agrupar_mesmo_no(inst, kids, inst.local_embarque)
            ordem, _ = _vizinho_mais_proximo(inst, cur, grupos, inst.local_embarque)
            tokens += [("embarque", inst.local_embarque(gr[0]), gr) for gr in ordem]
            tokens.append(("desembarque", le, kids))
        else:
            tokens.append(("embarque", le, kids))
            grupos = _agrupar_mesmo_no(inst, kids, inst.local_desembarque)
            ordem, ult = _vizinho_mais_proximo(inst, le, grupos, inst.local_desembarque)
            tokens += [("desembarque", inst.local_desembarque(gr[0]), gr) for gr in ordem]
            le = ult
        cur = le
    return simular(inst, veiculo, tokens, "escola por escola")


HEURISTICAS = {
    "vizinho mais próximo": vizinho_mais_proximo,
    "escola por escola": escola_por_escola,
}
