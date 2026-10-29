# -*- coding: utf-8 -*-
"""RotaVan — interface gráfica (Streamlit).

Rodar:   streamlit run app.py
"""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import streamlit as st

from rotavan.avaliacao import ler_rota_informada, metricas, tabela_itinerario
from rotavan.cenario import (Cenario, Parametros, ler_criancas, ler_escolas, ler_escolas_osm,
                             ler_veiculos, gerar_cenario, s_para_hhmm)
from rotavan.heuristicas import HEURISTICAS
from rotavan.instancia import Instancia
from rotavan.mapa import desenhar
from rotavan.modelo import otimizar_com_nova_tentativa
from rotavan.rede import carregar_grafo

PASTA = Path(__file__).resolve().parent
DADOS = PASTA / "dados"

st.set_page_config(page_title="RotaVan Ijuí", page_icon="🚐", layout="wide")
st.title("RotaVan — rotas de transporte escolar particular em Ijuí")
st.caption("Otimização de rotas de vans escolares com janelas de horário, capacidade e tempo máximo a bordo "
           "— e acompanhamento ao vivo da van pelas famílias.")


@st.cache_resource(show_spinner="Carregando o mapa de ruas…")
def grafo(caminho: str):
    return carregar_grafo(caminho)


# ------------------------------------------------------------------ barra lateral
with st.sidebar:
    st.header("Mapa")
    arq_mapa = st.text_input("Arquivo da malha viária", str(DADOS / "ijui_drive.graphml"))
    if not Path(arq_mapa).exists():
        st.error("Mapa não encontrado. Rode antes:  python baixar_mapa_ijui.py")
        st.stop()
    G = grafo(arq_mapa)
    st.success(f"{G.number_of_nodes():,} cruzamentos carregados".replace(",", "."))

    st.header("Regras da operação")
    sentido = st.radio("Trajeto", ["ida", "volta"], horizontal=True,
                       format_func=lambda s: "Ida (casa → escola)" if s == "ida" else "Volta (escola → casa)")
    tmax = st.slider("Tempo máximo de cada criança na van (min)", 15, 90, 45, 5)
    folga = st.slider("Chegar pelo menos X min antes da entrada", 0, 20, 5)
    antecedencia = st.slider("…e no máximo Y min antes da entrada", 10, 60, 30, 5)
    tol_saida = st.slider("Volta: buscar até X min após o fim da aula", 0, 30, 15)
    saida_min = st.text_input("Motorista não sai antes de", "06:00")
    cedo = st.text_input("Não buscar criança antes de", "06:15")
    fator = st.slider("Fator de trânsito (multiplica os tempos do mapa)", 1.0, 2.0, 1.25, 0.05)
    embarque = st.number_input("Tempo de embarque em casa (s)", 10, 300, 60, 10)
    desemb = st.number_input("Tempo por criança na escola (s)", 5, 120, 30, 5)
    retorna = st.checkbox("Terminar na garagem", value=False)
    objetivo = st.radio("Minimizar", ["distancia", "tempo"], horizontal=True,
                        format_func=lambda s: "Distância (combustível)" if s == "distancia" else "Tempo")
    limite = st.slider("Tempo de busca do otimizador (s)", 1, 60, 10)
    st.header("Custos")
    consumo = st.number_input("Consumo da van (km/L)", 3.0, 20.0, 9.0, 0.5)
    preco = st.number_input("Preço do combustível (R$/L)", 3.0, 12.0, 6.50, 0.05)
    dias = st.number_input("Dias letivos por mês", 10, 26, 20)

params = Parametros(sentido=sentido, tempo_max_bordo_min=tmax, folga_min=folga,
                    antecedencia_max_min=antecedencia, tolerancia_saida_min=tol_saida,
                    embarque_s=int(embarque), desembarque_s=int(desemb), saida_minima=saida_min,
                    embarque_mais_cedo=cedo, fator_transito=fator, retorna_garagem=retorna,
                    minimizar=objetivo, tempo_limite_s=int(limite), consumo_km_l=consumo,
                    preco_litro=preco, dias_letivos_mes=int(dias))

