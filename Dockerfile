# Servidor de acompanhamento do RotaVan para hospedagem (ex.: Render, plano gratuito).
# Só copia o necessário para o servidor: o otimizador, o acompanhamento e o mapa compacto.
FROM python:3.11-slim
RUN useradd -m -u 1000 user
WORKDIR /home/user/app
COPY --chown=user requirements-nuvem.txt .
RUN pip install --no-cache-dir -r requirements-nuvem.txt
COPY --chown=user servidor.py ./
COPY --chown=user rotavan ./rotavan
COPY --chown=user dados/ijui_drive.json.gz dados/escolas_ijui_osm.csv ./dados/
USER user
# o Render informa a porta na variável PORT
ENV ROTAVAN_MAPA=/home/user/app/dados/ijui_drive.json.gz \
    ROTAVAN_BANCO=/tmp/rastreio.db \
    PYTHONUNBUFFERED=1
CMD ["python", "servidor.py"]
