// Tela da família: vê a van, a previsão de chegada e recebe os avisos.
"use strict";

const API = "/api/r/" + TOKEN;
const $ = id => document.getElementById(id);
let estado = null, mapa = null, mVan = null, mPonto = null, mEscola = null, enquadrou = null;
let mCel = null, trilhaCel = null;
let ultimoEvento = ler("rv_ult_" + TOKEN, null);
let confirmarRevogar = false;

function nome() { return (ler("rv_apelido_" + TOKEN, "") || "").trim() || estado?.crianca || "a criança"; }

function mostrarErro(msg) {
  const e = $("erro");
  if (!msg) { e.classList.add("oculto"); return; }
  e.textContent = msg; e.classList.remove("oculto");
}

// ---------------------------------------------------------------- textos
function textosStatus() {
  const v = estado.viagem, n = nome();
  const evs = (estado.eventos || []).filter(e => e.crianca && !e.anulado);
  const cel = estado.celular;
  if (!v && cel && cel.pos && cel.idade_s < 15 * 60) {
    if (cel.zona === "casa") return { t: `${n} está em casa.`, s: `Pelo celular, desde ${cel.zona_desde || cel.hora}.` };
    if (cel.zona === "escola") return { t: `${n} está na escola.`, s: `Pelo celular, desde ${cel.zona_desde || cel.hora}.` };
    if (cel.destino) return { t: `${n} está a caminho ${cel.destino === "escola" ? "da escola" : "de casa"}.`,
                              s: `Previsão de chegada ${cel.previsto} (pelo celular, pelas ruas).`, min: true, minCel: cel.faltam_min };
    return { t: `${n} está fora de casa e da escola.`, s: `Última posição do celular às ${cel.hora}.` };
  }
  if (!v) {
    const aus = estado.ausencias_hoje || [];
    const ultimo = evs[0];
    if (ultimo) return { t: ultimo.texto.replace(/^/, ""), s: "Último registro de hoje." };
    if (aus.length) return { t: `Avisado: ${n} não vai hoje (${aus.join(" e ")}).`, s: "" };
    return { t: "Nenhum trajeto em andamento agora.", s: estado.escola ? `Entrada ${estado.escola.entrada} · saída ${estado.escola.saida}` : "" };
  }
  const prev = v.previsto ? `Previsão ${v.previsto}` + (v.planejado && v.planejado !== v.previsto ? ` (horário de costume ${v.planejado})` : "") : "";
  const ida = v.sentido === "ida";
  switch (v.status) {
    case "aguardando":
      return ida ? { t: `A van está a caminho para buscar ${n}.`, s: prev, min: true }
                 : { t: `A van está indo buscar ${n} na escola.`, s: prev, min: true };
    case "a_bordo":
      return ida ? { t: `${n} está na van, a caminho da escola.`, s: prev.replace("Previsão", "Chegada à escola"), min: true }
                 : { t: `${n} está na van, a caminho de casa.`, s: prev.replace("Previsão", "Chegada em casa"), min: true };
    case "entregue":
      return { t: ida ? `${n} chegou à escola.` : `${n} foi entregue em casa.`, s: evs[0]?.texto || "" };
    case "ausente":
      return { t: `${n} não embarcou.`, s: evs[0]?.texto || "" };
  }
  return { t: "—", s: "" };
}

// ---------------------------------------------------------------- desenho
function desenharCelular() {
  const cel = estado.celular, box = $("cel");
  if (!cel) { box.classList.add("oculto"); return; }
  box.classList.remove("oculto");
  $("cel-titulo").textContent = "Celular de " + nome();
  const selo = $("cel-selo");
  if (!cel.pos) {
    selo.className = "selo"; selo.textContent = cel.janela_ativa ? "sem posição ainda" : "fora do horário";
    $("cel-txt").textContent = cel.janela_ativa ? "Aguardando a primeira posição do celular." :
      "O celular só é acompanhado nos horários de ida e volta da escola.";
    $("cel-sub").textContent = cel.ultima_hora ? `Última posição recebida às ${cel.ultima_hora}.` :
      "Confira se o Traccar Client está ligado no celular da criança.";
    return;
  }
  const min = Math.round(cel.idade_s / 60);
  if (cel.idade_s < 120) { selo.className = "selo ok vivo"; selo.innerHTML = "<span class='ponto'></span>Ao vivo"; }
  else if (cel.idade_s < 15 * 60) { selo.className = "selo alerta"; selo.textContent = `há ${min} min`; }
  else { selo.className = "selo erro"; selo.textContent = `sem sinal há ${min} min`; }
  const zona = { casa: "Em casa", escola: "Na escola", fora: "No caminho" }[cel.zona] || "Localizado";
  $("cel-txt").textContent = zona + (cel.destino ? ` — chega ${cel.destino === "escola" ? "à escola" : "em casa"} em ~${cel.faltam_min} min` : "");
  const det = [`Atualizado às ${cel.hora}`];
  if (cel.bateria !== null && cel.bateria !== undefined) det.push(`bateria ${Math.round(cel.bateria)}%`);
  if (cel.precisao) det.push(`precisão ~${Math.round(cel.precisao)} m`);
  $("cel-sub").textContent = det.join(" · ");
}