# ------------------------------------------------------------------ dados
aba_dados, aba_res, aba_vivo = st.tabs(["1 · Dados", "2 · Resultado", "3 · Acompanhar ao vivo"])

with aba_dados:
    modo = st.radio("Origem dos dados", ["Gerar um exemplo", "Carregar planilhas (CSV)"], horizontal=True)
    if modo == "Gerar um exemplo":
        c1, c2, c3, c4 = st.columns(4)
        n = c1.number_input("Crianças", 2, 60, 12)
        ne = c2.number_input("Escolas", 1, 6, 3)
        nv = c3.number_input("Vans", 1, 5, 1)
        cap = c4.number_input("Lugares por van", 4, 30, 15)
        sem = st.number_input("Semente (muda o sorteio)", 0, 10_000, 1)
        st.info("Exemplo sintético: casas sorteadas em cruzamentos de ruas residenciais e escolas reais "
                "do OpenStreetMap. Não são alunos reais.")
        if st.button("Gerar exemplo", type="primary"):
            esc_osm = DADOS / "escolas_ijui_osm.csv"
            esc = ler_escolas_osm(esc_osm) if esc_osm.exists() else None
            st.session_state.cen = gerar_cenario(G, int(n), int(ne), int(sem), escolas_osm=esc,
                                                 n_vans=int(nv), capacidade=int(cap), params=params)
            st.session_state.rota_atual = None
    else:
        st.markdown("Modelos de planilha em `dados/modelos/`. Coordenadas: no Google Maps, clique com o botão "
                    "direito no ponto e copie o número (lat, lon). Use o cruzamento mais próximo da casa, não "
                    "o endereço exato — o sistema não precisa dele.")
        f_esc = st.file_uploader("escolas.csv", type="csv")
        f_cri = st.file_uploader("criancas.csv", type="csv")
        f_vei = st.file_uploader("veiculos.csv", type="csv")
        f_rot = st.file_uploader("rota_atual.csv (opcional: a ordem que o motorista faz hoje)", type="csv")
        if st.button("Carregar", type="primary") and f_esc and f_cri and f_vei:
            st.session_state.cen = Cenario(ler_escolas(f_esc), ler_criancas(f_cri), ler_veiculos(f_vei), params,
                                           descricao="dados informados")
            st.session_state.rota_atual = pd.read_csv(f_rot, dtype=str) if f_rot else None

    cen = st.session_state.get("cen")
    if cen is not None:
        cen.params = params
        erros = cen.validar()
        for e in erros:
            st.error(e)
        st.subheader("Escolas")
        st.dataframe(pd.DataFrame([{"id": e.id, "nome": e.nome, "entrada": s_para_hhmm(e.entrada),
                                    "saída": s_para_hhmm(e.saida)} for e in cen.escolas]), hide_index=True)
        st.subheader(f"Crianças ({len(cen.criancas)})")
        st.dataframe(pd.DataFrame([{"id": c.id, "escola": c.escola, "lat": round(c.lat, 5), "lon": round(c.lon, 5)}
                                   for c in cen.criancas]), hide_index=True, height=240)

