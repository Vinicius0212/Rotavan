# -*- coding: utf-8 -*-
"""Liga o cenário à malha: locais de parada, matrizes e janelas de horário.

É a camada comum usada pelo otimizador (modelo.py), pelas heurísticas de
comparação (heuristicas.py) e pelo avaliador (avaliacao.py) — assim os três
enxergam exatamente as mesmas distâncias, tempos e regras."""
from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from .cenario import Cenario, hhmm_para_s
from .rede import Matrizes, calcular_matrizes

HORIZONTE = 24 * 3600


@dataclass
class Local:
    tipo: str        # "garagem" | "escola" | "casa"
    ref: str         # id do veículo, da escola ou da criança
    no: int          # nó da malha
    lat: float
    lon: float


@dataclass
class Instancia:
    cen: Cenario
    G: nx.MultiDiGraph
    locais: list[Local] = field(default_factory=list)
    loc_garagem: dict[str, int] = field(default_factory=dict)
    loc_escola: dict[str, int] = field(default_factory=dict)
    loc_casa: dict[str, int] = field(default_factory=dict)
    M: Matrizes | None = None

    @classmethod
    def criar(cls, cen: Cenario, G: nx.MultiDiGraph) -> "Instancia":
        if any(o.no is None for o in [*cen.escolas, *cen.criancas, *cen.veiculos]):
            cen.ajustar_a_malha(G)
        inst = cls(cen=cen, G=G)
        for v in cen.veiculos:
            inst.loc_garagem[v.id] = inst._add("garagem", v.id, v.no)
        for e in cen.escolas:
            inst.loc_escola[e.id] = inst._add("escola", e.id, e.no)
        for c in cen.criancas:
            inst.loc_casa[c.id] = inst._add("casa", c.id, c.no)
        inst.M = calcular_matrizes(G, [l.no for l in inst.locais], cen.params.fator_transito)
        return inst

    def _add(self, tipo, ref, no) -> int:
        self.locais.append(Local(tipo, ref, no, self.G.nodes[no]["y"], self.G.nodes[no]["x"]))
        return len(self.locais) - 1

    # ------------------------------------------------------------ atalhos
    @property
    def p(self):
        return self.cen.params

    def dist(self, a: int, b: int) -> float:
        return float(self.M.distancia[a, b])

    def tempo(self, a: int, b: int) -> float:
        return float(self.M.tempo[a, b])

    def crianca(self, cid):
        for c in self.cen.criancas:
            if c.id == cid:
                return c
        raise KeyError(cid)

    def servico(self, local: int) -> int:
        """Tempo de parada por criança: em casa é o embarque (ou a entrega ao
        responsável, na volta); na escola é a descida/subida na porta."""
        tipo = self.locais[local].tipo
        if tipo == "casa":
            return self.p.embarque_s
        if tipo == "escola":
            return self.p.desembarque_s
        return 0

    # ------------------------------------------------------------ janelas (s)
    def janela_na_casa(self, cid: str) -> tuple[int, int]:
        p = self.p
        c = self.crianca(cid)
        esc = self.cen.escola(c.escola)
        if p.sentido == "ida":
            cedo = c.embarque_mais_cedo if c.embarque_mais_cedo is not None else hhmm_para_s(p.embarque_mais_cedo)
            return int(cedo), int(esc.entrada - p.folga_min * 60)
        return 0, HORIZONTE

    def janela_na_escola(self, cid: str) -> tuple[int, int]:
        p = self.p
        esc = self.cen.escola(self.crianca(cid).escola)
        if p.sentido == "ida":
            return int(esc.entrada - p.antecedencia_max_min * 60), int(esc.entrada - p.folga_min * 60)
        return int(esc.saida), int(esc.saida + p.tolerancia_saida_min * 60)

    def janela_embarque(self, cid):
        return self.janela_na_casa(cid) if self.p.sentido == "ida" else self.janela_na_escola(cid)

    def janela_desembarque(self, cid):
        return self.janela_na_escola(cid) if self.p.sentido == "ida" else self.janela_na_casa(cid)

    def local_embarque(self, cid) -> int:
        c = self.crianca(cid)
        return self.loc_casa[cid] if self.p.sentido == "ida" else self.loc_escola[c.escola]

    def local_desembarque(self, cid) -> int:
        c = self.crianca(cid)
        return self.loc_escola[c.escola] if self.p.sentido == "ida" else self.loc_casa[cid]
