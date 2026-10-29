# -*- coding: utf-8 -*-
"""Tabelas e figura do experimento E5 (previsão de chegada e avisos).

    python analisar_eta.py                       # escreve em resultados/artigo/
    python analisar_eta.py --destino ../artigo   # direto no projeto LaTeX

Acrescenta a chave "E5" ao numeros.json do destino (sem apagar as demais)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import wilcoxon

from analisar import GRID, INK2, br, pct, salvar_fig, tabela_latex

PASTA = Path(__file__).resolve().parent
METODOS = ["Horário fixo", "Linha reta", "Posição + rota", "Posição + rota + ritmo"]
COR = {"Horário fixo": "#8a8984", "Linha reta": "#eb6834", "Posição + rota": "#1baf7a",
       "Posição + rota + ritmo": "#2a78d6"}
FAIXAS = ["0–5", "5–10", "10–15", "15–20", "20–30"]
CENARIOS = ["normal", "dia ruim"]
NOME_CEN = {"normal": "Dia normal", "dia ruim": "Dia ruim"}
PROPOSTO = "Posição + rota + ritmo"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--resultados", default=str(PASTA / "resultados"))
    ap.add_argument("--destino", default=None)
    a = ap.parse_args()
    res = Path(a.resultados)
    dest = Path(a.destino) if a.destino else res / "artigo"
    (dest / "tabelas").mkdir(parents=True, exist_ok=True)
    (dest / "figuras").mkdir(parents=True, exist_ok=True)

    v = pd.read_csv(res / "E5_eta_viagens.csv")
    av = pd.read_csv(res / "E5_eta_avisos.csv")
    erros = np.load(res / "E5_eta_erros.npz")
    cfg = json.loads((res / "E5_eta_config.json").read_text())

    def todos(cen, m, fx=None):
        fxs = [fx] if fx else FAIXAS
        partes = [erros[f"{cen}|{m}|{f}"] for f in fxs if f"{cen}|{m}|{f}" in erros.files]
        return np.concatenate(partes) if partes else np.array([])

    N = {"viagens": int(cfg["viagens"]), "planos": int(cfg["planos"]),
         "vel_reta_kmh": cfg["vel_linha_reta_kmh"], "cen": {}}
    linhas = []
    for cen in CENARIOS:
        N["cen"][cen] = {}
        for m in METODOS:
            e = todos(cen, m)
            d = {"eam": float(np.abs(e).mean()), "vies": float(e.mean()), "p90": float(np.percentile(np.abs(e), 90)),
                 "d2": float((np.abs(e) <= 2).mean()), "d3": float((np.abs(e) <= 3).mean()), "n": int(len(e)),
                 "faixa": {}}
            lin = {"Cenário": NOME_CEN[cen] if m == METODOS[0] else "", "Método": m}
            for fx in FAIXAS:
                ef = todos(cen, m, fx)
                d["faixa"][fx] = float(np.abs(ef).mean())
                lin[fx] = br(d["faixa"][fx], 1)
            lin["Geral"] = br(d["eam"], 1)
            lin["Viés"] = br(d["vies"], 1).replace("-", "$-$")
            lin["Erro|$\\le$2 min"] = pct(d["d2"], 0)
            if m == PROPOSTO:
                lin = {k: (f"\\textbf{{{x}}}" if k not in ("Cenário",) and x else x) for k, x in lin.items()}
            linhas.append(lin)
            N["cen"][cen][m] = d
    tab = pd.DataFrame(linhas)
    tab.columns = ["Cenário", "Método"] + [f"{f}|min" for f in FAIXAS] + ["Geral", "Viés", "Erro|$\\le$2 min"]
    t = tabela_latex(tab, "llrrrrrrrr")
    t = t.replace("\\\\\nDia ruim", "\\\\\n\\midrule\nDia ruim")
    (dest / "tabelas" / "tab_e5.tex").write_text(t, encoding="utf-8")

    # ---------------------------------------------------- teste pareado por viagem
    v["abs_x_n"] = v["eam_min"] * v["n"]
    por = v.groupby(["sentido", "semente", "cenario", "dia", "metodo"]).agg(s=("abs_x_n", "sum"), n=("n", "sum"))
    por["eam"] = por["s"] / por["n"]
    por = por["eam"].unstack("metodo")
    N["wilcoxon"] = {}
    for cen in CENARIOS:
        p = por.xs(cen, level="cenario")
        N["wilcoxon"][cen] = {}
        for m in METODOS:
            if m == PROPOSTO:
                continue
            dif = p[m] - p[PROPOSTO]
            _, pv = wilcoxon(dif, alternative="greater")
            N["wilcoxon"][cen][m] = {"p": float(pv), "melhor_em": float((dif > 0).mean()), "n": int(len(dif))}

    # ---------------------------------------------------- avisos de 5 min
    av["ok"] = av["antecedencia_real_min"].between(3, 7)
    av["tarde"] = av["antecedencia_real_min"] < 2
    linhas, N["avisos"] = [], {}
    for cen in CENARIOS:
        N["avisos"][cen] = {}
        for m in METODOS:
            x = av[(av.cenario == cen) & (av.metodo == m)]
            d = {"n": int(len(x)), "mediana": float(x.antecedencia_real_min.median()),
                 "ok": float(x.ok.mean()), "tarde": float(x.tarde.mean()),
                 "eam": float((x.antecedencia_real_min - 5).abs().mean())}
            N["avisos"][cen][m] = d
            lin = {"Cenário": NOME_CEN[cen] if m == METODOS[0] else "", "Método": m,
                   "Antecedência real|mediana (min)": br(d["mediana"], 1),
                   "Erro médio|(min)": br(d["eam"], 1),
                   "Entre 3 e|7 min": pct(d["ok"], 0), "Menos de|2 min": pct(d["tarde"], 0)}
            if m == PROPOSTO:
                lin = {k: (f"\\textbf{{{x}}}" if k != "Cenário" and x else x) for k, x in lin.items()}
            linhas.append(lin)
    t = tabela_latex(pd.DataFrame(linhas), "llrrrr").replace("\\\\\nDia ruim", "\\\\\n\\midrule\nDia ruim")
    (dest / "tabelas" / "tab_e5_avisos.tex").write_text(t, encoding="utf-8")

    # ---------------------------------------------------- figura
    fig, axs = plt.subplots(1, 2, figsize=(6.3, 2.7))
    xs = np.arange(len(FAIXAS))
    for ax, cen in zip(axs, CENARIOS):
        for m in METODOS:
            ys = [N["cen"][cen][m]["faixa"][f] for f in FAIXAS]
            ax.plot(xs, ys, color=COR[m], lw=2.4 if m == PROPOSTO else 1.6, marker="o", ms=4.5,
                    label=m, zorder=3 if m == PROPOSTO else 2)
        ax.set_title(NOME_CEN[cen], fontsize=10, color=INK2)
        ax.set_xticks(xs, FAIXAS)
        ax.set_xlabel("Quanto faltava para a chegada (min)")
        ax.grid(axis="y", color=GRID, lw=0.6)
        ax.set_ylim(bottom=0)
    axs[0].set_ylabel("Erro absoluto médio (min)")
    h, l = axs[0].get_legend_handles_labels()
    fig.legend(h, l, loc="upper center", ncol=4, frameon=False, fontsize=8.5, bbox_to_anchor=(0.5, 1.07))
    fig.tight_layout()
    salvar_fig(fig, dest / "figuras", "fig_e5_eta")

    arq = dest / "numeros.json"
    tudo = json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else {}
    tudo["E5"] = N
    arq.write_text(json.dumps(tudo, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({c: {m: round(N["cen"][c][m]["eam"], 2) for m in METODOS} for c in CENARIOS}, ensure_ascii=False))
    print(json.dumps(N["wilcoxon"], ensure_ascii=False))
    print(json.dumps(N["avisos"], ensure_ascii=False)[:800])


if __name__ == "__main__":
    main()