# ------------------------------------------------------------------ resultado
def _aba_resultado():
    cen = st.session_state.get("cen")
    if cen is None:
        st.info("Primeiro gere um exemplo ou carregue as planilhas na aba Dados.")
        return
    if cen.validar():
        st.error("Corrija os dados antes de otimizar.")
        return
    if st.button("Otimizar rotas", type="primary"):
        with st.spinner("Calculando distâncias pelas ruas…"):
            inst = Instancia.criar(cen, G)
        with st.spinner(f"Otimizando (até {params.tempo_limite_s} s)…"):
            res = otimizar_com_nova_tentativa(inst)
        comp = {}
        if res.itinerarios:
            comp["Otimizado"] = (res.itinerarios, metricas(inst, res.itinerarios))
        if len(cen.veiculos) == 1:
            for nome, f in HEURISTICAS.items():
                it = [f(inst)]
                comp[nome.capitalize()] = (it, metricas(inst, it))
        rot = st.session_state.get("rota_atual")
        if rot is not None:
            it = ler_rota_informada(inst, rot)
            comp["Rota atual"] = (it, metricas(inst, it))
        st.session_state.res = (inst, res, comp)

    if "res" in st.session_state:
        inst, res, comp = st.session_state.res
        if res.status != "ok":
            fora = ", ".join(res.nao_atendidas) if res.nao_atendidas else "—"
            st.error(f"Não foi possível atender todas as crianças respeitando as regras (fora: {fora}). "
                     "Tente aumentar o tempo máximo a bordo, a antecedência permitida ou o número de vans.")
        linhas = []
        for nome, (_, m) in comp.items():
            linhas.append({"Rota": nome, "Viável": "sim" if m["viavel"] else "não",
                           "Vans": m["vans_usadas"], "km": round(m["distancia_km"], 2),
                           "Duração (min)": round(m["duracao_total_min"], 1),
                           "A bordo médio (min)": round(m["bordo_medio_min"], 1),
                           "A bordo máx. (min)": round(m["bordo_max_min"], 1),
                           "Acima do limite": m["criancas_acima_tmax"], "Atrasadas": m["criancas_atrasadas"],
                           "R$/trajeto": round(m["custo_trajeto_r$"], 2),
                           "R$/mês*": round(m["custo_trajeto_r$"] * params.dias_letivos_mes, 2)})
        st.subheader("Comparação")
        st.dataframe(pd.DataFrame(linhas), hide_index=True)
        st.caption("*Só este trajeto (ida ou volta), nos dias letivos informados.")
        if "Otimizado" in comp:
            base = comp.get("Rota atual") or next((v for k, v in comp.items() if k != "Otimizado"), None)
            if base:
                km0, km1 = base[1]["distancia_km"], comp["Otimizado"][1]["distancia_km"]
                c1, c2, c3 = st.columns(3)
                c1.metric("Distância", f"{km1:.1f} km", f"{(km1 - km0) / km0 * 100:+.1f}%", delta_color="inverse")
                c2.metric("Economia por mês (este trajeto)",
                          f"R$ {(base[1]['custo_trajeto_r$'] - comp['Otimizado'][1]['custo_trajeto_r$']) * params.dias_letivos_mes:.2f}")
                c3.metric("Tempo do solver", f"{res.tempo_solver_s:.1f} s")

            itins = comp["Otimizado"][0]
            from streamlit_folium import st_folium
            m = desenhar(inst, itins, "Rota otimizada")
            st_folium(m, height=560, use_container_width=True, returned_objects=[])
            tab = tabela_itinerario(inst, itins)
            st.subheader("Itinerário para o motorista")
            st.dataframe(tab.drop(columns=["lat", "lon"]).rename(columns={
                "van": "Van", "ordem": "Ordem", "horario": "Horário", "acao": "Ação", "local": "Local",
                "criancas": "Crianças", "espera_min": "Espera (min)"}), hide_index=True)
            st.download_button("Baixar itinerário (CSV)", tab.to_csv(index=False).encode("utf-8-sig"),
                               "itinerario.csv", "text/csv")
            buf = io.BytesIO(); m.save(buf, close_file=False)
            st.download_button("Baixar mapa (HTML)", buf.getvalue(), "mapa_rota.html", "text/html")


with aba_res:
    _aba_resultado()


