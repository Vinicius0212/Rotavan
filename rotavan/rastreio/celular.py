# -*- coding: utf-8 -*-
"""Acompanhamento pelo celular da própria criança.

Motivação: a família pode não conhecer (nem ter como exigir algo do) motorista
contratado. O celular da criança, levado na mochila, passa a informar onde ela
está — inclusive dentro da van —, sem depender da colaboração do motorista.

Uma página web não envia a posição com a tela desligada. Por isso o celular da
criança usa o Traccar Client, aplicativo gratuito e de código aberto (Android e
iOS) que roda em segundo plano e envia a posição para um endereço configurável,
no protocolo OsmAnd (parâmetros na URL ou JSON). O RotaVan recebe essas posições
em /traccar e:

- guarda a posição só dentro das janelas de ida e volta da escola (opção
  "aula"), a não ser que a família escolha "sempre";
- reconhece se a criança está em casa, na escola ou no caminho (cercas
  virtuais em torno do ponto de casa e da escola) e avisa a família quando ela
  sai de casa, chega à escola, sai da escola e chega em casa;
- estima quanto falta para chegar ao destino pelo caminho nas ruas;
- avisa bateria fraca e falta de sinal durante o trajeto;
- se a criança está a bordo de uma viagem da van e o GPS do motorista parou, usa
  o celular dela como posição da van (ela está dentro da van).

As posições ficam 24 h e só a família daquela criança as vê."""
from __future__ import annotations

import json
from datetime import datetime

import networkx as nx

from ..cenario import s_para_hhmm
from ..rede import IndiceNos
from .plano import distancia_m

RAIO_CASA_M = 150          # o ponto de casa é o cruzamento mais próximo, não a porta
RAIO_ESCOLA_M = 200
HISTERESE_M = 60           # para sair de uma zona é preciso se afastar um pouco mais
PRECISAO_MAX_M = 200       # leituras piores que isto são descartadas
PRECISAO_ZONA_M = 100      # só leituras boas mudam a zona
SEM_SINAL_S = 10 * 60      # sem posição há 10 min no meio do caminho: avisa
BATERIA_FRACA = 15         # %
ANTES_ENTRADA_S, DEPOIS_ENTRADA_S = 2 * 3600, 30 * 60
ANTES_SAIDA_S, DEPOIS_SAIDA_S = 30 * 60, 2 * 3600
TRILHA_S = 3 * 3600


