"""Lê a foto da lista de produtos de um cupom fiscal com um modelo de visão (Gemini).

Não é OCR clássico: o modelo olha a imagem e devolve os itens já estruturados (JSON validado).
O código confere a matemática (soma dos itens - descontos = total); se não bater, a nota
fica marcada para revisão em vez de entrar errada na análise.

Uso:
    uv run python -m mercado.ler_foto data/fotos/03.png
Saída: outputs/fotos/<nome>.json (fora do git).

Privacidade: o prompt manda IGNORAR CPF/dados do consumidor; mesmo assim, prefira fotos
só da parte dos produtos. A imagem é enviada à API do modelo configurado.
"""
import argparse
import base64
import io
import json
import mimetypes
import os
import sys
import time
from pathlib import Path

import litellm
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

RAIZ = Path(__file__).resolve().parents[2]
PASTA_SAIDA = RAIZ / "outputs" / "fotos"
load_dotenv(RAIZ / ".env")
MODELO_VISAO = os.getenv("VISION_MODEL", "gemini/gemini-3.5-flash-lite")


class ItemFoto(BaseModel):
    produto: str = Field(description="descrição como impressa")
    quantidade: float
    unidade: str
    preco_unitario: float
    valor_total: float
    desconto: float = 0.0


class NotaFoto(BaseModel):
    itens: list[ItemFoto]
    data_emissao: str | None = Field(default=None, description="AAAA-MM-DD HH:MM:SS, se visível")
    valor_total: float | None = None
    desconto_total: float = 0.0
    valor_a_pagar: float | None = None


PROMPT = """Você lê fotos de cupons fiscais brasileiros (NFC-e). Extraia a LISTA DE PRODUTOS.
Para cada item: produto (descrição como impressa), quantidade, unidade (UN, KG...), preco_unitario, valor_total.
Atenção:
- Números entre parênteses, ex. "(2,99)", são TRIBUTOS: ignore, não são preço nem desconto.
- "Desconto sobre item 009 -3,00": registre desconto=3.0 no item de número 009 (positivo).
- Unidades como "kg9"/"un9": use só "KG"/"UN".
- Use ponto como separador decimal no JSON.
Também extraia, se visíveis: valor_total (soma bruta), desconto_total, valor_a_pagar e
data_emissao no formato "AAAA-MM-DD HH:MM:SS" (null se a data não aparecer na foto).
IGNORE completamente CPF, nome e qualquer dado do consumidor. Não invente itens cortados na foto.
Responda SOMENTE com JSON:
{"itens": [{"produto": "...", "quantidade": 0, "unidade": "UN", "preco_unitario": 0, "valor_total": 0, "desconto": 0}],
 "valor_total": 0, "desconto_total": 0, "valor_a_pagar": 0, "data_emissao": null}"""


def imagem_base64(imagem: bytes, nome: str) -> str:
    tipo = mimetypes.guess_type(nome)[0] or "image/jpeg"
    return f"data:{tipo};base64,{base64.b64encode(imagem).decode()}"


LADO_MAXIMO_PX = 1600  # fotos de celular têm 3-4 mil px; reduzir acelera muito (sobretudo no modelo local)


def reduzir(imagem: bytes, lado_maximo: int = LADO_MAXIMO_PX) -> bytes:
    """Reduz a foto mantendo a proporção e corrige a rotação do EXIF (foto de celular 'deitada')."""
    from PIL import Image, ImageOps
    foto = ImageOps.exif_transpose(Image.open(io.BytesIO(imagem))).convert("RGB")
    foto.thumbnail((lado_maximo, lado_maximo))
    saida = io.BytesIO()
    foto.save(saida, format="JPEG", quality=90)
    return saida.getvalue()


def eh_local(modelo: str) -> bool:
    return modelo.startswith(("ollama/", "ollama_chat/"))


def ler_foto(imagem: bytes, nome: str = "foto.jpg", modelo: str = MODELO_VISAO, tentativas: int = 2) -> NotaFoto:
    """Modelo local (Ollama, ex.: ollama_chat/qwen2.5vl:7b): a foto não sai do computador.
    Modelo de API (ex.: gemini/...): a foto é enviada ao provedor."""
    conversa = [{"role": "user", "content": [
        {"type": "text", "text": PROMPT},
        {"type": "image_url", "image_url": {"url": imagem_base64(reduzir(imagem), "foto.jpg")}},
    ]}]
    parametros = {"model": modelo, "temperature": 0, "response_format": {"type": "json_object"}}
    if eh_local(modelo):
        parametros.update(api_base=os.getenv("API_BASE", "http://localhost:11434"), timeout=1200)
    erro = None
    for _ in range(tentativas):
        texto = _completar(conversa, parametros)
        try:
            return normalizar(NotaFoto.model_validate_json(texto))
        except ValidationError as e:
            erro = e
            conversa += [{"role": "assistant", "content": texto},
                         {"role": "user", "content": f"JSON inválido: {str(e)[:300]}. Responda só o JSON corrigido."}]
    raise ValueError(f"modelo não devolveu JSON válido: {erro}")


