# -*- coding: utf-8 -*-
"""Banco de dados do acompanhamento (SQLite, um arquivo só).

O que é guardado — e por quanto tempo — foi decidido pensando na LGPD:
- crianças aparecem só pelo código (C01, C02…); o nome nunca chega ao servidor
  (os pais podem dar um apelido que fica só no próprio celular);
- a posição da van só é registrada durante uma viagem iniciada pelo motorista;
- a trilha de posições é apagada RETENCAO_TRILHA_H horas depois do fim da viagem
  (a do celular da criança, RETENCAO_TRILHA_H horas depois de recebida);
- o celular da criança só tem a posição guardada nos horários de ida e volta da
  escola, a menos que a família escolha "sempre";
- o histórico de embarques/desembarques fica RETENCAO_EVENTOS_D dias (prestação
  de contas às famílias) e depois é apagado;
- o acesso dos pais exige aceitar o termo de consentimento e pode ser revogado."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

RETENCAO_TRILHA_H = 24
RETENCAO_EVENTOS_D = 30

ESQUEMA = """
CREATE TABLE IF NOT EXISTS linhas (
    id TEXT PRIMARY KEY,
    nome TEXT NOT NULL,
    cenario TEXT NOT NULL,          -- JSON: escolas, crianças (código + cruzamento), vans, parâmetros
    atribuicao TEXT,                -- JSON: {sentido: {crianca: van}}
    atualizada_em REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS acessos (
    token TEXT PRIMARY KEY,
    linha_id TEXT NOT NULL,
    papel TEXT NOT NULL,            -- 'motorista' | 'responsavel'
    ref TEXT NOT NULL,              -- van (motorista) ou criança (responsável)
    criado_em REAL NOT NULL,
    consentimento_em REAL,
    revogado_em REAL
);
CREATE TABLE IF NOT EXISTS viagens (
    id TEXT PRIMARY KEY,
    linha_id TEXT NOT NULL,
    van TEXT NOT NULL,
    sentido TEXT NOT NULL,
    data TEXT NOT NULL,
    estado TEXT NOT NULL,           -- 'em_andamento' | 'encerrada'
    inicio_epoch REAL NOT NULL,
    fim_epoch REAL,
    desloc_s REAL NOT NULL DEFAULT 0,   -- relógio do plano = relógio local - desloc_s
    simulacao INTEGER NOT NULL DEFAULT 0,
    mult REAL NOT NULL DEFAULT 1,
    t0_plano REAL,                  -- simulação: hora do plano no início
    plano TEXT NOT NULL,
    alertas TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS posicoes (
    viagem_id TEXT NOT NULL,
    t REAL NOT NULL,                -- relógio do plano (s desde 00:00)
    lat REAL NOT NULL, lon REAL NOT NULL,
    precisao REAL,
    epoch REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_pos ON posicoes(viagem_id, t);
CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    linha_id TEXT NOT NULL,
    data TEXT NOT NULL,
    viagem_id TEXT,
    crianca TEXT,                   -- NULL = evento da viagem toda
    tipo TEXT NOT NULL,
    t REAL,                         -- relógio do plano
    hora TEXT,                      -- hora para mostrar (HH:MM)
    texto TEXT,
    origem TEXT,                    -- 'motorista' | 'responsavel' | 'sistema' | 'simulacao'
    epoch REAL NOT NULL,
    anulado INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_ev ON eventos(linha_id, data, crianca);
CREATE TABLE IF NOT EXISTS posicoes_cel (       -- celular da criança (Traccar Client)
    linha_id TEXT NOT NULL,
    crianca TEXT NOT NULL,
    epoch REAL NOT NULL,
    lat REAL NOT NULL, lon REAL NOT NULL,
    precisao REAL, bateria REAL, velocidade REAL
);
CREATE INDEX IF NOT EXISTS ix_pcel ON posicoes_cel(linha_id, crianca, epoch);
CREATE TABLE IF NOT EXISTS estado_cel (
    linha_id TEXT NOT NULL,
    crianca TEXT NOT NULL,
    zona TEXT,                      -- 'casa' | 'escola' | 'fora'
    zona_anterior TEXT,
    desde REAL,
    avisos TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (linha_id, crianca)
);
CREATE TABLE IF NOT EXISTS ausencias (
    linha_id TEXT NOT NULL,
    data TEXT NOT NULL,
    crianca TEXT NOT NULL,
    sentido TEXT NOT NULL,          -- 'ida' | 'volta'
    criado_em REAL NOT NULL,
    PRIMARY KEY (linha_id, data, crianca, sentido)
);
"""


class Armazem:
    def __init__(self, caminho: str | Path = ":memory:"):
        self.caminho = str(caminho)
        if self.caminho != ":memory:":
            Path(self.caminho).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(self.caminho, check_same_thread=False, isolation_level=None)
        self._con.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            if self.caminho != ":memory:":
                self._con.execute("PRAGMA journal_mode=WAL")
            self._con.executescript(ESQUEMA)
            # bancos criados por versões anteriores
            cols = {r[1] for r in self._con.execute("PRAGMA table_info(linhas)")}
            if "opcoes" not in cols:
                self._con.execute("ALTER TABLE linhas ADD COLUMN opcoes TEXT")

    # ------------------------------------------------------------ básico
    def q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self._lock:
            return self._con.execute(sql, args).fetchall()

    def um(self, sql: str, args=()):
        r = self.q(sql, args)
        return r[0] if r else None

    def x(self, sql: str, args=()) -> int:
        with self._lock:
            cur = self._con.execute(sql, args)
            return cur.lastrowid

    def muitos(self, sql: str, linhas) -> None:
        with self._lock:
            self._con.execute("BEGIN")
            self._con.executemany(sql, linhas)
            self._con.execute("COMMIT")

    # ------------------------------------------------------------ retenção
    def limpar(self, agora: float | None = None) -> dict:
        agora = agora or time.time()
        lim_trilha = agora - RETENCAO_TRILHA_H * 3600
        lim_ev = agora - RETENCAO_EVENTOS_D * 86400
        with self._lock:
            n1 = self._con.execute(
                "DELETE FROM posicoes WHERE viagem_id IN "
                "(SELECT id FROM viagens WHERE estado='encerrada' AND fim_epoch < ?)", (lim_trilha,)).rowcount
            n2 = self._con.execute("DELETE FROM eventos WHERE epoch < ?", (lim_ev,)).rowcount
            n3 = self._con.execute("DELETE FROM viagens WHERE estado='encerrada' AND fim_epoch < ?", (lim_ev,)).rowcount
            n4 = self._con.execute("DELETE FROM ausencias WHERE criado_em < ?", (lim_ev,)).rowcount
            n5 = self._con.execute("DELETE FROM posicoes_cel WHERE epoch < ?", (lim_trilha,)).rowcount
        return {"posicoes": n1, "eventos": n2, "viagens": n3, "ausencias": n4, "posicoes_celular": n5}


def js(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