class CelularMixin:
    """Métodos da Central para o celular da criança (ver Central em servico.py)."""

    # ------------------------------------------------------------ dados da criança
    def _crianca_e_escola(self, linha_id: str, k: str):
        cen = json.loads(self._linha(linha_id)["cenario"])
        c = next((c for c in cen["criancas"] if c["id"] == k), None)
        e = next((e for e in cen["escolas"] if c and e["id"] == c["escola"]), None)
        return c, e

    def janela_ativa(self, linha_id: str, k: str, epoch: float | None = None) -> bool:
        """Dentro do horário de ida/volta da escola (dias úteis), ou sempre."""
        if self.opcoes(linha_id).get("janela_celular") == "sempre":
            return True
        _, e = self._crianca_e_escola(linha_id, k)
        if e is None:
            return False
        d = self._local(epoch)
        if d.weekday() >= 5:
            return False
        s = self._seg_do_dia(epoch)
        return (e["entrada"] - ANTES_ENTRADA_S <= s <= e["entrada"] + DEPOIS_ENTRADA_S
                or e["saida"] - ANTES_SAIDA_S <= s <= e["saida"] + DEPOIS_SAIDA_S)

    def _zona(self, c, e, lat, lon, atual: str | None) -> str:
        dc = distancia_m(lat, lon, c["lat"], c["lon"])
        de = distancia_m(lat, lon, e["lat"], e["lon"]) if e else 1e9
        if dc <= RAIO_CASA_M or (atual == "casa" and dc <= RAIO_CASA_M + HISTERESE_M):
            return "casa"
        if de <= RAIO_ESCOLA_M or (atual == "escola" and de <= RAIO_ESCOLA_M + HISTERESE_M):
            return "escola"
        return "fora"

    # ------------------------------------------------------------ recepção (Traccar Client)
    def receber_celular(self, device_id: str, pontos: list[dict]) -> dict:
        """pontos: [{ts (epoch s), lat, lon, precisao?, bateria? (%), velocidade? (m/s)}]."""
        a = self.db.um("SELECT * FROM acessos WHERE token=? AND papel='celular' AND revogado_em IS NULL",
                       (device_id,))
        if a is None:
            from .servico import ErroRastreio
            raise ErroRastreio("Celular não cadastrado.", 404)
        lid, k = a["linha_id"], a["ref"]
        agora = self.relogio()
        aceitas, fora, ruins = [], 0, 0
        for p in sorted(pontos, key=lambda p: p.get("ts") or agora):
            ts = min(float(p.get("ts") or agora), agora)
            if p.get("precisao") is not None and float(p["precisao"]) > PRECISAO_MAX_M:
                ruins += 1
                continue
            if not self.janela_ativa(lid, k, ts):
                fora += 1
                continue
            aceitas.append((lid, k, ts, float(p["lat"]), float(p["lon"]), p.get("precisao"),
                            p.get("bateria"), p.get("velocidade")))
        if aceitas:
            self.db.muitos("INSERT INTO posicoes_cel VALUES (?,?,?,?,?,?,?,?)", aceitas)
            for x in aceitas:
                self._atualizar_zona(lid, k, x[2], x[3], x[4], x[5], x[6])
            self._alimentar_van(lid, k, aceitas[-1])
        return {"recebidas": len(aceitas), "fora_do_horario": fora, "imprecisas": ruins}

    def _estado_cel(self, lid, k):
        return self.db.um("SELECT * FROM estado_cel WHERE linha_id=? AND crianca=?", (lid, k))

    def _aviso_cel(self, lid, k, tipo, texto, epoch):
        self._evento(lid, None, k, tipo, None, s_para_hhmm(self._seg_do_dia(epoch)), texto, "celular")

    def _atualizar_zona(self, lid, k, ts, lat, lon, precisao, bateria):
        c, e = self._crianca_e_escola(lid, k)
        if c is None:
            return
        st = self._estado_cel(lid, k)
        atual = st["zona"] if st else None
        avisos = set(json.loads(st["avisos"])) if st else set()
        hoje = self._local(ts).strftime("%Y-%m-%d")
        if bateria is not None and float(bateria) <= BATERIA_FRACA and f"bat:{hoje}" not in avisos:
            self._aviso_cel(lid, k, "cel_bateria", f"Bateria do celular em {int(float(bateria))}% — "
                                                   "lembre de carregar", ts)
            avisos.add(f"bat:{hoje}")
        nova = atual
        if precisao is None or float(precisao) <= PRECISAO_ZONA_M or atual is None:
            nova = self._zona(c, e, lat, lon, atual)
        hora = s_para_hhmm(self._seg_do_dia(ts))
        if st is None:
            self.db.x("INSERT INTO estado_cel VALUES (?,?,?,?,?,?)", (lid, k, nova, None, ts, json.dumps([])))
            return
        if nova != atual:
            textos = {
                ("casa", "fora"): f"Saiu de casa às {hora}",
                ("escola", "fora"): f"Saiu da escola às {hora}",
                ("fora", "escola"): f"Chegou à escola às {hora}",
                ("casa", "escola"): f"Chegou à escola às {hora}",
                ("fora", "casa"): f"Chegou em casa às {hora}",
                ("escola", "casa"): f"Chegou em casa às {hora}",
            }
            self._aviso_cel(lid, k, "cel_zona", textos[(atual, nova)] + " (pelo celular)", ts)
            avisos = {x for x in avisos if not x.startswith("sinal")}
            self.db.x("UPDATE estado_cel SET zona=?, zona_anterior=?, desde=?, avisos=? WHERE linha_id=? AND crianca=?",
                      (nova, atual, ts, json.dumps(sorted(avisos)), lid, k))
        else:
            self.db.x("UPDATE estado_cel SET avisos=? WHERE linha_id=? AND crianca=?",
                      (json.dumps(sorted(avisos)), lid, k))

    def verificar_celulares(self) -> None:
        """Chamado periodicamente: avisa se, no meio do caminho, o celular parou de mandar posição."""
        agora = self.relogio()
        for st in self.db.q("SELECT * FROM estado_cel WHERE zona='fora'"):
            lid, k = st["linha_id"], st["crianca"]
            ult = self.db.um("SELECT MAX(epoch) e FROM posicoes_cel WHERE linha_id=? AND crianca=?", (lid, k))
            if not ult or ult["e"] is None or not self.janela_ativa(lid, k, agora):
                continue
            avisos = set(json.loads(st["avisos"]))
            chave = f"sinal:{int(st['desde'])}"
            if agora - ult["e"] > SEM_SINAL_S and chave not in avisos:
                self._aviso_cel(lid, k, "cel_sem_sinal",
                                f"Sem sinal do celular há {int((agora - ult['e']) // 60)} min, no meio do caminho "
                                f"(última posição às {s_para_hhmm(self._seg_do_dia(ult['e']))})", agora)
                avisos.add(chave)
                self.db.x("UPDATE estado_cel SET avisos=? WHERE linha_id=? AND crianca=?",
                          (json.dumps(sorted(avisos)), lid, k))

    def _alimentar_van(self, lid, k, ponto) -> None:
        """A criança está a bordo e o GPS do motorista parou: o celular dela vira a posição da van."""
        v = self._viagem_ativa(lid, crianca=k)
        if v is None or v["simulacao"] or self._status_criancas(v).get(k) != "a_bordo":
            return
        est = self._estimador(v)
        if est.t_pos is not None and self.relogio_plano(v) - est.t_pos < 30:
            return
        self._registrar_posicoes(v, [{"lat": ponto[3], "lon": ponto[4], "precisao": ponto[5], "ts": ponto[2]}])

    # ------------------------------------------------------------ tela da família
    def _tempo_pelas_ruas(self, lat, lon, lat2, lon2, fator: float) -> float:
        if self.G is None:
            return 1.4 * distancia_m(lat, lon, lat2, lon2) / (25 / 3.6)
        if self._idx is None:
            self._idx = IndiceNos(self.G)
        a, _ = self._idx.mais_proximo(lat, lon)
        b, _ = self._idx.mais_proximo(lat2, lon2)
        try:
            return float(nx.shortest_path_length(self.G, a, b, weight="travel_time")) * fator
        except nx.NetworkXNoPath:
            return 1.4 * distancia_m(lat, lon, lat2, lon2) / (25 / 3.6)

    def estado_celular(self, lid: str, k: str) -> dict | None:
        a = self.db.um("SELECT token FROM acessos WHERE linha_id=? AND papel='celular' AND ref=? "
                       "AND revogado_em IS NULL", (lid, k))
        if a is None:
            return None
        agora = self.relogio()
        out = {"configurado": True, "janela_ativa": self.janela_ativa(lid, k, agora),
               "modo": self.opcoes(lid).get("janela_celular"), "pos": None}
        desde = agora - TRILHA_S
        inicio_dia = datetime.fromtimestamp(agora, self._local().tzinfo).replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp()
        pts = self.db.q("SELECT epoch, lat, lon, precisao, bateria FROM posicoes_cel WHERE linha_id=? AND crianca=? "
                        "AND epoch >= ? ORDER BY epoch", (lid, k, max(desde, inicio_dia)))
        if not pts:
            ult = self.db.um("SELECT MAX(epoch) e FROM posicoes_cel WHERE linha_id=? AND crianca=?", (lid, k))
            out["ultima_hora"] = s_para_hhmm(self._seg_do_dia(ult["e"])) if ult and ult["e"] else None
            return out
        p = pts[-1]
        passo = max(1, len(pts) // 300)
        st = self._estado_cel(lid, k)
        out.update({
            "pos": [p["lat"], p["lon"]], "idade_s": round(agora - p["epoch"]), "precisao": p["precisao"],
            "bateria": p["bateria"], "hora": s_para_hhmm(self._seg_do_dia(p["epoch"])),
            "trilha": [[x["lat"], x["lon"]] for x in pts[::passo]] + [[p["lat"], p["lon"]]],
            "zona": st["zona"] if st else None,
            "zona_desde": s_para_hhmm(self._seg_do_dia(st["desde"])) if st and st["desde"] else None,
        })
        if st and st["zona"] == "fora" and agora - p["epoch"] < 5 * 60:
            c, e = self._crianca_e_escola(lid, k)
            destino = "escola" if st["zona_anterior"] in (None, "casa") else "casa"
            alvo = (e["lat"], e["lon"]) if destino == "escola" and e else (c["lat"], c["lon"])
            fator = float(json.loads(self._linha(lid)["cenario"])["params"].get("fator_transito", 1.25))
            seg = self._tempo_pelas_ruas(p["lat"], p["lon"], *alvo, fator)
            out.update({"destino": destino, "faltam_min": max(1, round(seg / 60)),
                        "previsto": s_para_hhmm(self._seg_do_dia(agora + seg))})
        return out
