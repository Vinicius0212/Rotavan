# -*- coding: utf-8 -*-
"""Teste ponta a ponta das telas do acompanhamento num navegador automatizado.

Não roda com o pytest comum (precisa do mapa real e do Playwright):
    pip install playwright && python -m playwright install chromium
    python tests/e2e_navegador.py

Publica uma linha de exemplo num banco temporário, sobe o servidor, abre a tela
do motorista (modo demonstração, 20x) e a de uma família, aceita o termo e
acompanha a viagem até o fim. Salva capturas de tela em resultados/telas/."""
import sys
import tempfile
import threading
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from rotavan.cenario import Parametros, gerar_cenario, ler_escolas_osm  # noqa: E402
from rotavan.rastreio.armazem import Armazem  # noqa: E402
from rotavan.rastreio.servico import Central  # noqa: E402
from rotavan.rede import carregar_grafo  # noqa: E402
from servidor import criar_app  # noqa: E402

PORTA = 8799


def main():
    G = carregar_grafo(RAIZ / "dados" / "ijui_drive.graphml")
    cen = gerar_cenario(G, 10, 2, 5, escolas_osm=ler_escolas_osm(RAIZ / "dados" / "escolas_ijui_osm.csv"),
                        params=Parametros(tempo_limite_s=5))
    central = Central(Armazem(Path(tempfile.mkdtemp()) / "e2e.db"), G)
    pub = central.publicar_linha("Linha de teste", cen)
    tok = {(a["papel"], a["ref"]): a["token"] for a in pub["acessos"]}
    srv = uvicorn.Server(uvicorn.Config(criar_app(central), port=PORTA, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    time.sleep(2)
    base = f"http://localhost:{PORTA}"
    saida = RAIZ / "resultados" / "telas"
    saida.mkdir(parents=True, exist_ok=True)
    erros = []
    with sync_playwright() as p:
        br = p.chromium.launch()
        ctx = br.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True,
                             has_touch=True, locale="pt-BR")
        pm = ctx.new_page()
        pm.on("pageerror", lambda e: erros.append(str(e)))
        pm.goto(f"{base}/m/{tok[('motorista', cen.veiculos[0].id)]}")
        pm.wait_for_selector("#tela-inicio:not(.oculto)")
        pm.check("#simular"); pm.select_option("#mult", "20")
        pm.click("[data-sentido=ida]")
        pm.wait_for_selector("#tela-viagem:not(.oculto)", timeout=60000)
        ordem = [c["id"] for pa in pm.evaluate("estado")["viagem"]["paradas"] if pa["tipo"] == "embarque"
                 for c in pa["criancas"]]
        k = ordem[len(ordem) // 2]
        pr = ctx.new_page()
        pr.on("pageerror", lambda e: erros.append(str(e)))
        pr.goto(f"{base}/r/{tok[('responsavel', k)]}")
        pr.wait_for_selector("#tela-termo:not(.oculto)")
        pr.check("#aceito"); pr.click("#btn-aceito")
        pr.wait_for_selector("#tela-acomp:not(.oculto)")
        time.sleep(10)
        pr.screenshot(path=str(saida / "familia_aguardando.png"))
        pm.bring_to_front(); pm.screenshot(path=str(saida / "motorista.png"))
        estados = set()
        for _ in range(240):
            time.sleep(1)
            v = pr.evaluate("estado && estado.viagem && estado.viagem.status")
            estados.add(v)
            if pr.evaluate("estado && !estado.viagem") and len(estados) > 2:
                break
        pr.screenshot(path=str(saida / "familia_fim.png"), full_page=True)
        eventos = pr.inner_text("#eventos")
        br.close()
    srv.should_exit = True
    ok = not erros and {"aguardando", "a_bordo"} <= estados and "Embarcou" in eventos and "Trajeto encerrado" in eventos
    print("estados vistos:", estados)
    print("erros nas páginas:", erros or "nenhum")
    print("RESULTADO:", "OK" if ok else "FALHOU")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
