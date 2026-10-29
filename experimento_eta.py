# -*- coding: utf-8 -*-
"""Experimento E5 — precisão da previsão de chegada e dos avisos às famílias.

Pergunta: usar a rota otimizada (ordem das paradas + trajeto pelas ruas + tempos
planejados) junto com a posição GPS da van prevê melhor a chegada do que
(a) um horário fixo de passagem ou (b) a distância em linha reta, que é o que um
app genérico de localização consegue fazer?

Procedimento
1. Para cada instância (malha real de Ijuí, 12 crianças, 3 escolas, 1 van; ida e
   volta), o otimizador gera a rota e o plano de viagem.
2. Cada plano é "dirigido" em vários dias simulados com trânsito incerto
   (rotavan/rastreio/simulacao.py): dias normais (fator do dia ~ LogNormal(0; 0,15))
   e dias ruins (trânsito 35 % mais lento + mais incidentes).
3. A cada 30 s de viagem, cada método prevê a chegada a todas as paradas que
   faltam; o erro é comparado com a chegada real da simulação.
4. Avisos "a van chega em ~5 min": para cada família, mede-se quanto tempo
   realmente faltava quando o aviso disparou.

    python experimento_eta.py            (~15 min)
    python experimento_eta.py --rapido   (conferência rápida)
Saídas: resultados/E5_eta_viagens.csv, E5_eta_avisos.csv, E5_eta_erros.npz"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from rotavan.cenario import Parametros, gerar_cenario, ler_escolas_osm
from rotavan.instancia import Instancia
from rotavan.modelo import otimizar_com_nova_tentativa
from rotavan.rastreio.plano import Estimador, criar_plano, previsao_linha_reta, velocidade_linha_reta
from rotavan.rastreio.simulacao import ConfigSimulacao, simular_viagem

PASTA = Path(__file__).resolve().parent
METODOS = ["Horário fixo", "Linha reta", "Posição + rota", "Posição + rota + ritmo"]
FAIXAS = [(0, 5), (5, 10), (10, 15), (15, 20), (20, 30)]
CENARIOS = {
    "normal": (ConfigSimulacao(), None),
    "dia ruim": (ConfigSimulacao(p_incidente=0.30), 1.35),
}
AVISO_S = 300
PASSO_AVAL_S = 30


def faixa(h_min: float):
    for a, b in FAIXAS:
        if a <= h_min < b:
            return f"{a}–{b}"
    return None


def avaliar_viagem(pl, sim, vel_reta):
    """Erros por método e faixa de antecedência + tempos reais dos avisos."""
    est_a, est_p = Estimador(pl), Estimador(pl, modo="plano")
    evs = list(sim.eventos)
    feitos: set[tuple[str, str]] = set()
    atend = pl.atendimentos
    erros = {m: {} for m in METODOS}
    prox_aval = sim.pontos[0][0]
    # avisos: só a parada que importa para a família (ida: casa; volta: casa)
    alvo_familia = {p.ordem for p in atend if (p.tipo == "embarque") == (pl.sentido == "ida")}
    aviso = {m: {} for m in METODOS}
    for o in alvo_familia:   # horário fixo: avisa 5 min antes do horário planejado
        aviso["Horário fixo"][o] = next(p.chegada for p in atend if p.ordem == o) - AVISO_S
    for t, lat, lon in sim.pontos:
        while evs and evs[0][0] <= t:
            _, tipo, k = evs.pop(0)
            feitos.add((tipo, k))
        est_a.atualizar(t, lat, lon)
        est_p.atualizar(t, lat, lon)
        pend = {p.ordem: [k for k in p.criancas if (p.tipo, k) not in feitos] for p in atend}
        pend = {o: ks for o, ks in pend.items() if ks}
        if not pend:
            break
        prev = {
            "Posição + rota + ritmo": {x.ordem: x.chegada for x in est_a.prever(t, pend)},
            "Posição + rota": {x.ordem: x.chegada for x in est_p.prever(t, pend)},
            "Linha reta": previsao_linha_reta(pl, t, lat, lon, list(pend), vel_reta),
            "Horário fixo": {p.ordem: p.chegada for p in atend if p.ordem in pend},
        }
        avaliar = t >= prox_aval
        if avaliar:
            prox_aval += PASSO_AVAL_S
        for o in pend:
            real = sim.chegadas[o]
            if real <= t:          # já chegou (atendendo): não é mais previsão
                continue
            for m in METODOS:
                if m != "Horário fixo" and o in alvo_familia and o not in aviso[m] and prev[m][o] - t <= AVISO_S:
                    aviso[m][o] = t
                if avaliar:
                    fx = faixa((real - t) / 60)
                    if fx:
                        erros[m].setdefault(fx, []).append((prev[m][o] - real) / 60)
    avisos = []
    for m in METODOS:
        for o in alvo_familia:
            if o in aviso[m]:
                avisos.append({"metodo": m, "antecedencia_real_min": (sim.chegadas[o] - aviso[m][o]) / 60})
    return erros, avisos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mapa", default=str(PASTA / "dados" / "ijui_drive.graphml"))
    ap.add_argument("--escolas", default=str(PASTA / "dados" / "escolas_ijui_osm.csv"))
    ap.add_argument("--saida", default=str(PASTA / "resultados"))
    ap.add_argument("--rapido", action="store_true")
    a = ap.parse_args()
    from rotavan.rede import carregar_grafo
    G = carregar_grafo(a.mapa)
    esc = ler_escolas_osm(a.escolas)
    sementes = range(1, 4) if a.rapido else range(1, 31)
    dias = {"normal": 2 if a.rapido else 10, "dia ruim": 1 if a.rapido else 3}
    linhas, avisos_all, todos = [], [], {}
    t0 = time.time()
    planos = []
    for sentido in ("ida", "volta"):
        for sem in sementes:
            params = Parametros(sentido=sentido)
            cen = gerar_cenario(G, 12, 3, sem, escolas_osm=esc, params=params)
            inst = Instancia.criar(cen, G)
            res = otimizar_com_nova_tentativa(inst)
            if res.status != "ok":
                print(f"{sentido} semente {sem}: sem rota viável, fica de fora")
                continue
            planos.append((sentido, sem, criar_plano(inst, res.itinerarios[0])))
            print(f"{sentido} semente {sem}: plano ok ({time.time() - t0:.0f} s)", flush=True)
    vel_reta = velocidade_linha_reta([p for _, _, p in planos])
    print(f"velocidade calibrada da linha reta: {vel_reta * 3.6:.1f} km/h")
    for sentido, sem, pl in planos:
        rng = np.random.default_rng(1000 * sem + (0 if sentido == "ida" else 500))
        for cenario, (cfg, fator) in CENARIOS.items():
            for dia in range(dias[cenario]):
                sim = simular_viagem(pl, rng, cfg, fator_dia=fator)
                erros, avisos = avaliar_viagem(pl, sim, vel_reta)
                base = {"sentido": sentido, "semente": sem, "cenario": cenario, "dia": dia,
                        "fator_dia": round(sim.fator_dia, 3), "incidente": sim.incidente is not None,
                        "duracao_plan_min": round((pl.paradas[-1].chegada - pl.saida) / 60, 1)}
                for m, por_faixa in erros.items():
                    for fx, es in por_faixa.items():
                        es = np.array(es)
                        linhas.append({**base, "metodo": m, "faixa": fx, "n": len(es),
                                       "erro_medio_min": float(es.mean()), "eam_min": float(np.abs(es).mean()),
                                       "dentro_2min": int((np.abs(es) <= 2).sum()),
                                       "dentro_3min": int((np.abs(es) <= 3).sum())})
                        todos.setdefault(f"{cenario}|{m}|{fx}", []).extend(es.round(3).tolist())
                for av in avisos:
                    avisos_all.append({**base, **av})
    json.dump({"vel_linha_reta_kmh": vel_reta * 3.6, "planos": len(planos),
               "viagens": len(planos) * sum(dias.values())},
              open(Path(a.saida) / "E5_eta_config.json", "w"), indent=1)
    out = Path(a.saida)
    out.mkdir(exist_ok=True)
    df = pd.DataFrame(linhas)
    df.to_csv(out / "E5_eta_viagens.csv", index=False)
    pd.DataFrame(avisos_all).to_csv(out / "E5_eta_avisos.csv", index=False)
    np.savez_compressed(out / "E5_eta_erros.npz", **{k: np.array(v, dtype=np.float32) for k, v in todos.items()})
    print("Pronto:", out / "E5_eta_viagens.csv", f"({time.time() - t0:.0f} s)")


if __name__ == "__main__":
    main()
