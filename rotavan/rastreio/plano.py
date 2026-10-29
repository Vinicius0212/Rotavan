# -*- coding: utf-8 -*-
"""Plano da viagem e previsão de chegada (ETA) a partir da posição da van.

A ideia central do módulo de acompanhamento é aproveitar o que o otimizador já
calculou: a ordem das paradas, o trajeto pelas ruas e o horário previsto de cada
parada. Com a posição GPS do celular do motorista, o sistema descobre em que
ponto do trajeto planejado a van está e estima quanto falta para cada parada
seguinte — inclusive para a casa (ou a escola) de cada criança.

O tempo planejado de cada trecho vem da malha viária (com o fator de trânsito).
Como o trânsito real de cada dia é diferente, o estimador aprende um "ritmo" ao
longo da viagem: a razão entre o tempo real e o tempo planejado nos trechos já
percorridos. No começo da viagem há pouca informação, então o ritmo é "puxado"
para 1 como se já houvesse 400 s de condução exatamente no tempo planejado
(peso_prior_s; valor escolhido em instâncias de ajuste separadas das de teste).

Tudo aqui é Python puro (sem rede, sem banco), para poder ser testado e usado
nos experimentos de simulação."""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

import numpy as np

from ..avaliacao import Itinerario
from ..instancia import Instancia

R_TERRA = 6_371_008.8

# Limiares (metros / segundos)
DIST_NA_ROTA_M = 70.0        # GPS a até isto do trajeto = "na rota"
RAIO_PARADA_M = 60.0         # perto disto de uma parada = van parada atendendo
JANELA_BUSCA_M = 2500.0      # procura a posição só um pouco à frente da última (evita "pular" de trecho)
RECUO_BUSCA_M = 80.0         # tolera um pequeno recuo por erro do GPS
VEL_PARADA_MS = 2.0          # abaixo disto (7 km/h) perto de uma parada = van parada atendendo
VEL_LINHA_RETA_MS = 25 / 3.6 # para o método ingênuo e para quando a van sai da rota sem mapa


def _xy(lat, lon, lat0, lon0):
    """Coordenadas locais em metros (projeção equiretangular; ótima para uma cidade)."""
    x = math.radians(lon - lon0) * R_TERRA * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * R_TERRA
    return x, y


def distancia_m(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * R_TERRA * math.asin(math.sqrt(a))


# ------------------------------------------------------------------ estruturas
@dataclass
class ParadaPlano:
    ordem: int
    tipo: str                 # "saida" | "embarque" | "desembarque" | "fim"
    local: int                # índice do local na instância
    no: int                   # nó da malha
    lat: float
    lon: float
    nome: str                 # "Escola X" / "Ponto de C03" / "Garagem"
    criancas: list[str]
    chegada: float            # horário planejado de chegada (s desde 00:00)
    inicio: float             # horário planejado de início do atendimento
    lb: float                 # não pode começar antes disto (janela)
    servico_s: float          # tempo de parada por criança
    cum_s: float = 0.0        # tempo de condução planejado acumulado até aqui
    cum_m: float = 0.0        # distância acumulada até aqui


@dataclass
class PlanoViagem:
    van: str
    sentido: str
    paradas: list[ParadaPlano]
    trajeto: list[list[float]]          # [[lat, lon, cum_s, cum_m], ...] seguindo as ruas
    escola_de: dict[str, str] = field(default_factory=dict)   # criança -> id da escola

    # ----------------------------------------------------------- serialização
    def to_dict(self) -> dict:
        return {"van": self.van, "sentido": self.sentido, "escola_de": self.escola_de,
                "paradas": [asdict(p) for p in self.paradas], "trajeto": self.trajeto}

    @classmethod
    def from_dict(cls, d: dict) -> "PlanoViagem":
        return cls(van=d["van"], sentido=d["sentido"], escola_de=d.get("escola_de", {}),
                   paradas=[ParadaPlano(**p) for p in d["paradas"]], trajeto=d["trajeto"])

    # ----------------------------------------------------------- consultas
    @property
    def saida(self) -> float:
        return self.paradas[0].inicio

    @property
    def atendimentos(self) -> list[ParadaPlano]:
        return [p for p in self.paradas if p.tipo in ("embarque", "desembarque")]

    def criancas(self) -> list[str]:
        return list(dict.fromkeys(k for p in self.paradas if p.tipo == "embarque" for k in p.criancas))

    def parada_de(self, crianca: str, tipo: str) -> ParadaPlano | None:
        for p in self.paradas:
            if p.tipo == tipo and crianca in p.criancas:
                return p
        return None

    def ponto_em(self, cum_s: float) -> tuple[float, float]:
        """Posição (lat, lon) no trajeto planejado depois de `cum_s` segundos de condução."""
        tr = self.trajeto
        if cum_s <= tr[0][2]:
            return tr[0][0], tr[0][1]
        ts = [p[2] for p in tr]
        i = int(np.searchsorted(ts, cum_s))
        if i >= len(tr):
            return tr[-1][0], tr[-1][1]
        a, b = tr[i - 1], tr[i]
        f = 0.0 if b[2] == a[2] else (cum_s - a[2]) / (b[2] - a[2])
        return a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])


