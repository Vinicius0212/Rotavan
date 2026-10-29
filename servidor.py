# -*- coding: utf-8 -*-
"""RotaVan Acompanhamento — servidor web (FastAPI).

Rodar:   python servidor.py            (abre em http://localhost:8000)
         python servidor.py --tunel    (cria também um endereço https temporário)

Telas (abrem no navegador do celular, sem instalar nada da loja):
  /m/<código>   motorista: inicia o trajeto, envia o GPS, marca embarque/desembarque
  /r/<código>   responsável: vê a van, a previsão de chegada e os avisos
  /traccar      recebe a posição do celular da criança (aplicativo Traccar Client)
  /admin        cadastro rápido de uma família (casa, escola, horários) e links

Os códigos são gerados na aba "3 · Acompanhar ao vivo" do RotaVan (app.py).

Atenção: o navegador só libera o GPS em páginas https (ou no próprio
computador, em localhost). Para usar no celular, rode abrir_rastreio.bat, que
cria um endereço https temporário (Cloudflare Tunnel)."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import base64
import json
import secrets
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from rotavan.rastreio.armazem import Armazem
from rotavan.rastreio.servico import Central, ErroRastreio

PASTA = Path(__file__).resolve().parent
WEB = PASTA / "rotavan" / "rastreio" / "web"


# ------------------------------------------------------------------ corpos das requisições
class Iniciar(BaseModel):
    sentido: str
    simulacao: bool = False
    mult: float = Field(1.0, ge=1.0, le=30.0)


class Ponto(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    precisao: float | None = None
    ts: float | None = None            # epoch (s) em que o celular leu a posição


class Posicoes(BaseModel):
    pontos: list[Ponto] = Field(max_length=2000)


class Marcar(BaseModel):
    crianca: str
    tipo: str


class Crianca(BaseModel):
    crianca: str


class Encerrar(BaseModel):
    conferi_a_van: bool = False


class Consentimento(BaseModel):
    aceito: bool


class Falta(BaseModel):
    sentidos: list[str]
    desfazer: bool = False


class Familia(BaseModel):
    linha_id: str | None = None
    nome: str = Field(min_length=1, max_length=80)
    criancas: list[dict] = Field(min_length=1, max_length=30)
    garagem: dict | None = None
    capacidade: int = Field(15, ge=1, le=60)
    janela_celular: str = "aula"


# ------------------------------------------------------------------ Traccar Client
def _epoch(valor) -> float | None:
    """timestamp do OsmAnd: segundos, milissegundos ou ISO 8601."""
    if valor in (None, ""):
        return None
    try:
        v = float(valor)
        return v / 1000 if v > 1e11 else v
    except (TypeError, ValueError):
        txt = str(valor).replace("Z", "+00:00")
        try:
            d = datetime.fromisoformat(txt)
        except ValueError:
            d = datetime.strptime(txt, "%Y-%m-%d %H:%M:%S")
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()


def ler_traccar(corpo: bytes, tipo: str, query: dict) -> tuple[str | None, list[dict]]:
    """Aceita os dois formatos do protocolo OsmAnd usado pelo Traccar Client:
    parâmetros na URL/formulário (id, lat, lon, timestamp, accuracy, batt, speed em nós)
    ou JSON ({"device_id", "location": {"timestamp", "coords": {...}, "battery": {"level"}}})."""
    if tipo.startswith("application/json") and corpo:
        raiz = json.loads(corpo)
        locs = raiz.get("location")
        locs = locs if isinstance(locs, list) else [locs] if locs else []
        pontos = []
        for loc in locs:
            co = loc.get("coords") or {}
            bat = (loc.get("battery") or {}).get("level")
            pontos.append({"ts": _epoch(loc.get("timestamp")), "lat": co.get("latitude"), "lon": co.get("longitude"),
                           "precisao": co.get("accuracy"), "velocidade": co.get("speed"),
                           "bateria": None if bat is None or bat < 0 else round(bat * 100)})
        return raiz.get("device_id"), [p for p in pontos if p["lat"] is not None and p["lon"] is not None]
    q = dict(query)
    if not q.get("lat") and corpo:
        from urllib.parse import parse_qsl
        q.update(dict(parse_qsl(corpo.decode("ascii", "ignore"))))
    dev = q.get("id") or q.get("deviceid")
    lat, lon = q.get("lat"), q.get("lon")
    if (lat is None or lon is None) and q.get("location"):
        lat, lon = q["location"].split(",")[:2]
    if lat is None or lon is None:
        return dev, []
    vel = q.get("speed")
    return dev, [{"ts": _epoch(q.get("timestamp")), "lat": float(lat), "lon": float(lon),
                  "precisao": float(q["accuracy"]) if q.get("accuracy") else None,
                  "bateria": float(q["batt"]) if q.get("batt") else None,
                  "velocidade": float(vel) * 0.514444 if vel else None}]


# ------------------------------------------------------------------ aplicação
def criar_app(central: Central, simular_em_segundo_plano: bool = True) -> FastAPI:
    app = FastAPI(title="RotaVan Acompanhamento", docs_url=None, redoc_url=None)
    app.state.central = central

    @app.middleware("http")
    async def cabecalhos(request: Request, call_next):
        resp = await call_next(request)
        # o código de acesso está no endereço: não pode vazar para os servidores do mapa
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.exception_handler(ErroRastreio)
    async def _erro(_req, exc: ErroRastreio):
        return JSONResponse({"erro": str(exc), **exc.extra}, status_code=exc.status)

    # --------------------------------------------------------- páginas
    @app.get("/")
    def inicio():
        return FileResponse(WEB / "index.html")

    @app.get("/m/{token}")
    def pagina_motorista(token: str):
        return FileResponse(WEB / "motorista.html")

    @app.get("/r/{token}")
    def pagina_responsavel(token: str):
        return FileResponse(WEB / "responsavel.html")

    @app.get("/sw.js")
    def service_worker():
        return FileResponse(WEB / "sw.js", media_type="text/javascript",
                            headers={"Service-Worker-Allowed": "/"})

    @app.get("/manifest.webmanifest")
    def manifesto():
        return FileResponse(WEB / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/manifest/{papel}/{token}")
    def manifesto_pessoal(papel: str, token: str):
        """Ao "Adicionar à tela inicial", o atalho abre direto no link da pessoa."""
        if papel not in ("m", "r"):
            return JSONResponse({"erro": "papel inválido"}, status_code=404)
        return JSONResponse({
            "name": "RotaVan " + ("Motorista" if papel == "m" else "Família"), "short_name": "RotaVan",
            "start_url": f"/{papel}/{token}", "scope": "/", "display": "standalone",
            "background_color": "#f4f3ef", "theme_color": "#f2b705",
            "icons": [{"src": "/static/icone-192.png", "sizes": "192x192", "type": "image/png"},
                      {"src": "/static/icone-512.png", "sizes": "512x512", "type": "image/png"}]},
            media_type="application/manifest+json")

    app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")

    # --------------------------------------------------------- API do motorista
    @app.get("/api/m/{token}/estado")
    def m_estado(token: str):
        return central.estado_motorista(token)

    @app.post("/api/m/{token}/iniciar")
    def m_iniciar(token: str, corpo: Iniciar):
        return central.iniciar(token, corpo.sentido, corpo.simulacao, corpo.mult)

    @app.post("/api/m/{token}/posicoes")
    def m_posicoes(token: str, corpo: Posicoes):
        return central.posicoes(token, [p.model_dump() for p in corpo.pontos])

    @app.post("/api/m/{token}/marcar")
    def m_marcar(token: str, corpo: Marcar):
        return central.marcar(token, corpo.crianca, corpo.tipo)

    @app.post("/api/m/{token}/desfazer")
    def m_desfazer(token: str, corpo: Crianca):
        return central.desfazer(token, corpo.crianca)

    @app.post("/api/m/{token}/encerrar")
    def m_encerrar(token: str, corpo: Encerrar):
        return central.encerrar(token, corpo.conferi_a_van)

    # --------------------------------------------------------- API do responsável
    @app.get("/api/r/{token}/estado")
    def r_estado(token: str):
        return central.estado_responsavel(token)

    @app.post("/api/r/{token}/consentimento")
    def r_consentimento(token: str, corpo: Consentimento):
        return central.consentir(token, corpo.aceito)

    @app.post("/api/r/{token}/falta")
    def r_falta(token: str, corpo: Falta):
        return central.avisar_falta(token, corpo.sentidos, corpo.desfazer)

    @app.get("/api/saude")
    def saude():
        return {"ok": True, "hora": time.strftime("%H:%M:%S")}

    # --------------------------------------------------------- celular da criança (Traccar Client)
    async def _traccar(request: Request):
        corpo = await request.body()
        dev, pontos = ler_traccar(corpo, request.headers.get("content-type", ""), dict(request.query_params))
        if not dev:
            return PlainTextResponse("faltou o identificador do celular (id)", status_code=400)
        if pontos:
            central.receber_celular(str(dev), pontos)
        return PlainTextResponse("OK")

    app.add_api_route("/traccar", _traccar, methods=["GET", "POST"])

    @app.post("/")
    async def traccar_na_raiz(request: Request):
        """Se o endereço do servidor for configurado sem /traccar no aplicativo."""
        return await _traccar(request)

    # --------------------------------------------------------- administração (cadastro rápido)
    senha_admin = os.environ.get("ROTAVAN_ADMIN_SENHA", "")

    def _admin(request: Request):
        dada = request.headers.get("x-admin-senha", "")
        if senha_admin:
            if not secrets.compare_digest(dada.encode(), senha_admin.encode()):
                raise ErroRastreio("Senha de administração incorreta.", 401)
        elif (request.client.host if request.client else "") not in ("127.0.0.1", "::1", "localhost", "testclient"):
            raise ErroRastreio("Defina ROTAVAN_ADMIN_SENHA para administrar pela internet.", 403)

    def _base(request: Request) -> str:
        return str(request.base_url).rstrip("/")

    def _pacote(request: Request, linha_id: str) -> dict:
        from rotavan.rastreio.cartoes import qr_png
        from rotavan.rastreio.configuracao import exportar
        base = _base(request)
        cfg = exportar(central, linha_id)
        def qr(t):
            return "data:image/png;base64," + base64.b64encode(qr_png(t, 5)).decode()
        acessos = []
        for a in central.links(linha_id):
            if a["papel"] == "celular":
                url = f"{base}/traccar"
                acessos.append({**a, "url": url, "qr": qr(url), "identificador": a["token"]})
            else:
                url = f"{base}/{'m' if a['papel'] == 'motorista' else 'r'}/{a['token']}"
                acessos.append({**a, "url": url, "qr": qr(url)})
        return {"linha_id": linha_id, "nome": cfg["nome"], "opcoes": cfg["opcoes"], "acessos": acessos,
                "config": json.dumps(cfg, ensure_ascii=False, separators=(",", ":"))}

    @app.get("/admin")
    def pagina_admin():
        return FileResponse(WEB / "admin.html")

    @app.get("/api/admin/escolas")
    def admin_escolas(request: Request):
        _admin(request)
        from rotavan.cenario import ler_escolas_osm
        arq = Path(os.environ.get("ROTAVAN_ESCOLAS", PASTA / "dados" / "escolas_ijui_osm.csv"))
        if not arq.exists():
            return []
        df = ler_escolas_osm(arq, raio_km=8)
        return [{"nome": r["name"], "lat": r["lat"], "lon": r["lon"]} for _, r in df.sort_values("name").iterrows()]

    @app.get("/api/admin/linhas")
    def admin_linhas(request: Request):
        _admin(request)
        return [_pacote(request, l["id"]) for l in central.linhas()]

    @app.post("/api/admin/familia")
    def admin_familia(request: Request, corpo: Familia):
        _admin(request)
        from rotavan.rastreio.configuracao import montar_cenario_familia
        if corpo.janela_celular not in ("aula", "sempre"):
            raise ErroRastreio("janela_celular deve ser 'aula' ou 'sempre'.")
        cen = montar_cenario_familia(corpo.model_dump(), central.G)
        pub = central.publicar_linha(corpo.nome, cen, linha_id=corpo.linha_id,
                                     opcoes={"janela_celular": corpo.janela_celular})
        return _pacote(request, pub["linha_id"])

    # --------------------------------------------------------- tarefas em segundo plano
    if simular_em_segundo_plano:
        def laco():
            n = 0
            while True:
                try:
                    central.passo_simulacao()
                    n += 1
                    if n % 60 == 0:
                        central.verificar_celulares()
                    if n % 3600 == 0:
                        central.db.limpar()
                except Exception as e:      # não derruba o servidor por causa disso
                    print("tarefa periódica:", e)
                time.sleep(1.0)
        threading.Thread(target=laco, daemon=True).start()
    return app


def central_padrao() -> Central:
    from rotavan.rede import carregar_grafo
    mapa = Path(os.environ.get("ROTAVAN_MAPA", PASTA / "dados" / "ijui_drive.graphml"))
    banco = Path(os.environ.get("ROTAVAN_BANCO", PASTA / "dados" / "rastreio.db"))
    if not mapa.exists():
        raise SystemExit(f"Mapa não encontrado em {mapa}. Rode antes: python baixar_mapa_ijui.py")
    print("Carregando o mapa de Ijuí…")
    central = Central(Armazem(banco), carregar_grafo(mapa))
    from rotavan.rastreio.configuracao import aplicar_do_ambiente
    n = aplicar_do_ambiente(central, os.environ.get("ROTAVAN_CONFIG"))
    if n:
        print(f"Configuração do ambiente aplicada ({n} linha(s)).")
    return central


if __name__ == "__main__":
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser(description="Servidor de acompanhamento ao vivo do RotaVan")
    ap.add_argument("--porta", type=int, default=int(os.environ.get("PORT", 8000)))
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--tunel", action="store_true", help="cria um endereço https temporário (cloudflared)")
    a = ap.parse_args()
    app = criar_app(central_padrao())
    print(f"Pronto. No computador: http://localhost:{a.porta}")
    if a.tunel:
        from rotavan.rastreio.tunel import abrir_tunel
        abrir_tunel(a.porta, PASTA / "dados" / "endereco_publico.txt", PASTA)
    # atrás do proxy https da hospedagem: confiar nos cabeçalhos X-Forwarded-* para montar os links
    uvicorn.run(app, host=a.host, port=a.porta, log_level="warning", proxy_headers=True, forwarded_allow_ips="*")
