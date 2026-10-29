# -*- coding: utf-8 -*-
"""Enumeração exaustiva (força bruta) para instâncias pequenas, com uma van.

Não é para uso prático — o número de sequências cresce fatorialmente. Serve
para validar o otimizador: em instâncias com até 5 crianças, dá para listar
todas as ordens possíveis de embarque/desembarque e saber qual é a melhor."""
from __future__ import annotations

from .avaliacao import Itinerario, metricas, simular
from .instancia import Instancia


def _sequencias(kids, cap):
    """Todas as ordens de eventos em que cada criança embarca antes de desembarcar."""
    n = len(kids)
    seq = []

    def rec(abordo, feitos, pend_emb):
        if len(seq) == 2 * n:
            yield list(seq)
            return
        for k in list(pend_emb):
            if len(abordo) < cap:
                seq.append(("embarque", k)); pend_emb.remove(k); abordo.add(k)
                yield from rec(abordo, feitos, pend_emb)
                abordo.remove(k); pend_emb.add(k); seq.pop()
        for k in list(abordo):
            seq.append(("desembarque", k)); abordo.remove(k)
            yield from rec(abordo, feitos, pend_emb)
            abordo.add(k); seq.pop()

    yield from rec(set(), set(), set(kids))


def melhor_rota_exata(inst: Instancia, veiculo: str | None = None) -> tuple[Itinerario | None, int, int]:
    """Devolve (melhor itinerário viável, nº de sequências viáveis, nº total)."""
    veiculo = veiculo or inst.cen.veiculos[0].id
    cap = next(v.capacidade for v in inst.cen.veiculos if v.id == veiculo)
    kids = [c.id for c in inst.cen.criancas]
    melhor, melhor_km, viaveis, total = None, float("inf"), 0, 0
    for seq in _sequencias(kids, cap):
        total += 1
        tokens = []
        for tipo, k in seq:
            loc = inst.local_embarque(k) if tipo == "embarque" else inst.local_desembarque(k)
            if tokens and tokens[-1][0] == tipo and inst.locais[tokens[-1][1]].no == inst.locais[loc].no:
                tokens[-1][2].append(k)
            else:
                tokens.append((tipo, loc, [k]))
        it = simular(inst, veiculo, tokens, "exato")
        m = metricas(inst, [it])
        if m["viavel"]:
            viaveis += 1
            if m["distancia_km"] < melhor_km - 1e-9:
                melhor, melhor_km = it, m["distancia_km"]
    return melhor, viaveis, total
