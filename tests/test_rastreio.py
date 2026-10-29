# -*- coding: utf-8 -*-
"""Testes do acompanhamento ao vivo (plano, previsão de chegada, regras e API)."""
import json
from datetime import datetime

import numpy as np
import pytest
from fastapi.testclient import TestClient

from rotavan.cenario import gerar_cenario
from rotavan.instancia import Instancia
from rotavan.modelo import otimizar_com_nova_tentativa
from rotavan.rastreio.armazem import Armazem
from rotavan.rastreio.plano import Estimador, PlanoViagem, criar_plano, distancia_m
from rotavan.rastreio.servico import FUSO, Central, ErroRastreio, cenario_de_dict
from rotavan.rastreio.simulacao import ConfigSimulacao, simular_viagem
from rotavan.rede import grafo_teste

SEM_RUIDO = ConfigSimulacao(sigma_dia=0, sigma_trecho=0, sigma_parada=0, p_incidente=0,
                            desvio_saida_min=0, gps_erro_m=0)


@pytest.fixture(scope="module")
def G():
    return grafo_teste(n=12)


@pytest.fixture(scope="module")
def plano(G):
    cen = gerar_cenario(G, 7, 2, semente=4)
    inst = Instancia.criar(cen, G)
    it = otimizar_com_nova_tentativa(inst, 3).itinerarios[0]
    return inst, it, criar_plano(inst, it)


class Relogio:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


# ------------------------------------------------------------------ plano e previsão
def test_plano_usa_os_mesmos_tempos_do_otimizador(plano):
    inst, it, pl = plano
    acum = 0.0
    for a, b in zip(it.paradas[:-1], it.paradas[1:]):
        acum += inst.tempo(a.local, b.local)
    assert pl.paradas[-1].cum_s == pytest.approx(acum, abs=1.0)
    # o trajeto começa na garagem e termina na última parada
    assert distancia_m(*pl.trajeto[0][:2], pl.paradas[0].lat, pl.paradas[0].lon) < 1
    assert distancia_m(*pl.trajeto[-1][:2], pl.paradas[-1].lat, pl.paradas[-1].lon) < 1
    # serializa e volta igual
    assert PlanoViagem.from_dict(pl.to_dict()).to_dict() == pl.to_dict()


def test_projecao_na_rota_e_fora_dela(plano):
    _, _, pl = plano
    est = Estimador(pl)
    meio = pl.paradas[-1].cum_s / 2
    lat, lon = pl.ponto_em(meio)
    r = est.atualizar(pl.saida + meio, lat, lon)
    assert r["na_rota"] and r["cum_s"] == pytest.approx(meio, abs=3)
    r = est.atualizar(pl.saida + meio + 5, lat + 0.004, lon)       # ~440 m ao norte
    assert not r["na_rota"] and est.fora_da_rota


def test_sem_transito_a_previsao_bate_com_o_real(plano):
    _, _, pl = plano
    sim = simular_viagem(pl, np.random.default_rng(1), SEM_RUIDO)
    est = Estimador(pl)
    t, lat, lon = sim.pontos[3]
    est.atualizar(t, lat, lon)
    prev = {p.ordem: p.chegada for p in est.prever(t)}
    for ordem, real in sim.chegadas.items():
        if ordem in prev:
            assert prev[ordem] == pytest.approx(real, abs=8)


def test_ritmo_aprende_dia_de_transito_lento(plano):
    _, _, pl = plano
    sim = simular_viagem(pl, np.random.default_rng(2), SEM_RUIDO, fator_dia=1.4)
    ada, fixo = Estimador(pl), Estimador(pl, modo="plano")
    alvo = pl.atendimentos[-1]
    corte = sim.chegadas[alvo.ordem] - 0.45 * (sim.chegadas[alvo.ordem] - sim.pontos[0][0])
    for t, lat, lon in sim.pontos:
        if t > corte:
            break
        ada.atualizar(t, lat, lon); fixo.atualizar(t, lat, lon)
    assert 1.15 < ada.ritmo < 1.45          # aprendeu (puxado para 1 pelo prior)
    pend = {alvo.ordem: alvo.criancas}
    e_ada = abs(ada.prever(t, pend)[0].chegada - sim.chegadas[alvo.ordem])
    e_fixo = abs(fixo.prever(t, pend)[0].chegada - sim.chegadas[alvo.ordem])
    assert e_ada < e_fixo