# ------------------------------------------------------------------ construção
def _nome_local(inst: Instancia, local: int) -> str:
    loc = inst.locais[local]
    if loc.tipo == "escola":
        return inst.cen.escola(loc.ref).nome
    if loc.tipo == "garagem":
        return "Garagem"
    return f"Ponto de {loc.ref}"


def criar_plano(inst: Instancia, it: Itinerario) -> PlanoViagem:
    """Converte o itinerário otimizado num plano de viagem acompanhável: paradas
    com horários e janelas + o trajeto pelas ruas com o tempo planejado em cada
    ponto (o mesmo tempo que o otimizador usou)."""
    G = inst.G
    fator = inst.p.fator_transito
    paradas: list[ParadaPlano] = []
    trajeto: list[list[float]] = []
    cum_s = cum_m = 0.0
    import networkx as nx

    for ordem, pa in enumerate(it.paradas):
        loc = inst.locais[pa.local]
        if ordem > 0:
            a = inst.locais[it.paradas[ordem - 1].local].no
            b = loc.no
            if a != b:
                nos = nx.shortest_path(G, a, b, weight="travel_time")
                for u, v in zip(nos[:-1], nos[1:]):
                    dados = min(G.get_edge_data(u, v).values(), key=lambda e: e.get("travel_time", 1e9))
                    t_aresta = float(dados.get("travel_time", 0.0)) * fator
                    geom = dados.get("geometry")
                    if geom is not None:
                        pts = [(y, x) for x, y in geom.coords]
                    else:
                        pts = [(G.nodes[u]["y"], G.nodes[u]["x"]), (G.nodes[v]["y"], G.nodes[v]["x"])]
                    comp = [distancia_m(*p, *q) for p, q in zip(pts[:-1], pts[1:])]
                    total = sum(comp) or 1.0
                    if not trajeto:
                        trajeto.append([pts[0][0], pts[0][1], cum_s, cum_m])
                    for (q, d) in zip(pts[1:], comp):
                        cum_s += t_aresta * d / total
                        cum_m += d
                        trajeto.append([q[0], q[1], round(cum_s, 2), round(cum_m, 1)])
        if not trajeto:
            trajeto.append([loc.lat, loc.lon, 0.0, 0.0])
        if pa.tipo == "embarque":
            lb = max((inst.janela_embarque(k)[0] for k in pa.criancas), default=0)
        elif pa.tipo == "desembarque":
            lb = max((inst.janela_desembarque(k)[0] for k in pa.criancas), default=0)
        else:
            lb = pa.inicio
        paradas.append(ParadaPlano(
            ordem=ordem, tipo=pa.tipo, local=pa.local, no=int(loc.no), lat=loc.lat, lon=loc.lon,
            nome=_nome_local(inst, pa.local), criancas=list(pa.criancas), chegada=float(pa.chegada),
            inicio=float(pa.inicio), lb=float(lb), servico_s=float(inst.servico(pa.local)),
            cum_s=round(cum_s, 2), cum_m=round(cum_m, 1)))
    escola_de = {c.id: c.escola for c in inst.cen.criancas}
    return PlanoViagem(it.veiculo, inst.p.sentido, paradas, trajeto,
                       {k: escola_de[k] for p in paradas for k in p.criancas})