function desenharMapa() {
  const v = estado.viagem, cel = estado.celular;
  const temVan = v && v.van, temCel = cel && cel.pos;
  const box = $("mapa");
  if (!temVan && !temCel) { box.classList.add("oculto"); return; }
  box.classList.remove("oculto");
  if (!mapa) mapa = novoMapa("mapa");
  setTimeout(() => mapa.invalidateSize(), 30);
  if (estado.ponto && !mPonto) mPonto = L.marker([estado.ponto.lat, estado.ponto.lon], { icon: iconePino("⌂", "#2a6fdb") }).addTo(mapa).bindTooltip("Seu ponto");
  if (estado.escola && !mEscola) mEscola = L.marker([estado.escola.lat, estado.escola.lon], { icon: iconePino("E", "#c0392b") }).addTo(mapa).bindTooltip(estado.escola.nome);
  if (temCel) {
    const ini = (nome()[0] || "•").toUpperCase();
    if (!trilhaCel) trilhaCel = L.polyline(cel.trilha || [], { color: "#d6336c", weight: 4, opacity: .7, dashArray: "6 6" }).addTo(mapa);
    else trilhaCel.setLatLngs(cel.trilha || []);
    if (!mCel) mCel = L.marker(cel.pos, { icon: iconePino(ini, "#d6336c"), zIndexOffset: 900 }).addTo(mapa).bindTooltip("Celular de " + nome());
    else mCel.setLatLng(cel.pos);
  }
  if (!temVan) {
    if (enquadrou !== "cel" || !mapa.getBounds().pad(-0.1).contains(cel.pos)) {
      const pts = [cel.pos];
      if (cel.destino === "escola" && mEscola) pts.push(mEscola.getLatLng());
      else if (cel.destino === "casa" && mPonto) pts.push(mPonto.getLatLng());
      mapa.fitBounds(L.latLngBounds(pts), { padding: [50, 50], maxZoom: 16 });
      enquadrou = "cel";
    }
    return;
  }
  if (!mVan) mVan = L.marker(v.van, { icon: iconeVan(), zIndexOffset: 1000 }).addTo(mapa).bindTooltip("Van");
  else mVan.setLatLng(v.van);
  // enquadra a van e o destino que importa agora (casa ou escola); refaz quando
  // a situação muda (embarcou) ou quando a van sai da área visível
  const chave = v.sentido + ":" + v.status;
  if (enquadrou !== chave || !mapa.getBounds().pad(-0.1).contains(v.van)) {
    const alvo = (v.sentido === "ida") === (v.status === "aguardando") ? mPonto : mEscola;
    const pts = [v.van, alvo ? alvo.getLatLng() : v.van];
    mapa.fitBounds(L.latLngBounds(pts), { padding: [50, 50], maxZoom: 16 });
    enquadrou = chave;
  }
}

