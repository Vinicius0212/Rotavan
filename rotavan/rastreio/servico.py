# -*- coding: utf-8 -*-
"""Regras do acompanhamento ao vivo (independentes da parte web).

Papéis:
- operador (dono da van): publica a "linha" a partir do RotaVan e entrega os links;
- motorista: inicia o trajeto, o celular dele envia a posição, marca quem subiu/desceu;
- responsável: vê a van no mapa enquanto o filho está no trajeto, a previsão de
  chegada, os embarques/desembarques, e avisa quando a criança não vai.

A cada posição recebida o sistema atualiza a previsão de chegada (plano.Estimador)
e gera os avisos: "a van chega em ~5 min", "embarcou", "chegou à escola",
"atraso de ~10 min", "não embarcou". No fim do trajeto o motorista só consegue
encerrar depois de conferir que nenhuma criança ficou marcada como "a bordo"."""
from __future__ import annotations

import json
import secrets
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo

import networkx as nx
import numpy as np

from ..avaliacao import simular
from ..cenario import Cenario, Crianca, Escola, Parametros, Veiculo, s_para_hhmm
from ..heuristicas import HEURISTICAS
from ..instancia import Instancia
from ..modelo import otimizar_com_nova_tentativa
from ..rede import IndiceNos
from .armazem import Armazem, js
from .plano import Estimador, PlanoViagem, criar_plano
from .celular import CelularMixin
from .simulacao import ConfigSimulacao, simular_viagem

FUSO = ZoneInfo("America/Sao_Paulo")
AVISO_PROXIMIDADE_S = 5 * 60          # avisa quando faltar ~5 min
AVISOS_ATRASO_MIN = (10, 20)          # avisa atraso previsto de 10 e de 20 min
FORA_DO_HORARIO_S = 30 * 60           # iniciou >30 min longe do horário planejado: desloca o plano
POSICAO_VELHA_S = 60                  # sem GPS há mais que isto = "sinal perdido"


class ErroRastreio(Exception):
    def __init__(self, msg: str, status: int = 400, extra: dict | None = None):
        super().__init__(msg)
        self.status = status
        self.extra = extra or {}


# ------------------------------------------------------------------ cenário <-> JSON
def cenario_para_dict(cen: Cenario) -> dict:
    return {
        "escolas": [{"id": e.id, "nome": e.nome, "lat": e.lat, "lon": e.lon, "entrada": e.entrada,
                     "saida": e.saida} for e in cen.escolas],
        "criancas": [{"id": c.id, "lat": c.lat, "lon": c.lon, "escola": c.escola,
                      "embarque_mais_cedo": c.embarque_mais_cedo} for c in cen.criancas],
        "veiculos": [{"id": v.id, "capacidade": v.capacidade, "lat": v.lat, "lon": v.lon} for v in cen.veiculos],
        "params": cen.params.dict(),
    }


def cenario_de_dict(d: dict) -> Cenario:
    return Cenario([Escola(**e) for e in d["escolas"]], [Crianca(**c) for c in d["criancas"]],
                   [Veiculo(**v) for v in d["veiculos"]], Parametros(**d["params"]))


def _token() -> str:
    return secrets.token_urlsafe(9)


def _token_curto() -> str:
    """Identificador do celular da criança: digitado à mão no Traccar Client,
    então só letras minúsculas e números, sem caracteres que se confundem."""
    alfabeto = "abcdefghjkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alfabeto) for _ in range(10))


