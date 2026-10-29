# -*- coding: utf-8 -*-
"""Monta a pasta rotavan_nuvem/ para publicar o servidor de acompanhamento no
plano gratuito do Render (endereço https fixo, sem deixar o PC ligado).

    python preparar_nuvem.py

Depois, siga o GUIA_NUVEM.md (ou o guia em PDF) que acompanha o projeto."""
import shutil
from pathlib import Path

from rotavan.rede import carregar_grafo, salvar_grafo_leve

RAIZ = Path(__file__).resolve().parent
DEST = RAIZ / "rotavan_nuvem"

README = """# RotaVan — acompanhamento do transporte escolar (Ijuí/RS)

Servidor do TCC "RotaVan", pronto para o plano gratuito do Render (https fixo).

- `/admin` — cadastro rápido (casa, escola, horários) e links; senha no segredo `ROTAVAN_ADMIN_SENHA`.
- `/r/<código>` — tela da família · `/m/<código>` — tela do motorista.
- `/traccar` — recebe a posição do celular da criança (app Traccar Client).

Nenhum dado pessoal fica neste repositório: a configuração da linha vai no segredo
`ROTAVAN_CONFIG` (só códigos, escola, horários e o cruzamento mais próximo de casa).
"""

DOCKERFILE = """FROM python:3.11-slim
RUN useradd -m -u 1000 user
WORKDIR /home/user/app
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=user . .
USER user
# o Render informa a porta na variável PORT; sem ela, usa 8000
ENV ROTAVAN_MAPA=/home/user/app/dados/ijui_drive.json.gz \\
    ROTAVAN_BANCO=/tmp/rastreio.db \\
    PYTHONUNBUFFERED=1
CMD ["python", "servidor.py"]
"""

RENDER_YAML = """services:
  - type: web
    name: rotavan
    runtime: docker
    plan: free
    healthCheckPath: /api/saude
    envVars:
      - key: ROTAVAN_ADMIN_SENHA
        sync: false
      - key: ROTAVAN_CONFIG
        sync: false
"""

REQ = """fastapi>=0.110
uvicorn>=0.27
pydantic>=2.0
ortools>=9.10
networkx>=3.0
shapely>=2.0
pandas>=2.0
numpy>=1.24
qrcode>=7.4
pillow>=10.0
tzdata>=2024.1
"""


def main():
    if DEST.exists():
        shutil.rmtree(DEST)
    (DEST / "dados").mkdir(parents=True)
    shutil.copytree(RAIZ / "rotavan", DEST / "rotavan",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "mapa.py"))
    shutil.copy(RAIZ / "servidor.py", DEST / "servidor.py")
    shutil.copy(RAIZ / "dados" / "escolas_ijui_osm.csv", DEST / "dados" / "escolas_ijui_osm.csv")
    G = carregar_grafo(RAIZ / "dados" / "ijui_drive.graphml")
    salvar_grafo_leve(G, DEST / "dados" / "ijui_drive.json.gz")
    (DEST / "README.md").write_text(README, encoding="utf-8")
    (DEST / "Dockerfile").write_text(DOCKERFILE, encoding="utf-8")
    (DEST / "requirements.txt").write_text(REQ, encoding="utf-8")
    (DEST / "render.yaml").write_text(RENDER_YAML, encoding="utf-8")
    n = sum(1 for _ in DEST.rglob("*") if _.is_file())
    print(f"Pronto: {DEST} ({n} arquivos). Envie a pasta para um repositório do GitHub e crie o serviço no Render.")


if __name__ == "__main__":
    main()
