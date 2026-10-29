# -*- coding: utf-8 -*-
"""Testes automatizados (pytest). Usam uma malha em grade, sem precisar do mapa real."""
import numpy as np
import pytest

from rotavan.avaliacao import metricas, simular, tokens_de_sequencia, ler_rota_informada
from rotavan.cenario import Parametros, gerar_cenario, hhmm_para_s, s_para_hhmm
from rotavan.exato import melhor_rota_exata
from rotavan.heuristicas import HEURISTICAS
from rotavan.instancia import Instancia
from rotavan.modelo import otimizar
from rotavan.rede import IndiceNos, calcular_matrizes, grafo_teste

import pandas as pd


@pytest.fixture(scope="module")
def G():
    return grafo_teste(n=20, semente=3)


def _inst(G, n=8, escolas=2, semente=0, **kw):
    cen = gerar_cenario(G, n, escolas, semente, params=Parametros(**kw))
    return Instancia.criar(cen, G)


# ------------------------------------------------------------------ rede
def test_horarios_ida_e_volta():
    assert hhmm_para_s("07:30") == 27000
    assert s_para_hhmm(27000) == "07:30"


def test_no_mais_proximo(G):
    idx = IndiceNos(G)
    n0 = next(iter(G.nodes))
    no, d = idx.mais_proximo(G.nodes[n0]["y"], G.nodes[n0]["x"])
    assert no == n0 and d < 1e-6


def test_matriz_diagonal_zero_e_positiva(G):
    nos = list(G.nodes)[:10]
    M = calcular_matrizes(G, nos)
    assert np.allclose(np.diag(M.distancia), 0)
    assert (M.distancia[~np.eye(10, dtype=bool)] > 0).all()
    # desigualdade triangular (caminhos mínimos)
    for i in range(10):
        for j in range(10):
            for k in range(10):
                assert M.distancia[i, j] <= M.distancia[i, k] + M.distancia[k, j] + 1e-6


def test_mao_unica_gera_assimetria(G):
    M = calcular_matrizes(G, list(G.nodes))
    assert not np.allclose(M.distancia, M.distancia.T)


# ------------------------------------------------------------------ otimizador
def _verificar_viabilidade(inst, itins):
    """Checagem independente das regras, direto nos horários do itinerário."""
    emb, des, van_de = {}, {}, {}
    for it in itins:
        carga = 0
        cap = next(v.capacidade for v in inst.cen.veiculos if v.id == it.veiculo)
        for pa in it.paradas:
            for k, t in pa.eventos:
                if pa.tipo == "embarque":
                    emb[k] = t; van_de.setdefault(k, set()).add(it.veiculo); carga += 1
                    a, b = inst.janela_embarque(k)
                else:
                    des[k] = t; van_de.setdefault(k, set()).add(it.veiculo); carga -= 1
                    a, b = inst.janela_desembarque(k)
                assert a - 1 <= t <= b + 1, f"{k} fora da janela"
                assert 0 <= carga <= cap
    for c in inst.cen.criancas:
        assert c.id in emb and c.id in des, f"{c.id} não atendida"
        assert emb[c.id] <= des[c.id]
        assert len(van_de[c.id]) == 1
        assert des[c.id] - emb[c.id] <= inst.p.tempo_max_bordo_min * 60 + 1


@pytest.mark.parametrize("sentido", ["ida", "volta"])
def test_otimizador_respeita_regras(G, sentido):
    inst = _inst(G, n=8, sentido=sentido, tempo_limite_s=2)
    r = otimizar(inst)
    assert r.status == "ok"
    _verificar_viabilidade(inst, r.itinerarios)
    assert metricas(inst, r.itinerarios)["viavel"]


def test_frota_com_capacidade(G):
    cen = gerar_cenario(G, 14, 3, 5, n_vans=3, capacidade=6, params=Parametros(tempo_limite_s=3))
    inst = Instancia.criar(cen, G)
    r = otimizar(inst)
    assert r.status == "ok"
    _verificar_viabilidade(inst, r.itinerarios)
    m = metricas(inst, r.itinerarios)
    # uma van pode reaproveitar lugares depois de deixar crianças numa escola,
    # então o nº de vans pode ser menor que 14/6; o que não pode é passar de 6 a bordo
    assert m["carga_max"] <= 6 and m["violacoes_capacidade"] == 0 and m["vans_usadas"] >= 2


