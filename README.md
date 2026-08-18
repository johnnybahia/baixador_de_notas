# Baixador de notas fiscais (NF-e, CT-e e NFS-e)

Script Python que baixa, com certificado digital A1, os XMLs dos documentos
fiscais em que o seu CNPJ é parte interessada:

| Documento | Serviço | Protocolo | Endpoint |
|---|---|---|---|
| Nota fiscal de produto (NF-e) | `NFeDistribuicaoDFe` | SOAP | `www1.nfe.fazenda.gov.br` |
| Nota de transporte (CT-e) | `CTeDistribuicaoDFe` | SOAP | `www1.cte.fazenda.gov.br` |
| Nota de serviço (NFS-e nacional) | ADN `/contribuintes/DFe/{NSU}` | REST/JSON | `adn.nfse.gov.br` |

Os três serviços funcionam por **NSU** (número sequencial único): cada execução
continua de onde a anterior parou, e o ponteiro fica gravado em
`estado_nsu.json`, na pasta de controle.

## Instalação

```bash
pip install -r requirements.txt
cp config.ini.exemplo config.ini      # no Windows: copy
```

Edite o `config.ini` com o CNPJ, o caminho do `.pfx`, a senha e as pastas.
O `config.ini` está no `.gitignore` porque guarda a senha do certificado.

## Uso

```bash
python baixador.py                        # varre os três serviços
python baixador.py --servico nfe cte      # só NF-e e CT-e
python baixador.py --status               # mostra NSU/bloqueios e sai
python baixador.py --servico nfse --nsu 0 # reprocessa a NFS-e do começo
python baixador.py --max-lotes 5 -v       # execução curta, log detalhado
python baixador.py --ultimos 30           # só os últimos 30 dias
python baixador.py --chave 2925...2349    # uma nota específica
```

Outras opções: `--config CAMINHO`, `--sem-conferencia`, `--ignorar-bloqueio`.

## Escolher um período (em vez dos 90 dias)

**As três APIs paginam por NSU e nenhuma aceita filtro por data.** Não existe
"me dê só julho". O que o script faz é baixar tudo do mesmo jeito e **separar
por pasta** — nada é descartado:

```
<pasta de saída>/
├── <notas do período>.xml
├── _EVENTOS_E_RESUMOS/
└── _FORA_DO_PERIODO/     ← baixadas, fora do intervalo escolhido
```

```bash
python baixador.py --de 01/07/2026 --ate 31/07/2026
python baixador.py --ultimos 15                       # janela móvel
```

Também dá para fixar no `config.ini`, seção `[PERIODO]` (`de`, `ate` ou
`ultimos_dias`), útil para execução agendada. A linha de comando tem preferência.
Sem nada configurado, tudo cai direto na pasta de saída, como antes.

O filtro vale para nota **e** evento, cada um pela sua própria data — um
cancelamento de agosto de uma nota de julho vai para `_FORA_DO_PERIODO`, mas
está lá. Documento cuja data não dá para ler é sempre tratado como dentro do
período, para não sumir da vista por falha de leitura.

### `--pular-anteriores` (esse sim deixa notas para trás)

```bash
python baixador.py --de 01/07/2026 --pular-anteriores
```

Faz uma busca binária (~10 a 15 requisições) pelo NSU em que o período começa e
salta direto para lá, com margem de 200 NSUs para trás por causa de nota
autorizada com atraso. Serve para quando você quer economizar tempo e cota da
SEFAZ ao começar do zero. Vale para NF-e e CT-e; a NFS-e não tem como, porque o
ADN não informa o `maxNSU`.

> **Atenção:** o que for pulado **não volta** nas próximas execuções, porque o
> ponteiro de NSU avança. Se a ideia é não perder nada, não use esta opção — o
> período sozinho já separa as pastas sem descartar nota. Para buscar o que ficou
> para trás: `--servico nfe --nsu 0`.

## Baixar documentos avulsos por chave