# ------------------------------------------------------------------ regras e API
@pytest.fixture()
def sistema(G):
    cen = gerar_cenario(G, 5, 2, semente=7)
    cen.params.tempo_limite_s = 2
    inst = Instancia.criar(cen, G)
    it = otimizar_com_nova_tentativa(inst, 3).itinerarios[0]
    saida = criar_plano(inst, it).saida
    d = datetime(2026, 10, 21, tzinfo=FUSO).timestamp() + saida + 60
    rel = Relogio(d)
    central = Central(Armazem(":memory:"), G, relogio=rel)
    pub = central.publicar_linha("Linha teste", cen)
    tok = {(a["papel"], a["ref"]): a["token"] for a in pub["acessos"]}
    from servidor import criar_app
    cli = TestClient(criar_app(central, simular_em_segundo_plano=False))
    return central, cli, tok, rel, cen


def _tok(tok, papel):
    return next(v for (p, _), v in tok.items() if p == papel)


def test_fluxo_completo_da_ida(sistema):
    central, cli, tok, rel, cen = sistema
    m = _tok(tok, "motorista")
    k = cen.criancas[0].id
    r = tok[("responsavel", k)]
    # antes do consentimento a família não vê localização nenhuma
    e = cli.get(f"/api/r/{r}/estado").json()
    assert e["consentiu"] is False and "viagem" not in e and "ponto" not in e
    cli.post(f"/api/r/{r}/consentimento", json={"aceito": True})
    assert cli.post(f"/api/m/{m}/iniciar", json={"sentido": "ida"}).status_code == 200
    v = cli.get(f"/api/m/{m}/estado").json()["viagem"]
    assert v and not v["fora_do_horario"]
    # a van anda pelo trajeto
    lat, lon = v["trajeto"][2]
    assert cli.post(f"/api/m/{m}/posicoes", json={"pontos": [{"lat": lat, "lon": lon, "ts": rel.t}]}).status_code == 200
    e = cli.get(f"/api/r/{r}/estado").json()
    assert e["viagem"]["status"] == "aguardando" and e["viagem"]["van"] is not None
    assert e["viagem"]["faltam_min"] is not None
    # embarca todo mundo
    for c in cen.criancas:
        assert cli.post(f"/api/m/{m}/marcar", json={"crianca": c.id, "tipo": "embarque"}).status_code == 200
    # não pode encerrar com criança a bordo
    resp = cli.post(f"/api/m/{m}/encerrar", json={"conferi_a_van": True})
    assert resp.status_code == 409 and set(resp.json()["a_bordo"]) == {c.id for c in cen.criancas}
    for c in cen.criancas:
        cli.post(f"/api/m/{m}/marcar", json={"crianca": c.id, "tipo": "desembarque"})
    # depois da entrega a família deixa de ver a van
    e = cli.get(f"/api/r/{r}/estado").json()
    assert e["viagem"]["status"] == "entregue" and "van" not in e["viagem"]
    textos = [x["texto"] for x in e["eventos"]]
    assert any("Embarcou" in t for t in textos) and any("Chegou à escola" in t for t in textos)
    # precisa confirmar a conferência da van
    assert cli.post(f"/api/m/{m}/encerrar", json={"conferi_a_van": False}).status_code == 409
    assert cli.post(f"/api/m/{m}/encerrar", json={"conferi_a_van": True}).status_code == 200
    assert cli.get(f"/api/m/{m}/estado").json()["viagem"] is None