# ------------------------------------------------------------------ acompanhamento ao vivo
def _aba_vivo():
    from rotavan.rastreio.armazem import Armazem
    from rotavan.rastreio.cartoes import cartoes_html, qr_png, url_de
    from rotavan.rastreio.servico import Central

    st.markdown(
        "Publique a linha para que **o celular do motorista envie a posição da van** e **cada família "
        "acompanhe pelo navegador** (sem instalar aplicativo): a van no mapa, a previsão de chegada calculada "
        "a partir da rota otimizada, e avisos de embarque, chegada à escola e atraso.")
    cen = st.session_state.get("cen")
    if cen is None:
        st.info("Primeiro gere um exemplo ou carregue as planilhas na aba Dados.")
        return

    @st.cache_resource
    def central(caminho_banco: str, _G):
        return Central(Armazem(caminho_banco), _G)

    c = central(str(DADOS / "rastreio.db"), G)
    existentes = c.linhas()
    opcoes = ["(nova linha)"] + [f"{l['nome']}  ·  {l['id']}" for l in existentes]
    escolha = st.selectbox("Linha", opcoes, help="Publicar de novo numa linha existente mantém os links já entregues.")
    linha_id = None if escolha == opcoes[0] else escolha.split("·")[-1].strip()
    nome_padrao = next((l["nome"] for l in existentes if l["id"] == linha_id), "Minha van escolar")
    c1, c2 = st.columns([1, 1])
    nome = c1.text_input("Nome da linha (aparece para as famílias)", nome_padrao)
    arq_url = DADOS / "endereco_publico.txt"
    padrao = arq_url.read_text(encoding="utf-8").strip() if arq_url.exists() else "http://localhost:8000"
    base = c2.text_input("Endereço do servidor", st.session_state.get("base_url", padrao),
                         help="Ao rodar abrir_rastreio.bat aparece um endereço https://….trycloudflare.com — cole aqui.")
    st.session_state.base_url = base
    if st.button("Publicar / atualizar linha", type="primary"):
        atr = None
        if "res" in st.session_state:
            _, res, _ = st.session_state.res
            if res.itinerarios and len(cen.veiculos) > 1:
                atr = {cen.params.sentido: {k: it.veiculo for it in res.itinerarios for pa in it.paradas
                                            for k in pa.criancas}}
        pub = c.publicar_linha(nome, cen, linha_id=linha_id, atribuicao=atr)
        st.session_state.linha_pub = pub["linha_id"]
        st.success("Linha publicada. Entregue os links abaixo (ou imprima os cartões com QR code).")
        st.rerun()

    lid = linha_id or st.session_state.get("linha_pub")
    if not lid:
        return
    acessos = c.links(lid)
    if base.startswith("http://") and "localhost" not in base and "127.0.0.1" not in base:
        st.warning("Em endereço http (sem s) o celular não libera o GPS. Use o endereço https do túnel.")
    rot = {"motorista": "Motorista · van ", "responsavel": "Família · ", "celular": "Celular da criança (Traccar) · "}
    tab = pd.DataFrame([{"Para": rot[a["papel"]] + a["ref"],
                         "Termo aceito": "sim" if a["consentimento_em"] else ("não" if a["papel"] == "responsavel" else "—"),
                         "Identificador (Traccar)": a["token"] if a["papel"] == "celular" else "",
                         "Link": url_de(base, a["papel"], a["token"])} for a in acessos])
    st.dataframe(tab, hide_index=True, column_config={"Link": st.column_config.LinkColumn("Link")})
    d1, d2 = st.columns(2)
    d1.download_button("Baixar cartões com QR code (imprimir)", cartoes_html(nome, base, acessos).encode("utf-8"),
                       "cartoes_rotavan.html", "text/html")
    d2.download_button("Baixar links (CSV)", tab.to_csv(index=False).encode("utf-8-sig"), "links_rotavan.csv", "text/csv")
    with st.expander("QR codes"):
        cols = st.columns(4)
        for i, a in enumerate(acessos):
            with cols[i % 4]:
                st.image(qr_png(url_de(base, a["papel"], a["token"]), 4),
                         caption=rot[a["papel"]] + a["ref"])
    with st.expander("Perdeu ou vazou um link? Gerar um novo"):
        quem = st.selectbox("Para quem", [f"{a['papel']}:{a['ref']}" for a in acessos])
        if st.button("Gerar novo link (o antigo deixa de funcionar)"):
            papel, ref = quem.split(":")
            c.novo_link(lid, papel, ref)
            st.rerun()
    st.caption("Para o servidor rodar: dê dois cliques em **abrir_rastreio.bat** (deixe a janela aberta). "
               "Celular da criança: instale o app gratuito **Traccar Client**, use o *Identificador* acima e, "
               "como endereço do servidor, o link da linha 'Celular da criança'.")


with aba_vivo:
    _aba_vivo()
