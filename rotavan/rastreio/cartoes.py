# -*- coding: utf-8 -*-
"""Links de acesso, QR codes e cartões para imprimir e entregar às famílias."""
from __future__ import annotations

import base64
import html
import io

import qrcode


def url_de(base: str, papel: str, token: str) -> str:
    if papel == "celular":                       # Traccar Client: o código vai no campo "identificador"
        return f"{base.rstrip('/')}/traccar"
    return f"{base.rstrip('/')}/{'m' if papel == 'motorista' else 'r'}/{token}"


def qr_png(texto: str, caixa: int = 6) -> bytes:
    q = qrcode.QRCode(border=2, box_size=caixa, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(texto)
    q.make(fit=True)
    b = io.BytesIO()
    q.make_image(fill_color="black", back_color="white").save(b, format="PNG")
    return b.getvalue()


def cartoes_html(nome_linha: str, base: str, acessos: list[dict]) -> str:
    """Página para imprimir: um cartão por família (e um por motorista)."""
    cards = []
    for a in acessos:
        url = url_de(base, a["papel"], a["token"])
        img = base64.b64encode(qr_png(url)).decode()
        if a["papel"] == "celular":
            titulo = f"Celular da criança · {a['ref']} (Traccar Client)"
            texto = (f"Instale o Traccar Client no celular da criança. Identificador do dispositivo: "
                     f"{a['token']}. Endereço do servidor: o do QR code. Precisão alta, frequência 30 s, "
                     f"localização permitida o tempo todo.")
        elif a["papel"] == "motorista":
            titulo, texto = f"Motorista · van {a['ref']}", ("Abra no celular que fica na van. Toque em Ida ou Volta para "
                                                          "começar; mantenha a tela aberta durante o trajeto.")
        else:
            titulo, texto = f"Família · código {a['ref']}", ("Aponte a câmera do celular para o QR code. Na primeira vez, "
                                                           "leia e aceite o termo. Dica: adicione à tela inicial.")
        cards.append(f"""<div class="c"><img src="data:image/png;base64,{img}"><div>
<h3>{html.escape(titulo)}</h3><p>{html.escape(texto)}</p><p class="u">{html.escape(url)}</p>
<p class="n">Não repasse este link. Se perder, peça um novo.</p></div></div>""")
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>Cartões — {html.escape(nome_linha)}</title>
<style>body{{font:14px system-ui,sans-serif;margin:18px;color:#1d2125}}h1{{font-size:18px}}
.g{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}.c{{display:flex;gap:12px;border:1.5px dashed #999;border-radius:10px;
padding:12px;break-inside:avoid}}.c img{{width:130px;height:130px}}h3{{margin:0 0 6px;font-size:16px}}p{{margin:4px 0}}
.u{{font:11px monospace;word-break:break-all;color:#444}}.n{{font-size:11px;color:#a33}}
@media print{{body{{margin:6mm}}}}</style></head><body>
<h1>RotaVan · {html.escape(nome_linha)} — links de acompanhamento</h1><div class="g">{''.join(cards)}</div></body></html>"""