def test_marcacao_invalida_e_desfazer(sistema):
    central, cli, tok, rel, cen = sistema
    m = _tok(tok, "motorista")
    k = cen.criancas[1].id
    cli.post(f"/api/m/{m}/iniciar", json={"sentido": "ida"})
    assert cli.post(f"/api/m/{m}/marcar", json={"crianca": k, "tipo": "desembarque"}).status_code == 409
    assert cli.post(f"/api/m/{m}/marcar", json={"crianca": k, "tipo": "embarque"}).status_code == 200
    assert cli.post(f"/api/m/{m}/desfazer", json={"crianca": k}).status_code == 200
    assert cli.post(f"/api/m/{m}/marcar", json={"crianca": k, "tipo": "ausente"}).status_code == 200
    assert cli.post(f"/api/m/{m}/iniciar", json={"sentido": "ida"}).status_code == 409   # já em andamento


def test_aviso_de_falta_tira_a_crianca_da_rota(sistema):
    central, cli, tok, rel, cen = sistema
    m = _tok(tok, "motorista")
    k = cen.criancas[2].id
    r = tok[("responsavel", k)]
    assert cli.post(f"/api/r/{r}/falta", json={"sentidos": ["ida"]}).status_code == 403   # sem consentimento
    cli.post(f"/api/r/{r}/consentimento", json={"aceito": True})
    assert cli.post(f"/api/r/{r}/falta", json={"sentidos": ["ida"]}).json()["ausencias"] == ["ida"]
    cli.post(f"/api/m/{m}/iniciar", json={"sentido": "ida"})
    v = cli.get(f"/api/m/{m}/estado").json()["viagem"]
    na_rota = {c["id"] for p in v["paradas"] for c in p["criancas"]}
    assert k not in na_rota and len(na_rota) == len(cen.criancas) - 1


def test_demonstracao_simulada_ate_o_fim(sistema):
    central, cli, tok, rel, cen = sistema
    m = _tok(tok, "motorista")
    r = tok[("responsavel", cen.criancas[0].id)]
    cli.post(f"/api/r/{r}/consentimento", json={"aceito": True})
    assert cli.post(f"/api/m/{m}/iniciar", json={"sentido": "ida", "simulacao": True, "mult": 30}).status_code == 200
    for _ in range(600):
        rel.t += 2
        central.passo_simulacao()
        if cli.get(f"/api/m/{m}/estado").json()["viagem"] is None:
            break
    assert cli.get(f"/api/m/{m}/estado").json()["viagem"] is None
    tipos = [e["tipo"] for e in cli.get(f"/api/r/{r}/estado").json()["eventos"]]
    assert {"inicio", "embarque", "desembarque", "fim"} <= set(tipos)
    assert "aviso_proximidade" in tipos


def test_links_revogados_e_retencao(sistema):
    central, cli, tok, rel, cen = sistema
    m = _tok(tok, "motorista")
    k = cen.criancas[0].id
    linha = central.linhas()[0]["id"]
    antigo = tok[("responsavel", k)]
    novo = central.novo_link(linha, "responsavel", k)
    assert cli.get(f"/api/r/{antigo}/estado").status_code == 404
    assert cli.get(f"/api/r/{novo}/estado").status_code == 200
    assert cli.get("/api/r/naoexiste/estado").status_code == 404
    # a trilha de GPS some 24 h depois do fim da viagem
    cli.post(f"/api/m/{m}/iniciar", json={"sentido": "ida"})
    v = cli.get(f"/api/m/{m}/estado").json()["viagem"]
    cli.post(f"/api/m/{m}/posicoes", json={"pontos": [{"lat": p[0], "lon": p[1]} for p in v["trajeto"][:5]]})
    cli.post(f"/api/m/{m}/marcar", json={"crianca": cen.criancas[0].id, "tipo": "ausente"})
    for c in cen.criancas[1:]:
        cli.post(f"/api/m/{m}/marcar", json={"crianca": c.id, "tipo": "ausente"})
    assert cli.post(f"/api/m/{m}/encerrar", json={"conferi_a_van": True}).status_code == 200
    assert central.db.um("SELECT COUNT(*) n FROM posicoes")["n"] > 0
    rel.t += 25 * 3600
    central.db.limpar(rel.t)
    assert central.db.um("SELECT COUNT(*) n FROM posicoes")["n"] == 0


