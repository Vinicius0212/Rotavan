# -*- coding: utf-8 -*-
"""Endereço https temporário para o servidor (Cloudflare Tunnel, gratuito, sem conta).

O navegador do celular só entrega o GPS a páginas https. O cloudflared cria um
endereço do tipo https://palavras-aleatorias.trycloudflare.com que encaminha
para o servidor rodando no computador. O endereço muda a cada vez que é aberto
(para uso contínuo, o caminho é hospedar o servidor num serviço com domínio fixo)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

PADRAO_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def achar_cloudflared(pasta: Path | None = None) -> str | None:
    candidatos = [shutil.which("cloudflared")]
    if pasta:
        candidatos += [str(pasta / "cloudflared.exe"), str(pasta / "cloudflared")]
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            candidatos += [str(Path(base) / "cloudflared" / "cloudflared.exe"),
                           str(Path(base) / "Microsoft" / "WinGet" / "Links" / "cloudflared.exe")]
    return next((c for c in candidatos if c and Path(c).exists()), None)


def abrir_tunel(porta: int, arquivo_url: Path, pasta: Path | None = None) -> threading.Thread | None:
    exe = achar_cloudflared(pasta)
    if exe is None:
        print("\n[túnel] cloudflared não encontrado. Para usar o GPS no celular, instale uma vez com:\n"
              "        winget install --id Cloudflare.cloudflared\n"
              "        (ou baixe cloudflared-windows-amd64.exe do GitHub da Cloudflare, renomeie para\n"
              "        cloudflared.exe e coloque nesta pasta). Depois abra de novo.\n")
        return None

    def rodar():
        proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://localhost:{porta}"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        achou = False
        for linha in proc.stdout:
            m = PADRAO_URL.search(linha)
            if m and not achou:
                achou = True
                url = m.group(0)
                arquivo_url.write_text(url, encoding="utf-8")
                print("\n" + "=" * 64 + f"\n  ENDEREÇO PARA O CELULAR:  {url}\n"
                      "  (cole este endereço na aba '3 · Acompanhar ao vivo' do RotaVan)\n" + "=" * 64 + "\n")
    t = threading.Thread(target=rodar, daemon=True)
    t.start()
    return t
