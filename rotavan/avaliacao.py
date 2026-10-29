# -*- coding: utf-8 -*-
"""Itinerários, simulação de horários e métricas.

Toda rota — a do motorista, a de uma heurística ou a do otimizador — é avaliada
pelas mesmas funções, com a mesma malha e as mesmas regras. É isso que torna a
comparação justa."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .cenario import s_para_hhmm, hhmm_para_s
from .instancia import Instancia, HORIZONTE

TOL_S = 1.0   # tolerância numérica (s)


@dataclass
class Parada:
    tipo: str                      # "saida" | "embarque" | "desembarque" | "fim"
    local: int
    chegada: float                 # hora de chegada (s)
    inicio: float                  # início do atendimento, após eventual espera (s)
    eventos: list = field(default_factory=list)   # [(crianca, hora_do_evento)]

    @property
    def criancas(self):
        return [c for c, _ in self.eventos]


@dataclass
class Itinerario:
    veiculo: str
    paradas: list[Parada]
    metodo: str = ""

    def locais(self):
        return [p.local for p in self.paradas]


# ------------------------------------------------------------------ simulação
def _simular_de(inst: Instancia, garagem: int, tokens, t0: float) -> list[Parada]:
    paradas = [Parada("saida", garagem, t0, t0)]
    cur, t = garagem, t0
    for tipo, loc, kids in tokens:
        chegada = t + inst.tempo(cur, loc)
        jan = inst.janela_embarque if tipo == "embarque" else inst.janela_desembarque
        lb = max((jan(k)[0] for k in kids), default=0)
        inicio = max(chegada, lb)
        serv = inst.servico(loc)
        eventos = [(k, inicio + i * serv) for i, k in enumerate(kids)]
        paradas.append(Parada(tipo, loc, chegada, inicio, eventos))
        t = inicio + serv * len(kids)
        cur = loc
    if inst.p.retorna_garagem:
        chegada = t + inst.tempo(cur, garagem)
        paradas.append(Parada("fim", garagem, chegada, chegada))
    return paradas


def _folga_minima(inst: Instancia, paradas) -> float:
    folgas = []
    for p in paradas:
        if p.tipo not in ("embarque", "desembarque"):
            continue
        jan = inst.janela_embarque if p.tipo == "embarque" else inst.janela_desembarque
        for k, t in p.eventos:
            ub = jan(k)[1]
            if ub < HORIZONTE:
                folgas.append(ub - t)
    return min(folgas) if folgas else 0.0


def simular(inst: Instancia, veiculo: str, tokens, metodo: str = "") -> Itinerario:
    """Calcula os horários de uma sequência de paradas.

    Ida: o motorista sai o mais tarde possível sem atrasar nenhuma escola.
    Volta: sai de modo a chegar à primeira escola no horário de saída das aulas."""
    g = inst.loc_garagem[veiculo]
    if not tokens:
        return Itinerario(veiculo, [Parada("saida", g, 0, 0)], metodo)
    if inst.p.sentido == "ida":
        t0 = float(hhmm_para_s(inst.p.saida_minima))
        paradas = _simular_de(inst, g, tokens, t0)
        for _ in range(60):
            folga = _folga_minima(inst, paradas)
            if folga <= TOL_S:
                break
            t0 += folga
            paradas = _simular_de(inst, g, tokens, t0)
    else:
        tipo, loc, kids = tokens[0]
        lb = max(inst.janela_embarque(k)[0] for k in kids)
        t0 = lb - inst.tempo(g, loc)
        paradas = _simular_de(inst, g, tokens, t0)
    return Itinerario(veiculo, paradas, metodo)


# ------------------------------------------------------------------ rota informada
def tokens_de_sequencia(inst: Instancia, sequencia: list[str]):
    """Converte a sequência de paradas que o motorista faz hoje em eventos.

    Ida:   códigos de criança = buscar em casa; código de escola = deixar ali
           todas as crianças a bordo que estudam nela.
    Volta: código de escola = buscar ali as crianças daquela escola que esta van
           entrega; código de criança = deixar em casa."""
    ids_c = {c.id for c in inst.cen.criancas}
    ids_e = {e.id for e in inst.cen.escolas}
    tokens = []
    if inst.p.sentido == "ida":
        abordo = []
        for t in sequencia:
            t = str(t).strip()
            if t in ids_c:
                tokens.append(("embarque", inst.loc_casa[t], [t])); abordo.append(t)
            elif t in ids_e:
                desce = [k for k in abordo if inst.crianca(k).escola == t]
                abordo = [k for k in abordo if k not in desce]
                if desce:
                    tokens.append(("desembarque", inst.loc_escola[t], desce))
            else:
                raise ValueError(f"Parada desconhecida: {t}")
    else:
        entregas = [str(t).strip() for t in sequencia if str(t).strip() in ids_c]
        for t in sequencia:
            t = str(t).strip()
            if t in ids_e:
                sobe = [k for k in entregas if inst.crianca(k).escola == t]
                if sobe:
                    tokens.append(("embarque", inst.loc_escola[t], sobe))
            elif t in ids_c:
                tokens.append(("desembarque", inst.loc_casa[t], [t]))
            else:
                raise ValueError(f"Parada desconhecida: {t}")
    return tokens


def ler_rota_informada(inst: Instancia, caminho_ou_df) -> list[Itinerario]:
    """CSV com colunas `veiculo` (opcional) e `parada`, na ordem em que o
    motorista faz hoje."""
    df = caminho_ou_df if isinstance(caminho_ou_df, pd.DataFrame) else pd.read_csv(caminho_ou_df, dtype=str)
    if "veiculo" not in df.columns:
        df = df.assign(veiculo=inst.cen.veiculos[0].id)
    itins = []
    for vid, grupo in df.groupby("veiculo", sort=False):
        itins.append(simular(inst, str(vid), tokens_de_sequencia(inst, list(grupo["parada"])), "rota informada"))
    return itins


# ------------------------------------------------------------------ métricas
def metricas(inst: Instancia, itins: list[Itinerario]) -> dict:
    p = inst.p
    dist = tempo_cond = 0.0
    duracoes = []
    embarque, desembarque = {}, {}
    atrasos, cargas_excedidas, carga_max = [], 0, 0
    cap = {v.id: v.capacidade for v in inst.cen.veiculos}
    usadas = 0
    for it in itins:
        ps = it.paradas
        if len(ps) <= 1:
            continue
        usadas += 1
        carga = 0
        for a, b in zip(ps[:-1], ps[1:]):
            dist += inst.dist(a.local, b.local)
            tempo_cond += inst.tempo(a.local, b.local)
        for pa in ps:
            if pa.tipo == "embarque":
                carga += len(pa.eventos)
                for k, t in pa.eventos:
                    embarque[k] = t
                    ub = inst.janela_embarque(k)[1]
                    if ub < HORIZONTE:
                        atrasos.append((k, max(0.0, t - ub)))
            elif pa.tipo == "desembarque":
                for k, t in pa.eventos:
                    desembarque[k] = t
                    ub = inst.janela_desembarque(k)[1]
                    if ub < HORIZONTE:
                        atrasos.append((k, max(0.0, t - ub)))
                carga -= len(pa.eventos)
            carga_max = max(carga_max, carga)
            if carga > cap[it.veiculo]:
                cargas_excedidas += 1
        fim = ps[-1]
        t_fim = fim.inicio + inst.servico(fim.local) * len(fim.eventos)
        duracoes.append(t_fim - ps[0].inicio)
    ids = [c.id for c in inst.cen.criancas]
    atendidas = [k for k in ids if k in embarque and k in desembarque]
    bordo = np.array([desembarque[k] - embarque[k] for k in atendidas]) / 60.0
    tmax = p.tempo_max_bordo_min
    n_excesso = int((bordo > tmax + TOL_S / 60).sum()) if len(bordo) else 0
    atrasadas = {k for k, a in atrasos if a > TOL_S}
    n_atraso = len(atrasadas)
    atrasos = np.array([a for _, a in atrasos]) / 60.0 if atrasos else np.zeros(1)
    km = dist / 1000.0
    litros = km / p.consumo_km_l
    return {
        "vans_usadas": usadas,
        "distancia_km": km,
        "tempo_conducao_min": tempo_cond / 60.0,
        "duracao_total_min": float(np.sum(duracoes)) / 60.0 if duracoes else 0.0,
        "duracao_max_min": float(np.max(duracoes)) / 60.0 if duracoes else 0.0,
        "bordo_medio_min": float(bordo.mean()) if len(bordo) else 0.0,
        "bordo_max_min": float(bordo.max()) if len(bordo) else 0.0,
        "criancas_acima_tmax": n_excesso,
        "criancas_atrasadas": n_atraso,
        "atraso_max_min": float(atrasos.max()),
        "carga_max": carga_max,
        "violacoes_capacidade": cargas_excedidas,
        "criancas_atendidas": len(atendidas),
        "criancas_total": len(ids),
        "litros_trajeto": litros,
        "custo_trajeto_r$": litros * p.preco_litro,
        "viavel": bool(n_excesso == 0 and n_atraso == 0 and cargas_excedidas == 0
                       and len(atendidas) == len(ids)),
    }


def tabela_itinerario(inst: Instancia, itins: list[Itinerario]) -> pd.DataFrame:
    """Itinerário legível para o motorista (hora, parada, quem sobe e quem desce)."""
    linhas = []
    for it in itins:
        if len(it.paradas) <= 1:
            continue
        for i, pa in enumerate(it.paradas):
            loc = inst.locais[pa.local]
            if loc.tipo == "escola":
                nome = inst.cen.escola(loc.ref).nome
            elif loc.tipo == "garagem":
                nome = "Garagem"
            else:
                nome = f"Casa de {loc.ref}"
            linhas.append({
                "van": it.veiculo, "ordem": i, "horario": s_para_hhmm(pa.inicio),
                "acao": {"saida": "Saída", "embarque": "Buscar", "desembarque": "Deixar", "fim": "Chegada"}[pa.tipo],
                "local": nome, "criancas": ", ".join(pa.criancas),
                "espera_min": round((pa.inicio - pa.chegada) / 60.0, 1),
                "lat": round(loc.lat, 6), "lon": round(loc.lon, 6),
            })
    return pd.DataFrame(linhas)
