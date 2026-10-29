// Tela do motorista: inicia o trajeto, envia o GPS e registra embarques/desembarques.
"use strict";

const API = "/api/m/" + TOKEN;
let estado = null;
let mapa = null, camadaRota = null, camadaParadas = null, marcadorVan = null, enquadrado = null;
let buffer = [];            // posições ainda não enviadas (ex.: sem internet)
let ultimaLeitura = 0, idWatch = null, wakeLock = null, gpsStatus = "aguardando";

const $ = id => document.getElementById(id);

function mostrarErro(msg) {
  const e = $("erro");
  if (!msg) { e.classList.add("oculto"); return; }
  e.textContent = msg; e.classList.remove("oculto");
}

// ---------------------------------------------------------------- GPS
function iniciarGPS() {
  if (idWatch !== null || !estado?.viagem || estado.viagem.simulacao) return;
  if (!("geolocation" in navigator)) { gpsStatus = "indisponível"; return; }
  if (!window.isSecureContext) { gpsStatus = "precisa https"; return; }
  idWatch = navigator.geolocation.watchPosition(p => {
    gpsStatus = "ok";
    const agora = Date.now();
    if (agora - ultimaLeitura < 4000) return;           // no máximo 1 posição a cada 4 s
    ultimaLeitura = agora;
    buffer.push({ lat: p.coords.latitude, lon: p.coords.longitude, precisao: p.coords.accuracy, ts: p.timestamp / 1000 });
    if (buffer.length > 1500) buffer = buffer.slice(-1500);
  }, err => {
    gpsStatus = err.code === 1 ? "sem permissão" : "sem sinal";
    desenharSelos();
  }, { enableHighAccuracy: true, maximumAge: 2000, timeout: 20000 });
  manterTelaLigada();
}

function pararGPS() {
  if (idWatch !== null) navigator.geolocation.clearWatch(idWatch);
  idWatch = null; buffer = [];
  if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
}

async function manterTelaLigada() {
  try { if ("wakeLock" in navigator && !wakeLock) wakeLock = await navigator.wakeLock.request("screen"); } catch (_) {}
}
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && estado?.viagem) { wakeLock = null; manterTelaLigada(); }
});

async function enviarPosicoes() {
  if (!buffer.length) return;
  const lote = buffer.slice(0, 500);
  try {
    await api(API + "/posicoes", { pontos: lote });
    buffer = buffer.slice(lote.length);
  } catch (e) {
    if (e.status === 409) buffer = [];   // trajeto encerrado
    // sem internet: mantém no buffer e tenta de novo
  }
}

// ---------------------------------------------------------------- ações
async function iniciar(sentido) {
  const simulacao = $("simular").checked;
  const mult = parseFloat($("mult").value);
  document.querySelectorAll("[data-sentido]").forEach(b => { b.disabled = true; });
  toast("Calculando a melhor rota de hoje…", 8000);
  try {
    const r = await api(API + "/iniciar", { sentido, simulacao, mult });
    if (r.aviso) toast(r.aviso, 8000);
    else if (r.fora_do_horario) toast("Fora do horário das aulas: os horários foram ajustados para começar agora.", 7000);
    else toast("Trajeto iniciado. Boa viagem!");
    enquadrado = null;
    await atualizar();
  } catch (e) {
    toast(e.message, 6000);
  } finally {
    document.querySelectorAll("[data-sentido]").forEach(b => { b.disabled = false; });
  }
}

async function marcar(crianca, tipo) {
  try {
    const r = await api(API + "/marcar", { crianca, tipo });
    toast(crianca + ": " + r.texto);
    if (navigator.vibrate) navigator.vibrate(60);
  } catch (e) { toast(e.message, 5000); }
  await atualizar();
}

async function desfazer(crianca) {
  try { await api(API + "/desfazer", { crianca }); toast("Desfeito: " + crianca); } catch (e) { toast(e.message); }
  await atualizar();
}

function abrirEncerrar() {
  $("conferi").checked = false; $("fim-ok").disabled = true;
  const pend = (estado.viagem?.paradas || []).flatMap(p => p.criancas).filter(c => c.status === "a_bordo").map(c => c.id);
  const box = $("fim-pendentes");
  if (pend.length) {
    box.innerHTML = "Ainda constam <b>a bordo</b>: " + esc([...new Set(pend)].join(", ")) + ". Marque o desembarque antes de encerrar.";
    box.classList.remove("oculto");
  } else box.classList.add("oculto");
  $("modal-fim").classList.remove("oculto");
}

