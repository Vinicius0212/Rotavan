// Cadastro rápido de uma família: casa, escola, horários; mostra os links e a configuração.
"use strict";

const $ = id => document.getElementById(id);
let senha = "";
try { senha = sessionStorage.getItem("rv_admin") || ""; } catch (_) {}
let mapa = null, modo = "casa", editando = null;
const pontos = { casa: null, escola: null, garagem: null };
const marcas = {};
let escolas = [];

async function adm(caminho, corpo) {
  const op = { headers: { "X-Admin-Senha": senha } };
  if (corpo !== undefined) { op.method = "POST"; op.headers["Content-Type"] = "application/json"; op.body = JSON.stringify(corpo); }
  const r = await fetch(caminho, op);
  let d = {}; try { d = await r.json(); } catch (_) {}
  if (!r.ok) { const e = new Error(d.erro || d.detail?.[0]?.msg || ("Erro " + r.status)); e.status = r.status; throw e; }
  return d;
}

function erro(msg) { const e = $("erro"); e.textContent = msg || ""; e.classList.toggle("oculto", !msg); if (msg) scrollTo(0, 0); }

const CORES = { casa: "#2a6fdb", escola: "#c0392b", garagem: "#1d2125" };
const ROTULO = { casa: "⌂", escola: "E", garagem: "G" };
function marcar(tipo, latlng) {
  pontos[tipo] = latlng;
  if (marcas[tipo]) marcas[tipo].setLatLng(latlng);
  else marcas[tipo] = L.marker(latlng, { icon: iconePino(ROTULO[tipo], CORES[tipo]) }).addTo(mapa);
}
function setModo(m) {
  modo = m;
  $("modo-txt").innerHTML = "<b>" + { casa: "a casa", escola: "a escola", garagem: "a garagem da van" }[m] + "</b>";
}

async function entrar(silencioso = false) {
  try {
    const linhas = await adm("/api/admin/linhas");
    try { sessionStorage.setItem("rv_admin", senha); } catch (_) {}
    $("tela-senha").classList.add("oculto");
    $("tela-cad").classList.remove("oculto");
    erro("");
    if (!mapa) {
      mapa = novoMapa("mapa-cad");
      mapa.on("click", ev => {
        marcar(modo, [ev.latlng.lat, ev.latlng.lng]);
        if (modo === "escola") { $("escola").value = ""; $("escola-nome").classList.remove("oculto"); }
      });
      escolas = await adm("/api/admin/escolas");
      $("escola").innerHTML = '<option value="">— escolha ou marque no mapa —</option>' +
        escolas.map((e, i) => `<option value="${i}">${esc(e.nome)}</option>`).join("");
    }
    desenharLista(linhas);
  } catch (e) {
    if (!silencioso) erro(e.message);
  }
}

function s2hhmm(s) { s = Math.round(s); return String(Math.floor(s / 3600)).padStart(2, "0") + ":" + String(Math.floor((s % 3600) / 60)).padStart(2, "0"); }

function editar(l) {
  const cfg = JSON.parse(l.config), c = cfg.cenario.criancas[0], e = cfg.cenario.escolas.find(x => x.id === c.escola);
  const v = cfg.cenario.veiculos[0];
  editando = l.linha_id;
  $("titulo-form").textContent = "Editar: " + l.nome;
  $("nome").value = l.nome; $("codigo").value = c.id;
  $("entrada").value = s2hhmm(e.entrada); $("saida").value = s2hhmm(e.saida);
  $("janela").value = cfg.opcoes.janela_celular || "aula";
  marcar("casa", [c.lat, c.lon]); marcar("escola", [e.lat, e.lon]);
  if (v) marcar("garagem", [v.lat, v.lon]);
  const i = escolas.findIndex(x => x.nome === e.nome);
  $("escola").value = i >= 0 ? String(i) : "";
  if (i < 0) { $("escola-nome").value = e.nome; $("escola-nome").classList.remove("oculto"); }
  mapa.fitBounds(L.latLngBounds([[c.lat, c.lon], [e.lat, e.lon]]), { padding: [40, 40] });
  $("titulo-form").scrollIntoView({ behavior: "smooth" });
}

