# RotaVan — rotas otimizadas e acompanhamento ao vivo do transporte escolar em Ijuí/RS

Software do TCC "RotaVan: otimização de rotas e acompanhamento em tempo real do
transporte escolar particular urbano em Ijuí/RS". Tem duas partes que trabalham juntas:

1. **Otimização** (`app.py`, no computador do transportador): calcula a melhor ordem de paradas.
2. **Acompanhamento ao vivo** (`servidor.py`, aberto no celular): o celular do motorista envia a
   posição da van; cada família vê a van no mapa, a previsão de chegada e recebe avisos.

A parte de otimização calcula a melhor ordem para uma van escolar buscar as crianças em casa
e deixá-las nas escolas (ida) ou buscar nas escolas e levar para casa (volta),
respeitando:

- o horário de entrada (ou saída) de cada escola;
- o número de lugares da van;
- um tempo máximo que cada criança pode ficar dentro da van;
- as ruas de mão única de Ijuí (malha do OpenStreetMap).

Também compara a rota otimizada com a rota que o motorista faz hoje (ou com duas
heurísticas que imitam um planejamento intuitivo) e estima a economia de combustível.

## Instalação (uma vez)

Precisa de Python 3.10 ou mais novo. No Prompt de Comando, dentro desta pasta:

```
pip install -r requirements.txt
python baixar_mapa_ijui.py
```

O segundo comando baixa o mapa de ruas de Ijuí e a lista de escolas do
OpenStreetMap para a pasta `dados/` (leva de 30 s a 2 min).

## Usar pela interface

```
streamlit run app.py
```

Abre no navegador. Na aba **Dados**, gere um exemplo ou carregue as planilhas
(modelos em `dados/modelos/`). Na aba **Resultado**, clique em **Otimizar rotas**:
aparecem o mapa, o itinerário com horários e a comparação.

### Planilhas

| Arquivo | Colunas |
|---|---|
| `escolas.csv` | `id, nome, lat, lon, entrada, saida` (horários como `07:30`) |
| `criancas.csv` | `id, lat, lon, escola` — `id` é um código (C01, C02…), **nunca o nome** |
| `veiculos.csv` | `id, capacidade, lat, lon` — `lat, lon` = garagem (de onde a van sai) |
| `rota_atual.csv` (opcional) | `veiculo, parada` — a ordem que o motorista faz hoje, usando os códigos das crianças e das escolas |

**Privacidade (LGPD, art. 14):** não use o endereço exato. Informe o cruzamento
mais próximo da casa — o sistema já trabalha no nível do cruzamento e descarta
qualquer precisão maior. Colete os dados só com consentimento dos responsáveis.

## Acompanhamento ao vivo (motorista e famílias)

1. No RotaVan (`app.py`), gere ou carregue os dados e abra a aba **3 · Acompanhar ao vivo**.
2. Dê dois cliques em **`abrir_rastreio.bat`** e deixe a janela aberta. Na primeira vez ele
   instala o que falta e o `cloudflared`, que cria um endereço **https** temporário
   (`https://….trycloudflare.com`) — o celular só libera o GPS em páginas https.
3. Cole esse endereço no campo *Endereço do servidor* da aba 3 e clique em **Publicar**.
   Aparecem um link por van (motorista) e um por criança (família), com QR codes e cartões
   para imprimir.
4. **Motorista** (`/m/<código>`): toca em *Ida* ou *Volta*; o sistema recalcula a rota do dia
   sem quem avisou falta e passa a enviar o GPS. Marca *Subiu*, *Faltou*, *Desceu/Entregue*.
   Só consegue encerrar depois de confirmar que conferiu a van e que ninguém ficou a bordo.
5. **Família** (`/r/<código>`): aceita o termo (LGPD) na primeira vez; vê a van e "chega em ~N min"
   enquanto a criança está no trajeto; recebe avisos (van a ~5 min, embarcou, chegou à escola,
   entregue em casa, atraso, não embarcou); pode avisar "não vai hoje".

Para testar sem sair de carro, marque **Modo demonstração** na tela do motorista: a van
"anda sozinha" pelo trajeto com trânsito simulado (1× a 20× mais rápido).

Sem o túnel, dá para testar tudo no próprio computador em `http://localhost:8000`
(`python servidor.py`). O endereço do túnel gratuito muda a cada vez que é aberto; para uso
contínuo, o servidor deve ser hospedado num serviço com domínio fixo e https.

### Celular da criança (quando não dá para contar com o motorista)

Instale o app gratuito **Traccar Client** (Android/iOS) no celular da criança. Ele manda a
localização em segundo plano, com a tela apagada. Configure:

- *Identificador do dispositivo*: o código da linha "Celular da criança" (aba 3 ou `/admin`);
- *Endereço do servidor*: `https://SEU-ENDERECO/traccar`;
- precisão alta, frequência 30 s, buffer offline ligado; localização "o tempo todo" e app sem
  restrição de bateria.