function desenhar() {
  $("titulo").textContent = "RotaVan · " + nome();
  $("cab").textContent = estado.linha;
  if (!estado.consentiu) {
    $("tela-acomp").classList.add("oculto");
    $("tela-termo").classList.remove("oculto");
    $("t-cod").textContent = "(" + estado.crianca + ")";
    return;
  }
  $("tela-termo").classList.add("oculto");
  $("tela-acomp").classList.remove("oculto");

  const v = estado.viagem;
  const vivo = $("selo-vivo");
  if (v && v.van !== undefined) {
    if (v.sem_posicao) { vivo.className = "selo alerta"; vivo.innerHTML = "<span class='ponto'></span>Aguardando o GPS da van"; }
    else if (v.sinal_perdido) { vivo.className = "selo erro"; vivo.innerHTML = "<span class='ponto'></span>Sinal da van perdido"; }
    else { vivo.className = "selo ok vivo"; vivo.innerHTML = "<span class='ponto'></span>Ao vivo" + (v.simulacao ? " · demonstração" : ""); }
  } else if (v && v.status === "entregue") { vivo.className = "selo ok"; vivo.textContent = v.sentido === "ida" ? "Na escola" : "Em casa"; }
  else if (v && v.status === "ausente") { vivo.className = "selo erro"; vivo.textContent = "Não embarcou"; }
  else if (v) { vivo.className = "selo"; vivo.textContent = "Trajeto em andamento"; }
  else if (estado.celular && estado.celular.pos && estado.celular.idade_s < 15 * 60) {
    vivo.className = "selo ok"; vivo.textContent = "Pelo celular de " + nome();
  }
  else { vivo.className = "selo"; vivo.textContent = "Van sem trajeto agora"; }

  const at = $("selo-atraso");
  if (v && v.atraso_min >= 5 && (v.status === "aguardando" || v.status === "a_bordo")) {
    at.textContent = `Atraso ~${v.atraso_min} min`; at.classList.remove("oculto");
  } else at.classList.add("oculto");

  const tx = textosStatus();
  $("st-titulo").textContent = tx.t;
  $("st-sub").textContent = tx.s || "";
  const faltam = tx.minCel !== undefined ? tx.minCel : (v ? v.faltam_min : null);
  if (tx.min && faltam !== null && faltam !== undefined) {
    $("st-min").textContent = faltam <= 0 ? "agora" : faltam;
    $("st-tempo").classList.remove("oculto");
    $("st-tempo").lastElementChild.style.display = faltam <= 0 ? "none" : "";
  } else $("st-tempo").classList.add("oculto");

  desenharCelular();
  desenharMapa();

  $("eventos").innerHTML = (estado.eventos || []).map(e =>
    `<li><span class="h">${esc(e.hora || "")}</span><span class="${e.anulado ? "anulado" : ""}">${esc(e.texto)}</span></li>`
  ).join("") || `<li class="pequeno">Nenhum registro hoje.</li>`;

  const aus = estado.ausencias_hoje || [];
  $("faltas-estado").textContent = aus.length ? "Avisado para hoje: não vai na " + aus.join(" e na ") + "." : "";
  document.querySelectorAll("[data-falta]").forEach(b => {
    const s = b.dataset.falta, marcado = aus.includes(s);
    b.textContent = marcado ? (s === "ida" ? "Cancelar aviso (ida)" : "Cancelar aviso (volta)")
                            : (s === "ida" ? "Não vai na ida" : "Não volta de van");
    b.classList.toggle("vermelho", marcado); b.classList.toggle("sec", !marcado);
  });
  $("escola-info").textContent = estado.escola ? `${estado.escola.nome} — entrada ${estado.escola.entrada}, saída ${estado.escola.saida}` : "";
  $("btn-notif").textContent = notificacoesAtivas() ? "Notificações ativadas ✓" : "Ativar notificações";
}

function verificarNovos() {
  const evs = (estado.eventos || []).filter(e => !e.anulado);
  const maior = evs.reduce((m, e) => Math.max(m, e.id), 0);
  if (ultimoEvento === null) { ultimoEvento = maior; guardar("rv_ult_" + TOKEN, maior); return; }
  const novos = evs.filter(e => e.id > ultimoEvento).reverse();
  novos.forEach(e => avisar("RotaVan · " + nome(), e.texto));
  if (maior > ultimoEvento) { ultimoEvento = maior; guardar("rv_ult_" + TOKEN, maior); }
}

async function atualizar() {
  try {
    estado = await api(API + "/estado");
    mostrarErro(null);
  } catch (e) {
    mostrarErro(e.status === 404 ? e.message : "Sem conexão. Tentando de novo…");
    return;
  }
  desenhar();
  if (estado.consentiu) verificarNovos();
}

// ---------------------------------------------------------------- ações
$("aceito").addEventListener("change", e => { $("btn-aceito").disabled = !e.target.checked; });
$("btn-aceito").addEventListener("click", async () => {
  await api(API + "/consentimento", { aceito: true });
  await atualizar();
});
$("btn-revogar").addEventListener("click", async () => {
  if (!confirmarRevogar) {
    confirmarRevogar = true;
    $("btn-revogar").textContent = "Toque de novo para confirmar a revogação";
    $("btn-revogar").classList.add("vermelho");
    setTimeout(() => { confirmarRevogar = false; $("btn-revogar").textContent = "Revogar meu acesso"; $("btn-revogar").classList.remove("vermelho"); }, 5000);
    return;
  }
  await api(API + "/consentimento", { aceito: false });
  confirmarRevogar = false;
  toast("Acesso revogado. A localização não é mais exibida neste link.");
  await atualizar();
});
document.querySelectorAll("[data-falta]").forEach(b => b.addEventListener("click", async () => {
  const s = b.dataset.falta;
  const desfazer = (estado.ausencias_hoje || []).includes(s);
  try {
    await api(API + "/falta", { sentidos: [s], desfazer });
    toast(desfazer ? "Aviso cancelado." : "Motorista avisado.");
  } catch (e) { toast(e.message); }
  await atualizar();
}));
$("apelido").value = ler("rv_apelido_" + TOKEN, "");
$("apelido").addEventListener("input", e => { guardar("rv_apelido_" + TOKEN, e.target.value); if (estado) desenhar(); });
$("btn-notif").addEventListener("click", async () => {
  await registrarSW();
  if (await pedirNotificacoes()) { avisar("RotaVan", "Notificações ativadas. Mantenha esta página aberta ou na Tela de Início."); }
  desenhar();
});

registrarSW();
atualizar();
setInterval(atualizar, 5000);
