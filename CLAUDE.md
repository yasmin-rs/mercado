# mercado — assistente de compras a partir de notas fiscais (NFC-e)

Segundo projeto do portfólio AI Builder (o primeiro é a central de reviews em `Desktop\if`).

## Ideia
- Histórico real de compras de mercado da autora (SP), a partir das chaves de NFC-e escaneadas no app Méliuz
  e, no futuro, escaneando o QR code da notinha (ou OCR).
- Objetivos: recorrência ("ovo acaba a cada ~20 dias" → lista de compras/alerta), estatísticas de gasto,
  alerta de variação de preço, metas (economizar, emagrecer, ganhar massa) e a IA comentando os hábitos.

## Regras
- **Nunca expor qual mercado**: CNPJ e nome da loja viram pseudônimo (loja_A, loja_B...) antes de qualquer saída publicável.
- `data/` e `outputs/` ficam fora do git (chaves têm CNPJ; notas têm CPF parcial e endereço). Nada de chave no código; `.env` fora do git.
- Antes de qualquer lote que chame modelo pago: mostrar estimativa (chamadas × tokens) e pedir confirmação.
- Não fazer commit (a autora commita). Nomes de funções e comentários em português.
- Não automatizar quebra de captcha da Sefaz: consulta semiautomática (a pessoa abre e salva a página) ou via link do QR code.

## Estado atual
- Pipeline completo: foto -> QR (qrcode.py, Sefaz sem captcha) ou visão (ler_foto.py, com conferência) ->
  notas.py -> padronizar.py (Gemini + dicionário + correções) -> recorrencia.py / habitos.py -> app/app.py.
- `demo.py` gera `demo/itens.csv` (base PÚBLICA anonimizada: sem Kaggle, farmácia mascarada, sem horário/marca).
  O app entra em modo demo sem `outputs/itens_padronizados.csv` ou com MODO_DEMO=1.
- Análises da demo pré-geradas em `demo/analises/<meta>.json` (`python -m mercado.habitos --entrada demo/itens.csv --saida demo/analises`).
- Notas do Kaggle (`data/kaggle/`, `fonte=kaggle`): dataset https://www.kaggle.com/datasets/sc0v1n0/electronic-tax-document, CC0.
  Entram na demo (só nome padronizado); a análise de hábitos usa só as notas da autora.

## Próximos passos
1. Publicar e preencher LINK_DA_DEMO no README.
2. Streamlit Cloud: secret GEMINI_API_KEY.
3. Alerta de variação de preço.
