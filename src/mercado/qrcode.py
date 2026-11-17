"""QR code da NFC-e: lê o link a partir da FOTO e baixa a nota oficial da Sefaz (sem captcha).

O QR da NFC-e é denso (link longo) e o papel costuma estar amassado, então tentamos três
leitores (WeChat/OpenCV, OpenCV padrão, ZXing) em várias versões da imagem: inteira, cortes
(o QR costuma ficar no rodapé ou num canto), ampliada e binarizada. Tudo roda local: a foto
não sai do computador nesta etapa.
"""
import time
import urllib.request

import cv2
import numpy as np
import zxingcpp

DOMINIO_SEFAZ_SP = "nfce.fazenda.sp.gov.br"
_wechat = None


def _leitor_wechat():
    global _wechat
    if _wechat is None:
        _wechat = cv2.wechat_qrcode_WeChatQRCode()
    return _wechat


def _versoes(cinza: np.ndarray):
    """Gera recortes e tratamentos da imagem, do mais barato ao mais caro."""
    a, l = cinza.shape
    recortes = [cinza, cinza[:, : l // 2], cinza[:, l // 2:], cinza[a // 2:, :], cinza[: a // 2, :],
                cinza[a // 3:, : 2 * l // 3]]
    for r in recortes:
        if r.size == 0:
            continue
        for fator in (1, 2, 3):
            ampliada = r if fator == 1 else cv2.resize(r, None, fx=fator, fy=fator, interpolation=cv2.INTER_CUBIC)
            yield ampliada
            yield cv2.threshold(ampliada, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]


def ler_qrcode(imagem: bytes) -> str | None:
    """Bytes da foto -> texto do QR (ou None se nenhum leitor conseguir)."""
    matriz = cv2.imdecode(np.frombuffer(imagem, np.uint8), cv2.IMREAD_COLOR)
    if matriz is None:
        return None
    cinza = cv2.cvtColor(matriz, cv2.COLOR_BGR2GRAY)
    padrao = cv2.QRCodeDetector()
    for versao in _versoes(cinza):
        textos, _ = _leitor_wechat().detectAndDecode(versao)
        if textos and textos[0]:
            return textos[0]
        texto, _, _ = padrao.detectAndDecode(versao)
        if texto:
            return texto
        lidos = zxingcpp.read_barcodes(versao)
        if lidos and lidos[0].text:
            return lidos[0].text
    return None


def eh_link_nfce(texto: str | None) -> bool:
    return bool(texto) and texto.startswith("https://") and DOMINIO_SEFAZ_SP in texto


# A Sefaz derruba a conexão (RemoteDisconnected) de pedidos sem os cabeçalhos de um navegador comum.
CABECALHOS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/130.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "pt-BR,pt;q=0.9",
}


def baixar_nota(link: str, timeout: int = 30, tentativas: int = 2) -> str:
    """Abre o link do QR (só Sefaz-SP, por segurança) e devolve o HTML da nota."""
    if not eh_link_nfce(link):
        raise ValueError("o QR não é de uma NFC-e de SP")
    for tentativa in range(tentativas):
        try:
            with urllib.request.urlopen(urllib.request.Request(link, headers=CABECALHOS), timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            if tentativa == tentativas - 1:
                raise
            time.sleep(3)
