# -*- coding: utf-8 -*-
"""Figura estática (para o artigo) comparando duas rotas sobre a malha real.

    python figura_mapa.py --cenario resultados/exemplo_n12 --destino resultados/artigo/figuras
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from rotavan.avaliacao import metricas, tabela_itinerario
from rotavan.cenario import Cenario, Parametros, ler_criancas, ler_escolas, ler_veiculos
from rotavan.heuristicas import vizinho_mais_proximo
from rotavan.instancia import Instancia
from rotavan.modelo import otimizar
from rotavan.rede import caminho, carregar_grafo, coords_caminho, grafo_teste

PASTA = Path(__file__).resolve().parent
plt.rcParams.update({"font.family": "serif", "font.serif": ["Nimbus Roman", "Times New Roman", "DejaVu Serif"]})
AZUL, LARANJA, INK2 = "#2a78d6", "#eb6834", "#52514e"


def _rota_xy(inst, it):
    pts = []
    for a, b in zip(it.paradas[:-1], it.paradas[1:]):
        na, nb = inst.locais[a.local].no, inst.locais[b.local].no
        if na != nb:
            seg = coords_caminho(inst.G, caminho(inst.G, na, nb))
            pts.extend(seg if not pts else seg[1:])
    return [p[1] for p in pts], [p[0] for p in pts]


def painel(ax, inst, it, cor, titulo, bbox):
    G = inst.G
    x0, x1, y0, y1 = bbox
    for u, v, d in G.edges(data=True):
        xs = [G.nodes[u]["x"], G.nodes[v]["x"]]; ys = [G.nodes[u]["y"], G.nodes[v]["y"]]
        if "geometry" in d:
            xs, ys = d["geometry"].xy
        if max(xs) < x0 or min(xs) > x1 or max(ys) < y0 or min(ys) > y1:
            continue
        ax.plot(xs, ys, color="#d9d8d4", lw=0.5, zorder=1)
    xs, ys = _rota_xy(inst, it)
    ax.plot(xs, ys, color=cor, lw=2, alpha=0.9, zorder=3, solid_capstyle="round")
    for i, pa in enumerate(it.paradas[1:], start=1):
        l = inst.locais[pa.local]
        if l.tipo == "casa":
            ax.scatter(l.lon, l.lat, s=28, color="#ffd166", edgecolor="#333", lw=0.6, zorder=5)
        ax.annotate(str(i), (l.lon, l.lat), xytext=(4, 4), textcoords="offset points", fontsize=6.5,
                    color="#0b0b0b", zorder=6)
    for l in inst.locais:
        if l.tipo == "escola":
            ax.scatter(l.lon, l.lat, s=60, marker="s", color="#c0392b", edgecolor="white", lw=0.8, zorder=6)
        elif l.tipo == "garagem":
            ax.scatter(l.lon, l.lat, s=70, marker="^", color="#0b0b0b", edgecolor="white", lw=0.8, zorder=6)
    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1)
    ax.set_aspect(1 / 0.88)   # cos(28°) ~ 0,88: mantém a proporção real em Ijuí
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color("#d9d8d4")
    ax.set_title(titulo, fontsize=9.5, color="#0b0b0b")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mapa", default=str(PASTA / "dados" / "ijui_drive.graphml"))
    ap.add_argument("--cenario", default=str(PASTA / "resultados" / "exemplo_n12"))
    ap.add_argument("--destino", default=str(PASTA / "resultados" / "artigo" / "figuras"))
    ap.add_argument("--tabelas", default=None, help="pasta para salvar tab_itinerario.tex")
    ap.add_argument("--teste", action="store_true")
    a = ap.parse_args()
    G = grafo_teste(n=25) if a.teste else carregar_grafo(a.mapa)
    c = Path(a.cenario)
    cen = Cenario(ler_escolas(c / "escolas.csv"), ler_criancas(c / "criancas.csv"), ler_veiculos(c / "veiculos.csv"),
                  Parametros(sentido="ida", tempo_limite_s=10))
    inst = Instancia.criar(cen, G)
    vmp = vizinho_mais_proximo(inst)
    opt = otimizar(inst).itinerarios
    m0, m1 = metricas(inst, [vmp]), metricas(inst, opt)
    lats = [l.lat for l in inst.locais]; lons = [l.lon for l in inst.locais]
    mx, my = (max(lons) - min(lons)) * 0.08 + 0.002, (max(lats) - min(lats)) * 0.08 + 0.002
    bbox = (min(lons) - mx, max(lons) + mx, min(lats) - my, max(lats) + my)
    fig, axs = plt.subplots(1, 2, figsize=(7.0, 3.9))
    br = lambda x: f"{x:.1f}".replace(".", ",")
    painel(axs[0], inst, vmp, LARANJA,
           f"(a) Vizinho mais próximo\n{br(m0['distancia_km'])} km; maior tempo a bordo {br(m0['bordo_max_min'])} min", bbox)
    painel(axs[1], inst, opt[0], AZUL,
           f"(b) Otimizado (OR-Tools)\n{br(m1['distancia_km'])} km; maior tempo a bordo {br(m1['bordo_max_min'])} min", bbox)
    plt.tight_layout()
    d = Path(a.destino); d.mkdir(parents=True, exist_ok=True)
    fig.savefig(d / "fig_mapa_exemplo.pdf", bbox_inches="tight")
    fig.savefig(d / "fig_mapa_exemplo.png", dpi=220, bbox_inches="tight")
    if a.tabelas:
        esc = lambda t: str(t).replace("&", "\\&").replace("%", "\\%").replace("_", "\\_").replace("#", "\\#")
        tab = tabela_itinerario(inst, opt)
        linhas = ["\\begin{tabular}{rllp{5.4cm}p{3.4cm}}", "\\toprule",
                  "Ordem & Horário & Ação & Local & Crianças \\\\", "\\midrule"]
        for _, r in tab.iterrows():
            linhas.append(f"{r['ordem']} & {r['horario']} & {r['acao']} & {esc(r['local'])} & {esc(r['criancas'])} \\\\")
        linhas += ["\\bottomrule", "\\end{tabular}"]
        Path(a.tabelas).mkdir(parents=True, exist_ok=True)
        (Path(a.tabelas) / "tab_itinerario.tex").write_text("\n".join(linhas) + "\n", encoding="utf-8")
    print("ok", m0["distancia_km"], m1["distancia_km"])


if __name__ == "__main__":
    main()