async function encerrar() {
  try {
    await api(API + "/encerrar", { conferi_a_van: $("conferi").checked });
    $("modal-fim").classList.add("oculto");
    pararGPS();
    toast("Trajeto encerrado.");
  } catch (e) {
    toast(e.message, 6000);
  }
  await atualizar();
}

// ---------------------------------------------------------------- desenho
function rotuloStatus(s) {
  return { aguardando: "aguardando", a_bordo: "a bordo", entregue: "entregue", ausente: "ausente" }[s] || s;
}
function classeStatus(s) {
  return { a_bordo: "selo alerta", entregue: "selo ok", ausente: "selo erro" }[s] || "selo";
}

function botoesCrianca(p, c) {
  const faltou = (estado.ausencias?.[c.id] || []).includes(estado.viagem.sentido);
  let html = `<div class="crianca"><span class="cod">${esc(c.id)}</span><span class="${classeStatus(c.status)}">${rotuloStatus(c.status)}</span>`;
  if (faltou && c.status === "aguardando") html += `<span class="selo erro">família avisou falta</span>`;
  html += `<span class="espaco"></span>`;
  if (p.tipo === "embarque" && c.status === "aguardando") {
    html += `<button class="verde peq" data-acao="embarque" data-c="${esc(c.id)}">Subiu</button>`;
    html += `<button class="sec peq" data-acao="ausente" data-c="${esc(c.id)}">Faltou</button>`;
  } else if (p.tipo === "desembarque" && c.status === "a_bordo") {
    html += `<button class="verde peq" data-acao="desembarque" data-c="${esc(c.id)}">${estado.viagem.sentido === "ida" ? "Desceu na escola" : "Entregue"}</button>`;
  } else if ((p.tipo === "embarque" && (c.status === "a_bordo" || c.status === "ausente")) || (p.tipo === "desembarque" && c.status === "entregue")) {
    html += `<button class="sec peq" data-acao="desfazer" data-c="${esc(c.id)}">Desfazer</button>`;
  }
  return html + "</div>";
}

function navegarURL(p) {
  return `https://www.google.com/maps/dir/?api=1&destination=${p.lat},${p.lon}&travelmode=driving`;
}

function desenharSelos() {
  const v = estado?.viagem;
  const g = $("selo-gps");
  if (v?.simulacao) { g.className = "selo alerta vivo"; g.lastElementChild.textContent = "GPS simulado"; }
  else if (gpsStatus === "ok") { g.className = "selo ok vivo"; g.lastElementChild.textContent = "GPS ok" + (buffer.length > 3 ? ` · ${buffer.length} na fila` : ""); }
  else { g.className = "selo erro"; g.lastElementChild.textContent = "GPS: " + gpsStatus; }
  const r = $("selo-rota");
  if (!v?.pos) { r.className = "selo"; r.textContent = "aguardando posição"; }
  else if (v.na_rota) { r.className = "selo ok"; r.textContent = "na rota"; }
  else { r.className = "selo alerta"; r.textContent = "fora da rota planejada"; }
  $("relogio").textContent = v ? v.agora + (v.simulacao ? " (simulado)" : "") : "";
}

function desenharMapa() {
  const v = estado.viagem;
  if (!mapa) {
    mapa = novoMapa("mapa");
    camadaRota = L.layerGroup().addTo(mapa);
    camadaParadas = L.layerGroup().addTo(mapa);
  }
  if (enquadrado !== v.id) {
    camadaRota.clearLayers(); camadaParadas.clearLayers();
    const linha = L.polyline(v.trajeto, { color: "#2a6fdb", weight: 5, opacity: .75 }).addTo(camadaRota);
    v.paradas.forEach(p => {
      if (p.tipo === "saida") return;
      const cor = p.tipo === "embarque" ? "#2a6fdb" : "#c0392b";
      L.marker([p.lat, p.lon], { icon: iconePino(p.ordem, cor) })
        .bindTooltip(`${p.ordem}. ${p.nome} — ${p.criancas.map(c => c.id).join(", ")}`).addTo(camadaParadas);
    });
    mapa.fitBounds(linha.getBounds(), { padding: [20, 20] });
    enquadrado = v.id;
    setTimeout(() => mapa.invalidateSize(), 50);
  }
  if (v.pos) {
    if (!marcadorVan) marcadorVan = L.marker(v.pos, { icon: iconeVan(), zIndexOffset: 1000 }).addTo(mapa);
    else marcadorVan.setLatLng(v.pos);
    if (!mapa.getBounds().contains(v.pos)) mapa.panTo(v.pos);
  }
}

