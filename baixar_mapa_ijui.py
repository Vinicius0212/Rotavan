# -*- coding: utf-8 -*-
"""
Baixa, uma única vez, a malha viária de Ijuí/RS (OpenStreetMap) e as escolas
mapeadas no município, salvando tudo na subpasta "dados" ao lado deste script.

Como rodar (no Prompt de Comando / Anaconda Prompt, dentro da pasta TCC\\rotavan):
    pip install osmnx
    python baixar_mapa_ijui.py

Leva de 30 segundos a 2 minutos, dependendo da internet.
"""
from pathlib import Path
import sys

try:
    import osmnx as ox
except ImportError:
    sys.exit("Falta o pacote osmnx. Rode primeiro:  pip install osmnx")

LUGAR = "Ijuí, Rio Grande do Sul, Brasil"
PASTA = Path(__file__).resolve().parent / "dados"
PASTA.mkdir(exist_ok=True)

print("1/4  Buscando o limite do município ...")
limite = ox.geocode_to_gdf(LUGAR)
limite[["geometry"]].to_file(PASTA / "limite_ijui.geojson", driver="GeoJSON")

print("2/4  Baixando a malha viária (ruas por onde passa carro) ...")
G = ox.graph_from_place(LUGAR, network_type="drive", simplify=True)
G = ox.add_edge_speeds(G)          # velocidade por tipo de via (km/h)
G = ox.add_edge_travel_times(G)    # tempo de percurso por trecho (s)
ox.save_graphml(G, PASTA / "ijui_drive.graphml")
print(f"     {G.number_of_nodes()} cruzamentos, {G.number_of_edges()} trechos de rua")

print("3/4  Baixando as escolas mapeadas ...")
tags = {"amenity": ["school", "kindergarten"]}
escolas = ox.features_from_place(LUGAR, tags=tags)
escolas = escolas.to_crs(epsg=31982)          # SIRGAS 2000 / UTM 22S, em metros
escolas["geometry"] = escolas.geometry.centroid
escolas = escolas.to_crs(epsg=4326)
cols = [c for c in ["name", "amenity", "operator", "operator:type", "isced:level"] if c in escolas.columns]
saida = escolas[cols].copy()
saida["lat"] = escolas.geometry.y
saida["lon"] = escolas.geometry.x
saida = saida.reset_index()
saida.to_csv(PASTA / "escolas_ijui_osm.csv", index=False, encoding="utf-8")
print(f"     {len(saida)} escolas/creches encontradas")

print("4/4  Pronto! Arquivos salvos em:", PASTA)
for f in sorted(PASTA.iterdir()):
    print("     -", f.name, f"({f.stat().st_size/1024:.0f} KB)")