def test_cabecalho_nao_vaza_o_codigo(sistema):
    _, cli, tok, _, _ = sistema
    r = cli.get("/r/qualquer")
    assert r.headers["referrer-policy"] == "no-referrer"
    assert cli.get(f"/manifest/r/abc").json()["start_url"] == "/r/abc"


# ------------------------------------------------------------------ celular da criança (Traccar Client)
def _ponto_entre(a, b, f):
    return a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])


def test_celular_da_crianca_cercas_e_avisos(sistema):
    central, cli, tok, rel, cen = sistema
    def dist(c):
        e = next(e for e in cen.escolas if e.id == c.escola)
        return distancia_m(c.lat, c.lon, e.lat, e.lon)
    k = max(cen.criancas, key=dist)            # a que mora mais longe da escola
    assert dist(k) > 700
    esc = next(e for e in cen.escolas if e.id == k.escola)
    dev = tok[("celular", k.id)]
    r = tok[("responsavel", k.id)]
    assert len(dev) == 10 and dev.isalnum()
    cli.post(f"/api/r/{r}/consentimento", json={"aceito": True})
    casa, escola = (k.lat, k.lon), (esc.lat, esc.lon)
    # formato 1: parâmetros na URL (como o Traccar Client antigo / OsmAnd)
    assert cli.get(f"/traccar?id={dev}&lat={casa[0]}&lon={casa[1]}&timestamp={int(rel.t)}&accuracy=10&batt=80").status_code == 200
    # formato 2: JSON (Traccar Client atual)
    for i, f in enumerate((0.45, 0.55), start=1):
        lat, lon = _ponto_entre(casa, escola, f)
        rel.t += 120
        corpo = {"device_id": dev, "location": {"timestamp": f"{__import__('datetime').datetime.fromtimestamp(rel.t, FUSO).isoformat()}",
                                                "coords": {"latitude": lat, "longitude": lon, "accuracy": 8, "speed": 6},
                                                "battery": {"level": 0.12, "is_charging": False}}}
        assert cli.post("/traccar", json=corpo).status_code == 200
    e = cli.get(f"/api/r/{r}/estado").json()["celular"]
    assert e["zona"] == "fora" and e["destino"] == "escola" and e["faltam_min"] >= 1 and len(e["trilha"]) >= 3
    rel.t += 120
    cli.post("/", json={"device_id": dev, "location": {"timestamp": int(rel.t * 1000),
                                                         "coords": {"latitude": escola[0], "longitude": escola[1], "accuracy": 8}}})
    est = cli.get(f"/api/r/{r}/estado").json()
    assert est["celular"]["zona"] == "escola"
    textos = [x["texto"] for x in est["eventos"]]
    assert any("Saiu de casa" in t for t in textos) and any("Chegou à escola" in t for t in textos)
    assert any("Bateria" in t for t in textos)
    # celular desconhecido
    assert cli.get(f"/traccar?id=naoexiste&lat=1&lon=1").status_code == 404
    # a trilha do celular também some em 24 h
    rel.t += 25 * 3600
    central.db.limpar(rel.t)
    assert central.db.um("SELECT COUNT(*) n FROM posicoes_cel")["n"] == 0