function desenharViagem() {
  const v = estado.viagem;
  $("tela-inicio").classList.add("oculto");
  $("tela-viagem").classList.remove("oculto");
  const av = $("aviso-viagem");
  if (v.fora_do_horario) { av.textContent = "Teste fora do horário das aulas: horários deslocados para começar agora."; av.classList.remove("oculto"); }
  else av.classList.add("oculto");
  desenharSelos();
  desenharMapa();

  const pend = v.paradas.filter(p => p.pendente);
  const prox = pend[0];
  const box = $("proxima-conteudo");
  if (prox) {
    const atraso = prox.atraso_min >= 3 ? `<span class="selo alerta">+${Math.round(prox.atraso_min)} min</span>` : "";
    box.innerHTML = `<div class="linha-flex"><div><p class="destaque">${prox.ordem}. ${esc(prox.nome)}</p>
      <p class="sub">${prox.tipo === "embarque" ? "Buscar" : "Deixar"} · previsto ${esc(prox.previsto)} (plano ${esc(prox.planejado)}) ${atraso}</p></div></div>
      ${prox.criancas.map(c => botoesCrianca(prox, c)).join("")}
      <a class="botao sec" style="margin-top:12px;width:100%" href="${navegarURL(prox)}" target="_blank" rel="noopener noreferrer">Abrir navegação até aqui</a>`;
  } else {
    box.innerHTML = `<p class="destaque">Todas as paradas concluídas</p><p class="sub">Confira a van e encerre o trajeto.</p>`;
  }

  $("paradas").innerHTML = v.paradas.filter(p => p.tipo !== "saida").map(p => {
    const feita = !p.pendente;
    const atual = prox && p.ordem === prox.ordem;
    return `<li><div class="parada ${feita ? "feita" : ""} ${atual ? "atual" : ""}">
      <div class="num">${p.ordem}</div>
      <div><b>${p.tipo === "embarque" ? "Buscar" : p.tipo === "desembarque" ? "Deixar" : "Fim"}</b> · ${esc(p.nome)}
        ${p.criancas.map(c => botoesCrianca(p, c)).join("")}</div>
      <div class="hora">${esc(p.previsto || p.planejado)}<small>plano ${esc(p.planejado)}</small></div>
    </div></li>`;
  }).join("");

  $("eventos").innerHTML = (v.eventos || []).slice(0, 30).map(e =>
    `<li><span class="h">${esc(e.hora || "")}</span><span class="${e.anulado ? "anulado" : ""}">${e.crianca ? "<b>" + esc(e.crianca) + "</b> · " : ""}${esc(e.texto)}</span></li>`
  ).join("") || `<li class="pequeno">Nada ainda.</li>`;
}

function desenharInicio() {
  $("tela-viagem").classList.add("oculto");
  $("tela-inicio").classList.remove("oculto");
  const aus = estado.ausencias || {};
  const itens = Object.entries(aus).map(([k, ss]) => `<li><b>${esc(k)}</b> — não vai: ${esc(ss.join(" e "))}</li>`);
  $("faltas").innerHTML = itens.join("") || `<li class="pequeno">Nenhuma família avisou falta hoje.</li>`;
}

async function atualizar() {
  try {
    estado = await api(API + "/estado");
    mostrarErro(null);
  } catch (e) {
    mostrarErro(e.status === 404 ? e.message : "Sem conexão com o servidor. Tentando de novo…");
    return;
  }
  $("cab").textContent = `${estado.linha} · van ${estado.van}`;
  if (estado.viagem) { desenharViagem(); iniciarGPS(); }
  else { pararGPS(); desenharInicio(); }
}

// ---------------------------------------------------------------- eventos da tela
document.addEventListener("click", ev => {
  const b = ev.target.closest("button");
  if (!b) return;
  if (b.dataset.sentido) iniciar(b.dataset.sentido);
  else if (b.dataset.acao === "desfazer") desfazer(b.dataset.c);
  else if (b.dataset.acao) marcar(b.dataset.c, b.dataset.acao);
});
$("simular").addEventListener("change", e => $("vel-box").classList.toggle("oculto", !e.target.checked));
$("btn-encerrar").addEventListener("click", abrirEncerrar);
$("fim-cancelar").addEventListener("click", () => $("modal-fim").classList.add("oculto"));
$("conferi").addEventListener("change", e => { $("fim-ok").disabled = !e.target.checked; });
$("fim-ok").addEventListener("click", encerrar);

registrarSW();
atualizar();
setInterval(enviarPosicoes, 5000);
setInterval(atualizar, 5000);