# ------------------------------------------------------------------ projeção do GPS
class _Geometria:
    """Pré-cálculo do trajeto em metros, para projetar o GPS rapidamente."""

    def __init__(self, plano: PlanoViagem):
        tr = plano.trajeto
        self.lat0, self.lon0 = tr[0][0], tr[0][1]
        self.xy = np.array([_xy(p[0], p[1], self.lat0, self.lon0) for p in tr])
        self.cum_s = np.array([p[2] for p in tr])
        self.cum_m = np.array([p[3] for p in tr])

    def _melhor(self, px, py, ini, fim):
        A = self.xy[ini:fim - 1]
        B = self.xy[ini + 1:fim]
        AB = B - A
        L2 = (AB ** 2).sum(axis=1)
        L2[L2 == 0] = 1e-9
        t = np.clip(((px - A[:, 0]) * AB[:, 0] + (py - A[:, 1]) * AB[:, 1]) / L2, 0, 1)
        Q = A + AB * t[:, None]
        d = np.hypot(Q[:, 0] - px, Q[:, 1] - py)
        return d, t

    def _resultado(self, i, f, d):
        cs = self.cum_s[i] + f * (self.cum_s[i + 1] - self.cum_s[i])
        cm = self.cum_m[i] + f * (self.cum_m[i + 1] - self.cum_m[i])
        return float(cs), float(cm), float(d), i

    def projetar(self, lat, lon, a_partir_m: float = 0.0):
        """Ponto do trajeto mais próximo, procurando primeiro numa janela à frente
        da última posição conhecida (o trajeto pode passar duas vezes pela mesma
        rua). Se não achar ali (ex.: o GPS ficou desligado um tempo), procura no
        resto do trajeto e fica com o primeiro trecho compatível."""
        px, py = _xy(lat, lon, self.lat0, self.lon0)
        n = len(self.xy)
        if n == 1:
            d = math.hypot(px - self.xy[0, 0], py - self.xy[0, 1])
            return 0.0, 0.0, d, 0
        ini = int(np.searchsorted(self.cum_m, a_partir_m - RECUO_BUSCA_M, side="right")) - 1
        fim = int(np.searchsorted(self.cum_m, a_partir_m + JANELA_BUSCA_M, side="right"))
        ini, fim = max(ini, 0), min(max(fim, ini + 2), n)
        d, t = self._melhor(px, py, ini, fim)
        j = int(np.argmin(d))
        if d[j] <= DIST_NA_ROTA_M or fim >= n:
            return self._resultado(ini + j, float(t[j]), d[j])
        d2, t2 = self._melhor(px, py, ini, n)
        perto = np.flatnonzero(d2 <= DIST_NA_ROTA_M)
        if len(perto):
            j2 = int(perto[0])
            return self._resultado(ini + j2, float(t2[j2]), d2[j2])
        return self._resultado(ini + j, float(t[j]), d[j])


# ------------------------------------------------------------------ estimador de chegada
@dataclass
class Previsao:
    ordem: int                 # ordem da parada no plano
    chegada: float             # horário previsto de chegada (relógio do plano)
    inicio: float              # horário previsto de atendimento (após espera da janela)
    atraso_s: float            # chegada prevista - chegada planejada