class Central(CelularMixin):
    """Ponto único de acesso às regras. O servidor web só traduz HTTP para cá."""

    def __init__(self, armazem: Armazem, G: nx.MultiDiGraph, relogio=time.time):
        self.db = armazem
        self.G = G
        self.relogio = relogio
        self._idx = None
        self._est: dict[str, Estimador] = {}
        self._sims: dict[str, dict] = {}
        self._lock = threading.RLock()
        # viagens simuladas não sobrevivem a um reinício do servidor
        for v in self.db.q("SELECT id FROM viagens WHERE estado='em_andamento' AND simulacao=1"):
            self.db.x("UPDATE viagens SET estado='encerrada', fim_epoch=? WHERE id=?", (self.relogio(), v["id"]))
        self.db.limpar(self.relogio())

    # ------------------------------------------------------------ relógio
    def _local(self, epoch: float | None = None) -> datetime:
        return datetime.fromtimestamp(epoch if epoch is not None else self.relogio(), FUSO)

    def hoje(self) -> str:
        return self._local().strftime("%Y-%m-%d")

    def _seg_do_dia(self, epoch: float | None = None) -> float:
        d = self._local(epoch)
        return d.hour * 3600 + d.minute * 60 + d.second + d.microsecond / 1e6

    def relogio_plano(self, v) -> float:
        """Hora no 'relógio do plano' de uma viagem (s desde 00:00)."""
        agora = self.relogio()
        if v["simulacao"]:
            return v["t0_plano"] + (agora - v["inicio_epoch"]) * v["mult"]
        return self._seg_do_dia(agora) - v["desloc_s"]

    def hora_exibida(self, v, t_plano: float) -> str:
        return s_para_hhmm(t_plano if v["simulacao"] else t_plano + v["desloc_s"])

    # ------------------------------------------------------------ publicação (operador)
    def publicar_linha(self, nome: str, cen: Cenario, linha_id: str | None = None,
                       atribuicao: dict | None = None, tokens: dict | None = None,
                       opcoes: dict | None = None) -> dict:
        """Cria/atualiza a linha e garante um acesso para cada van (motorista), cada
        criança (família) e o celular de cada criança (Traccar Client).
        Links já entregues continuam valendo; os de crianças removidas são revogados.

        `tokens`: {"papel:ref": token} para fixar códigos (configuração da nuvem,
        que precisa sobreviver a reinícios do servidor).
        `opcoes`: {"janela_celular": "aula" | "sempre"}."""
        erros = cen.validar()
        if erros:
            raise ErroRastreio("; ".join(erros))
        linha_id = linha_id or uuid.uuid4().hex[:10]
        agora = self.relogio()
        dados = cenario_para_dict(cen)
        ant = self.db.um("SELECT opcoes FROM linhas WHERE id=?", (linha_id,))
        opc = {"janela_celular": "aula", **(json.loads(ant["opcoes"] or "{}") if ant else {}), **(opcoes or {})}
        if ant:
            self.db.x("UPDATE linhas SET nome=?, cenario=?, atribuicao=?, atualizada_em=?, opcoes=? WHERE id=?",
                      (nome, js(dados), js(atribuicao or {}), agora, js(opc), linha_id))
        else:
            self.db.x("INSERT INTO linhas (id, nome, cenario, atribuicao, atualizada_em, opcoes) VALUES (?,?,?,?,?,?)",
                      (linha_id, nome, js(dados), js(atribuicao or {}), agora, js(opc)))
        quer = ([("motorista", v.id) for v in cen.veiculos] + [("responsavel", c.id) for c in cen.criancas]
                + [("celular", c.id) for c in cen.criancas])
        existentes = {(r["papel"], r["ref"]): r["token"] for r in self.db.q(
            "SELECT * FROM acessos WHERE linha_id=? AND revogado_em IS NULL", (linha_id,))}
        tokens = tokens or {}
        for chave in quer:
            fixo = tokens.get(f"{chave[0]}:{chave[1]}")
            if chave in existentes and (fixo is None or fixo == existentes[chave]):
                continue
            if chave in existentes:
                self.db.x("UPDATE acessos SET revogado_em=? WHERE token=?", (agora, existentes[chave]))
            tok = fixo or (_token_curto() if chave[0] == "celular" else _token())
            self.db.x("DELETE FROM acessos WHERE token=?", (tok,))
            self.db.x("INSERT INTO acessos (token, linha_id, papel, ref, criado_em) VALUES (?,?,?,?,?)",
                      (tok, linha_id, chave[0], chave[1], agora))
        for chave, tok in existentes.items():
            if chave not in quer:
                self.db.x("UPDATE acessos SET revogado_em=? WHERE token=?", (agora, tok))
        return {"linha_id": linha_id, "acessos": self.links(linha_id), "opcoes": opc}

    def opcoes(self, linha_id: str) -> dict:
        r = self.db.um("SELECT opcoes FROM linhas WHERE id=?", (linha_id,))
        return {"janela_celular": "aula", **json.loads((r and r["opcoes"]) or "{}")}

    def links(self, linha_id: str) -> list[dict]:
        return [dict(r) for r in self.db.q(
            "SELECT token, papel, ref, consentimento_em FROM acessos WHERE linha_id=? AND revogado_em IS NULL "
            "ORDER BY papel, ref", (linha_id,))]

    def novo_link(self, linha_id: str, papel: str, ref: str) -> str:
        """Revoga o link atual (perdido/vazado) e cria outro."""
        agora = self.relogio()
        self.db.x("UPDATE acessos SET revogado_em=? WHERE linha_id=? AND papel=? AND ref=? AND revogado_em IS NULL",
                  (agora, linha_id, papel, ref))
        tok = _token_curto() if papel == "celular" else _token()
        self.db.x("INSERT INTO acessos (token, linha_id, papel, ref, criado_em) VALUES (?,?,?,?,?)",
                  (tok, linha_id, papel, ref, agora))
        return tok

    def linhas(self) -> list[dict]:
        return [dict(r) for r in self.db.q("SELECT id, nome, atualizada_em FROM linhas ORDER BY atualizada_em DESC")]

    # ------------------------------------------------------------ acesso
    def acesso(self, token: str, papel: str):
        a = self.db.um("SELECT * FROM acessos WHERE token=? AND papel=? AND revogado_em IS NULL", (token, papel))
        if a is None:
            raise ErroRastreio("Link inválido ou revogado. Peça um novo ao responsável pela van.", 404)
        return a

    def _linha(self, linha_id: str):
        return self.db.um("SELECT * FROM linhas WHERE id=?", (linha_id,))

    def _viagem_ativa(self, linha_id: str, van: str | None = None, crianca: str | None = None):
        vs = self.db.q("SELECT * FROM viagens WHERE linha_id=? AND estado='em_andamento' ORDER BY inicio_epoch DESC",
                       (linha_id,))
        for v in vs:
            if van is not None and v["van"] == van:
                return v
            if crianca is not None and crianca in PlanoViagem.from_dict(json.loads(v["plano"])).criancas():
                return v
        return None

    def _viagem(self, vid: str):
        return self.db.um("SELECT * FROM viagens WHERE id=?", (vid,))

    # ------------------------------------------------------------ ausências (responsável)
    def ausencias(self, linha_id: str, data: str) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for r in self.db.q("SELECT crianca, sentido FROM ausencias WHERE linha_id=? AND data=?", (linha_id, data)):
            out.setdefault(r["crianca"], []).append(r["sentido"])
        return out

    # ------------------------------------------------------------ eventos
    def _evento(self, linha_id, viagem_id, crianca, tipo, t, hora, texto, origem):
        return self.db.x(
            "INSERT INTO eventos (linha_id, data, viagem_id, crianca, tipo, t, hora, texto, origem, epoch) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (linha_id, self.hoje(), viagem_id, crianca, tipo, t, hora, texto, origem, self.relogio()))

    def _status_criancas(self, v) -> dict[str, str]:
        """aguardando | a_bordo | entregue | ausente, a partir dos eventos da viagem."""
        plano = PlanoViagem.from_dict(json.loads(v["plano"]))
        st = {k: "aguardando" for k in plano.criancas()}
        for e in self.db.q("SELECT crianca, tipo FROM eventos WHERE viagem_id=? AND anulado=0 AND crianca IS NOT NULL "
                           "AND tipo IN ('embarque','desembarque','ausente') ORDER BY id", (v["id"],)):
            st[e["crianca"]] = {"embarque": "a_bordo", "desembarque": "entregue", "ausente": "ausente"}[e["tipo"]]
        return st

    def _pendentes(self, plano: PlanoViagem, status: dict[str, str]) -> dict[int, list[str]]:
        pend = {}
        for p in plano.atendimentos:
            if p.tipo == "embarque":
                ks = [k for k in p.criancas if status.get(k) == "aguardando"]
            else:
                ks = [k for k in p.criancas if status.get(k) in ("aguardando", "a_bordo")]
            if ks:
                pend[p.ordem] = ks
        return pend

    # ------------------------------------------------------------ início da viagem (motorista)
    def _atribuicao(self, linha, cen: Cenario, sentido: str, ausentes: set[str]) -> dict[str, str]:
        atr = json.loads(linha["atribuicao"] or "{}").get(sentido)
        if atr:
            return atr
        if len(cen.veiculos) == 1:
            atr = {c.id: cen.veiculos[0].id for c in cen.criancas}
        else:   # frota: otimiza a frota inteira uma vez e guarda quem vai em qual van
            cen_p = Cenario(cen.escolas, [c for c in cen.criancas], cen.veiculos, cen.params)
            res = otimizar_com_nova_tentativa(Instancia.criar(cen_p, self.G))
            atr = {k: it.veiculo for it in res.itinerarios for pa in it.paradas for k in pa.criancas}
            for c in cen.criancas:              # quem ficou de fora vai para a van menos cheia
                if c.id not in atr:
                    cont = {v.id: list(atr.values()).count(v.id) for v in cen.veiculos}
                    atr[c.id] = min(cont, key=cont.get)
        tudo = json.loads(linha["atribuicao"] or "{}")
        tudo[sentido] = atr
        self.db.x("UPDATE linhas SET atribuicao=? WHERE id=?", (js(tudo), linha["id"]))
        return atr

    def planejar(self, linha_id: str, van: str, sentido: str, data: str | None = None):
        """Otimiza a rota da van para hoje, sem as crianças que avisaram falta."""
        linha = self._linha(linha_id)
        cen = cenario_de_dict(json.loads(linha["cenario"]))
        cen.params.sentido = sentido
        data = data or self.hoje()
        aus = {k for k, ss in self.ausencias(linha_id, data).items() if sentido in ss}
        atr = self._atribuicao(linha, cen, sentido, aus)
        kids = [c for c in cen.criancas if atr.get(c.id) == van and c.id not in aus]
        if not kids:
            raise ErroRastreio("Nenhuma criança para levar neste trajeto hoje.")
        veic = [v for v in cen.veiculos if v.id == van]
        sub = Cenario(cen.escolas, kids, veic, cen.params, descricao=f"{linha['nome']} {van} {sentido} {data}")
        inst = Instancia.criar(sub, self.G)
        res = otimizar_com_nova_tentativa(inst, tempo_limite_s=min(cen.params.tempo_limite_s, 10))
        it = res.itinerarios[0] if res.itinerarios else None
        aviso = None
        if it is None or res.status != "ok":
            # nenhuma rota respeita todas as regras: usa a heurística (leva todo mundo) e avisa
            it = HEURISTICAS["vizinho mais próximo"](inst)
            aviso = "Não há rota que respeite todas as regras hoje; usando a ordem mais próxima possível."
        return inst, criar_plano(inst, it), aviso

    def iniciar(self, token: str, sentido: str, simulacao: bool = False, mult: float = 1.0) -> dict:
        a = self.acesso(token, "motorista")
        if sentido not in ("ida", "volta"):
            raise ErroRastreio("Sentido deve ser 'ida' ou 'volta'.")
        with self._lock:
            if self._viagem_ativa(a["linha_id"], van=a["ref"]):
                raise ErroRastreio("Já existe um trajeto em andamento para esta van.", 409)
            inst, plano, aviso = self.planejar(a["linha_id"], a["ref"], sentido)
            agora = self.relogio()
            desloc, t0 = 0.0, None
            if simulacao:
                t0 = plano.saida
            else:
                dif = self._seg_do_dia(agora) - plano.saida
                if abs(dif) > FORA_DO_HORARIO_S:
                    desloc = dif          # teste fora do horário: o plano "começa agora"
            vid = uuid.uuid4().hex[:12]
            self.db.x("INSERT INTO viagens (id, linha_id, van, sentido, data, estado, inicio_epoch, desloc_s, simulacao,"
                      " mult, t0_plano, plano) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (vid, a["linha_id"], a["ref"], sentido, self.hoje(), "em_andamento", agora, desloc,
                       int(simulacao), float(mult), t0, js(plano.to_dict())))
            v = self._viagem(vid)
            self._est[vid] = self._novo_estimador(plano, inst.p.fator_transito)
            t = self.relogio_plano(v)
            txt = ("A van saiu para buscar as crianças" if sentido == "ida" else "A van saiu para buscar na escola")
            self._evento(a["linha_id"], vid, None, "inicio", t, self.hora_exibida(v, t), txt,
                         "simulacao" if simulacao else "motorista")
            # crianças que avisaram falta: registra para o motorista e para a família
            aus = self.ausencias(a["linha_id"], self.hoje())
            for k, ss in aus.items():
                if sentido in ss:
                    self._evento(a["linha_id"], vid, k, "falta_avisada", t, self.hora_exibida(v, t),
                                 "Família avisou que não vai hoje — fora da rota", "sistema")
            if simulacao:
                sim = simular_viagem(plano, np.random.default_rng(int(agora) % 2**31), ConfigSimulacao(), t_saida=t0)
                self._sims[vid] = {"sim": sim, "i": 0, "j": 0}
            return {"viagem_id": vid, "aviso": aviso, "fora_do_horario": bool(desloc)}

    def _novo_estimador(self, plano: PlanoViagem, fator: float = 1.25) -> Estimador:
        """Se a van sai do trajeto planejado (desvio, rua fechada), a previsão até a
        próxima parada passa a ser calculada pela malha a partir da posição atual."""
        def tempo_ate(lat, lon, no_destino) -> float:
            if self._idx is None:
                self._idx = IndiceNos(self.G)
            no, _ = self._idx.mais_proximo(lat, lon)
            try:
                return float(nx.shortest_path_length(self.G, no, no_destino, weight="travel_time")) * fator
            except nx.NetworkXNoPath:
                return 600.0
        return Estimador(plano, tempo_ate=tempo_ate)

    def _fator(self, linha_id: str) -> float:
        return float(json.loads(self._linha(linha_id)["cenario"])["params"].get("fator_transito", 1.25))

    def _estimador(self, v) -> Estimador:
        """Recupera (ou reconstrói, após reinício do servidor) o estimador da viagem."""
        est = self._est.get(v["id"])
        if est is None:
            est = self._novo_estimador(PlanoViagem.from_dict(json.loads(v["plano"])), self._fator(v["linha_id"]))
            for p in self.db.q("SELECT t, lat, lon, precisao FROM posicoes WHERE viagem_id=? ORDER BY t", (v["id"],)):
                est.atualizar(p["t"], p["lat"], p["lon"], p["precisao"])
            self._est[v["id"]] = est
        return est

    # ------------------------------------------------------------ posição (motorista)
    def posicoes(self, token: str, pontos: list[dict]) -> dict:
        """Recebe uma ou várias posições (o celular junta as que não conseguiu
        enviar quando ficou sem internet)."""
        a = self.acesso(token, "motorista")
        v = self._viagem_ativa(a["linha_id"], van=a["ref"])
        if v is None:
            raise ErroRastreio("Nenhum trajeto em andamento. Toque em Iniciar.", 409)
        return self._registrar_posicoes(v, pontos)

    def _registrar_posicoes(self, v, pontos: list[dict]) -> dict:
        est = self._estimador(v)
        agora_epoch = self.relogio()
        t_agora = self.relogio_plano(v)
        linhas = []
        def ts(p):
            return float(p["ts"]) if p.get("ts") is not None else agora_epoch
        for p in sorted(pontos, key=ts):
            # converte o horário do celular para o relógio do plano
            # (relógio do celular adiantado/atrasado: limita a no máximo "agora")
            t = p["t"] if "t" in p else t_agora - max(0.0, agora_epoch - ts(p)) * v["mult"]
            r = est.atualizar(t, float(p["lat"]), float(p["lon"]), p.get("precisao"))
            if r.get("aceita"):
                linhas.append((v["id"], t, float(p["lat"]), float(p["lon"]), p.get("precisao"), agora_epoch))
        if linhas:
            self.db.muitos("INSERT INTO posicoes VALUES (?,?,?,?,?,?)", linhas)
        self._verificar_avisos(v)
        return {"recebidas": len(linhas), "na_rota": not est.fora_da_rota}

    # ------------------------------------------------------------ embarque/desembarque (motorista)
    def marcar(self, token: str, crianca: str, tipo: str, origem: str = "motorista") -> dict:
        a = self.acesso(token, "motorista")
        v = self._viagem_ativa(a["linha_id"], van=a["ref"])
        if v is None:
            raise ErroRastreio("Nenhum trajeto em andamento.", 409)
        return self._marcar(v, crianca, tipo, origem)

    def _marcar(self, v, crianca: str, tipo: str, origem: str) -> dict:
        if tipo not in ("embarque", "desembarque", "ausente"):
            raise ErroRastreio("Tipo inválido.")
        plano = PlanoViagem.from_dict(json.loads(v["plano"]))
        if crianca not in plano.criancas():
            raise ErroRastreio(f"{crianca} não está neste trajeto.")
        st = self._status_criancas(v)[crianca]
        validos = {"embarque": ("aguardando",), "desembarque": ("a_bordo",), "ausente": ("aguardando",)}
        if st not in validos[tipo]:
            raise ErroRastreio(f"{crianca} está '{st.replace('_', ' ')}'; não dá para marcar {tipo}.", 409)
        t = self.relogio_plano(v)
        hora = self.hora_exibida(v, t)
        esc = plano.parada_de(crianca, "desembarque" if v["sentido"] == "ida" else "embarque")
        nome_esc = esc.nome if esc else "escola"
        textos = {
            ("ida", "embarque"): f"Embarcou na van às {hora}",
            ("ida", "desembarque"): f"Chegou à escola ({nome_esc}) às {hora}",
            ("volta", "embarque"): f"Embarcou na van na saída da escola às {hora}",
            ("volta", "desembarque"): f"Foi entregue em casa às {hora}",
            ("ida", "ausente"): f"Não embarcou: o motorista passou e marcou ausência às {hora}",
            ("volta", "ausente"): f"Não embarcou na saída da escola (marcado às {hora}). Confirme com a escola.",
        }
        eid = self._evento(v["linha_id"], v["id"], crianca, tipo, t, hora, textos[(v["sentido"], tipo)], origem)
        self._verificar_avisos(v)
        return {"evento_id": eid, "texto": textos[(v["sentido"], tipo)]}

    def desfazer(self, token: str, crianca: str) -> dict:
        """Desfaz a última marcação de uma criança (toque errado)."""
        a = self.acesso(token, "motorista")
        v = self._viagem_ativa(a["linha_id"], van=a["ref"])
        if v is None:
            raise ErroRastreio("Nenhum trajeto em andamento.", 409)
        e = self.db.um("SELECT id FROM eventos WHERE viagem_id=? AND crianca=? AND anulado=0 AND "
                       "tipo IN ('embarque','desembarque','ausente') ORDER BY id DESC LIMIT 1", (v["id"], crianca))
        if e is None:
            raise ErroRastreio("Nada para desfazer.")
        self.db.x("UPDATE eventos SET anulado=1 WHERE id=?", (e["id"],))
        return {"ok": True}

    # ------------------------------------------------------------ fim
    def encerrar(self, token: str, conferi_a_van: bool = False) -> dict:
        a = self.acesso(token, "motorista")
        v = self._viagem_ativa(a["linha_id"], van=a["ref"])
        if v is None:
            raise ErroRastreio("Nenhum trajeto em andamento.", 409)
        return self._encerrar(v, conferi_a_van, "motorista")

    def _encerrar(self, v, conferi_a_van: bool, origem: str) -> dict:
        st = self._status_criancas(v)
        a_bordo = [k for k, s in st.items() if s == "a_bordo"]
        if a_bordo:
            raise ErroRastreio("Ainda há criança marcada como A BORDO: " + ", ".join(a_bordo)
                               + ". Confira a van e marque o desembarque.", 409, {"a_bordo": a_bordo})
        if not conferi_a_van:
            raise ErroRastreio("Confirme que conferiu todos os bancos da van antes de encerrar.", 409,
                               {"precisa_conferir": True})
        t = self.relogio_plano(v)
        hora = self.hora_exibida(v, t)
        self._evento(v["linha_id"], v["id"], None, "conferencia", t, hora,
                     "Motorista conferiu a van: nenhuma criança a bordo", origem)
        self._evento(v["linha_id"], v["id"], None, "fim", t, hora, "Trajeto encerrado", origem)
        self.db.x("UPDATE viagens SET estado='encerrada', fim_epoch=? WHERE id=?", (self.relogio(), v["id"]))
        self._est.pop(v["id"], None)
        self._sims.pop(v["id"], None)
        self.db.limpar(self.relogio())
        return {"ok": True}

    # ------------------------------------------------------------ avisos automáticos
    def _verificar_avisos(self, v) -> None:
        v = self._viagem(v["id"])
        if v["estado"] != "em_andamento":
            return
        plano = PlanoViagem.from_dict(json.loads(v["plano"]))
        est = self._estimador(v)
        if est.t_pos is None:
            return
        st = self._status_criancas(v)
        t = self.relogio_plano(v)
        prev = {p.ordem: p for p in est.prever(t, self._pendentes(plano, st))}
        enviados = set(json.loads(v["alertas"]))
        novos = []
        for k, s in st.items():
            # próxima parada que importa para a família: onde a criança será buscada/entregue
            if v["sentido"] == "ida" and s == "aguardando":
                alvo, frase = plano.parada_de(k, "embarque"), "A van deve chegar para buscar em ~{m} min"
            elif v["sentido"] == "volta" and s == "a_bordo":
                alvo, frase = plano.parada_de(k, "desembarque"), "A van deve chegar em casa em ~{m} min"
            else:
                continue
            p = prev.get(alvo.ordem) if alvo else None
            if p is None:
                continue
            falta = p.chegada - t
            chave = f"prox:{k}"
            if falta <= AVISO_PROXIMIDADE_S and chave not in enviados:
                m = max(1, int(round(falta / 60)))
                novos.append((k, "aviso_proximidade", frase.format(m=m), chave))
            for lim in AVISOS_ATRASO_MIN:
                chave = f"atraso{lim}:{k}"
                if p.atraso_s >= lim * 60 and chave not in enviados:
                    novos.append((k, "aviso_atraso", f"Atraso previsto de ~{int(p.atraso_s // 60)} min "
                                                    f"(nova previsão: {self.hora_exibida(v, p.chegada)})", chave))
        for k, tipo, texto, chave in novos:
            self._evento(v["linha_id"], v["id"], k, tipo, t, self.hora_exibida(v, t), texto, "sistema")
            enviados.add(chave)
        if novos:
            self.db.x("UPDATE viagens SET alertas=? WHERE id=?", (js(sorted(enviados)), v["id"]))

    # ------------------------------------------------------------ simulação (demonstração)
    def passo_simulacao(self) -> None:
        """Avança as viagens simuladas até o 'agora' do relógio simulado."""
        for vid in list(self._sims):
            v = self._viagem(vid)
            if v is None or v["estado"] != "em_andamento":
                self._sims.pop(vid, None)
                continue
            s = self._sims[vid]
            sim = s["sim"]
            t = self.relogio_plano(v)
            novos = []
            while s["i"] < len(sim.pontos) and sim.pontos[s["i"]][0] <= t:
                tt, lat, lon = sim.pontos[s["i"]]
                novos.append({"t": tt, "lat": lat, "lon": lon, "precisao": 8.0})
                s["i"] += 1
            if novos:
                self._registrar_posicoes(v, novos)
            while s["j"] < len(sim.eventos) and sim.eventos[s["j"]][0] <= t:
                _, tipo, k = sim.eventos[s["j"]]
                s["j"] += 1
                try:
                    self._marcar(v, k, tipo, "simulacao")
                except ErroRastreio:
                    pass   # o motorista (de verdade) já marcou pela tela
            if s["i"] >= len(sim.pontos) and s["j"] >= len(sim.eventos):
                try:
                    self._encerrar(v, True, "simulacao")
                except ErroRastreio:
                    pass

    # ------------------------------------------------------------ telas
    def estado_motorista(self, token: str) -> dict:
        a = self.acesso(token, "motorista")
        linha = self._linha(a["linha_id"])
        cen = json.loads(linha["cenario"])
        hoje = self.hoje()
        base = {"linha": linha["nome"], "van": a["ref"], "hoje": hoje,
                "ausencias": self.ausencias(a["linha_id"], hoje), "viagem": None}
        v = self._viagem_ativa(a["linha_id"], van=a["ref"])
        if v is None:
            atr = json.loads(linha["atribuicao"] or "{}")
            base["criancas"] = sorted(c["id"] for c in cen["criancas"]
                                      if not atr or any(x.get(c["id"], a["ref"]) == a["ref"] for x in atr.values()))
            return base
        plano = PlanoViagem.from_dict(json.loads(v["plano"]))
        est = self._estimador(v)
        st = self._status_criancas(v)
        t = self.relogio_plano(v)
        prev = {p.ordem: p for p in est.prever(t, self._pendentes(plano, st))}
        paradas = []
        for p in plano.paradas:
            pr = prev.get(p.ordem)
            paradas.append({
                "ordem": p.ordem, "tipo": p.tipo, "nome": p.nome, "lat": p.lat, "lon": p.lon,
                "criancas": [{"id": k, "status": st.get(k)} for k in p.criancas],
                "planejado": self.hora_exibida(v, p.chegada),
                "previsto": self.hora_exibida(v, pr.chegada) if pr else None,
                "atraso_min": round(pr.atraso_s / 60, 1) if pr else None,
                "pendente": pr is not None,
            })
        base["viagem"] = {
            "id": v["id"], "sentido": v["sentido"], "simulacao": bool(v["simulacao"]),
            "fora_do_horario": bool(v["desloc_s"]), "agora": self.hora_exibida(v, t),
            "paradas": paradas, "trajeto": [[p[0], p[1]] for p in plano.trajeto],
            "pos": list(est.pos) if est.pos else None, "na_rota": not est.fora_da_rota,
            "ritmo": round(est.ritmo, 2),
            "eventos": self._eventos(v["linha_id"], viagem_id=v["id"]),
        }
        return base

    def _eventos(self, linha_id: str, viagem_id: str | None = None, crianca: str | None = None,
                 data: str | None = None) -> list[dict]:
        if viagem_id:
            rs = self.db.q("SELECT * FROM eventos WHERE viagem_id=? ORDER BY id DESC", (viagem_id,))
        else:
            # eventos da criança + avisos gerais (início/fim) das viagens em que ela estava
            vids = [v["id"] for v in self.db.q("SELECT id, plano FROM viagens WHERE linha_id=? AND data=?",
                                                (linha_id, data))
                    if crianca in PlanoViagem.from_dict(json.loads(v["plano"])).criancas()]
            marc = ",".join("?" * len(vids)) or "''"
            rs = self.db.q(f"SELECT * FROM eventos WHERE linha_id=? AND data=? AND (crianca=? OR "
                           f"(crianca IS NULL AND viagem_id IN ({marc}))) ORDER BY id DESC",
                           (linha_id, data, crianca, *vids))
        return [{"id": r["id"], "crianca": r["crianca"], "tipo": r["tipo"], "hora": r["hora"], "texto": r["texto"],
                 "anulado": bool(r["anulado"])} for r in rs]

    def estado_responsavel(self, token: str) -> dict:
        a = self.acesso(token, "responsavel")
        k = a["ref"]
        linha = self._linha(a["linha_id"])
        cen = json.loads(linha["cenario"])
        hoje = self.hoje()
        crianca = next((c for c in cen["criancas"] if c["id"] == k), None)
        escola = next((e for e in cen["escolas"] if crianca and e["id"] == crianca["escola"]), None)
        out = {"crianca": k, "linha": linha["nome"], "consentiu": a["consentimento_em"] is not None,
               "ausencias_hoje": self.ausencias(a["linha_id"], hoje).get(k, []),
               "escola": {"nome": escola["nome"], "lat": escola["lat"], "lon": escola["lon"],
                          "entrada": s_para_hhmm(escola["entrada"]), "saida": s_para_hhmm(escola["saida"])}
               if escola else None}
        out["celular_configurado"] = self.db.um(
            "SELECT 1 FROM acessos WHERE linha_id=? AND papel='celular' AND ref=? AND revogado_em IS NULL",
            (a["linha_id"], k)) is not None
        if not out["consentiu"]:
            return out           # nada de localização antes do consentimento
        out["ponto"] = {"lat": crianca["lat"], "lon": crianca["lon"]} if crianca else None
        out["eventos"] = self._eventos(a["linha_id"], crianca=k, data=hoje)
        out["celular"] = self.estado_celular(a["linha_id"], k)
        v = self._viagem_ativa(a["linha_id"], crianca=k)
        out["viagem"] = None
        if v is None:
            return out
        plano = PlanoViagem.from_dict(json.loads(v["plano"]))
        st = self._status_criancas(v)
        s = st.get(k)
        est = self._estimador(v)
        t = self.relogio_plano(v)
        prev = {p.ordem: p for p in est.prever(t, self._pendentes(plano, st))}
        viagem = {"sentido": v["sentido"], "status": s, "simulacao": bool(v["simulacao"]),
                  "agora": self.hora_exibida(v, t)}
        # a família só vê a van enquanto a criança dela está no trajeto
        # (depois da entrega, ver a van revelaria por onde passam as outras crianças)
        if s in ("aguardando", "a_bordo"):
            tipo_alvo = "embarque" if (v["sentido"] == "ida" and s == "aguardando") else "desembarque"
            if v["sentido"] == "volta" and s == "aguardando":
                tipo_alvo = "embarque"
            alvo = plano.parada_de(k, tipo_alvo)
            pr = prev.get(alvo.ordem) if alvo else None
            ultima = self.db.um("SELECT epoch FROM posicoes WHERE viagem_id=? ORDER BY t DESC LIMIT 1", (v["id"],))
            idade = (self.relogio() - ultima["epoch"]) if ultima else None
            viagem.update({
                "van": list(est.pos) if est.pos else None,
                "sem_posicao": idade is None,
                "sinal_perdido": idade is not None and idade > POSICAO_VELHA_S,
                "alvo": {"tipo": tipo_alvo, "nome": "Seu ponto" if alvo and alvo.nome.startswith("Ponto")
                         else (alvo.nome if alvo else None)},
                "previsto": self.hora_exibida(v, pr.chegada) if pr else None,
                "faltam_min": max(0, round((pr.chegada - t) / 60)) if pr else None,
                "planejado": self.hora_exibida(v, alvo.chegada) if alvo else None,
                "atraso_min": round(pr.atraso_s / 60) if pr else None,
            })
        out["viagem"] = viagem
        return out

    # ------------------------------------------------------------ ações do responsável
    def consentir(self, token: str, aceito: bool) -> dict:
        a = self.acesso(token, "responsavel")
        if aceito:
            self.db.x("UPDATE acessos SET consentimento_em=? WHERE token=?", (self.relogio(), token))
        else:   # revogação: o link para de mostrar a localização imediatamente
            self.db.x("UPDATE acessos SET consentimento_em=NULL WHERE token=?", (token,))
        return {"ok": True, "consentiu": aceito}

    def avisar_falta(self, token: str, sentidos: list[str], desfazer: bool = False, data: str | None = None) -> dict:
        a = self.acesso(token, "responsavel")
        if a["consentimento_em"] is None:
            raise ErroRastreio("Aceite o termo antes de usar.", 403)
        data = data or self.hoje()
        for s in sentidos:
            if s not in ("ida", "volta"):
                raise ErroRastreio("Sentido inválido.")
            if desfazer:
                self.db.x("DELETE FROM ausencias WHERE linha_id=? AND data=? AND crianca=? AND sentido=?",
                          (a["linha_id"], data, a["ref"], s))
            else:
                self.db.x("INSERT OR IGNORE INTO ausencias VALUES (?,?,?,?,?)",
                          (a["linha_id"], data, a["ref"], s, self.relogio()))
        txt = ("Família cancelou o aviso de falta" if desfazer else "Família avisou que não vai") + \
              f" ({', '.join(sentidos)})"
        self._evento(a["linha_id"], None, a["ref"], "falta_avisada" if not desfazer else "falta_cancelada",
                     None, s_para_hhmm(self._seg_do_dia()), txt, "responsavel")
        return {"ok": True, "ausencias": self.ausencias(a["linha_id"], data).get(a["ref"], [])}
