// Funções usadas pelas telas do motorista e do responsável.
"use strict";

const TOKEN = location.pathname.split("/").filter(Boolean)[1] || "";
const VAN_SVG = '<svg viewBox="0 0 24 24" fill="#1d2125"><path d="M3 6a2 2 0 0 1 2-2h10.5a2 2 0 0 1 1.7.95L20 9.5l.9.4A2 2 0 0 1 22 11.7V16a1 1 0 0 1-1 1h-1.2a2.8 2.8 0 0 1-5.6 0H9.8a2.8 2.8 0 0 1-5.6 0H4a1 1 0 0 1-1-1V6Zm2 0v4h4V6H5Zm6 0v4h6.7l-2-4H11ZM7 15.6a1.4 1.4 0 1 0 0 2.8 1.4 1.4 0 0 0 0-2.8Zm10 0a1.4 1.4 0 1 0 0 2.8 1.4 1.4 0 0 0 0-2.8Z"/></svg>';

async function api(caminho, corpo) {
  const opcoes = corpo === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(corpo),
  };
  const r = await fetch(caminho, opcoes);
  let dados = {};
  try { dados = await r.json(); } catch (_) { /* resposta vazia */ }
  if (!r.ok) {
    const e = new Error(dados.erro || ("Erro " + r.status));
    e.status = r.status; e.dados = dados;
    throw e;
  }
  return dados;
}

let _toastTimer = null;
function toast(msg, ms = 3500) {
  let t = document.getElementById("toast");
  if (!t) { t = document.createElement("div"); t.id = "toast"; document.body.appendChild(t); }
  t.textContent = msg;
  t.classList.add("mostrar");
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => t.classList.remove("mostrar"), ms);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function guardar(chave, valor) { try { localStorage.setItem(chave, JSON.stringify(valor)); } catch (_) {} }
function ler(chave, padrao) {
  try { const v = localStorage.getItem(chave); return v === null ? padrao : JSON.parse(v); } catch (_) { return padrao; }
}

// ---------------------------------------------------------------- notificações
let _registro = null;
async function registrarSW() {
  if (!("serviceWorker" in navigator)) return null;
  try { _registro = await navigator.serviceWorker.register("/sw.js", { scope: "/" }); } catch (_) { _registro = null; }
  return _registro;
}

function notificacoesAtivas() {
  return "Notification" in window && Notification.permission === "granted";
}

async function pedirNotificacoes() {
  if (!("Notification" in window)) {
    toast("Este navegador não mostra notificações. No iPhone, adicione à Tela de Início primeiro.");
    return false;
  }
  const p = await Notification.requestPermission();
  return p === "granted";
}

let _audio = null;
function bip() {
  try {
    _audio = _audio || new (window.AudioContext || window.webkitAudioContext)();
    const o = _audio.createOscillator(), g = _audio.createGain();
    o.frequency.value = 880; g.gain.value = 0.08;
    o.connect(g); g.connect(_audio.destination); o.start(); o.stop(_audio.currentTime + 0.18);
  } catch (_) {}
}

async function avisar(titulo, corpo) {
  toast(corpo, 6000);
  if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
  bip();
  if (!notificacoesAtivas()) return;
  try {
    const reg = _registro || (await navigator.serviceWorker?.getRegistration());
    if (reg) await reg.showNotification(titulo, { body: corpo, icon: "/static/icone-192.png", badge: "/static/icone-192.png", tag: "rotavan-" + Date.now() });
    else new Notification(titulo, { body: corpo });
  } catch (_) {}
}

// ---------------------------------------------------------------- mapa
function iconeVan() {
  return L.divIcon({ className: "icone-van", html: "<div>" + VAN_SVG + "</div>", iconSize: [38, 38], iconAnchor: [19, 19] });
}
function iconePino(texto, cor) {
  return L.divIcon({ className: "pino", html: `<div style="background:${cor}">${esc(texto)}</div>`, iconSize: [26, 26], iconAnchor: [13, 13] });
}
function novoMapa(id) {
  const m = L.map(id, { zoomControl: true, attributionControl: true });
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "© OpenStreetMap",
    // o servidor de mapas recebe só o endereço do site, nunca o código de acesso da pessoa
    referrerPolicy: "strict-origin",
  }).addTo(m);
  m.setView([-28.3876, -53.9148], 14);
  return m;
}
