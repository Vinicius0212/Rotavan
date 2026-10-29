# -*- coding: utf-8 -*-
"""Mapa interativo (Folium/Leaflet) com as rotas seguindo as ruas reais."""
from __future__ import annotations

import os

import folium

from .avaliacao import Itinerario
from .cenario import s_para_hhmm
from .instancia import Instancia
from .rede import caminho, coords_caminho

CORES = ["#2a78d6", "#eb6834", "#1a9e77", "#8e44ad", "#c0392b", "#16a085"]


def desenhar(inst: Instancia, itins: list[Itinerario], titulo: str = "", tiles: str | None = "OpenStreetMap",
             cor_base: str | None = None, mapa: folium.Map | None = None, tracejado: bool = False) -> folium.Map:
    lats = [l.lat for l in inst.locais]
    lons = [l.lon for l in inst.locais]
    sem_internet = os.environ.get("ROTAVAN_SEM_TILES") == "1"
    if mapa is None:
        mapa = folium.Map(location=[sum(lats) / len(lats), sum(lons) / len(lons)], zoom_start=14,
                          tiles=None if sem_internet else tiles, control_scale=True)
        mapa.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])
        if sem_internet:
            _ruas(inst, mapa, (min(lats), max(lats), min(lons), max(lons)))
        _marcadores(inst, mapa)
    G = inst.G
    for i, it in enumerate(itins):
        if len(it.paradas) <= 1:
            continue
        cor = cor_base or CORES[i % len(CORES)]
        pts = []
        for a, b in zip(it.paradas[:-1], it.paradas[1:]):
            na, nb = inst.locais[a.local].no, inst.locais[b.local].no
            if na != nb:
                seg = coords_caminho(G, caminho(G, na, nb))
                pts.extend(seg if not pts else seg[1:])
        folium.PolyLine(pts, color=cor, weight=5, opacity=0.8,
                        dash_array="8 8" if tracejado else None,
                        tooltip=f"{it.veiculo} — {it.metodo}").add_to(mapa)
        for ordem, pa in enumerate(it.paradas[1:], start=1):
            loc = inst.locais[pa.local]
            folium.Marker(
                [loc.lat, loc.lon],
                icon=folium.DivIcon(html=(
                    f'<div style="font:600 11px sans-serif;color:#fff;background:{cor};'
                    f'border-radius:10px;padding:1px 5px;transform:translate(8px,-18px);'
                    f'white-space:nowrap">{ordem}</div>')),
                tooltip=f"{ordem}. {s_para_hhmm(pa.inicio)} — {', '.join(pa.criancas) or pa.tipo}",
            ).add_to(mapa)
    if titulo:
        mapa.get_root().html.add_child(folium.Element(
            f'<div style="position:fixed;top:10px;left:50px;z-index:9999;background:#fff;padding:6px 10px;'
            f'border-radius:6px;font:600 14px sans-serif;box-shadow:0 1px 4px rgba(0,0,0,.3)">{titulo}</div>'))
    return mapa


def _marcadores(inst: Instancia, mapa):
    for l in inst.locais:
        if l.tipo == "escola":
            e = inst.cen.escola(l.ref)
            folium.Marker([l.lat, l.lon], icon=folium.Icon(color="red", icon="graduation-cap", prefix="fa"),
                          tooltip=f"{e.nome} (entrada {s_para_hhmm(e.entrada)}, saída {s_para_hhmm(e.saida)})").add_to(mapa)
        elif l.tipo == "garagem":
            folium.Marker([l.lat, l.lon], icon=folium.Icon(color="black", icon="bus", prefix="fa"),
                          tooltip=f"Garagem {l.ref}").add_to(mapa)
        else:
            c = inst.crianca(l.ref)
            folium.CircleMarker([l.lat, l.lon], radius=6, color="#333", fill=True, fill_color="#ffd166",
                                fill_opacity=1, tooltip=f"{c.id} → {inst.cen.escola(c.escola).nome}").add_to(mapa)


def _ruas(inst: Instancia, mapa, bbox, margem=0.01):
    """Desenha a própria malha viária como fundo (para uso sem internet)."""
    y0, y1, x0, x1 = bbox[0] - margem, bbox[1] + margem, bbox[2] - margem, bbox[3] + margem
    G = inst.G
    linhas = []
    for u, v, d in G.edges(data=True):
        if "geometry" in d:
            xs, ys = d["geometry"].xy
            pts = list(zip(ys, xs))
        else:
            pts = [(G.nodes[u]["y"], G.nodes[u]["x"]), (G.nodes[v]["y"], G.nodes[v]["x"])]
        if all(y0 <= a <= y1 and x0 <= b <= x1 for a, b in pts):
            linhas.append(pts)
    folium.PolyLine(linhas, color="#c9c7c1", weight=1.5, opacity=1).add_to(mapa)