def test_celular_fora_do_horario_e_sem_sinal(sistema):
    central, cli, tok, rel, cen = sistema
    k = cen.criancas[1]
    dev = tok[("celular", k.id)]
    lid = central.linhas()[0]["id"]
    from datetime import datetime
    domingo = datetime(2026, 10, 18, 7, 0, tzinfo=FUSO).timestamp()
    assert central.receber_celular(dev, [{"ts": domingo, "lat": k.lat, "lon": k.lon}])["fora_do_horario"] == 1
    # no meio do caminho, o celular para de mandar posição
    central.receber_celular(dev, [{"ts": rel.t, "lat": k.lat, "lon": k.lon, "precisao": 10}])
    central.receber_celular(dev, [{"ts": rel.t + 60, "lat": k.lat + 0.01, "lon": k.lon, "precisao": 10}])
    rel.t += 60 + 11 * 60
    central.verificar_celulares()
    central.verificar_celulares()
    n = central.db.um("SELECT COUNT(*) n FROM eventos WHERE tipo='cel_sem_sinal' AND crianca=?", (k.id,))["n"]
    assert n == 1
    # opção "sempre": aceita no domingo
    central.publicar_linha("Linha teste", cenario_de_dict(json.loads(central._linha(lid)["cenario"])), linha_id=lid,
                           opcoes={"janela_celular": "sempre"})
    assert central.receber_celular(dev, [{"ts": domingo, "lat": k.lat, "lon": k.lon}])["recebidas"] == 1


def test_celular_vira_posicao_da_van_quando_o_motorista_some(sistema):
    central, cli, tok, rel, cen = sistema
    m = _tok(tok, "motorista")
    cli.post(f"/api/m/{m}/iniciar", json={"sentido": "ida"})
    v = cli.get(f"/api/m/{m}/estado").json()["viagem"]
    k = v["paradas"][1]["criancas"][0]["id"]
    cli.post(f"/api/m/{m}/marcar", json={"crianca": k, "tipo": "embarque"})
    lat, lon = v["trajeto"][len(v["trajeto"]) // 2]
    central.receber_celular(tok[("celular", k)], [{"ts": rel.t, "lat": lat, "lon": lon, "precisao": 8}])
    assert cli.get(f"/api/m/{m}/estado").json()["viagem"]["pos"] is not None


def test_cadastro_rapido_e_configuracao_portatil(sistema, G, monkeypatch):
    central, cli, tok, rel, cen = sistema
    esc = cen.escolas[0]
    corpo = {"nome": "Van da família", "janela_celular": "sempre",
             "criancas": [{"codigo": "c01", "lat": cen.criancas[0].lat + 0.0003, "lon": cen.criancas[0].lon,
                           "escola": {"nome": esc.nome, "lat": esc.lat, "lon": esc.lon, "entrada": "07:30", "saida": "11:45"}}]}
    p = cli.post("/api/admin/familia", json=corpo).json()
    papeis = sorted(a["papel"] for a in p["acessos"])
    assert papeis == ["celular", "motorista", "responsavel"]
    assert all(a["qr"].startswith("data:image/png") for a in p["acessos"])
    cfg = json.loads(p["config"])
    # a casa foi trocada pelo cruzamento mais próximo (não guarda o ponto exato)
    assert cfg["cenario"]["criancas"][0]["lat"] != corpo["criancas"][0]["lat"]
    # outro servidor (ou o mesmo, reiniciado com disco vazio) recebe a configuração e mantém os links
    from rotavan.rastreio.configuracao import aplicar_do_ambiente
    novo = Central(Armazem(":memory:"), G, relogio=rel)
    assert aplicar_do_ambiente(novo, p["config"]) == 1
    assert {a["token"] for a in novo.links(cfg["linha_id"])} == set(cfg["tokens"].values())
    # pela internet, sem senha configurada, o cadastro é recusado; com senha, exige o cabeçalho
    from servidor import criar_app
    monkeypatch.setenv("ROTAVAN_ADMIN_SENHA", "segredo")
    cli2 = TestClient(criar_app(central, simular_em_segundo_plano=False))
    assert cli2.get("/api/admin/linhas").status_code == 401
    assert cli2.get("/api/admin/linhas", headers={"X-Admin-Senha": "segredo"}).status_code == 200