A família passa a ver no link dela: em casa / na escola / no caminho (com previsão de chegada),
o rastro do dia e avisos de *saiu de casa*, *chegou à escola*, *saiu da escola*, *chegou em casa*,
bateria fraca e falta de sinal. Por padrão a posição só é guardada nos horários de ida e volta da
escola (dias úteis) e é apagada em 24 h; dá para escolher "sempre" para testar.

### Na nuvem, sem deixar o PC ligado (Render, grátis)

Este repositório já está pronto para o Render: o `Dockerfile` da raiz monta só o servidor de
acompanhamento (com o mapa compacto `dados/ijui_drive.json.gz` e `requirements-nuvem.txt`).

1. Em [render.com](https://render.com), entre com o GitHub → **New → Web Service** → escolha este repositório.
2. Runtime **Docker**, plano **Free**; em *Environment* crie `ROTAVAN_ADMIN_SENHA` (senha sua).
3. Depois do deploy, abra `https://SEU-ENDERECO.onrender.com/admin` para cadastrar casa, escola e horários.
4. Para os links sobreviverem aos reinícios do plano grátis, copie a "configuração" mostrada no
   `/admin` para a variável `ROTAVAN_CONFIG` no Render.

**Privacidade (decisões de projeto):** a criança é só um código (o apelido que a família
digita fica só no celular dela); quem é localizada é a **van**, e só durante o trajeto; a
família vê a van só até a sua criança ser entregue; a trilha de GPS é apagada 24 h depois e os
registros em 30 dias; o acesso exige consentimento e pode ser revogado; links vazados podem
ser trocados; o código de acesso não vaza para o servidor de mapas (`Referrer-Policy`).

## Reproduzir os experimentos do artigo

```
python experimentos.py        # ~45 min; gera resultados/*.csv
python analisar.py            # gera tabelas LaTeX e figuras em resultados/artigo/
python figura_mapa.py         # figura do exemplo sobre o mapa real
python experimento_eta.py     # E5: precisão da previsão de chegada (~20 min)
python analisar_eta.py        # tabelas e figura do E5
```

`python experimentos.py --rapido` roda uma versão curta para conferir que tudo funciona.

## Testes

```
python -m pytest -q tests
```

São 33 testes. Os de acompanhamento (`tests/test_rastreio.py`) percorrem a API do
servidor: consentimento, início do trajeto, GPS, embarques, bloqueio do encerramento com
criança a bordo, aviso de falta, demonstração completa, links revogados e apagamento da trilha.

Os testes de otimização usam uma malha em grade (não precisam do mapa real) e verificam, entre
outras coisas, que toda rota otimizada respeita as regras e que, em instâncias
pequenas, o otimizador encontra a mesma distância que a enumeração de todas as
ordens possíveis.

## Organização do código

| Módulo | Responsabilidade |
|---|---|
| `rotavan/rede.py` | carregar a malha, achar o cruzamento mais próximo, matrizes de distância e tempo (Dijkstra) |
| `rotavan/cenario.py` | escolas, crianças, veículos, parâmetros; leitura de CSV; gerador de exemplos |
| `rotavan/instancia.py` | liga o cenário à malha: locais, janelas de horário, tempos de parada |
| `rotavan/modelo.py` | modelo de coleta e entrega com janelas de tempo no OR-Tools |
| `rotavan/heuristicas.py` | vizinho mais próximo e escola por escola (linhas de base) |
| `rotavan/avaliacao.py` | simulação de horários, métricas e itinerário legível |
| `rotavan/exato.py` | enumeração exaustiva para validar o otimizador em instâncias pequenas |
| `rotavan/mapa.py` | mapa interativo (Folium) |
| `app.py` | interface (Streamlit) |
| `rotavan/rastreio/plano.py` | plano da viagem a partir da rota otimizada; projeção do GPS no trajeto; previsão de chegada com ritmo adaptativo |
| `rotavan/rastreio/servico.py` | regras do acompanhamento: início do trajeto, embarques, avisos, ausências, consentimento, encerramento seguro |
| `rotavan/rastreio/armazem.py` | banco SQLite e política de retenção de dados |
| `rotavan/rastreio/simulacao.py` | viagens simuladas com trânsito incerto (demonstração e experimento E5) |
| `rotavan/rastreio/web/` | telas do motorista e da família (HTML/JS, Leaflet), funcionam como app (PWA) |
| `rotavan/rastreio/celular.py` | celular da criança (Traccar Client): cercas casa/escola, avisos, janelas de horário |
| `rotavan/rastreio/configuracao.py` | cadastro rápido e configuração portátil (variável `ROTAVAN_CONFIG`) |
| `servidor.py` | servidor web (FastAPI); `/traccar` recebe o celular da criança; `/admin` cadastro rápido |
| `preparar_nuvem.py` | monta a versão para hospedagem gratuita (Render) |
