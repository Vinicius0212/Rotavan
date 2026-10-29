# -*- coding: utf-8 -*-
"""Transforma os CSVs de experimentos.py em tabelas (LaTeX) e figuras (PDF/PNG).

    python analisar.py                          # lê resultados/, escreve resultados/artigo/
    python analisar.py --destino ../artigo      # escreve direto no projeto LaTeX
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import wilcoxon

PASTA = Path(__file__).resolve().parent
COR = {"OR-Tools (GLS)": "#2a78d6", "vizinho mais próximo": "#eb6834", "escola por escola": "#1baf7a"}
NOME = {"OR-Tools (GLS)": "Otimizado", "vizinho mais próximo": "Vizinho mais próximo",
        "escola por escola": "Escola por escola"}
ORDEM = ["vizinho mais próximo", "escola por escola", "OR-Tools (GLS)"]
INK, INK2, GRID = "#0b0b0b", "#52514e", "#d9d8d4"

plt.rcParams.update({"font.family": "serif", "font.serif": ["Nimbus Roman", "Times New Roman", "DejaVu Serif"],
                     "font.size": 10, "axes.edgecolor": GRID, "axes.linewidth": 0.8,
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                     "axes.spines.top": False, "axes.spines.right": False})


def br(x, casas=1):
    """Número no formato brasileiro."""
    if pd.isna(x):
        return "--"
    return f"{x:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def pct(x, casas=1):
    return br(100 * x, casas) + "\\%"


def tabela_latex(df: pd.DataFrame, colfmt: str) -> str:
    cab = [("\\makecell{" + c.replace("|", "\\\\") + "}") if "|" in c else c for c in df.columns]
    linhas = ["\\begin{tabular}{" + colfmt + "}", "\\toprule",
              " & ".join(cab) + " \\\\", "\\midrule"]
    for _, r in df.iterrows():
        linhas.append(" & ".join(str(v) for v in r.values) + " \\\\")
    linhas += ["\\bottomrule", "\\end{tabular}"]
    return "\n".join(linhas) + "\n"


def salvar_fig(fig, destino: Path, nome: str):
    fig.savefig(destino / f"{nome}.pdf", bbox_inches="tight")
    fig.savefig(destino / f"{nome}.png", dpi=200, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------- E0
def e0(res, N):
    f = res / "E0_validacao.csv"
    if not f.exists():
        return
    d = pd.read_csv(f)
    iguais = (d["km_ortools"] <= d["km_exato"] + 1e-6).sum()
    N["E0"] = {"instancias": int(len(d)), "otimo_encontrado": int(iguais),
               "sequencias_por_instancia": int(d["sequencias"].iloc[0]),
               "viaveis_medio": float(d["viaveis"].mean()), "tempo_exato_medio_s": float(d["tempo_exato_s"].mean()),
               "ortools_viavel": int(d["ortools_viavel"].sum())}


# ---------------------------------------------------------------------------- E1
def resumo_metodos(d: pd.DataFrame, por: list[str]):
    g = d.groupby(por + ["metodo"])
    t = g.agg(km=("distancia_km", "mean"), km_dp=("distancia_km", "std"),
              dur=("duracao_total_min", "mean"), bordo=("bordo_medio_min", "mean"),
              bordo_max=("bordo_max_min", "mean"), viavel=("viavel", "mean"), n_inst=("viavel", "size"))
    return t.reset_index()


def reducoes(d: pd.DataFrame, chave: list[str]):
    """Redução percentual do otimizado frente a cada heurística, pareada por instância."""
    piv = d.pivot_table(index=chave, columns="metodo", values="distancia_km")
    out = {}
    for h in ["vizinho mais próximo", "escola por escola"]:
        if h not in piv or "OR-Tools (GLS)" not in piv:
            continue
        red = 1 - piv["OR-Tools (GLS)"] / piv[h]
        try:
            p = wilcoxon(piv[h], piv["OR-Tools (GLS)"], alternative="greater").pvalue
        except ValueError:
            p = float("nan")
        out[h] = {"media": float(red.mean()), "mediana": float(red.median()), "min": float(red.min()),
                  "max": float(red.max()), "p_wilcoxon": float(p), "n": int(red.notna().sum())}
    return out


def completas(d: pd.DataFrame, chave: list[str]):
    """Mantém só as instâncias em que o otimizador atendeu todas as crianças
    (comparar distâncias com crianças a menos seria injusto com as heurísticas)."""
    o = d[d.metodo == "OR-Tools (GLS)"]
    ok = o[o["nao_atendidas"] == 0].set_index(chave).index
    return d[d.set_index(chave).index.isin(ok)], len(o) - len(ok), len(o)


def e1(res, dest_tab, dest_fig, N):
    f = res / "E1_ida.csv"
    if not f.exists():
        return
    d_todas = pd.read_csv(f)
    d, fora, total = completas(d_todas, ["n", "semente"])
    N["E1_incompletas"] = {int(n): int((g.loc[g.metodo == "OR-Tools (GLS)", "nao_atendidas"] > 0).sum())
                           for n, g in d_todas.groupby("n")}
    N["E1_instancias"] = {int(n): int((g.metodo == "OR-Tools (GLS)").sum()) for n, g in d.groupby("n")}
    # nas instâncias incompletas, alguma heurística foi viável?
    inc = d_todas[~d_todas.set_index(["n", "semente"]).index.isin(d.set_index(["n", "semente"]).index)]
    N["E1_incompletas_heur_viavel"] = int(inc[inc.metodo != "OR-Tools (GLS)"].groupby(["n", "semente"])["viavel"].max().sum()) if len(inc) else 0
    t = resumo_metodos(d, ["n"])
    t["ordem"] = t["metodo"].map({m: i for i, m in enumerate(ORDEM)})
    t = t.sort_values(["n", "ordem"])
    tab = pd.DataFrame({
        "Crianças": t["n"].astype(int),
        "Método": t["metodo"].map(NOME),
        "Distância|(km)": [f"{br(a, 2)} $\\pm$ {br(b, 2)}" for a, b in zip(t["km"], t["km_dp"])],
        "Duração|(min)": t["dur"].map(lambda x: br(x, 1)),
        "A bordo|médio (min)": t["bordo"].map(lambda x: br(x, 1)),
        "A bordo|máx. (min)": t["bordo_max"].map(lambda x: br(x, 1)),
        "Rotas|viáveis": t["viavel"].map(lambda x: pct(x, 0)),
    })
    (dest_tab / "tab_e1.tex").write_text(tabela_latex(tab, "clrrrrr"), encoding="utf-8")

    N["E1"] = {"por_n": {}, "geral": reducoes(d, ["n", "semente"])}
    for n, dn in d.groupby("n"):
        N["E1"]["por_n"][int(n)] = {"reducao": reducoes(dn, ["semente"]),
                                    "resumo": resumo_metodos(dn, []).set_index("metodo").to_dict("index")}
    # tempo de bordo: otimizado x vizinho (pareado)
    piv = d.pivot_table(index=["n", "semente"], columns="metodo", values="bordo_medio_min")
    N["E1"]["bordo_medio"] = {m: float(piv[m].mean()) for m in piv.columns}
    piv = d.pivot_table(index=["n", "semente"], columns="metodo", values="viavel")
    N["E1"]["viavel"] = {m: float(piv[m].mean()) for m in piv.columns}
    N["E1"]["tempo_solver_s"] = float(d.loc[d.metodo == "OR-Tools (GLS)", "tempo_solver_s"].mean())

    # tabela de reduções
    linhas = []
    for n, dn in d.groupby("n"):
        for h, v in reducoes(dn, ["semente"]).items():
            linhas.append({"Crianças": int(n), "Comparado com": NOME[h], "Redução|média": pct(v["media"]),
                           "Mediana": pct(v["mediana"]), "Mín.–máx.": f"{pct(v['min'], 0)} a {pct(v['max'], 0)}",
                           "$p$ (Wilcoxon)": "$<$0,001" if v["p_wilcoxon"] < 0.001 else br(v["p_wilcoxon"], 3)})
    (dest_tab / "tab_e1_reducao.tex").write_text(tabela_latex(pd.DataFrame(linhas), "clrrcr"), encoding="utf-8")

    # figura: distância por instância (pontos) + média, por n e método
    ns = sorted(d["n"].unique())
    fig, ax = plt.subplots(figsize=(6.3, 3.0))
    larg = 0.26
    rng = np.random.default_rng(0)
    for j, m in enumerate(ORDEM):
        for i, n in enumerate(ns):
            v = d[(d.n == n) & (d.metodo == m)]["distancia_km"].values
            x = i + (j - 1) * larg
            ax.scatter(x + rng.uniform(-0.07, 0.07, len(v)), v, s=10, color=COR[m], alpha=0.45, linewidths=0)
            ax.plot([x - 0.1, x + 0.1], [v.mean()] * 2, color=COR[m], lw=2.2, solid_capstyle="round")
    ax.set_xticks(range(len(ns)), [f"{n} crianças" for n in ns])
    ax.set_ylabel("Distância do trajeto (km)")
    ax.grid(axis="y", color=GRID, lw=0.6); ax.set_axisbelow(True)
    ax.set_ylim(bottom=0)
    for m in ORDEM:
        ax.scatter([], [], color=COR[m], s=18, label=NOME[m])
    ax.legend(frameon=False, ncol=3, loc="upper left", fontsize=9, bbox_to_anchor=(0, 1.13))
    salvar_fig(fig, dest_fig, "fig_e1_distancia")


def e1b(res, dest_tab, N):
    f = res / "E1b_volta.csv"
    if not f.exists():
        return
    d_todas = pd.read_csv(f)
    d, fora, total = completas(d_todas, ["semente"])
    N["E1b_incompletas"] = int(fora)
    N["E1b_instancias"] = int(total - fora)
    v = d_todas[d_todas.metodo == "vizinho mais próximo"]
    N["E1b_vmp_acima"] = float((v["criancas_acima_tmax"] > 0).mean())
    N["E1b"] = {"reducao": reducoes(d, ["semente"]),
                "resumo": resumo_metodos(d, []).set_index("metodo").to_dict("index")}


# ---------------------------------------------------------------------------- E2
def e2(res, dest_tab, dest_fig, N):
    f = res / "E2_tmax.csv"
    if not f.exists():
        return
    d = pd.read_csv(f)
    o = d[d.metodo == "OR-Tools (GLS)"].copy()
    o["todos"] = o["nao_atendidas"] == 0
    g = o.groupby("tmax").agg(todos=("todos", "mean"), km=("distancia_km", "mean"),
                              bordo=("bordo_medio_min", "mean"), bordo_max=("bordo_max_min", "mean"),
                              fora=("nao_atendidas", "mean")).reset_index()
    ok = o[o.todos].groupby("tmax")["distancia_km"].mean()
    g["km_ok"] = g["tmax"].map(ok)
    h = d[d.metodo != "OR-Tools (GLS)"].groupby(["tmax", "metodo"])["viavel"].mean().unstack()
    tab = pd.DataFrame({
        "$T_{\\max}$ (min)": g["tmax"].astype(int),
        "Todos|atendidos": g["todos"].map(lambda x: pct(x, 0)),
        "Crianças fora|(média)": g["fora"].map(lambda x: br(x, 1)),
        "Distância|(km)*": g["km_ok"].map(lambda x: br(x, 2)),
        "A bordo|médio (min)": g["bordo"].map(lambda x: br(x, 1)),
        "Viz. mais próx.|viável": g["tmax"].map(lambda t: pct(h.loc[t, "vizinho mais próximo"], 0)),
        "Escola por|escola viável": g["tmax"].map(lambda t: pct(h.loc[t, "escola por escola"], 0)),
    })
    (dest_tab / "tab_e2.tex").write_text(tabela_latex(tab, "crrrrrr"), encoding="utf-8")
    N["E2"] = g.set_index("tmax").to_dict("index")

    fig, ax = plt.subplots(figsize=(6.3, 2.8))
    ax.plot(g["tmax"], 100 * g["todos"], color=COR["OR-Tools (GLS)"], lw=2, marker="o", ms=7, label="Otimizado")
    for m in ["vizinho mais próximo", "escola por escola"]:
        ax.plot(g["tmax"], [100 * h.loc[t, m] for t in g["tmax"]], color=COR[m], lw=2, marker="o", ms=7,
                ls=(0, (4, 2)), label=NOME[m])
    ax.set_xlabel("Tempo máximo permitido a bordo, $T_{\\max}$ (min)")
    ax.set_ylabel("Instâncias viáveis (%)")
    ax.set_ylim(-3, 105); ax.set_xticks(g["tmax"])
    ax.grid(axis="y", color=GRID, lw=0.6); ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=9, ncol=3, loc="lower left", bbox_to_anchor=(0, 1.02))
    salvar_fig(fig, dest_fig, "fig_e2_tmax")


# ---------------------------------------------------------------------------- E3
def e3(res, dest_tab, N):
    f = res / "E3_frota.csv"
    if not f.exists():
        return
    d = pd.read_csv(f)
    g = d.groupby("n").agg(vans=("vans_usadas", "mean"), vans_min=("vans_usadas", "min"),
                           vans_max=("vans_usadas", "max"), km=("distancia_km", "mean"),
                           bordo=("bordo_medio_min", "mean"), bordo_max=("bordo_max_min", "mean"),
                           carga=("carga_max", "mean"), viavel=("viavel", "mean"),
                           t=("tempo_solver_s", "mean")).reset_index()
    tab = pd.DataFrame({
        "Crianças": g["n"].astype(int),
        "Vans usadas|(média)": g["vans"].map(lambda x: br(x, 1)),
        "Faixa": [f"{int(a)}--{int(b)}" for a, b in zip(g["vans_min"], g["vans_max"])],
        "Distância|total (km)": g["km"].map(lambda x: br(x, 1)),
        "A bordo|médio (min)": g["bordo"].map(lambda x: br(x, 1)),
        "Lotação máx.|(média)": g["carga"].map(lambda x: br(x, 1)),
        "Todos|atendidos": g["viavel"].map(lambda x: pct(x, 0)),
    })
    (dest_tab / "tab_e3.tex").write_text(tabela_latex(tab, "crcrrrr"), encoding="utf-8")
    N["E3"] = g.set_index("n").to_dict("index")


# ---------------------------------------------------------------------------- E4
def e4(res, dest_tab, dest_fig, N):
    f = res / "E4_algoritmo.csv"
    if not f.exists():
        return
    d = pd.read_csv(f)
    d = d[d["nao_atendidas"] == 0].copy()
    melhor = d.groupby("semente")["distancia_km"].min()
    d["gap"] = d["distancia_km"] / d["semente"].map(melhor) - 1
    todas = pd.read_csv(f)
    ok = todas.assign(ok=todas["nao_atendidas"] == 0).groupby("metodo")["ok"].mean()
    ordem = ["Só solução inicial", "GLS 1 s", "GLS 5 s", "GLS 10 s", "GLS 30 s", "Tabu 10 s", "Recozimento 10 s"]
    g = d.groupby("metodo").agg(km=("distancia_km", "mean"), gap=("gap", "mean"), gap_max=("gap", "max"),
                                t=("tempo_solver_s", "mean")).reindex([o for o in ordem if o in d.metodo.unique()])
    g["ok"] = ok.reindex(g.index)
    tab = pd.DataFrame({
        "Configuração": g.index,
        "Todos|atendidos": g["ok"].map(lambda x: pct(x, 0)),
        "Acima da melhor|distância (média)": g["gap"].map(lambda x: pct(x, 1)),
        "Pior|caso": g["gap_max"].map(lambda x: pct(x, 1)),
        "Tempo (s)": g["t"].map(lambda x: br(x, 1)),
    })
    (dest_tab / "tab_e4.tex").write_text(tabela_latex(tab, "lrrrr"), encoding="utf-8")
    N["E4"] = g.to_dict("index")


# ---------------------------------------------------------------------------- economia
def economia(N, preco=6.50, consumo=9.0, dias=20):
    try:
        ida = N["E1"]["por_n"][12]["resumo"]
        volta = N["E1b"]["resumo"]
    except KeyError:
        return
    base = "vizinho mais próximo"
    dk_ida = ida[base]["km"] - ida["OR-Tools (GLS)"]["km"]
    dk_volta = volta[base]["km"] - volta["OR-Tools (GLS)"]["km"]
    km_mes = (dk_ida + dk_volta) * dias
    N["economia"] = {"km_dia_ida": dk_ida, "km_dia_volta": dk_volta, "km_mes": km_mes,
                     "litros_mes": km_mes / consumo, "reais_mes": km_mes / consumo * preco,
                     "reais_ano_letivo": km_mes / consumo * preco * 10,
                     "premissas": {"preco_litro": preco, "consumo_km_l": consumo, "dias_mes": dias,
                                   "meses_letivos": 10}}


def ambiente() -> str:
    """Descrição do computador (para a Metodologia)."""
    import os, platform, re
    cpu = platform.processor() or "processador não identificado"
    try:
        txt = open("/proc/cpuinfo", encoding="utf-8").read()
        cpu = re.search(r"model name\s*:\s*(.+)", txt).group(1).strip()
        mem = int(re.search(r"MemTotal:\s*(\d+)", open("/proc/meminfo").read()).group(1)) / 1024 ** 2
    except Exception:
        mem = None
    import ortools
    cpu = cpu.replace("(R)", "").replace("(TM)", "").replace(" Processor @ ", " de ").replace("GHz", " GHz")
    cpu = re.sub(r"(\d)\.(\d)", r"\1,\2", cpu).replace("  ", " ")
    partes = [f"um computador com processador {cpu}", f"{os.cpu_count()} núcleos"]
    if mem:
        partes.append(f"{mem:.0f} GB de memória".replace(".", ","))
    return (", ".join(partes) + f", Python {platform.python_version()} e OR-Tools {ortools.__version__}; "
            "cada execução do otimizador usa um único núcleo")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--resultados", default=str(PASTA / "resultados"))
    ap.add_argument("--destino", default=None)
    a = ap.parse_args()
    res = Path(a.resultados)
    dest = Path(a.destino) if a.destino else res / "artigo"
    (dest / "tabelas").mkdir(parents=True, exist_ok=True)
    (dest / "figuras").mkdir(parents=True, exist_ok=True)
    N = {"ambiente": ambiente()}
    e0(res, N)
    e1(res, dest / "tabelas", dest / "figuras", N)
    e1b(res, dest / "tabelas", N)
    e2(res, dest / "tabelas", dest / "figuras", N)
    e3(res, dest / "tabelas", N)
    e4(res, dest / "tabelas", dest / "figuras", N)
    economia(N)
    if (res / "resumo.json").exists():
        N["malha"] = json.loads((res / "resumo.json").read_text(encoding="utf-8"))
    (dest / "numeros.json").write_text(json.dumps(N, indent=2, ensure_ascii=False, default=float), encoding="utf-8")
    print("Tabelas e figuras em", dest)


if __name__ == "__main__":
    main()
