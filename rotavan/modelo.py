# -*- coding: utf-8 -*-
"""Otimizador: problema de coleta e entrega com janelas de tempo (PDPTW).

Cada criança é um par (embarque, desembarque) que precisa ser atendido pela
mesma van, nesta ordem. Restrições:
  * capacidade da van (lugares);
  * janela de horário na escola (chegar entre Y e X minutos antes da entrada,
    ou buscar logo após a saída, na volta);
  * tempo máximo a bordo por criança (conforto e segurança);
  * ruas de mão única (matrizes assimétricas).
Objetivo: menor distância (ou tempo) total; com frota > 1, menor nº de vans.

Resolvido com o Google OR-Tools (Routing): construção por inserção mais barata
em paralelo seguida de busca local guiada (Guided Local Search)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from .avaliacao import Itinerario, Parada
from .cenario import hhmm_para_s
from .instancia import Instancia, HORIZONTE

CUSTO_FIXO_VAN = 1_000_000     # em metros equivalentes: usar uma van a mais "custa" 1000 km
PENALIDADE_NAO_ATENDER = 100_000_000   # deixar uma criança sem atendimento "custa" 100 mil km
PESO_DURACAO = 1               # desempate: 1 s de jornada ~ 1 m (favorece sair mais tarde)

ESTRATEGIAS = {
    "insercao": routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION,
    "insercao_local": routing_enums_pb2.FirstSolutionStrategy.LOCAL_CHEAPEST_INSERTION,
    "automatica": routing_enums_pb2.FirstSolutionStrategy.AUTOMATIC,
    "arco_mais_barato": routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC,
}
METAHEURISTICAS = {
    "gls": routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH,
    "descida": routing_enums_pb2.LocalSearchMetaheuristic.GREEDY_DESCENT,
    "tabu": routing_enums_pb2.LocalSearchMetaheuristic.TABU_SEARCH,
    "recozimento": routing_enums_pb2.LocalSearchMetaheuristic.SIMULATED_ANNEALING,
}


@dataclass
class ResultadoOtimizacao:
    itinerarios: list[Itinerario]
    status: str
    tempo_solver_s: float
    objetivo: int | None
    nao_atendidas: list = field(default_factory=list)   # crianças que não couberam nas regras


def otimizar(inst: Instancia, tempo_limite_s: float | None = None, metaheuristica: str = "gls",
             estrategia: str = "insercao", so_solucao_inicial: bool = False,
             peso_duracao: int = PESO_DURACAO, rota_inicial: Itinerario | None = None) -> ResultadoOtimizacao:
    p = inst.p
    cen = inst.cen
    V = len(cen.veiculos)
    criancas = [c.id for c in cen.criancas]

    # ---------------- nós do modelo -> locais da instância (-1 = fim aberto)
    node_local, node_kid, node_tipo = [], [], []
    starts, ends = [], []
    for v in cen.veiculos:
        starts.append(len(node_local)); node_local.append(inst.loc_garagem[v.id]); node_kid.append(None); node_tipo.append("saida")
    for v in cen.veiculos:
        ends.append(len(node_local))
        node_local.append(inst.loc_garagem[v.id] if p.retorna_garagem else -1)
        node_kid.append(None); node_tipo.append("fim")
    pares = []
    for k in criancas:
        pi = len(node_local); node_local.append(inst.local_embarque(k)); node_kid.append(k); node_tipo.append("embarque")
        di = len(node_local); node_local.append(inst.local_desembarque(k)); node_kid.append(k); node_tipo.append("desembarque")
        pares.append((pi, di))
    N = len(node_local)

    man = pywrapcp.RoutingIndexManager(N, V, starts, ends)
    rot = pywrapcp.RoutingModel(man)
    solver = rot.solver()

    D, T = inst.M.distancia, inst.M.tempo
    serv = [inst.servico(l) if node_kid[i] else 0 for i, l in enumerate(node_local)]

    def _dist(i, j):
        a, b = node_local[man.IndexToNode(i)], node_local[man.IndexToNode(j)]
        return 0 if a < 0 or b < 0 else int(round(D[a, b]))

    def _tempo(i, j):
        ni, nj = man.IndexToNode(i), man.IndexToNode(j)
        a, b = node_local[ni], node_local[nj]
        viagem = 0 if a < 0 or b < 0 else T[a, b]
        return int(round(serv[ni] + viagem))

    cb_dist = rot.RegisterTransitCallback(_dist)
    cb_tempo = rot.RegisterTransitCallback(_tempo)
    rot.SetArcCostEvaluatorOfAllVehicles(cb_dist if p.minimizar == "distancia" else cb_tempo)
    if V > 1 and p.minimizar_vans:
        rot.SetFixedCostOfAllVehicles(CUSTO_FIXO_VAN)

    # ---------------- tempo
    rot.AddDimension(cb_tempo, 3600, HORIZONTE, False, "Tempo")
    tempo = rot.GetDimensionOrDie("Tempo")
    tempo.SetSpanCostCoefficientForAllVehicles(peso_duracao)
    t_min = hhmm_para_s(p.saida_minima) if p.sentido == "ida" else 0
    for v in range(V):
        tempo.CumulVar(rot.Start(v)).SetRange(t_min, HORIZONTE)

    # ---------------- carga
    demanda = [0] * N
    for pi, di in pares:
        demanda[pi], demanda[di] = 1, -1
    cb_dem = rot.RegisterUnaryTransitCallback(lambda i: demanda[man.IndexToNode(i)])
    rot.AddDimensionWithVehicleCapacity(cb_dem, 0, [v.capacidade for v in cen.veiculos], True, "Carga")

    # ---------------- pares, janelas e tempo máximo a bordo
    tmax = int(round(p.tempo_max_bordo_min * 60))
    for (pi, di), k in zip(pares, criancas):
        ip, idl = man.NodeToIndex(pi), man.NodeToIndex(di)
        rot.AddPickupAndDelivery(ip, idl)                        # par casa-escola
        solver.Add(rot.VehicleVar(ip) == rot.VehicleVar(idl))    # mesma van
        solver.Add(tempo.CumulVar(ip) <= tempo.CumulVar(idl))    # embarca antes
        solver.Add(tempo.CumulVar(idl) - tempo.CumulVar(ip) <= tmax)   # tempo a bordo
        tempo.CumulVar(ip).SetRange(*inst.janela_embarque(k))    # janelas (s)
        tempo.CumulVar(idl).SetRange(*inst.janela_desembarque(k))
        # Permite deixar uma criança de fora, a um custo proibitivo: assim sempre existe
        # uma solução inicial e, se alguém realmente não couber nas regras, o sistema
        # diz quem é, em vez de só responder "inviável" (RF07).
        rot.AddDisjunction([ip], PENALIDADE_NAO_ATENDER)        # diagnóstico (RF07)
        rot.AddDisjunction([idl], PENALIDADE_NAO_ATENDER)
        solver.Add(rot.ActiveVar(ip) == rot.ActiveVar(idl))
    for v in range(V):   # sair o mais tarde possível: menos tempo das crianças na van
        rot.AddVariableMaximizedByFinalizer(tempo.CumulVar(rot.Start(v)))

    # ---------------- busca
    prm = pywrapcp.DefaultRoutingSearchParameters()
    prm.first_solution_strategy = ESTRATEGIAS[estrategia]
    if so_solucao_inicial:
        prm.solution_limit = 1          # para na primeira solução construída
    else:
        prm.local_search_metaheuristic = METAHEURISTICAS[metaheuristica]
    limite = tempo_limite_s if tempo_limite_s is not None else p.tempo_limite_s
    prm.time_limit.FromMilliseconds(int(limite * 1000))

    t0 = time.perf_counter()
    sol = None
    if rota_inicial is not None:
        # partida a quente: começa da rota de uma heurística (se ela for aceita pelo modelo)
        rot.CloseModelWithParameters(prm)
        no_emb = {k: pi for (pi, _), k in zip(pares, criancas)}
        no_des = {k: di for (_, di), k in zip(pares, criancas)}
        seq = [man.NodeToIndex(no_emb[k] if pa.tipo == "embarque" else no_des[k])
               for pa in rota_inicial.paradas if pa.tipo in ("embarque", "desembarque") for k, _ in pa.eventos]
        rotas = [seq if v.id == rota_inicial.veiculo else [] for v in cen.veiculos]
        inicial = rot.ReadAssignmentFromRoutes(rotas, True)
        if inicial is not None:
            sol = rot.SolveFromAssignmentWithParameters(inicial, prm)
    if sol is None:
        sol = rot.SolveWithParameters(prm)
    dt = time.perf_counter() - t0
    if sol is None:
        return ResultadoOtimizacao([], "sem solução viável", dt, None)
    fora = sorted({node_kid[man.IndexToNode(man.NodeToIndex(pi))] for pi, _ in pares
                   if sol.Value(rot.NextVar(man.NodeToIndex(pi))) == man.NodeToIndex(pi)})

    # ---------------- extração
    itins = []
    for v in range(V):
        vid = cen.veiculos[v].id
        idx = rot.Start(v)
        paradas: list[Parada] = []
        cur_prev = None
        t_prev_fim = None
        while True:
            n = man.IndexToNode(idx)
            loc = node_local[n]
            t = sol.Value(tempo.CumulVar(idx))
            tipo = node_tipo[n]
            if loc >= 0:
                chegada = t if cur_prev is None else min(t, t_prev_fim + T[cur_prev, loc])
                ult = paradas[-1] if paradas else None
                mesmo = (ult is not None and ult.tipo == tipo and tipo in ("embarque", "desembarque")
                         and inst.locais[ult.local].no == inst.locais[loc].no)
                if mesmo:
                    ult.eventos.append((node_kid[n], t))
                else:
                    ev = [(node_kid[n], t)] if node_kid[n] else []
                    paradas.append(Parada(tipo, loc, chegada, t, ev))
                t_prev_fim = t + serv[n]
                cur_prev = loc
            if rot.IsEnd(idx):
                break
            idx = sol.Value(rot.NextVar(idx))
        if len(paradas) > 1 and not (len(paradas) == 2 and paradas[1].tipo == "fim"):
            itins.append(Itinerario(vid, paradas, "otimizado (OR-Tools)"))
        else:
            itins.append(Itinerario(vid, paradas[:1], "otimizado (OR-Tools)"))
    status = "ok" if not fora else "parcial"
    return ResultadoOtimizacao(itins, status, dt, sol.ObjectiveValue(), fora)


def otimizar_com_nova_tentativa(inst: Instancia, tempo_limite_s: float | None = None) -> ResultadoOtimizacao:
    """Estratégia usada pela interface e pelos experimentos.

    1. Inserção mais barata em paralelo + busca local guiada (tempo padrão).
    2. Se alguma criança ficou de fora: outra construção inicial, com o triplo do tempo.
    3. Se ainda ficou alguém de fora e alguma heurística simples encontrou rota que
       respeita todas as regras (uma van): parte dessa rota e continua a busca.
    Fica com o melhor resultado (menos crianças de fora; depois, menor custo)."""
    from .avaliacao import metricas
    from .heuristicas import HEURISTICAS

    lim = tempo_limite_s if tempo_limite_s is not None else inst.p.tempo_limite_s
    t_total = 0.0
    tentativas = []
    r = otimizar(inst, lim)
    tentativas.append(r); t_total += r.tempo_solver_s
    if r.status != "ok":
        r = otimizar(inst, 3 * lim, estrategia="insercao_local")
        tentativas.append(r); t_total += r.tempo_solver_s
    if r.status != "ok" and len(inst.cen.veiculos) == 1:
        viaveis = [it for it in (f(inst) for f in HEURISTICAS.values()) if metricas(inst, [it])["viavel"]]
        if viaveis:
            melhor_h = min(viaveis, key=lambda it: metricas(inst, [it])["distancia_km"])
            r = otimizar(inst, lim, rota_inicial=melhor_h)
            tentativas.append(r); t_total += r.tempo_solver_s
    validas = [x for x in tentativas if x.itinerarios]
    melhor = min(validas, key=lambda x: (len(x.nao_atendidas), x.objetivo), default=tentativas[0])
    melhor.tempo_solver_s = t_total
    return melhor
