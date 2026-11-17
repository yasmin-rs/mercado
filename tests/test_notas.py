"""Testes do leitor de notas com uma página FICTÍCIA no mesmo formato da Sefaz-SP."""
from mercado.notas import extrair_nota, ler_html, montar_tabelas, numero_br

HTML = """<html><body><div class="txtCenter">
<div id="u20" class="txtTopo">MERCADO FICTICIO LTDA</div>
<div class="text"> CNPJ: 11.222.333/0001-81</div><div class="text">RUA X, 1, SAO PAULO, SP</div></div>
<table id="tabResult"><tbody>
<tr id="Item + 1"><td><span class="txtTit">OVOS BRANCOS 12UN</span><span class="RCod">(Código: 789 )</span><br>
<span class="Rqtd"><strong>Qtde.:</strong>2</span><span class="RUN"><strong>UN: </strong>UN</span>
<span class="RvlUnit"><strong>Vl. Unit.:</strong>&nbsp; 12,50</span></td>
<td class="txtTit noWrap">Vl. Total<br><span class="valor">25,00</span></td></tr>
<tr id="Item + 2"><td><span class="txtTit">BANANA PRATA</span><span class="RCod">(Código: 5 )</span><br>
<span class="Rqtd"><strong>Qtde.:</strong>1,250</span><span class="RUN"><strong>UN: </strong>kg9</span>
<span class="RvlUnit"><strong>Vl. Unit.:</strong>&nbsp; 6,00</span></td>
<td class="txtTit noWrap">Vl. Total<br><span class="valor">7,50</span></td></tr>
</tbody></table>
<div id="totalNota">
<div id="linhaTotal"><label>Qtd. total de itens:</label><span class="totalNumb">2</span></div>
<div id="linhaTotal"><label>Valor total R$:</label><span class="totalNumb">32,50</span></div>
<div id="linhaTotal"><label>Descontos R$:</label><span class="totalNumb">2,50</span></div>
<div id="linhaTotal"><label>Valor a pagar R$:</label><span class="totalNumb txtMax">30,00</span></div>
<div id="linhaForma"><label>Forma de pagamento:</label><span class="totalNumb txtTitR">Valor pago R$:</span></div>
<div id="linhaTotal"><label class="tx">Cartão de Débito</label><span class="totalNumb">30,00</span></div>
<div id="linhaTotal"><label class="tx">Troco </label><span class="totalNumb">0,00</span></div>
</div>
<div id="infos"><li><strong>Número: </strong>123<strong> Série: </strong>1<strong> Emissão: </strong>05/10/2026 18:30:00 - Via Consumidor</li>
<span class="chave">3526 1011 2223 3300 0181 6500 1000 0001 2310 0000 0011</span>
<h4>Consumidor</h4><li>CPF: 123.456.789-00</li></div>
</body></html>"""


def test_numero_br():
    assert numero_br("1.234,56") == 1234.56 and numero_br(" 0,432 ") == 0.432 and numero_br("") is None


def test_extrai_itens_totais_e_pagamento():
    n = extrair_nota(HTML)
    assert [i["produto"] for i in n["itens"]] == ["OVOS BRANCOS 12UN", "BANANA PRATA"]
    banana = n["itens"][1]
    assert (banana["quantidade"], banana["unidade"], banana["preco_unitario"], banana["valor_total"]) == (1.25, "KG", 6.0, 7.5)
    assert (n["total"], n["desconto"], n["troco"], n["pagamento"]) == (30.0, 2.5, 0.0, "Cartão de Débito")
    assert str(n["emissao"]) == "2026-10-05 18:30:00" and n["cnpj"] == "11222333000181" and len(n["chave"]) == 44


def test_cpf_do_consumidor_nao_e_extraido():
    assert "123.456.789" not in str(extrair_nota(HTML))


def test_le_mhtml(tmp_path):
    mhtml = ("From: <Saved by Blink>\r\nMIME-Version: 1.0\r\n"
             'Content-Type: multipart/related; boundary="XX"\r\n\r\n--XX\r\n'
             "Content-Type: text/html\r\nContent-Transfer-Encoding: 8bit\r\n\r\n" + HTML + "\r\n--XX--\r\n")
    arquivo = tmp_path / "nota"
    arquivo.write_bytes(mhtml.encode("utf-8"))
    assert extrair_nota(ler_html(arquivo))["pagamento"] == "Cartão de Débito"


def test_tabelas_sem_nome_da_loja_e_sem_duplicata(tmp_path):
    nota = {**extrair_nota(HTML), "arquivo": "a"}
    compras, itens, lojas = montar_tabelas([nota, {**nota, "arquivo": "b"}], tmp_path / "lojas.csv")  # mesma nota 2x
    assert len(compras) == 1 and len(itens) == 2 and bool(compras["confere"][0])
    assert "MERCADO FICTICIO" not in compras.to_csv() + itens.to_csv()
    assert lojas.iloc[0]["loja"] == "loja_A"