class Estimador:
    """Acompanha uma viagem: recebe posições GPS e prevê a chegada às paradas.

    modo = "adaptativo": ritmo aprendido com a própria viagem (padrão);
           "plano":      ritmo fixo = 1 (só posição + tempos do plano);
    """

    def __init__(self, plano: PlanoViagem, modo: str = "adaptativo", peso_prior_s: float = 400.0,
                 limites=(0.6, 2.5), tempo_ate=None, razao_max: float | None = None):
        self.plano = plano
        self.modo = modo
        self.geo = _Geometria(plano)
        self.peso = peso_prior_s
        self.lim = limites
        self.tempo_ate = tempo_ate        # f(lat, lon, no_destino) -> s pela malha (quando sai da rota)
        self.razao_max = razao_max        # opcional: limita o peso de uma retenção pontual (testado; não ajudou)
        self.cum_s = 0.0
        self.cum_m = 0.0
        self.real = 0.0                   # s reais dirigindo nos trechos já percorridos
        self.plan = 0.0                   # s planejados correspondentes
        self.ultimo: tuple | None = None   # (t, cum_s, cum_m) da última posição na rota
        self.fora_da_rota = False
        self.dist_rota_m = 0.0
        self.pos: tuple[float, float] | None = None
        self.t_pos: float | None = None

    # ----------------------------------------------------------- ritmo
    @property
    def ritmo(self) -> float:
        if self.modo == "plano":
            return 1.0
        r = (self.real + self.peso) / (self.plan + self.peso)
        return float(min(max(r, self.lim[0]), self.lim[1]))

    def _perto_de_parada(self, cum_m: float) -> bool:
        return any(abs(p.cum_m - cum_m) <= RAIO_PARADA_M for p in self.plano.paradas)

    # ----------------------------------------------------------- atualização
    def atualizar(self, t: float, lat: float, lon: float, precisao_m: float | None = None) -> dict:
        """Registra uma posição (t no relógio do plano, em s desde 00:00)."""
        if precisao_m is not None and precisao_m > 150:      # leitura muito ruim: ignora
            return {"aceita": False}
        self.pos, self.t_pos = (lat, lon), t
        cs, cm, d, _ = self.geo.projetar(lat, lon, self.cum_m)
        self.dist_rota_m = d
        if d > DIST_NA_ROTA_M:
            self.fora_da_rota = True
            self.ultimo = None
            return {"aceita": True, "na_rota": False, "dist_rota_m": d}
        self.fora_da_rota = False
        if cm < self.cum_m:                    # recuo pequeno = ruído do GPS: mantém o avanço
            cs, cm = self.cum_s, self.cum_m
        if self.ultimo is not None:
            dt = t - self.ultimo[0]
            dc = cs - self.ultimo[1]
            dm = cm - self.ultimo[2]
            # parado (ou quase) perto de uma parada = embarque/espera: não conta como trânsito.
            # O GPS "treme" alguns metros com a van parada, por isso o critério é a velocidade.
            devagar = dt > 0 and dm / dt < VEL_PARADA_MS
            parado_em_parada = devagar and self._perto_de_parada(cm)
            if 0 < dt <= 120 and not parado_em_parada:
                dc = max(dc, 0.0)
                # (opcional) cada leitura conta no máximo razao_max × o tempo planejado que avançou
                self.real += dt if self.razao_max is None else min(dt, self.razao_max * dc)
                self.plan += dc
        self.cum_s, self.cum_m = cs, cm
        self.ultimo = (t, cs, cm)
        return {"aceita": True, "na_rota": True, "dist_rota_m": d, "cum_s": cs}

    # ----------------------------------------------------------- previsão
    def prever(self, t: float, pendentes: dict[int, list[str]] | None = None) -> list[Previsao]:
        """Prevê chegada às paradas que ainda têm crianças pendentes.

        `pendentes`: {ordem da parada: [crianças ainda não atendidas ali]}. Se
        None, considera pendentes todas as paradas à frente da posição atual."""
        pl = self.plano
        if pendentes is None:
            pendentes = {p.ordem: list(p.criancas) for p in pl.atendimentos if p.cum_m >= self.cum_m - 1}
        r = self.ritmo
        relogio = t
        cum = self.cum_s
        out: list[Previsao] = []
        primeira = True
        for p in pl.atendimentos:
            kids = pendentes.get(p.ordem)
            if not kids:
                continue
            if primeira and self.fora_da_rota and self.pos is not None:
                if self.tempo_ate is not None:
                    conducao = self.tempo_ate(self.pos[0], self.pos[1], p.no) * r
                else:
                    conducao = 1.4 * distancia_m(*self.pos, p.lat, p.lon) / VEL_LINHA_RETA_MS
            else:
                conducao = max(0.0, p.cum_s - cum) * r
            chegada = relogio + conducao
            inicio = max(chegada, p.lb)
            out.append(Previsao(p.ordem, chegada, inicio, chegada - p.chegada))
            relogio = inicio + p.servico_s * len(kids)
            cum = max(cum, p.cum_s)
            primeira = False
        return out


# ------------------------------------------------------------------ métodos de comparação
def previsao_horario_fixo(plano: PlanoViagem, ordem: int) -> float:
    """O que um "horário de passagem" fixo diria: a hora planejada, sempre."""
    return next(p.chegada for p in plano.paradas if p.ordem == ordem)


def previsao_linha_reta(plano: PlanoViagem, t: float, lat: float, lon: float,
                        pendentes_ordens: list[int], vel_ms: float = VEL_LINHA_RETA_MS) -> dict[int, float]:
    """Método ingênuo de apps de localização: distância em linha reta / velocidade
    média, parada a parada, sem conhecer as ruas nem o plano."""
    out = {}
    relogio, pos = t, (lat, lon)
    for p in plano.atendimentos:
        if p.ordem not in pendentes_ordens:
            continue
        relogio += distancia_m(*pos, p.lat, p.lon) / vel_ms
        out[p.ordem] = relogio
        relogio += p.servico_s * len(p.criancas)
        pos = (p.lat, p.lon)
    return out


def velocidade_linha_reta(planos: list[PlanoViagem]) -> float:
    """Velocidade "em linha reta" média dos planos (distância reta entre paradas
    consecutivas / tempo de condução planejado). Calibra o método ingênuo para
    que a comparação seja justa com ele."""
    dist = tempo = 0.0
    for pl in planos:
        for a, b in zip(pl.paradas[:-1], pl.paradas[1:]):
            dist += distancia_m(a.lat, a.lon, b.lat, b.lon)
            tempo += b.cum_s - a.cum_s
    return dist / tempo if tempo else VEL_LINHA_RETA_MS