Até 10 chaves por execução, misturando os três tipos. O script identifica
sozinho o que é cada uma (modelo 55/65 = NF-e, 57/67 = CT-e, 50 dígitos = NFS-e):

```bash
python baixador.py --chave 29250712345678000199550010000012341000012349
python baixador.py --chave CHAVE_NFE CHAVE_CTE CHAVE_NFSE
python baixador.py --chave "2925 0712 3456 7800 0199 5500 1000 0012 3410 0001 2349"
```

Aceita chave crua, colada do portal em grupos de 4, ou várias separadas por
espaço, vírgula ou quebra de linha. Nesse modo o script **não mexe no NSU** e
**ignora o filtro de período** — você pediu aquela nota, ela é gravada.

Por baixo: NF-e e CT-e vão por `consChNFe`/`consChCTe` no mesmo
`DistribuicaoDFe`; NFS-e vai no Sefin Nacional (`GET /nfse/{chave}`).

Limitação da SEFAZ: a consulta por chave só devolve o XML se o CNPJ do
certificado for parte interessada no documento (destinatário, tomador…). Para
nota de terceiro vem `cStat 137` sem documento.

Para rodar sozinho, agende `python baixador.py` no Agendador de Tarefas
(Windows) ou no cron a cada 1–2 horas. Um arquivo de lock impede que duas
execuções se atropelem.

## O que é gravado

```
<pasta de saída>/
├── 29250712345678000199550010000012341000012349.xml   ← documento, nome = chave
├── _EVENTOS_E_RESUMOS/
│   └── 2925...349-procEventoNFe-000000000000042.xml   ← evento/resumo
└── _FORA_DO_PERIODO/                                  ← só se houver período
```

- **Documento** (`nfeProc`, `cteProc`, `NFSe`, …) vai para a raiz da pasta de
  saída, nomeado pela chave de acesso — então rodar duas vezes não duplica nada.
- **Evento e resumo** (`resNFe`, `procEventoNFe`, cancelamento, ciência da
  operação…) vão para `_EVENTOS_E_RESUMOS`, com raiz e NSU no nome, porque a
  mesma chave costuma ter vários.

Na pasta de controle ficam `estado_nsu.json`, `baixador.log`,
`baixador.lock` e, quando há conferência, `relatorio_faltantes.csv`.

## Conferência com planilha (opcional)

Se a pasta `planilhas` tiver arquivos `.xlsx` com uma coluna **`Chave`**, o
script compara as chaves da planilha com os XMLs baixados e escreve as que
faltaram em `relatorio_faltantes.csv`. Colunas opcionais aproveitadas no
relatório: `Tipo`, `Num`, `DtAut`, `Valor`, `Emissor Nome`.

A causa mais comum de chave faltante é a **janela de 90 dias**: o Ambiente
Nacional não entrega documento mais antigo que isso via distribuição por NSU.

## Cuidados de operação

- **Pasta de controle em disco local, nunca no OneDrive.** Se o
  `estado_nsu.json` for sincronizado entre máquinas, o NSU volta atrás ou pula.
- **cStat 656 (consumo indevido).** A SEFAZ bloqueia por ~1h quem consulta
  demais. O script detecta, grava o bloqueio no estado e pula o serviço nas
  execuções seguintes até o prazo vencer. Não force com `--ignorar-bloqueio`
  sem necessidade, e mantenha `pausa_segundos >= 2`.
- **NFS-e retornando 401/403.** O ADN só entrega documento em que o CNPJ do
  certificado é prestador, tomador ou intermediário, e o município precisa
  estar conveniado ao sistema nacional. NFS-e de prefeitura com sistema próprio
  não passa por aqui.
- **`cuf`** é o código IBGE da UF do autor da consulta (29 BA, 31 MG, 33 RJ,
  35 SP, 41 PR…), não o do emissor da nota.

## Testes

```bash
python -m unittest testes_baixador -v
```

Os testes não acessam a rede nem precisam de certificado real: usam sessões
HTTP falsas e um `.pfx` auto-assinado gerado na hora.