def _completar(conversa: list, parametros: dict) -> str:
    """Chamada com nova tentativa para falhas passageiras (503 sobrecarga, 429 limite por minuto)."""
    for espera in (15, 40, None):
        try:
            return litellm.completion(messages=conversa, **parametros).choices[0].message.content or ""
        except Exception as e:
            texto = str(e).lower()
            passageiro = ("503" in texto or "unavailable" in texto or "429" in texto) and "per day" not in texto
            if espera is None or not passageiro:
                raise
            time.sleep(espera)


def normalizar(nota: NotaFoto) -> NotaFoto:
    """Padroniza para o formato da Sefaz: valor do item BRUTO (qtd x preço) e desconto à parte.
    Cupons de farmácia imprimem o valor já com desconto; o modelo às vezes copia esse valor líquido
    e TAMBÉM o desconto, o que descontaria duas vezes."""
    for i in nota.itens:
        bruto = round(i.quantidade * i.preco_unitario, 2)
        if abs(bruto - i.valor_total) > 0.02 and i.desconto and abs(bruto - i.desconto - i.valor_total) <= 0.02:
            i.valor_total = bruto
    return nota


def conferir(nota: NotaFoto) -> list[str]:
    """Checagens em código: a conta fecha? (o modelo pode ler um dígito errado)."""
    problemas = []
    for i in nota.itens:
        if abs(round(i.quantidade * i.preco_unitario, 2) - i.valor_total) > 0.02:
            problemas.append(f"{i.produto}: {i.quantidade} x {i.preco_unitario} != {i.valor_total}")
    soma = round(sum(i.valor_total for i in nota.itens), 2)
    descontos = round(sum(i.desconto for i in nota.itens), 2)
    if nota.valor_total is not None and abs(soma - nota.valor_total) > 0.02:
        problemas.append(f"soma dos itens {soma} != valor total impresso {nota.valor_total}")
    if nota.valor_a_pagar is not None and abs(soma - descontos - nota.valor_a_pagar) > 0.02:
        problemas.append(f"soma {soma} - descontos {descontos} != valor a pagar {nota.valor_a_pagar}")
    return problemas


def para_nota(foto: NotaFoto, arquivo: str) -> dict:
    """Mesmo formato de notas.extrair_nota(), para entrar nas mesmas tabelas."""
    import pandas as pd
    return {
        "chave": None, "emissao": pd.to_datetime(foto.data_emissao, errors="coerce") if foto.data_emissao else None,
        "loja_nome": "", "cnpj": None, "total": foto.valor_a_pagar, "troco": 0.0, "pagamento": None,
        "desconto": round(sum(i.desconto for i in foto.itens), 2) or foto.desconto_total,
        "itens": [{"produto": i.produto, "codigo": None, "quantidade": i.quantidade, "unidade": i.unidade.upper(),
                   "preco_unitario": i.preco_unitario, "valor_total": i.valor_total} for i in foto.itens],
        "arquivo": arquivo, "origem": "foto",
    }


def principal() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("fotos", nargs="+", type=Path)
    parser.add_argument("--modelo", default=MODELO_VISAO)
    args = parser.parse_args()
    print(f"{len(args.fotos)} foto(s) -> {len(args.fotos)} chamada(s) ao {args.modelo}")
    PASTA_SAIDA.mkdir(parents=True, exist_ok=True)
    for foto in args.fotos:
        nota = ler_foto(foto.read_bytes(), foto.name, args.modelo)
        problemas = conferir(nota)
        (PASTA_SAIDA / f"{foto.stem}.json").write_text(
            json.dumps({**nota.model_dump(), "problemas": problemas}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n== {foto.name}: {len(nota.itens)} itens | total {nota.valor_total} | desconto {nota.desconto_total}"
              f" | a pagar {nota.valor_a_pagar}")
        for i in nota.itens:
            d = f"  (desconto {i.desconto})" if i.desconto else ""
            print(f"  {i.produto:<28} {i.quantidade:>7} {i.unidade:<3} x {i.preco_unitario:>6} = {i.valor_total:>7}{d}")
        print("  CONFERE" if not problemas else "  ! revisar:\n    " + "\n    ".join(problemas))


if __name__ == "__main__":
    principal()
