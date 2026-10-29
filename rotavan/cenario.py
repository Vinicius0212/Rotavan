# -*- coding: utf-8 -*-
"""Dados de entrada: escolas, crianças, veículos e parâmetros operacionais.

Horários são guardados em segundos desde a meia-noite. Todas as coordenadas de
crianças são ajustadas ao cruzamento mais próximo da malha viária — o sistema
nunca precisa (nem guarda) o número da casa, o que reduz a exposição de dados
pessoais de menores (LGPD, art. 14)."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

from .rede import IndiceNos, _haversine

CENTRO_IJUI = (-28.3876, -53.9148)   # praça central de Ijuí (aprox.)


# ------------------------------------------------------------------ horários
def hhmm_para_s(txt) -> int:
    if isinstance(txt, (int, np.integer, float, np.floating)) and not pd.isna(txt):
        return int(txt)
    h, m = str(txt).strip().split(":")[:2]
    return int(h) * 3600 + int(m) * 60


def s_para_hhmm(s: float) -> str:
    s = int(round(s))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}"


# ------------------------------------------------------------------ entidades
@dataclass
class Escola:
    id: str
    nome: str
    lat: float
    lon: float
    entrada: int            # s desde 00:00
    saida: int              # s desde 00:00
    no: int | None = None   # nó da malha viária


@dataclass
class Crianca:
    id: str                 # código anônimo (ex.: C01) — nunca o nome
    lat: float
    lon: float
    escola: str
    embarque_mais_cedo: int | None = None   # ida: não buscar antes disto (s)
    no: int | None = None


@dataclass
class Veiculo:
    id: str
    capacidade: int
    lat: float              # garagem (onde o motorista sai)
    lon: float
    no: int | None = None


@dataclass
class Parametros:
    sentido: str = "ida"               # "ida" (casa → escola) ou "volta" (escola → casa)
    tempo_max_bordo_min: float = 45.0  # tempo máximo que uma criança pode ficar na van
    folga_min: float = 5.0             # chegar pelo menos X min antes da entrada
    antecedencia_max_min: float = 30.0 # e no máximo Y min antes da entrada
    tolerancia_saida_min: float = 15.0 # volta: buscar até X min após o fim da aula
    embarque_s: int = 60               # tempo para uma criança embarcar em casa
    desembarque_s: int = 30            # tempo por criança na porta da escola
    saida_minima: str = "06:00"        # motorista não sai antes disto
    embarque_mais_cedo: str = "06:15"  # padrão, se a família não informar
    fator_transito: float = 1.25       # multiplica os tempos da malha (trânsito, semáforos)
    retorna_garagem: bool = False      # ida: termina na última escola?
    minimizar: str = "distancia"       # "distancia" ou "tempo"
    minimizar_vans: bool = True        # com frota > 1, usar o menor nº de vans possível
    tempo_limite_s: int = 10           # tempo de busca do solver
    consumo_km_l: float = 9.0          # van a diesel em uso urbano (ajuste ao veículo real)
    preco_litro: float = 6.50          # R$/L — diesel S10, Porto Alegre, ANP (13–19 set. 2026); atualize
    dias_letivos_mes: int = 20

    def dict(self):
        return asdict(self)


@dataclass
class Cenario:
    escolas: list[Escola]
    criancas: list[Crianca]
    veiculos: list[Veiculo]
    params: Parametros = field(default_factory=Parametros)
    descricao: str = ""

    def escola(self, eid: str) -> Escola:
        for e in self.escolas:
            if e.id == eid:
                return e
        raise KeyError(eid)

    # --- ajuste à malha ----------------------------------------------------
    def ajustar_a_malha(self, G: nx.MultiDiGraph, dist_max_m: float = 600.0) -> list[str]:
        """Associa cada ponto ao cruzamento mais próximo. Devolve avisos para
        pontos muito longe de qualquer rua (provável erro de digitação)."""
        idx = IndiceNos(G)
        avisos = []
        for obj in [*self.escolas, *self.criancas, *self.veiculos]:
            no, d = idx.mais_proximo(obj.lat, obj.lon)
            obj.no = no
            if d > dist_max_m:
                avisos.append(f"{type(obj).__name__} {obj.id}: ponto a {d:.0f} m da rua mais próxima")
            if isinstance(obj, Crianca):   # anonimiza: passa a ser o cruzamento
                obj.lat, obj.lon = G.nodes[no]["y"], G.nodes[no]["x"]
        return avisos

    # --- validação ---------------------------------------------------------
    def validar(self) -> list[str]:
        erros = []
        ids_esc = {e.id for e in self.escolas}
        for c in self.criancas:
            if c.escola not in ids_esc:
                erros.append(f"Criança {c.id}: escola '{c.escola}' não cadastrada")
        if len({c.id for c in self.criancas}) != len(self.criancas):
            erros.append("Há códigos de criança repetidos")
        if not self.veiculos:
            erros.append("Cadastre pelo menos um veículo")
        return erros


# ------------------------------------------------------------------ arquivos CSV
def ler_escolas(caminho) -> list[Escola]:
    df = pd.read_csv(caminho, dtype={"id": str})
    return [Escola(id=str(r.id), nome=str(r.nome), lat=float(r.lat), lon=float(r.lon),
                   entrada=hhmm_para_s(r.entrada), saida=hhmm_para_s(r.saida))
            for r in df.itertuples()]


def ler_criancas(caminho) -> list[Crianca]:
    df = pd.read_csv(caminho, dtype={"id": str, "escola": str})
    out = []
    for r in df.itertuples():
        cedo = getattr(r, "embarque_mais_cedo", None)
        out.append(Crianca(id=str(r.id), lat=float(r.lat), lon=float(r.lon), escola=str(r.escola),
                           embarque_mais_cedo=None if cedo is None or pd.isna(cedo) else hhmm_para_s(cedo)))
    return out


def ler_veiculos(caminho) -> list[Veiculo]:
    df = pd.read_csv(caminho, dtype={"id": str})
    return [Veiculo(id=str(r.id), capacidade=int(r.capacidade), lat=float(r.lat), lon=float(r.lon))
            for r in df.itertuples()]


def salvar_cenario(cen: Cenario, pasta) -> None:
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"id": e.id, "nome": e.nome, "lat": e.lat, "lon": e.lon,
                   "entrada": s_para_hhmm(e.entrada), "saida": s_para_hhmm(e.saida)}
                  for e in cen.escolas]).to_csv(pasta / "escolas.csv", index=False)
    pd.DataFrame([{"id": c.id, "lat": round(c.lat, 6), "lon": round(c.lon, 6), "escola": c.escola}
                  for c in cen.criancas]).to_csv(pasta / "criancas.csv", index=False)
    pd.DataFrame([{"id": v.id, "capacidade": v.capacidade, "lat": v.lat, "lon": v.lon}
                  for v in cen.veiculos]).to_csv(pasta / "veiculos.csv", index=False)


def ler_escolas_osm(caminho, centro=CENTRO_IJUI, raio_km: float = 4.0) -> pd.DataFrame:
    """Escolas baixadas do OSM, só as nomeadas e dentro da área urbana."""
    df = pd.read_csv(caminho)
    df = df[df["name"].notna()].copy()
    d = _haversine(centro[0], centro[1], df["lat"].values, df["lon"].values)
    df = df[d <= raio_km * 1000].copy()
    # remove duplicatas: mesmo nome (ignorando maiúsculas) ou pontos a menos de 150 m
    df["_nome"] = df["name"].str.lower().str.strip()
    df = df.drop_duplicates("_nome")
    manter = []
    for i, r in df.iterrows():
        if all(_haversine(r["lat"], r["lon"], df.at[j, "lat"], df.at[j, "lon"]) > 150 for j in manter):
            manter.append(i)
    return df.loc[manter].drop(columns="_nome").reset_index(drop=True)


# ------------------------------------------------------------------ cenário sintético
_TIPOS_RESIDENCIAIS = {"residential", "living_street", "unclassified", "tertiary", "secondary"}


def _nos_residenciais(G, centro, raio_m):
    cand = set()
    for u, v, d in G.edges(data=True):
        hw = d.get("highway", "")
        hw = set(hw) if isinstance(hw, list) else {hw}
        if hw & _TIPOS_RESIDENCIAIS:
            cand.update((u, v))
    cand = np.array(sorted(cand))
    lat = np.array([G.nodes[n]["y"] for n in cand])
    lon = np.array([G.nodes[n]["x"] for n in cand])
    d = _haversine(centro[0], centro[1], lat, lon)
    return cand[d <= raio_m]


def gerar_cenario(G: nx.MultiDiGraph, n_criancas: int, n_escolas: int = 3, semente: int = 0,
                  escolas_osm: pd.DataFrame | None = None, centro=CENTRO_IJUI, raio_km: float = 3.0,
                  entradas=("07:30", "07:45"), saidas=("11:45", "12:00"), n_vans: int = 1,
                  capacidade: int = 15, params: Parametros | None = None,
                  raio_atuacao_km: float | None = 2.0) -> Cenario:
    """Instância sintética plausível: casas em cruzamentos de ruas residenciais da
    área urbana, escolas reais do OSM (quando disponíveis) e uma garagem aleatória.

    `raio_atuacao_km`: um motorista costuma atender uma região da cidade, não a
    cidade inteira. Sorteia-se o centro dessa região (dentro da área urbana) e as
    casas e a garagem ficam a até esse raio dele. None = cidade inteira.

    Serve para testar e comparar métodos. NÃO representa alunos reais."""
    rng = np.random.default_rng(semente)
    lats = np.array([G.nodes[n]["y"] for n in G.nodes]); lons = np.array([G.nodes[n]["x"] for n in G.nodes])
    if not (lats.min() <= centro[0] <= lats.max() and lons.min() <= centro[1] <= lons.max()):
        centro = (float(lats.mean()), float(lons.mean()))   # malha de teste: usa o centro da grade
    nos = _nos_residenciais(G, centro, raio_km * 1000)
    if len(nos) < n_criancas + n_escolas + n_vans:
        raise ValueError("Poucos cruzamentos residenciais no raio escolhido")

    if escolas_osm is not None and len(escolas_osm) >= n_escolas:
        esc_df = escolas_osm.sample(n=n_escolas, random_state=int(rng.integers(1e9)))
        escolas = [Escola(id=f"E{i+1}", nome=str(r["name"]), lat=float(r["lat"]), lon=float(r["lon"]),
                          entrada=hhmm_para_s(rng.choice(entradas)), saida=hhmm_para_s(rng.choice(saidas)))
                   for i, (_, r) in enumerate(esc_df.iterrows())]
    else:
        esc_nos = rng.choice(nos, size=n_escolas, replace=False)
        escolas = [Escola(id=f"E{i+1}", nome=f"Escola {chr(65+i)}", lat=G.nodes[n]["y"], lon=G.nodes[n]["x"],
                          entrada=hhmm_para_s(rng.choice(entradas)), saida=hhmm_para_s(rng.choice(saidas)))
                   for i, n in enumerate(esc_nos)]

    nos_casa = nos
    if raio_atuacao_km:
        centro_reg = rng.choice(_nos_residenciais(G, centro, raio_km * 500))
        cy, cx = G.nodes[centro_reg]["y"], G.nodes[centro_reg]["x"]
        lat_n = np.array([G.nodes[n]["y"] for n in nos]); lon_n = np.array([G.nodes[n]["x"] for n in nos])
        nos_casa = nos[_haversine(cy, cx, lat_n, lon_n) <= raio_atuacao_km * 1000]
        if len(nos_casa) < n_criancas + n_vans:
            nos_casa = nos
    casas = rng.choice(nos_casa, size=n_criancas, replace=False)
    peso = rng.dirichlet(np.ones(n_escolas) * 2.0)          # escolas com tamanhos diferentes
    atrib = rng.choice(n_escolas, size=n_criancas, p=peso)
    criancas = [Crianca(id=f"C{i+1:02d}", lat=G.nodes[n]["y"], lon=G.nodes[n]["x"], escola=escolas[a].id)
                for i, (n, a) in enumerate(zip(casas, atrib))]

    garagens = rng.choice(np.setdiff1d(nos_casa, casas), size=n_vans, replace=False)
    veiculos = [Veiculo(id=f"V{i+1}", capacidade=capacidade, lat=G.nodes[n]["y"], lon=G.nodes[n]["x"])
                for i, n in enumerate(garagens)]
    cen = Cenario(escolas, criancas, veiculos, params or Parametros(),
                  descricao=f"sintético n={n_criancas} escolas={n_escolas} semente={semente}")
    cen.ajustar_a_malha(G)
    return cen
