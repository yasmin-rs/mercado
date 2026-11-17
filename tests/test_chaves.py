import pandas as pd

from mercado.chaves import decodificar_chave, digito_verificador, limpar_chave, pseudonimizar_lojas

# chave FICTÍCIA montada para teste: SP, 2025-03, CNPJ 11.222.333/0001-81, NFC-e, série 1, nota 123456
BASE_43 = "35" + "2503" + "11222333000181" + "65" + "001" + "000123456" + "1" + "12345678"
CHAVE = BASE_43 + str(digito_verificador(BASE_43))


def test_decodifica_campos():
    info = decodificar_chave(CHAVE)
    assert info["valida"] and info["uf"] == "SP" and info["modelo"] == "NFC-e"
    assert (info["ano"], info["mes"], info["numero"], info["serie"]) == (2025, 3, 123456, 1)


def test_aceita_chave_com_espacos():
    com_espacos = " ".join(CHAVE[i:i + 4] for i in range(0, 44, 4))
    assert limpar_chave(com_espacos) == CHAVE and decodificar_chave(com_espacos)["valida"]


def test_digito_errado_e_tamanho_errado():
    errada = CHAVE[:43] + str((int(CHAVE[43]) + 1) % 10)
    assert not decodificar_chave(errada)["valida"]
    assert "43 dígitos" in decodificar_chave(CHAVE[:43])["problema"]


def test_digito_verificador_exemplo_conhecido():
    # resto 0 ou 1 => DV 0
    assert digito_verificador("0" * 43) == 0


def test_pseudonimo_tira_cnpj_e_e_estavel(tmp_path):
    arquivo = tmp_path / "lojas.csv"
    df = pd.DataFrame({"cnpj": ["x", "y", "y"], "chave": ["1", "2", "3"]})
    out = pseudonimizar_lojas(df, arquivo)
    assert "cnpj" not in out.columns and list(out["loja"]) == ["loja_A", "loja_B", "loja_B"]
    # loja nova aparece e fica mais frequente: as antigas NÃO mudam de letra
    df2 = pd.DataFrame({"cnpj": ["z", "z", "z", "y", "x"], "chave": list("abcde")})
    assert list(pseudonimizar_lojas(df2, arquivo)["loja"]) == ["loja_C"] * 3 + ["loja_B", "loja_A"]


def test_nome_apelido_passa_de_z():
    from mercado.lojas import nome_apelido
    assert (nome_apelido(0), nome_apelido(25), nome_apelido(26)) == ("loja_A", "loja_Z", "loja_AA")
