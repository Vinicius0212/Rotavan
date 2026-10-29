# -*- coding: utf-8 -*-
"""Simulação de uma viagem "real" a partir do plano.

Serve para duas coisas:
1. demonstração: o servidor pode "dirigir" a van sozinho, para mostrar a tela dos
   pais sem precisar sair de carro;
2. experimento: gera centenas de viagens com trânsito variável para medir o erro
   da previsão de chegada (experimento_eta.py).

O modelo de incerteza é simples e explícito (está descrito no artigo):
- cada dia tem um fator de trânsito D ~ LogNormal(0, sigma_dia): dias bons e ruins;
- cada trecho entre paradas tem um fator próprio L ~ LogNormal(0, sigma_trecho);
- tempo real de condução do trecho = tempo planejado × D × L;
- com probabilidade p_incidente a viagem tem uma retenção (obra, acidente,
  fila de escola) de 2 a 6 min num ponto aleatório;
- o embarque/desembarque leva o tempo planejado × LogNormal(0, sigma_parada);
- a saída da garagem varia ± alguns minutos;
- o GPS do celular tem erro aleatório de ~8 m e manda posição a cada 5 s."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .plano import PlanoViagem, R_TERRA


@dataclass
class ConfigSimulacao:
    sigma_dia: float = 0.15
    sigma_trecho: float = 0.20
    sigma_parada: float = 0.40
    p_incidente: float = 0.15
    incidente_min: tuple = (2.0, 6.0)
    desvio_saida_min: float = 2.0
    gps_erro_m: float = 8.0
    periodo_gps_s: float = 5.0


@dataclass
class ViagemSimulada:
    pontos: list[tuple[float, float, float]]          # (t, lat, lon) com ruído
    eventos: list[tuple[float, str, str]]             # (t, tipo, crianca)
    chegadas: dict[int, float]                         # ordem da parada -> hora real de chegada
    fator_dia: float = 1.0
    incidente: tuple | None = None                     # (cum_s, duração s)
    cum_real: list[tuple[float, float]] = field(default_factory=list)   # (t, cum_s) sem ruído


def simular_viagem(plano: PlanoViagem, rng: np.random.Generator, cfg: ConfigSimulacao | None = None,
                   t_saida: float | None = None, fator_dia: float | None = None) -> ViagemSimulada:
    cfg = cfg or ConfigSimulacao()
    D = float(rng.lognormal(0.0, cfg.sigma_dia)) if fator_dia is None else float(fator_dia)
    t = (plano.saida if t_saida is None else t_saida) + rng.normal(0, cfg.desvio_saida_min * 60)
    ps = plano.paradas
    incidente = None
    if rng.random() < cfg.p_incidente and ps[-1].cum_s > 60:
        incidente = (float(rng.uniform(30, ps[-1].cum_s - 30)), float(rng.uniform(*cfg.incidente_min) * 60))

    # linha do tempo contínua: lista de (t_real, cum_s) — interpolada depois
    linha = [(t, 0.0)]
    eventos, chegadas = [], {}
    for a, b in zip(ps[:-1], ps[1:]):
        L = float(rng.lognormal(0.0, cfg.sigma_trecho))
        dur_plan = b.cum_s - a.cum_s
        if incidente and a.cum_s <= incidente[0] < b.cum_s:
            t1 = t + (incidente[0] - a.cum_s) * D * L
            linha.append((t1, incidente[0]))
            linha.append((t1 + incidente[1], incidente[0]))
            t = t1 + incidente[1] + (b.cum_s - incidente[0]) * D * L
        else:
            t = t + dur_plan * D * L
        linha.append((t, b.cum_s))
        chegadas[b.ordem] = t
        if b.tipo in ("embarque", "desembarque"):
            t = max(t, b.lb)                         # espera a janela (não busca antes da hora)
            for k in b.criancas:
                t += b.servico_s * float(rng.lognormal(0.0, cfg.sigma_parada))
                eventos.append((t, b.tipo, k))
            linha.append((t, b.cum_s))

    # amostra o GPS a cada período
    ts = np.array([x[0] for x in linha])
    cs = np.array([x[1] for x in linha])
    pontos, cum_real = [], []
    lat_m = 1.0 / (R_TERRA * math.pi / 180)
    for tt in np.arange(ts[0], ts[-1] + 1e-9, cfg.periodo_gps_s):
        c = float(np.interp(tt, ts, cs))
        lat, lon = plano.ponto_em(c)
        dn, de = rng.normal(0, cfg.gps_erro_m, 2)
        lat += dn * lat_m
        lon += de * lat_m / math.cos(math.radians(lat))
        pontos.append((float(tt), lat, lon))
        cum_real.append((float(tt), c))
    return ViagemSimulada(pontos, sorted(eventos), chegadas, D, incidente, cum_real)
