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

Precisa do **Python 3.9 ou mais novo**. Baixe em [python.org](https://www.python.org/downloads/)
e, no instalador, deixe marcado **"Add python.exe to PATH"** e **"tcl/tk and IDLE"**
(esse segundo é o que desenha a janela do classificador).

Abra o Prompt de Comando **na pasta do projeto** e rode:

```bat
cd /d "C:\Users\juy\OneDrive\SEPARADOR DE NOTAS PARA PAGAMENTO"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
copy config.ini.exemplo config.ini
```

Isso instala as dependências dos dois programas de uma vez. Para conferir:

```bat
python -c "import requests, pdfplumber, pymupdf, tkinter; print('ok')"
```

Depois edite o `config.ini` com o CNPJ, o caminho do `.pfx`, a senha e as pastas.
O `config.ini` está no `.gitignore` porque guarda a senha do certificado.

### Se der erro

| Mensagem | O que fazer |
|---|---|
| `'python' não é reconhecido` | reinstale o Python marcando "Add python.exe to PATH", ou use `py` no lugar de `python` |
| `No module named pip` | `python -m ensurepip --upgrade` |
| `No module named tkinter` | reinstale o Python marcando "tcl/tk and IDLE" |
| `Could not open requirements file` | você não está na pasta do projeto — use o `cd /d` acima |
| `Access is denied` / permissão | `python -m pip install --user -r requirements.txt` |
| `O pacote reportlab é obrigatório` | seu `pynfse-nacional` veio sem o extra: `python -m pip install "pynfse-nacional[pdf]"` |

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

## Classificador de vencimentos (`contas.py`)

Segunda etapa: lê os XMLs baixados, gera o DANFE/DACTE/DANFSe e arquiva o PDF
na pasta do vencimento (`ano/mês/dd-mm-aaaa`). Rode pelo `SEPARAR CONTAS.BAT`
ou com `python contas.py`.

### Prazo automático dos CT-e

O CT-e de frete quase nunca traz vencimento no XML. Ao abrir, o programa
pergunta em quantos dias após a emissão o frete vence — e guarda a resposta em
`preferencias_contas.json` para a próxima execução.

A ordem de decisão é:

1. duplicata do XML (`dVenc`), quando existe — vale para NF-e e CT-e;
2. "Vencimento: dd/mm/aaaa" impresso no PDF;
3. **só para CT-e**, emissão + o prazo informado.

Deixe o campo em `0` (ou vazio) para desligar. **Nota de serviço e qualquer
nota sem prazo continuam indo para a revisão manual, uma a uma** — o prazo do
frete não é aplicado a elas. O método usado fica registrado no
`_log_classificacao.csv` (`xml`, `pdf-regex`, `prazo-cte-28d`, `manual`…).

### Quando o PDF oficial não sai

Algumas NFS-e trazem o grupo IBS/CBS (reforma tributária) com campo vazio, e a
biblioteca que desenha o DANFSe recusa o XML inteiro por causa disso
(`cIndOp inválido`). Isso é um defeito dela: ela tenta ignorar falhas de
validação nesse grupo, mas a própria exceção não é capturada pelo `except`.

O classificador contorna em dois níveis:

1. tenta de novo sem o grupo IBS/CBS — ele só carrega tributos, nada que
   interesse ao controle de vencimento, e o DANFSe sai completo no resto;
2. se ainda assim não sair, monta uma **folha de resumo** com o que dá para ler
   do XML (número, emissão, valor, emitente, chave e o motivo da falha).

A nota nunca fica presa por causa disso. Quem for arquivado como resumo ganha
`_SEM_PDF_OFICIAL` no nome do arquivo e aparece contado no fim da execução, para
você saber quais conferir no portal.

O XML original nunca é alterado — a remoção do grupo acontece só na cópia usada
para desenhar o PDF.

### Nome dos arquivos

O PDF é arquivado como **`EMITENTE - NUMERO.pdf`** — quem emitiu a nota (no
CT-e, a transportadora) e o número dela. Se o XML não trouxer um dos dois, cai
para a chave de acesso.

Nota parcelada leva a parcela no nome, porque vai uma cópia para a pasta de
cada vencimento:

```
2026/08 - Agosto/14-08-2026/FORNECEDOR EXEMPLO LTDA - 4521 (parcela 1 de 2).pdf
2026/09 - Setembro/13-09-2026/FORNECEDOR EXEMPLO LTDA - 4521 (parcela 2 de 2).pdf
```

Se duas notas diferentes gerarem o mesmo nome, a segunda vira `... [2].pdf` —
nenhum arquivo é sobrescrito.

### Renomear o que já foi arquivado antes

Os PDFs arquivados antes desta mudança estão nomeados pela chave de acesso. Para
acertá-los, sem mexer nos que já estão no padrão novo:

```bat
python contas.py --renomear             ← prévia: mostra o que faria, não muda nada
python contas.py --renomear --aplicar   ← renomeia de verdade
python contas.py --desfazer-renomear    ← volta tudo, se não gostar
```

Como o XML é apagado depois do arquivamento, o nome do emitente é lido **de
dentro do próprio PDF**. Já o **número e o CNPJ saem da chave de acesso**, que os
carrega — nada de adivinhação:

| Chave | Onde está o CNPJ | Onde está o número |
|---|---|---|
| 44 dígitos (NF-e, CT-e) | posições 7 a 20 | posições 26 a 34 |
| 50 dígitos (NFS-e) | posições 10 a 23 | posições 24 a 36 |

Se o nome do emitente não puder ser lido do PDF, o arquivo fica
`CNPJ 12.345.678-0001-99 - 4521.pdf` — feio, mas correto e identificável.
Sufixos de parcela e de resumo são preservados, e nenhum arquivo é sobrescrito.

Sempre rode a prévia primeiro. A leitura do nome dentro do PDF foi testada com um
DANFE real; para DACTE e DANFSe ela usa marcações genéricas ("PRESTADOR",
"EMITENTE") e pode não acertar todos — a prévia existe justamente para você ver
antes, e o `--desfazer-renomear` para voltar depois.

### Notas parceladas

Quando o XML traz as duplicatas (`dVenc`), as parcelas são detectadas sozinhas
e cada pasta de vencimento recebe uma cópia.

Quando não traz — nota de serviço, ou nota sem data de vencimento — dá para
lançar as parcelas na revisão manual, de dois jeitos:

| O que você quer | O que digitar |
|---|---|
| Uma data só | `10/08/2026` |
| Datas irregulares | `10/08/2026, 25/09/2026, 30/10/2026` |
| 3 parcelas a cada 30 dias | `10/08/2026` e ponha **Parcelas: 3**, **a cada 30 dias** |
| 2 parcelas quinzenais | `01/08/2026` e ponha **Parcelas: 2**, **a cada 15 dias** |

Datas repetidas viram uma só, e a ordem não importa — são ordenadas sozinhas.
Cada parcela recebe uma cópia da nota na pasta do seu vencimento, e uma linha
própria no `_log_classificacao.csv`.

### Visualizador de PDF

A janela de revisão mostra a nota com zoom:

| Ação | Como |
|---|---|
| Aumentar / diminuir | botões `−` `+`, `Ctrl` + `+` / `Ctrl` + `−`, ou `Ctrl` + roda do mouse |
| Caber na largura | botão **Largura** (padrão ao abrir cada nota) |
| Caber a página inteira | botão **Ajustar** ou `Ctrl` + `0` |
| Rolar | roda do mouse, `Shift` + roda na horizontal, ou arrastar com o botão esquerdo |
| Trocar de página | botões `◀` `▶` |

O zoom escolhido na mão é respeitado; enquanto você não mexe, a nota se
reajusta sozinha ao redimensionar a janela.

## Testes

```bash
python -m unittest testes_baixador -v    # baixador
python -m unittest testes_contas -v      # classificador
```

Os testes não acessam a rede nem precisam de certificado real: usam sessões
HTTP falsas e um `.pfx` auto-assinado gerado na hora. Os do classificador
abrem janelas de verdade para conferir o zoom — em máquina sem interface
gráfica, esses são pulados automaticamente.
