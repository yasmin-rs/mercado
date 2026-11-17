"""Entrada única de notas: uma foto -> nota no histórico.

    1. tenta o QR code (local, sem IA) -> baixa a nota OFICIAL da Sefaz-SP (exata, sem captcha);
    2. se não houver QR legível, lê a lista de produtos da foto com o modelo de visão
       e confere a matemática (soma - descontos = total).
Depois de salvar: refaz as tabelas e padroniza só os produtos novos.
"""
import json
import os
from datetime import datetime
from pathlib import Path

from mercado import ler_foto as lf
from mercado import notas as nt
from mercado import padronizar as pz
from mercado.qrcode import baixar_nota, eh_link_nfce, ler_qrcode


def processar_imagem(imagem: bytes, nome: str = "foto.jpg", usar_visao: bool = True) -> dict:
    resultado = {"origem": None, "nota": None, "html": None, "problemas": [], "aviso_qr": None}
    link = ler_qrcode(imagem)
    if eh_link_nfce(link):
        try:
            html = baixar_nota(link)
            resultado.update(origem="qrcode", html=html, nota=nt.extrair_nota(html))
            return resultado
        except Exception as e:  # Sefaz fora do ar, bloqueio, página diferente...
            resultado["aviso_qr"] = f"QR lido, mas a nota não abriu ({type(e).__name__}); usando a foto."
    elif link:
        resultado["aviso_qr"] = "Há um QR na foto, mas não é de NFC-e de SP."
    if not usar_visao:
        resultado["problemas"] = ["QR não legível e leitura por foto desligada."]
        return resultado
    foto = lf.ler_foto(imagem, nome)
    resultado.update(origem="foto", nota=lf.para_nota(foto, nome), problemas=lf.conferir(foto))
    return resultado


def ja_registrada(nota: dict) -> bool:
    if not nota.get("chave"):
        return False
    return any(nota["chave"] in p.name for p in nt.PASTA_NOTAS.glob("*"))


def salvar(resultado: dict, emissao: datetime | None = None) -> Path:
    """Grava a nota nos dados brutos (data/, fora do git)."""
    nota = resultado["nota"]
    if resultado["origem"] == "qrcode":
        nt.PASTA_NOTAS.mkdir(parents=True, exist_ok=True)
        caminho = nt.PASTA_NOTAS / f"qr_{nota['chave']}.html"
        caminho.write_text(resultado["html"], encoding="utf-8")
        return caminho
    nt.PASTA_FOTOS_LIDAS.mkdir(parents=True, exist_ok=True)
    data = emissao or nota["emissao"]
    nome = f"foto_{datetime.now():%Y%m%d_%H%M%S}"
    registro = {**nota, "arquivo": nome, "emissao": data.isoformat() if data is not None else None}
    caminho = nt.PASTA_FOTOS_LIDAS / f"{nome}.json"
    caminho.write_text(json.dumps(registro, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return caminho


def atualizar_historico(modelo: str | None = None):
    """Refaz compras/itens e padroniza só os nomes novos. -> (compras, itens_padronizados, falhas)."""
    compras, itens, _, _ = nt.gerar_tabelas()
    if itens.empty:
        return compras, itens, []
    itens_pad, falhas = pz.aplicar_padronizacao(itens, modelo or os.getenv("MODEL_NAME", "gemini/gemini-3.5-flash-lite"))
    return compras, itens_pad, falhas
