# -*- coding: utf-8 -*-
"""Bateria de experimentos do TCC (gera as tabelas e figuras do artigo).

    python experimentos.py                  # tudo, com o mapa real (~45 min)
    python experimentos.py --rapido         # versão curta, para conferir se roda
    python experimentos.py --so E1 E4       # só alguns experimentos

Saídas em resultados/: um CSV por experimento (uma linha por instância e método)
e resumo.json. As tabelas e figuras são montadas por analisar.py."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd

from rotavan.avaliacao import metricas
from rotavan.cenario import Parametros, gerar_cenario, ler_escolas_osm, salvar_cenario
from rotavan.heuristicas import HEURISTICAS
from rotavan.instancia import Instancia
from rotavan.modelo import otimizar, otimizar_com_nova_tentativa
from rotavan.exato import melhor_rota_exata
from rotavan.rede import carregar_grafo, grafo_teste

PASTA = Path(__file__).resolve().parent


def _linha(exp, cen, metodo, m, extra=None):
    d = {"experimento": exp, "instancia": cen.descricao, **m, "metodo": metodo}
    d.update(extra or {})
    return d


def rodar_instancia(G, esc, exp, n, sem, params, n_escolas=3, n_vans=1, cap=15,
                    heuristicas=True, variantes=None):
    cen = gerar_cenario(G, n, n_escolas, sem, escolas_osm=esc, n_vans=n_vans, capacidade=cap, params=params)
    inst = Instancia.criar(cen, G)
    linhas = []
    extra = {"n": n, "semente": sem, "sentido": params.sentido, "tmax": params.tempo_max_bordo_min,
             "vans_frota": n_vans}
    if heuristicas and n_vans == 1:
        for nome, f in HEURISTICAS.items():
            linhas.append(_linha(exp, cen, nome, metricas(inst, [f(inst)]), extra))
    for rotulo, kw in (variantes or {"OR-Tools (GLS)": None}).items():
        # método proposto = o mesmo comportamento da interface (com nova tentativa se alguém ficar de fora)
        r = otimizar_com_nova_tentativa(inst) if kw is None else otimizar(inst, **kw)
        m = metricas(inst, r.itinerarios) if r.itinerarios else {"viavel": False}
        linhas.append(_linha(exp, cen, rotulo, m, {**extra, "status": r.status, "nao_atendidas": len(r.nao_atendidas),
                                                   "tempo_solver_s": r.tempo_solver_s, "objetivo": r.objetivo}))
    return linhas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mapa", default=str(PASTA / "dados" / "ijui_drive.graphml"))
    ap.add_argument("--escolas", default=str(PASTA / "dados" / "escolas_ijui_osm.csv"))
    ap.add_argument("--saida", default=str(PASTA / "resultados"))
    ap.add_argument("--rapido", action="store_true")
    ap.add_argument("--teste", action="store_true", help="usa malha em grade (sem mapa real)")
    ap.add_argument("--so", nargs="*", default=None)
    a = ap.parse_args()

    out = Path(a.saida); out.mkdir(parents=True, exist_ok=True)
    if a.teste:
        G, esc = grafo_teste(n=25), None
    else:
        G = carregar_grafo(a.mapa)
        esc = ler_escolas_osm(a.escolas) if Path(a.escolas).exists() else None
    r = a.rapido
    lim = 2 if r else 10
    seeds = range(3) if r else range(20)
    quer = lambda e: a.so is None or e in a.so
    t_ini = time.time()
    info = {"nos_malha": G.number_of_nodes(), "arestas_malha": G.number_of_edges(),
            "escolas_osm": 0 if esc is None else len(esc), "rapido": r, "teste": a.teste}

    # E0 — validação: otimizador x enumeração exaustiva (instâncias pequenas)
    if quer("E0"):
        L = []
        for s in (range(3) if r else range(30)):
            cen = gerar_cenario(G, 4, 2, 500 + s, escolas_osm=esc, params=Parametros(sentido="ida"))
            inst = Instancia.criar(cen, G)
            t = time.perf_counter(); melhor, nv, tot = melhor_rota_exata(inst); te = time.perf_counter() - t
            ro = otimizar(inst, 2, peso_duracao=0)
            mo = metricas(inst, ro.itinerarios)
            L.append({"semente": 500 + s, "sequencias": tot, "viaveis": nv, "tempo_exato_s": te,
                      "km_exato": metricas(inst, [melhor])["distancia_km"] if melhor else None,
                      "km_ortools": mo["distancia_km"], "ortools_viavel": mo["viavel"],
                      "tempo_ortools_s": ro.tempo_solver_s})
            print(f"E0 semente={s}  ({time.time() - t_ini:.0f}s)", flush=True)
        pd.DataFrame(L).to_csv(out / "E0_validacao.csv", index=False)

    # E1 — ida: heurísticas x otimizador, 1 van de 15 lugares
    if quer("E1"):
        L = []
        for n in ((8, 12) if r else (8, 12, 15)):
            for s in seeds:
                L += rodar_instancia(G, esc, "E1", n, 1000 + s, Parametros(sentido="ida", tempo_limite_s=lim))
                print(f"E1 n={n} semente={s}  ({time.time() - t_ini:.0f}s)", flush=True)
        pd.DataFrame(L).to_csv(out / "E1_ida.csv", index=False)

    # E1b — volta
    if quer("E1b"):
        L = []
        for s in seeds:
            L += rodar_instancia(G, esc, "E1b", 12, 1000 + s, Parametros(sentido="volta", tempo_limite_s=lim))
            print(f"E1b semente={s}  ({time.time() - t_ini:.0f}s)", flush=True)
        pd.DataFrame(L).to_csv(out / "E1b_volta.csv", index=False)

    # E2 — sensibilidade ao tempo máximo a bordo
    if quer("E2"):
        L = []
        for tmax in ((30, 45) if r else (25, 30, 35, 45, 60)):
            for s in (range(3) if r else range(10)):
                L += rodar_instancia(G, esc, "E2", 15, 2000 + s,
                                     Parametros(sentido="ida", tempo_max_bordo_min=tmax, tempo_limite_s=lim))
                print(f"E2 tmax={tmax} semente={s}  ({time.time() - t_ini:.0f}s)", flush=True)
        pd.DataFrame(L).to_csv(out / "E2_tmax.csv", index=False)

    # E3 — frota: quantas vans são necessárias
    if quer("E3"):
        L = []
        for n in ((20,) if r else (20, 30, 40)):
            for s in (range(2) if r else range(10)):
                L += rodar_instancia(G, esc, "E3", n, 3000 + s,
                                     Parametros(sentido="ida", tempo_limite_s=lim * 2),
                                     n_escolas=4, n_vans=4, heuristicas=False)
                print(f"E3 n={n} semente={s}  ({time.time() - t_ini:.0f}s)", flush=True)
        pd.DataFrame(L).to_csv(out / "E3_frota.csv", index=False)

    # E4 — algoritmo: solução inicial x metaheurísticas x tempo de busca
    if quer("E4"):
        var = {"Só solução inicial": {"so_solucao_inicial": True, "tempo_limite_s": 30},
               "GLS 1 s": {"metaheuristica": "gls", "tempo_limite_s": 1},
               "GLS 5 s": {"metaheuristica": "gls", "tempo_limite_s": 5},
               "GLS 10 s": {"metaheuristica": "gls", "tempo_limite_s": 10},
               "GLS 30 s": {"metaheuristica": "gls", "tempo_limite_s": 30},
               "Tabu 10 s": {"metaheuristica": "tabu", "tempo_limite_s": 10},
               "Recozimento 10 s": {"metaheuristica": "recozimento", "tempo_limite_s": 10}}
        if r:
            var = {k: {**v, "tempo_limite_s": min(v["tempo_limite_s"], 2)} for k, v in list(var.items())[:3]}
        L = []
        for s in (range(2) if r else range(10)):
            L += rodar_instancia(G, esc, "E4", 15, 4000 + s, Parametros(sentido="ida"), heuristicas=False,
                                 variantes=var)
            print(f"E4 semente={s}  ({time.time() - t_ini:.0f}s)", flush=True)
        pd.DataFrame(L).to_csv(out / "E4_algoritmo.csv", index=False)

    # instância de exemplo para o estudo de caso ilustrativo (figura e apêndice)
    cen = gerar_cenario(G, 12, 3, 1007, escolas_osm=esc, params=Parametros(sentido="ida"))
    salvar_cenario(cen, out / "exemplo_n12")

    info["duracao_total_s"] = round(time.time() - t_ini, 1)
    (out / "resumo.json").write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Concluído em", info["duracao_total_s"], "s")


if __name__ == "__main__":
    main()