@pytest.mark.parametrize("semente", [0, 1, 2])
def test_otimizador_encontra_otimo_em_instancia_pequena(G, semente):
    inst = _inst(G, n=4, escolas=2, semente=semente, tempo_limite_s=2)
    melhor, viaveis, total = melhor_rota_exata(inst)
    assert total == 2520      # 8! / 2^4 ordens que respeitam embarque antes de desembarque
    r = otimizar(inst, peso_duracao=0)
    km_opt = metricas(inst, r.itinerarios)["distancia_km"]
    km_ex = metricas(inst, [melhor])["distancia_km"]
    assert km_opt <= km_ex + 1e-6


def test_otimizador_nao_perde_para_heuristicas(G):
    inst = _inst(G, n=10, escolas=3, semente=7, tempo_limite_s=3)
    km_opt = metricas(inst, otimizar(inst).itinerarios)["distancia_km"]
    for f in HEURISTICAS.values():
        m = metricas(inst, [f(inst)])
        if m["viavel"]:
            assert km_opt <= m["distancia_km"] + 1e-6


def test_crianca_que_nao_cabe_e_apontada(G):
    # tempo máximo a bordo impossível (1 min) -> ninguém que more longe cabe
    inst = _inst(G, n=5, escolas=1, semente=2, tempo_max_bordo_min=1, tempo_limite_s=1)
    r = otimizar(inst)
    assert r.status == "parcial" and r.nao_atendidas


# ------------------------------------------------------------------ heurísticas e avaliação
@pytest.mark.parametrize("sentido", ["ida", "volta"])
def test_heuristicas_atendem_todos(G, sentido):
    inst = _inst(G, n=9, escolas=3, semente=4, sentido=sentido)
    for f in HEURISTICAS.values():
        m = metricas(inst, [f(inst)])
        assert m["criancas_atendidas"] == 9


def test_rota_informada_ida(G):
    inst = _inst(G, n=4, escolas=2, semente=1)
    kids = [c.id for c in inst.cen.criancas]
    escolas = sorted({c.escola for c in inst.cen.criancas})
    seq = kids + escolas
    toks = tokens_de_sequencia(inst, seq)
    assert [t[0] for t in toks][:4] == ["embarque"] * 4
    it = ler_rota_informada(inst, pd.DataFrame({"parada": seq}))
    assert metricas(inst, it)["criancas_atendidas"] == 4


def test_distancia_e_soma_dos_trechos(G):
    inst = _inst(G, n=5, escolas=2, semente=9)
    it = HEURISTICAS["vizinho mais próximo"](inst)
    soma = sum(inst.dist(a.local, b.local) for a, b in zip(it.paradas[:-1], it.paradas[1:]))
    assert abs(metricas(inst, [it])["distancia_km"] - soma / 1000) < 1e-9


def test_simulacao_ida_sai_o_mais_tarde_possivel(G):
    inst = _inst(G, n=6, escolas=1, semente=3)
    it = HEURISTICAS["vizinho mais próximo"](inst)
    ult = it.paradas[-1]
    prazo = inst.janela_desembarque(ult.eventos[-1][0])[1]
    assert abs(ult.eventos[-1][1] - prazo) < 2   # a última criança chega no limite, sem atraso


def test_modelos_de_planilha_carregam_e_otimizam(G):
    from pathlib import Path
    from rotavan.cenario import Cenario, ler_criancas, ler_escolas, ler_veiculos
    pasta = Path(__file__).resolve().parents[1] / "dados" / "modelos"
    cen = Cenario(ler_escolas(pasta / "escolas.csv"), ler_criancas(pasta / "criancas.csv"),
                  ler_veiculos(pasta / "veiculos.csv"), Parametros(tempo_limite_s=1))
    assert not cen.validar()
    inst = Instancia.criar(cen, G)
    atual = ler_rota_informada(inst, pasta / "rota_atual.csv")
    assert metricas(inst, atual)["criancas_atendidas"] == 3
    r = otimizar(inst)
    assert metricas(inst, r.itinerarios)["distancia_km"] <= metricas(inst, atual)["distancia_km"] + 1e-6 \
        or not metricas(inst, atual)["viavel"]


def test_partida_a_quente_a_partir_de_heuristica(G):
    """Partindo da rota de uma heurística, o otimizador nunca termina pior que ela."""
    inst = _inst(G, n=7, escolas=2, semente=11, tempo_limite_s=1)
    h = HEURISTICAS["escola por escola"](inst)
    r = otimizar(inst, 1, rota_inicial=h)
    assert r.status == "ok"
    _verificar_viabilidade(inst, r.itinerarios)
    mh = metricas(inst, [h])
    if mh["viavel"]:
        assert metricas(inst, r.itinerarios)["distancia_km"] <= mh["distancia_km"] + 1e-6
