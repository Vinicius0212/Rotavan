# -*- coding: utf-8 -*-
"""Malha viária: carregamento do grafo OSM, ajuste ao ponto mais próximo e
cálculo das matrizes de distância (m) e tempo (s) entre pontos de parada."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import networkx as nx
import numpy as np

RAIO_TERRA_M = 6_371_008.8


def carregar_grafo(caminho: str | Path) -> nx.MultiDiGraph:
    """Lê o GraphML salvo por baixar_mapa_ijui.py e mantém só a maior componente
    fortemente conexa (garante que existe caminho de ida e volta entre quaisquer
    dois pontos, respeitando ruas de mão única)."""
    caminho = Path(caminho)
    if caminho.name.endswith(".json.gz"):   # versão leve para a nuvem (gerada por preparar_nuvem.py)
        return carregar_grafo_leve(caminho)
    import osmnx as ox

    G = ox.load_graphml(caminho)
    return maior_componente(G)


def salvar_grafo_leve(G: nx.MultiDiGraph, caminho) -> None:
    """Grava só o que o RotaVan usa (coordenadas, comprimento, tempo, tipo de via e
    geometria), em JSON compactado: carrega sem o OSMnx e sem depender de versões."""
    import gzip
    import json
    nos = {str(n): [round(d["y"], 7), round(d["x"], 7)] for n, d in G.nodes(data=True)}
    arestas = []
    for u, v, d in G.edges(data=True):
        geom = d.get("geometry")
        hw = d.get("highway", "")
        arestas.append([str(u), str(v), round(float(d.get("length", 0)), 2), round(float(d.get("travel_time", 0)), 2),
                        hw if isinstance(hw, str) else (hw[0] if hw else ""),
                        [[round(x, 7), round(y, 7)] for x, y in geom.coords] if geom is not None else None])
    with gzip.open(caminho, "wt", encoding="utf-8") as f:
        json.dump({"crs": G.graph.get("crs", "epsg:4326"), "nos": nos, "arestas": arestas}, f, separators=(",", ":"))


def carregar_grafo_leve(caminho) -> nx.MultiDiGraph:
    import gzip
    import json
    from shapely.geometry import LineString
    with gzip.open(caminho, "rt", encoding="utf-8") as f:
        d = json.load(f)
    G = nx.MultiDiGraph(crs=d["crs"])
    for n, (y, x) in d["nos"].items():
        G.add_node(int(n), y=y, x=x)
    for u, v, comp, tempo, hw, geom in d["arestas"]:
        attrs = {"length": comp, "travel_time": tempo, "highway": hw}
        if geom:
            attrs["geometry"] = LineString(geom)
        G.add_edge(int(u), int(v), **attrs)
    return G


def maior_componente(G: nx.MultiDiGraph) -> nx.MultiDiGraph:
    comp = max(nx.strongly_connected_components(G), key=len)
    H = G.subgraph(comp).copy()
    H.graph.update(G.graph)
    return H


def _haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 2 * RAIO_TERRA_M * np.arcsin(np.sqrt(a))


class IndiceNos:
    """Busca do cruzamento mais próximo de uma coordenada (sem dependências extras)."""

    def __init__(self, G: nx.MultiDiGraph):
        self.ids = np.array(list(G.nodes))
        self.lat = np.array([G.nodes[n]["y"] for n in self.ids], dtype=float)
        self.lon = np.array([G.nodes[n]["x"] for n in self.ids], dtype=float)

    def mais_proximo(self, lat: float, lon: float) -> tuple[int, float]:
        d = _haversine(lat, lon, self.lat, self.lon)
        i = int(np.argmin(d))
        return self.ids[i].item(), float(d[i])


@dataclass
class Matrizes:
    nos: list            # nó do grafo associado a cada local (mesma ordem das matrizes)
    distancia: np.ndarray  # metros
    tempo: np.ndarray      # segundos (já multiplicado pelo fator de trânsito)


def calcular_matrizes(G: nx.MultiDiGraph, nos: list, fator_transito: float = 1.0) -> Matrizes:
    """Matrizes assimétricas (mão única importa) de distância e tempo entre os nós.

    Um Dijkstra por nó de origem distinto. Para algumas centenas de paradas numa
    cidade do porte de Ijuí, leva poucos segundos."""
    unicos = list(dict.fromkeys(nos))
    pos = {n: i for i, n in enumerate(unicos)}
    k = len(unicos)
    D = np.zeros((k, k))
    T = np.zeros((k, k))
    alvos = set(unicos)
    for i, s in enumerate(unicos):
        dist = nx.single_source_dijkstra_path_length(G, s, weight="length")
        temp = nx.single_source_dijkstra_path_length(G, s, weight="travel_time")
        for t in alvos:
            j = pos[t]
            if t not in dist:
                raise ValueError(f"Nó {t} inalcançável a partir de {s}; use maior_componente().")
            D[i, j] = dist[t]
            T[i, j] = temp[t] * fator_transito
    idx = [pos[n] for n in nos]
    return Matrizes(nos=list(nos), distancia=D[np.ix_(idx, idx)], tempo=T[np.ix_(idx, idx)])


def caminho(G: nx.MultiDiGraph, origem, destino, peso: str = "length") -> list:
    """Sequência de nós do grafo entre dois nós (para desenhar a rota no mapa)."""
    return nx.shortest_path(G, origem, destino, weight=peso)


def coords_caminho(G: nx.MultiDiGraph, nos_caminho: list) -> list[tuple[float, float]]:
    """Coordenadas (lat, lon) seguindo a geometria real das ruas, quando existir."""
    pts: list[tuple[float, float]] = []
    for u, v in zip(nos_caminho[:-1], nos_caminho[1:]):
        dados = min(G.get_edge_data(u, v).values(), key=lambda e: e.get("length", 0))
        geom = dados.get("geometry")
        if geom is not None:
            seg = [(y, x) for x, y in geom.coords]
        else:
            seg = [(G.nodes[u]["y"], G.nodes[u]["x"]), (G.nodes[v]["y"], G.nodes[v]["x"])]
        if pts and seg and pts[-1] == seg[0]:
            seg = seg[1:]
        pts.extend(seg)
    if not pts and nos_caminho:
        n = nos_caminho[0]
        pts = [(G.nodes[n]["y"], G.nodes[n]["x"])]
    return pts


def grafo_teste(n: int = 12, espac_m: float = 150.0, lat0: float = -28.388, lon0: float = -53.920,
                velocidade_kmh: float = 30.0, mao_unica_frac: float = 0.15, semente: int = 0) -> nx.MultiDiGraph:
    """Malha em grade para testes automatizados (não substitui o mapa real).

    Algumas ruas viram mão única para exercitar a assimetria das matrizes."""
    rng = np.random.default_rng(semente)
    G = nx.MultiDiGraph(crs="epsg:4326")
    dlat = espac_m / 111_320.0
    dlon = espac_m / (111_320.0 * np.cos(np.radians(lat0)))
    for i in range(n):
        for j in range(n):
            G.add_node(i * n + j, y=lat0 + i * dlat, x=lon0 + j * dlon, street_count=4)
    v = velocidade_kmh / 3.6
    for i in range(n):
        for j in range(n):
            a = i * n + j
            for b in ([a + 1] if j + 1 < n else []) + ([a + n] if i + 1 < n else []):
                comp = espac_m
                attrs = dict(length=comp, travel_time=comp / v, highway="residential")
                if rng.random() < mao_unica_frac and 0 < i < n - 1 and 0 < j < n - 1:
                    G.add_edge(a, b, **attrs) if rng.random() < 0.5 else G.add_edge(b, a, **attrs)
                else:
                    G.add_edge(a, b, **attrs)
                    G.add_edge(b, a, **attrs)
    return maior_componente(G)