let ultimasLinhas = [];
function cartaoAcesso(a) {
  if (a.papel === "celular") {
    return `<div class="acesso"><img src="${a.qr}" alt="QR do endereço">
      <div><b>Celular da criança ${esc(a.ref)}</b> — aplicativo <b>Traccar Client</b>
      <ol class="passos pequeno">
        <li>Instale o Traccar Client (Play Store) no celular da criança.</li>
        <li><b>Identificador do dispositivo:</b> <span class="grande-id">${esc(a.identificador)}</span></li>
        <li><b>Endereço do servidor:</b> <span class="url">${esc(a.url)}</span></li>
        <li>Precisão: alta · Frequência: 30 s · Distância: 0 · Buffer offline: ligado.</li>
        <li>Permita a localização "o tempo todo" e tire o app da economia de bateria. Ligue o serviço.</li>
      </ol></div></div>`;
  }
  const quem = a.papel === "motorista" ? "Motorista · van " + esc(a.ref) : "Família · " + esc(a.ref);
  return `<div class="acesso"><img src="${a.qr}" alt="QR"><div><b>${quem}</b>
    ${a.papel === "responsavel" ? `<span class="selo ${a.consentimento_em ? "ok" : ""}">${a.consentimento_em ? "termo aceito" : "termo ainda não aceito"}</span>` : ""}
    <p class="url"><a href="${esc(a.url)}" target="_blank" rel="noopener noreferrer">${esc(a.url)}</a></p></div></div>`;
}

function desenharLista(linhas) {
  ultimasLinhas = linhas;
  $("lista").innerHTML = linhas.map((l, i) => `
    <div class="cartao">
      <h2>${esc(l.nome)}</h2>
      <p class="pequeno">Celular da criança: ${l.opcoes.janela_celular === "sempre" ? "posição guardada sempre" : "só nos horários da escola"}.</p>
      ${l.acessos.map(cartaoAcesso).join("")}
      <details style="margin-top:10px"><summary><b>Guardar a configuração</b> (para não perder os links se o servidor reiniciar)</summary>
        <p class="pequeno">Na hospedagem, crie um segredo chamado <code>ROTAVAN_CONFIG</code> com o texto abaixo.
        Ele não contém nomes nem endereços: só códigos, a escola, os horários e o cruzamento mais próximo de casa.</p>
        <textarea readonly>${esc(l.config)}</textarea>
        <button class="sec peq" data-copiar>Copiar</button>
      </details>
      <button class="sec peq" style="margin-top:10px" data-editar="${i}">Editar casa, escola ou horários</button>
    </div>`).join("");
}

async function salvar() {
  erro("");
  const iEsc = $("escola").value;
  let escola = null;
  if (iEsc !== "") escola = { nome: escolas[+iEsc].nome, lat: escolas[+iEsc].lat, lon: escolas[+iEsc].lon };
  else if (pontos.escola) escola = { nome: $("escola-nome").value.trim() || "Escola", lat: pontos.escola[0], lon: pontos.escola[1] };
  if (!pontos.casa) return erro("Marque a casa no mapa (toque no cruzamento mais próximo).");
  if (!escola) return erro("Escolha a escola na lista ou marque no mapa.");
  if (!/^\d{1,2}:\d{2}$/.test($("entrada").value) || !/^\d{1,2}:\d{2}$/.test($("saida").value)) return erro("Horários no formato 07:30.");
  const corpo = {
    linha_id: editando, nome: $("nome").value.trim() || "Van escolar", janela_celular: $("janela").value,
    criancas: [{ codigo: $("codigo").value.trim() || "C01", lat: pontos.casa[0], lon: pontos.casa[1],
                 escola: { ...escola, entrada: $("entrada").value, saida: $("saida").value } }],
    garagem: pontos.garagem ? { lat: pontos.garagem[0], lon: pontos.garagem[1] } : null,
  };
  $("btn-salvar").disabled = true;
  try {
    await adm("/api/admin/familia", corpo);
    toast("Salvo. Os links estão no topo da página.");
    await entrar();
    scrollTo(0, 0);
  } catch (e) { erro(e.message); }
  finally { $("btn-salvar").disabled = false; }
}

$("btn-entrar").addEventListener("click", () => { senha = $("senha").value; entrar(); });
$("senha").addEventListener("keydown", e => { if (e.key === "Enter") { senha = $("senha").value; entrar(); } });
$("m-casa").addEventListener("click", () => setModo("casa"));
$("m-escola").addEventListener("click", () => setModo("escola"));
$("m-garagem").addEventListener("click", () => setModo("garagem"));
$("escola").addEventListener("change", e => {
  if (e.target.value === "") return;
  const x = escolas[+e.target.value];
  marcar("escola", [x.lat, x.lon]);
  $("escola-nome").classList.add("oculto");
  mapa.panTo([x.lat, x.lon]);
});
$("btn-salvar").addEventListener("click", salvar);
document.addEventListener("click", ev => {
  const ed = ev.target.closest("[data-editar]");
  if (ed) return editar(ultimasLinhas[+ed.dataset.editar]);
  const b = ev.target.closest("[data-copiar]");
  if (!b) return;
  const t = b.parentElement.querySelector("textarea");
  t.select();
  (navigator.clipboard ? navigator.clipboard.writeText(t.value) : Promise.reject()).then(
    () => toast("Copiado."), () => { document.execCommand("copy"); toast("Copiado."); });
});
entrar(true);
