"""
Classificador de notas fiscais por data de vencimento (NF-e, NFS-e, CT-e)
Gera o PDF (DANFE/DACTE/DANFSe) localmente a partir do XML - a pasta de
origem so precisa conter os XMLs.

Requisitos:
    pip install -r requirements.txt
"""

import os
import re
import sys
import csv
import shutil
import tempfile
import traceback
import json
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path
import tkinter as tk

import pdfplumber

# ===================== CONFIGURACAO =====================
# Estes sao apenas os valores padrao. Se preferencias_contas.json trouxer
# "pasta_origem" / "pasta_destino", eles mandam - assim atualizar este
# arquivo nao apaga os caminhos da sua maquina.
PASTA_ORIGEM = Path(r"C:\Users\juy\OneDrive\SEPARADOR DE NOTAS PARA PAGAMENTO\NOTAS DE ENTRADA")          # so XML
PASTA_DESTINO = Path(r"C:\Users\juy\OneDrive\SEPARADOR DE NOTAS PARA PAGAMENTO\NOTAS DE DESTINO")
PASTA_PENDENTES = PASTA_DESTINO / "_PENDENTES"
PASTA_A_VISTA = PASTA_DESTINO / "_A_VISTA_SEM_VENCIMENTO"
ARQUIVO_LOG = PASTA_DESTINO / "_log_classificacao.csv"

MESES_PT = {
    1: "01 - Janeiro", 2: "02 - Fevereiro", 3: "03 - Março", 4: "04 - Abril",
    5: "05 - Maio", 6: "06 - Junho", 7: "07 - Julho", 8: "08 - Agosto",
    9: "09 - Setembro", 10: "10 - Outubro", 11: "11 - Novembro", 12: "12 - Dezembro",
}

REGEX_DATA = re.compile(r"(\d{2})[/.\-](\d{2})[/.\-](\d{4})")
REGEX_VENCIMENTO_PDF = re.compile(
    r"vencimento[:\s]*[^\d]{0,15}(\d{2})[/.\-](\d{2})[/.\-](\d{4})", re.IGNORECASE
)
REGEX_CHAVE = re.compile(r"\d{50}|\d{44}")
# o Windows recusa estes caracteres em nome de arquivo
CARACTERES_PROIBIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Preferencias que a janela inicial guarda entre execucoes
ARQUIVO_PREFERENCIAS = Path(__file__).parent / "preferencias_contas.json"

# Zoom do visualizador de PDF
ZOOM_MINIMO = 0.25
ZOOM_MAXIMO = 6.0
ZOOM_PASSO = 1.25


# ===================== XML =====================
def ler_xml(xml_path):
    root = ET.parse(xml_path).getroot()
    try:
        texto = xml_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        texto = xml_path.read_text(encoding="latin-1")
    return texto.lstrip("\ufeff"), root


def extrair_chave(root):
    for el in root.iter():
        for valor in el.attrib.values():
            m = REGEX_CHAVE.search(valor)
            if m:
                return m.group(0)
    return None


def identificar_tipo(root):
    tag_raiz = root.tag.split("}")[-1].lower()
    if "cte" in tag_raiz:
        return "CTe"
    if "nfse" in tag_raiz:
        return "NFSe"
    if "nfe" in tag_raiz:
        return "NFe"
    # fallback estrutural: CT-e ANTES de NF-e (CT-e contem infNFe da carga transportada)
    if root.find(".//{*}infCte") is not None:
        return "CTe"
    if root.find(".//{*}infNFSe") is not None:
        return "NFSe"
    for el in root.findall(".//{*}infNFe"):
        if el.attrib.get("Id"):
            return "NFe"
    return "DESCONHECIDO"


def _parse_data_iso_ou_br(texto):
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            continue
    return None


def extrair_vencimentos_nfe_cte(root):
    vencimentos = []
    for dup in root.findall(".//{*}dup"):
        dvenc = dup.find("{*}dVenc")
        if dvenc is not None and dvenc.text:
            data = _parse_data_iso_ou_br(dvenc.text.strip())
            if data:
                vencimentos.append(data)
    return sorted(set(vencimentos))


def extrair_data_emissao(root):
    """Emissao do proprio documento (o CT-e cita chaves de NF-e, nao datas)."""
    for caminho in (".//{*}infCte/{*}ide/{*}dhEmi", ".//{*}infCte/{*}ide/{*}dEmi"):
        el = root.find(caminho)
        if el is not None and el.text:
            data = _parse_data_iso_ou_br(el.text.strip()[:10])
            if data:
                return data

    for tag in ("dhEmi", "dEmi", "dhProc"):
        for el in root.iter():
            if el.tag.split("}")[-1] == tag and el.text:
                data = _parse_data_iso_ou_br(el.text.strip()[:10])
                if data:
                    return data
    return None


def vencimento_por_prazo(root, tipo, prazo_dias):
    """
    Prazo automatico do frete: emissao + N dias, so para CT-e.
    NFS-e e qualquer documento sem prazo continuam indo para a revisao manual.
    """
    if tipo != "CTe" or not prazo_dias:
        return None
    emissao = extrair_data_emissao(root)
    if emissao is None:
        return None
    return emissao + timedelta(days=prazo_dias)


def primeiro_texto(root, tags):
    for tag in tags:
        for el in root.iter():
            if el.tag.split("}")[-1] == tag and el.text and el.text.strip():
                return el.text.strip()
    return ""


def nome_do_emitente(root):
    """Quem emitiu a nota - e quem voce paga. No CT-e, a transportadora."""
    for grupo in ("emit", "prest", "prestador"):
        for el in root.iter():
            if el.tag.split("}")[-1] == grupo:
                nome = primeiro_texto(el, ("xNome", "xFant"))
                if nome:
                    return nome
    return primeiro_texto(root, ("xNome", "xFant"))


def numero_do_documento(root):
    return primeiro_texto(root, ("nNFSe", "nNF", "nCT", "nDPS"))


def limpar_para_arquivo(texto, limite=60):
    """Tira o que o Windows nao aceita em nome de arquivo."""
    texto = CARACTERES_PROIBIDOS.sub("", texto or "")
    texto = re.sub(r"\s+", " ", texto).strip(" .")
    return texto[:limite].strip(" .")


def nome_base_arquivo(root, chave, xml_path):
    """
    Nome do PDF: "EMITENTE - NUMERO". Cai para a chave quando o XML nao
    traz um dos dois - melhor um nome feio do que dois arquivos iguais.
    """
    emitente = limpar_para_arquivo(nome_do_emitente(root))
    numero = limpar_para_arquivo(numero_do_documento(root), limite=15)

    if emitente and numero:
        return f"{emitente} - {numero}"
    if emitente:
        return emitente
    return chave or xml_path.stem


def interpretar_vencimentos(texto, parcelas="1", intervalo="30"):
    """
    Le o campo de vencimento da revisao manual. Aceita:
      "10/08/2026"                          -> uma parcela
      "10/08/2026, 10/09/2026"              -> duas, como digitadas
      "10/08/2026" + parcelas 3, a cada 30  -> 10/08, 09/09 e 09/10
    """
    datas = []
    for d, m, a in REGEX_DATA.findall(texto or ""):
        try:
            datas.append(date(int(a), int(m), int(d)))
        except ValueError:
            raise ValueError(f"Data invalida: {d}/{m}/{a}")
    if not datas:
        raise ValueError("Informe ao menos uma data (DD/MM/AAAA).")

    try:
        quantidade = int(str(parcelas).strip() or 1)
        passo = int(str(intervalo).strip() or 30)
    except ValueError:
        raise ValueError("Parcelas e intervalo devem ser numeros.")
    if quantidade < 1 or passo < 1:
        raise ValueError("Parcelas e intervalo devem ser maiores que zero.")

    if quantidade > 1:
        if len(datas) == 1:
            datas = [datas[0] + timedelta(days=passo * i) for i in range(quantidade)]
        elif len(datas) != quantidade:
            raise ValueError(
                f"Voce digitou {len(datas)} data(s) e pediu {quantidade} parcelas."
            )
    return sorted(set(datas))


def extrair_vencimento_nfse(root):
    for el in root.iter():
        tag_local = el.tag.split("}")[-1]
        if "venc" in tag_local.lower() and el.text:
            data = _parse_data_iso_ou_br(el.text.strip())
            if data:
                return data
    return None


# ===================== GERACAO DE PDF =====================
def remover_ibscbs(xml_texto):
    """
    Devolve o XML sem o grupo IBS/CBS (reforma tributaria), ou None se ele
    nem existia.

    Motivo: NFS-e que trazem o grupo com campo vazio (cIndOp, por exemplo)
    derrubam o parser da pynfse_nacional. A propria biblioteca tenta ignorar
    esse tipo de falha, mas a excecao dela nao herda de ValueError e escapa
    do except. Sem o grupo, o parser devolve None e o DANFSe sai normal -
    o grupo so carrega tributos, nada que apareca no controle de vencimento.

    Mexe apenas na copia usada para desenhar o PDF; o XML gravado fica intacto.
    """
    raiz = ET.fromstring(xml_texto)
    removidos = 0
    for pai in raiz.iter():
        for filho in list(pai):
            if filho.tag.split("}")[-1] == "IBSCBS":
                pai.remove(filho)
                removidos += 1
    if not removidos:
        return None

    # sem isto o ElementTree devolveria tudo prefixado com ns0:
    if raiz.tag.startswith("{"):
        ET.register_namespace("", raiz.tag[1:].split("}")[0])
    return ET.tostring(raiz, encoding="unicode")


def gerar_pdf(tipo, xml_texto, destino_pdf):
    if tipo == "NFe":
        from brazilfiscalreport.danfe import Danfe
        Danfe(xml=xml_texto).output(str(destino_pdf))
    elif tipo == "CTe":
        from brazilfiscalreport.dacte import Dacte
        Dacte(xml_texto).output(str(destino_pdf))
    elif tipo == "NFSe":
        from pynfse_nacional.pdf_generator import generate_danfse_from_xml
        try:
            generate_danfse_from_xml(xml_content=xml_texto, output_path=str(destino_pdf))
        except Exception:
            sem_ibscbs = remover_ibscbs(xml_texto)
            if sem_ibscbs is None:
                raise
            generate_danfse_from_xml(
                xml_content=sem_ibscbs, output_path=str(destino_pdf)
            )
    else:
        raise ValueError(f"Tipo desconhecido, nao sei gerar PDF: {tipo}")


# Campos aproveitados no PDF de resumo, em ordem de preferencia
CAMPOS_RESUMO = (
    ("Numero", ("nNFSe", "nNF", "nCT")),
    ("Serie", ("serie",)),
    ("Emissao", ("dhEmi", "dEmi", "dhProc")),
    ("Valor", ("vLiq", "vServ", "vNF", "vTPrest", "vRec")),
    ("Emitente", ("xNome", "xFant")),
    ("Descricao", ("xDescServ", "xTribNac", "xNat", "natOp")),
)


def gerar_pdf_resumo(root, tipo, chave, destino_pdf, motivo=""):
    """
    Plano B: quando nem a biblioteca oficial monta o documento, desenha uma
    folha com os dados que dao para ler do XML. Assim a nota nao trava a fila
    - da para conferir o vencimento e arquivar como qualquer outra.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as rl_canvas

    largura, altura = A4
    pdf = rl_canvas.Canvas(str(destino_pdf), pagesize=A4)
    y = altura - 25 * mm

    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(20 * mm, y, f"RESUMO DO DOCUMENTO ({tipo})")
    y -= 7 * mm
    pdf.setFont("Helvetica", 9)
    pdf.drawString(20 * mm, y, "O PDF oficial nao pode ser gerado a partir deste XML.")
    y -= 12 * mm

    linhas = [("Chave", chave or "(nao encontrada)")]
    linhas += [(rotulo, primeiro_texto(root, tags)) for rotulo, tags in CAMPOS_RESUMO]

    for rotulo, valor in linhas:
        if not valor:
            continue
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(20 * mm, y, f"{rotulo}:")
        pdf.setFont("Helvetica", 10)
        # quebra o que for comprido demais para a linha
        texto = str(valor)
        while texto:
            pedaco, texto = texto[:78], texto[78:]
            pdf.drawString(50 * mm, y, pedaco)
            y -= 6 * mm
        y -= 2 * mm

    if motivo:
        y -= 6 * mm
        pdf.setFont("Helvetica-Oblique", 8)
        pdf.drawString(20 * mm, y, "Motivo:")
        y -= 5 * mm
        motivo = str(motivo)
        while motivo and y > 20 * mm:
            pedaco, motivo = motivo[:105], motivo[105:]
            pdf.drawString(20 * mm, y, pedaco)
            y -= 4.5 * mm

    pdf.showPage()
    pdf.save()


def extrair_vencimento_pdf(caminho_pdf):
    try:
        with pdfplumber.open(caminho_pdf) as pdf:
            texto = "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception:
        return None
    m = REGEX_VENCIMENTO_PDF.search(texto)
    if not m:
        return None
    d, mth, a = m.groups()
    try:
        return datetime(int(a), int(mth), int(d)).date()
    except ValueError:
        return None


def abrir_pdf(caminho_pdf):
    try:
        os.startfile(caminho_pdf)
    except Exception as e:
        print(f"  (nao consegui abrir o PDF automaticamente: {e})")


def abrir_documento(caminho_pdf):
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    return fitz.open(caminho_pdf)


def renderizar_pagina(pagina, escala):
    """Pagina do PDF -> imagem do Tk, na escala pedida."""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    from PIL import Image, ImageTk
    pix = pagina.get_pixmap(matrix=fitz.Matrix(escala, escala))
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return ImageTk.PhotoImage(img)


def escala_de_ajuste(pagina, largura, altura):
    """Escala que faz a pagina caber inteira no espaco disponivel."""
    if pagina.rect.width <= 0 or pagina.rect.height <= 0:
        return 1.0
    return min(largura / pagina.rect.width, altura / pagina.rect.height)


def limitar_escala(escala, minimo=ZOOM_MINIMO, maximo=ZOOM_MAXIMO):
    return max(minimo, min(maximo, escala))


# ============ RENOMEAR O QUE JA FOI ARQUIVADO PELA CHAVE ============
# O XML e apagado depois de arquivado, entao o nome do emitente so pode vir
# do proprio PDF. O numero e o CNPJ, esses, saem da chave - sem adivinhacao.
RE_EMITENTE_DANFE = re.compile(r"RECEBEMOS DE\s+(.+?)\s+OS PRODUTOS", re.IGNORECASE)
# Terminacao de razao social - o que realmente identifica uma empresa
RE_RAZAO_SOCIAL = re.compile(
    r"\b(LTDA|EIRELI|EPP|MEI|S/A|S\.A|SA)\b\.?", re.IGNORECASE
)
# Titulos e cabecalhos do proprio documento, que nao sao nome de ninguem
RE_TITULO_DOCUMENTO = re.compile(
    r"DANFE|DACTE|DANFSE|NOTA FISCAL|CONHECIMENTO DE TRANSPORTE|"
    r"DOCUMENTO AUXILIAR|CHAVE DE ACESSO|PRESTADOR|TOMADOR|DESTINAT|REMETENTE|"
    r"EMITENTE|CONSULTA DE AUTENTICIDADE|SECRETARIA|MINISTERIO",
    re.IGNORECASE,
)
ANCORAS_EMITENTE = ("PRESTADOR", "IDENTIFICACAO DO EMITENTE",
                    "IDENTIFICAÇÃO DO EMITENTE", "EMITENTE", "REMETENTE")
ARQUIVO_RENOMEACOES = "_renomeacoes.csv"


def dados_da_chave(chave):
    """
    CNPJ e numero que a propria chave carrega.
      44 digitos: cUF(2) AAMM(4) CNPJ(14) mod(2) serie(3) nNF(9) ...
      50 digitos (NFS-e): IBGE(7) amb(1) tpInsc(1) CNPJ(14) nNFSe(13) ...
    """
    try:
        if len(chave) == 44:
            return {"cnpj": chave[6:20], "numero": str(int(chave[25:34]))}
        if len(chave) == 50:
            return {"cnpj": chave[9:23], "numero": str(int(chave[23:36]))}
    except ValueError:
        return None
    return None


def formatar_cnpj(cnpj):
    if len(cnpj) != 14 or not cnpj.isdigit():
        return cnpj
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"


def emitente_do_pdf(caminho_pdf):
    """Tenta ler a razao social do emitente no texto do PDF. None se nao achar."""
    try:
        with pdfplumber.open(caminho_pdf) as pdf:
            texto = "\n".join(p.extract_text() or "" for p in pdf.pages[:1])
    except Exception:
        return None

    m = RE_EMITENTE_DANFE.search(texto)          # canhoto do DANFE
    if m:
        return m.group(1).strip()

    def parece_empresa(linha):
        # "DACTE - CONHECIMENTO DE TRANSPORTE" nao e nome de empresa, embora
        # tenha a palavra transporte; por isso os titulos sao descartados.
        return (
            RE_RAZAO_SOCIAL.search(linha)
            and not RE_TITULO_DOCUMENTO.search(linha)
            and len(linha) <= 70
        )

    linhas = [l.strip() for l in texto.splitlines() if l.strip()]
    for i, linha in enumerate(linhas):           # linha logo apos um cabecalho
        if any(a in linha.upper() for a in ANCORAS_EMITENTE):
            for seguinte in linhas[i + 1:i + 4]:
                if parece_empresa(seguinte):
                    return seguinte
    for linha in linhas[:25]:                    # ultimo recurso
        if parece_empresa(linha):
            return linha
    return None


def novo_nome_para(caminho_pdf):
    """
    Nome no padrao novo para um PDF ja arquivado, ou None quando ele nao esta
    nomeado pela chave (ou seja, ja foi renomeado antes).
    """
    m = REGEX_CHAVE.match(caminho_pdf.stem)
    if not m:
        return None
    chave = m.group(0)
    dados = dados_da_chave(chave)
    if not dados:
        return None

    emitente = emitente_do_pdf(caminho_pdf)
    if not emitente:
        # a barra do CNPJ nao pode virar nome de arquivo no Windows
        emitente = "CNPJ " + formatar_cnpj(dados["cnpj"]).replace("/", "-")

    resto = caminho_pdf.stem[len(chave):]        # "(parcela 1 de 2)", "_SEM_PDF_OFICIAL"
    base = limpar_para_arquivo(f"{emitente} - {dados['numero']}")
    return f"{base}{resto}{caminho_pdf.suffix}"


def renomear_existentes(pasta, aplicar=False):
    """
    Passa nas pastas de destino e renomeia so o que ainda esta com a chave.
    Sem --aplicar, apenas mostra o que faria.
    """
    if not pasta.exists():
        print(f"Pasta nao encontrada: {pasta}")
        return []

    mudancas = []
    for caminho in sorted(pasta.rglob("*.pdf")):
        novo = novo_nome_para(caminho)
        if not novo or novo == caminho.name:
            continue
        mudancas.append((caminho, caminho_livre(caminho.parent, novo)))

    if not mudancas:
        print("Nada a renomear: todos os PDFs ja estao no padrao novo.")
        return []

    print(f"{len(mudancas)} arquivo(s) a renomear:\n")
    for antigo, novo in mudancas[:40]:
        print(f"  {antigo.name}\n    -> {novo.name}")
    if len(mudancas) > 40:
        print(f"  ... e mais {len(mudancas) - 40}")

    if not aplicar:
        print("\nIsto foi so uma previa - nada mudou ainda.")
        print("Para aplicar:  python contas.py --renomear --aplicar")
        return mudancas

    registro = pasta / ARQUIVO_RENOMEACOES
    novo_registro = not registro.exists()
    feitos = 0
    with open(registro, "a", newline="", encoding="utf-8-sig") as f:
        escritor = csv.writer(f, delimiter=";")
        if novo_registro:
            escritor.writerow(["data", "antigo", "novo"])
        agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for antigo, novo in mudancas:
            try:
                antigo.rename(novo)
            except OSError as e:
                print(f"  nao consegui renomear {antigo.name}: {e}")
                continue
            escritor.writerow([agora, str(antigo), str(novo)])
            feitos += 1

    print(f"\n{feitos} arquivo(s) renomeado(s). Registro em: {registro}")
    print("Para voltar atras:  python contas.py --desfazer-renomear")
    return mudancas


def desfazer_renomeacoes(pasta):
    """Volta os nomes usando o registro deixado pelo --renomear --aplicar."""
    registro = pasta / ARQUIVO_RENOMEACOES
    if not registro.exists():
        print(f"Nao ha registro de renomeacao em {registro}")
        return 0

    with open(registro, newline="", encoding="utf-8-sig") as f:
        linhas = list(csv.reader(f, delimiter=";"))[1:]

    voltados = 0
    for linha in reversed(linhas):               # desfaz do mais recente
        if len(linha) < 3:
            continue
        antigo, novo = Path(linha[1]), Path(linha[2])
        if novo.exists() and not antigo.exists():
            try:
                novo.rename(antigo)
                voltados += 1
            except OSError as e:
                print(f"  nao consegui voltar {novo.name}: {e}")

    registro.unlink(missing_ok=True)
    print(f"{voltados} arquivo(s) com o nome antigo de volta.")
    return voltados


# ============ REPROCESSAR PDFs QUE JA ESTAO EM PASTA ============
# Serve para as notas que ficaram em _PENDENTES (ou que foram parar na
# pasta errada): o XML nao existe mais, mas o PDF tem chave, emitente e as
# vezes o proprio vencimento impresso.
MODELO_TIPO = {"55": "NFe", "65": "NFe", "57": "CTe", "67": "CTe"}


def pdf_legivel(caminho_pdf):
    """Arquivo corrompido nao adianta mandar para a revisao: a janela
    tambem nao conseguiria desenhar. Melhor avisar e deixar onde esta."""
    try:
        with pdfplumber.open(caminho_pdf) as pdf:
            return len(pdf.pages) > 0
    except Exception:
        return False


def texto_do_pdf(caminho_pdf, paginas=1):
    try:
        with pdfplumber.open(caminho_pdf) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages[:paginas])
    except Exception:
        return ""


RE_CHAVE_AGRUPADA = re.compile(r"(?:\d{4}[ .\-]+){12}\d{2}|(?:\d{4}[ .\-]+){10}\d{4}")
RE_CHAVE_ISOLADA = re.compile(r"(?<!\d)(?:\d{50}|\d{44})(?!\d)")


def dv_chave_valido(chave):
    """Digito verificador (modulo 11) da chave de 44 digitos."""
    if len(chave) != 44 or not chave.isdigit():
        return True                     # a de 50 usa outra regra; nao barra
    peso, soma = 2, 0
    for digito in reversed(chave[:43]):
        soma += int(digito) * peso
        peso = 2 if peso == 9 else peso + 1
    resto = soma % 11
    esperado = 0 if resto in (0, 1) else 11 - resto
    return esperado == int(chave[43])


def chave_do_pdf(texto):
    """
    Chave impressa no DANFE/DACTE/DANFSe, normalmente em grupos de 4 digitos.

    Juntar o texto inteiro nao serve: numeros de campos vizinhos se colam e
    formam uma chave que nao existe. Por isso aceitamos so o formato agrupado
    ou uma sequencia isolada na linha - e ainda conferimos o digito verificador.
    """
    candidatas = [re.sub(r"\D", "", t) for t in RE_CHAVE_AGRUPADA.findall(texto or "")]
    for linha in (texto or "").splitlines():
        compacta = re.sub(r"[ .\-]", "", linha)
        candidatas.extend(m.group(0) for m in RE_CHAVE_ISOLADA.finditer(compacta))

    for chave in candidatas:
        if len(chave) in (44, 50) and dv_chave_valido(chave):
            return chave
    return None


def tipo_pela_chave(chave):
    if not chave:
        return "DESCONHECIDO"
    if len(chave) == 50:
        return "NFSe"
    return MODELO_TIPO.get(chave[20:22], "DESCONHECIDO")


def item_de_pdf(caminho_pdf):
    """Monta o mesmo 'item' da fase 1, mas lendo o PDF em vez do XML."""
    texto = texto_do_pdf(caminho_pdf)
    chave = chave_do_pdf(texto) or (
        REGEX_CHAVE.search(caminho_pdf.stem).group(0)
        if REGEX_CHAVE.search(caminho_pdf.stem) else None
    )
    dados = dados_da_chave(chave) if chave else None
    emitente = emitente_do_pdf(caminho_pdf)

    if emitente and dados:
        nome_base = limpar_para_arquivo(f"{emitente} - {dados['numero']}")
    elif emitente:
        nome_base = limpar_para_arquivo(emitente)
    elif dados:
        nome_base = limpar_para_arquivo(
            "CNPJ " + formatar_cnpj(dados["cnpj"]).replace("/", "-")
            + f" - {dados['numero']}"
        )
    else:
        nome_base = caminho_pdf.stem

    return {
        "xml_path": caminho_pdf,      # so para o log; aqui a origem e o PDF
        "pdf_temp": caminho_pdf,
        "tipo": tipo_pela_chave(chave),
        "chave": chave,
        "nome_base": nome_base,
        "resumo": False,
    }


def reprocessar_pdfs(pasta):
    """
    Le os PDFs de uma pasta e os arquiva por vencimento, como se fossem notas
    novas. Quem tiver vencimento impresso vai direto; o resto passa pela
    janela de revisao. O arquivo original sai da pasta depois de arquivado.
    """
    if not pasta.exists():
        print(f"Pasta nao encontrada: {pasta}")
        return

    pdfs = sorted(p for p in pasta.glob("*.pdf"))
    if not pdfs:
        print(f"Nenhum PDF em {pasta}")
        return

    print(f"{len(pdfs)} PDF(s) em {pasta}\n")
    linhas_log = []
    fila = []
    contagem = {"auto": 0, "manual": 0, "a_vista": 0, "pendente": 0}

    def concluir(item, vencimentos, metodo):
        finalizar_item(item, vencimentos, metodo, linhas_log)
        if metodo != "pular":          # "pular" deixa o arquivo onde estava
            try:
                item["pdf_temp"].unlink()
            except OSError as e:
                print(f"  arquivei mas nao consegui remover o original: {e}")

    for caminho in pdfs:
        try:
            if not pdf_legivel(caminho):
                print(f"  {caminho.name}: PDF ilegivel, deixado onde estava.")
                continue
            item = item_de_pdf(caminho)
            data = extrair_vencimento_pdf(caminho)
            if data:
                concluir(item, [data], "pdf-regex")
                contagem["auto"] += 1
                print(f"  {caminho.name}: vence em {data:%d/%m/%Y}")
            else:
                fila.append(item)
        except Exception:
            print(f"Erro lendo {caminho.name}:")
            traceback.print_exc()

    if fila:
        print(f"\n{len(fila)} sem vencimento no PDF - abrindo a revisao manual.")

        def callback(item, vencimentos, metodo):
            concluir(item, vencimentos, metodo)
            if metodo == "manual":
                contagem["manual"] += 1
            elif metodo == "a_vista":
                contagem["a_vista"] += 1
            else:
                contagem["pendente"] += 1

        RevisorApp(fila, callback)

    registrar_log(linhas_log)
    print(
        f"\nConcluido: {contagem['auto']} pelo vencimento impresso, "
        f"{contagem['manual']} manual(is), {contagem['a_vista']} a vista, "
        f"{contagem['pendente']} deixado(s) para depois."
    )


# ===================== ORGANIZACAO EM PASTAS =====================
def pasta_destino_para_data(data):
    pasta = PASTA_DESTINO / str(data.year) / MESES_PT[data.month] / data.strftime("%d-%m-%Y")
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def nome_do_arquivo(item, indice=0, total=1):
    """
    "EMITENTE - NUMERO.pdf". Nota parcelada leva a parcela no nome, porque
    vai uma copia para a pasta de cada vencimento e elas ficam parecidas.
    """
    base = item.get("nome_base") or item["chave"] or item["xml_path"].stem
    if total > 1:
        base += f" (parcela {indice + 1} de {total})"
    if item.get("resumo"):
        base += "_SEM_PDF_OFICIAL"
    return base + ".pdf"


def caminho_livre(pasta, nome):
    """Nao sobrescreve arquivo de outra nota que tenha o mesmo nome."""
    destino = pasta / nome
    if not destino.exists():
        return destino
    numero = 2
    while (pasta / f"{destino.stem} [{numero}]{destino.suffix}").exists():
        numero += 1
    return pasta / f"{destino.stem} [{numero}]{destino.suffix}"


def finalizar_item(item, vencimentos, metodo, linhas_log):
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def arquivar(pasta, nome, vencimento=""):
        pasta.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item["pdf_temp"], caminho_livre(pasta, nome))
        linhas_log.append([
            agora, item["xml_path"].name, item["tipo"], item["chave"] or "",
            vencimento, metodo, str(pasta),
        ])

    if metodo == "pular":
        arquivar(PASTA_PENDENTES, nome_do_arquivo(item))
        return

    if metodo == "a_vista":
        arquivar(PASTA_A_VISTA, nome_do_arquivo(item), "A_VISTA")
        return

    total = len(vencimentos)
    for indice, data in enumerate(vencimentos):
        arquivar(
            pasta_destino_para_data(data),
            nome_do_arquivo(item, indice, total),
            data.strftime("%d/%m/%Y"),
        )


def registrar_log(linhas):
    if not linhas:
        return
    novo = not ARQUIVO_LOG.exists()
    ARQUIVO_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(ARQUIVO_LOG, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        if novo:
            writer.writerow(["data_processamento", "arquivo_origem", "tipo", "chave", "vencimento", "metodo", "pasta_destino"])
        writer.writerows(linhas)


# ===================== PREFERENCIAS =====================
def carregar_preferencias():
    try:
        dados = json.loads(ARQUIVO_PREFERENCIAS.read_text(encoding="utf-8"))
        return dados if isinstance(dados, dict) else {}
    except Exception:
        return {}


def salvar_preferencias(preferencias):
    try:
        ARQUIVO_PREFERENCIAS.write_text(
            json.dumps(preferencias, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:
        print(f"(nao consegui guardar as preferencias: {e})")


def aplicar_pastas_das_preferencias(preferencias):
    """
    Deixa os caminhos virem do preferencias_contas.json quando ele os tiver.
    Substituir o contas.py por uma versao nova nao apaga mais a sua
    configuracao - ela mora no JSON, ao lado.
    """
    global PASTA_ORIGEM, PASTA_DESTINO, PASTA_PENDENTES, PASTA_A_VISTA, ARQUIVO_LOG

    origem = (preferencias.get("pasta_origem") or "").strip()
    destino = (preferencias.get("pasta_destino") or "").strip()
    if origem:
        PASTA_ORIGEM = Path(origem).expanduser()
    if destino:
        PASTA_DESTINO = Path(destino).expanduser()
        PASTA_PENDENTES = PASTA_DESTINO / "_PENDENTES"
        PASTA_A_VISTA = PASTA_DESTINO / "_A_VISTA_SEM_VENCIMENTO"
        ARQUIVO_LOG = PASTA_DESTINO / "_log_classificacao.csv"


def mostrar_pastas():
    print(f"Pasta de entrada: {PASTA_ORIGEM}")
    print(f"Pasta de destino: {PASTA_DESTINO}")
    if not PASTA_DESTINO.exists():
        print("  (a pasta de destino nao existe - confira o caminho)")
    print(f"(para trocar, edite {ARQUIVO_PREFERENCIAS.name}"
          " com pasta_origem e pasta_destino)\n")


def validar_prazo(texto):
    """Texto do campo -> dias. Vazio e 0 desligam o prazo automatico."""
    texto = (texto or "").strip()
    if not texto:
        return 0
    if not texto.isdigit():
        raise ValueError("Informe apenas numeros (dias).")
    dias = int(texto)
    if dias > 365:
        raise ValueError("Prazo muito alto (maximo 365 dias).")
    return dias


def perguntar_prazo_cte(padrao=0):
    """Janela inicial: prazo em dias para CT-e que nao traz vencimento."""
    escolha = {"dias": padrao, "confirmado": False}

    janela = tk.Tk()
    janela.title("Prazo de pagamento do frete")
    janela.geometry("470x260")
    janela.resizable(False, False)

    tk.Label(
        janela,
        text="Prazo automatico de vencimento dos CT-e",
        font=("Segoe UI", 12, "bold"),
    ).pack(pady=(16, 6))
    tk.Label(
        janela,
        text=("O CT-e de frete costuma nao trazer vencimento no XML.\n"
              "Informe em quantos dias apos a emissao ele vence.\n\n"
              "Notas de servico e demais notas sem prazo continuam\n"
              "sendo lancadas uma a uma, na revisao manual."),
        justify="center",
        fg="#444",
    ).pack(pady=(0, 10))

    linha = tk.Frame(janela)
    linha.pack()
    tk.Label(linha, text="Dias:").pack(side=tk.LEFT)
    entrada = tk.Entry(linha, width=8, font=("Segoe UI", 12), justify="center")
    entrada.pack(side=tk.LEFT, padx=6)
    entrada.insert(0, str(padrao) if padrao else "")
    tk.Label(linha, text="(0 ou vazio = sempre manual)", fg="#666").pack(side=tk.LEFT)

    label_erro = tk.Label(janela, text="", fg="red")
    label_erro.pack(pady=4)

    def confirmar():
        try:
            escolha["dias"] = validar_prazo(entrada.get())
        except ValueError as e:
            label_erro.config(text=str(e))
            return
        escolha["confirmado"] = True
        janela.destroy()

    tk.Button(janela, text="Continuar", width=16, command=confirmar).pack(pady=6)
    entrada.bind("<Return>", lambda e: confirmar())
    entrada.focus()
    janela.mainloop()

    return escolha["dias"] if escolha["confirmado"] else padrao


# ===================== VISUALIZADOR DE PDF =====================
class VisualizadorPdf(tk.Frame):
    """
    Mostra o PDF com zoom, rolagem e navegacao entre paginas.
      + / -            aumenta e diminui
      Ctrl + roda      zoom no ponteiro
      roda / Shift+roda rola vertical / horizontal
      arrastar         move a pagina
    """

    def __init__(self, master, largura=820, altura=600):
        super().__init__(master)
        self.doc = None
        self.pagina_atual = 0
        self.escala = 1.0
        self._imagem = None
        # "largura" ou "pagina" enquanto o ajuste e automatico; None depois
        # que o usuario mexe no zoom, para nao desfazer a escolha dele.
        self._modo_ajuste = "largura"
        self._reajuste_agendado = None

        barra = tk.Frame(self)
        barra.pack(fill=tk.X, pady=(0, 4))
        tk.Button(barra, text="−", width=3, command=self.menos_zoom).pack(side=tk.LEFT)
        tk.Button(barra, text="+", width=3, command=self.mais_zoom).pack(side=tk.LEFT, padx=(2, 6))
        self.label_zoom = tk.Label(barra, text="100%", width=6)
        self.label_zoom.pack(side=tk.LEFT)
        tk.Button(barra, text="Ajustar", command=self.ajustar).pack(side=tk.LEFT, padx=2)
        tk.Button(barra, text="Largura", command=self.ajustar_largura).pack(side=tk.LEFT, padx=2)

        self.botao_anterior = tk.Button(barra, text="◀", width=3, command=self.pagina_anterior)
        self.botao_anterior.pack(side=tk.LEFT, padx=(12, 2))
        self.label_pagina = tk.Label(barra, text="-", width=10)
        self.label_pagina.pack(side=tk.LEFT)
        self.botao_proxima = tk.Button(barra, text="▶", width=3, command=self.proxima_pagina)
        self.botao_proxima.pack(side=tk.LEFT, padx=2)

        area = tk.Frame(self)
        area.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(area, bg="#4a4a4a", width=largura, height=altura,
                                highlightthickness=0)
        barra_v = tk.Scrollbar(area, orient=tk.VERTICAL, command=self.canvas.yview)
        barra_h = tk.Scrollbar(area, orient=tk.HORIZONTAL, command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=barra_v.set, xscrollcommand=barra_h.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        barra_v.grid(row=0, column=1, sticky="ns")
        barra_h.grid(row=1, column=0, sticky="ew")
        area.rowconfigure(0, weight=1)
        area.columnconfigure(0, weight=1)

        self.canvas.bind("<Control-MouseWheel>", self._zoom_roda)      # Windows
        self.canvas.bind("<Control-Button-4>", self._zoom_roda)        # Linux
        self.canvas.bind("<Control-Button-5>", self._zoom_roda)
        self.canvas.bind("<MouseWheel>", self._rolar_vertical)
        self.canvas.bind("<Button-4>", self._rolar_vertical)
        self.canvas.bind("<Button-5>", self._rolar_vertical)
        self.canvas.bind("<Shift-MouseWheel>", self._rolar_horizontal)
        self.canvas.bind("<ButtonPress-1>", lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind("<B1-Motion>", lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        self.canvas.bind("<Configure>", self._ao_redimensionar)

    # ---------- ciclo de vida ----------
    def abrir(self, caminho_pdf):
        self.fechar()
        self.doc = abrir_documento(caminho_pdf)
        self.pagina_atual = 0
        self._modo_ajuste = "largura"
        # o canvas so tem o tamanho real depois que a janela se acomoda
        self.after_idle(self._reajustar)

    def _ao_redimensionar(self, evento=None):
        """Redimensionou a janela: refaz o ajuste, com folga para nao pesar."""
        if self.doc is None or self._modo_ajuste is None:
            return
        if self._reajuste_agendado is not None:
            self.after_cancel(self._reajuste_agendado)
        self._reajuste_agendado = self.after(120, self._reajustar)

    def _reajustar(self):
        self._reajuste_agendado = None
        if self.doc is None:
            return
        if self._modo_ajuste == "largura":
            self.ajustar_largura()
        else:
            self.ajustar()

    def fechar(self):
        if self.doc is not None:
            try:
                self.doc.close()
            except Exception:
                pass
        self.doc = None
        self._imagem = None
        self.canvas.delete("all")

    def _pagina(self):
        return self.doc[self.pagina_atual]

    # ---------- zoom ----------
    def _area(self):
        self.canvas.update_idletasks()
        return max(self.canvas.winfo_width(), 200), max(self.canvas.winfo_height(), 200)

    def ajustar(self):
        if self.doc is None:
            return
        self._modo_ajuste = "pagina"
        largura, altura = self._area()
        self.escala = limitar_escala(escala_de_ajuste(self._pagina(), largura, altura))
        self._desenhar()

    def ajustar_largura(self):
        if self.doc is None:
            return
        self._modo_ajuste = "largura"
        largura, _ = self._area()
        pagina = self._pagina()
        self.escala = limitar_escala(largura / pagina.rect.width if pagina.rect.width else 1.0)
        self._desenhar()

    def mais_zoom(self):
        self._aplicar_zoom(self.escala * ZOOM_PASSO)

    def menos_zoom(self):
        self._aplicar_zoom(self.escala / ZOOM_PASSO)

    def _aplicar_zoom(self, nova):
        if self.doc is None:
            return
        nova = limitar_escala(nova)
        if abs(nova - self.escala) < 1e-6:
            return
        self._modo_ajuste = None   # daqui em diante quem manda e o usuario
        self.escala = nova
        self._desenhar()

    def _zoom_roda(self, evento):
        if getattr(evento, "delta", 0) > 0 or getattr(evento, "num", 0) == 4:
            self.mais_zoom()
        else:
            self.menos_zoom()
        return "break"

    # ---------- rolagem ----------
    def _passos(self, evento):
        if getattr(evento, "num", 0) == 4:
            return -1
        if getattr(evento, "num", 0) == 5:
            return 1
        return -1 if evento.delta > 0 else 1

    def _rolar_vertical(self, evento):
        self.canvas.yview_scroll(self._passos(evento), "units")
        return "break"

    def _rolar_horizontal(self, evento):
        self.canvas.xview_scroll(self._passos(evento), "units")
        return "break"

    # ---------- paginas ----------
    def pagina_anterior(self):
        if self.doc is not None and self.pagina_atual > 0:
            self.pagina_atual -= 1
            self._desenhar()

    def proxima_pagina(self):
        if self.doc is not None and self.pagina_atual < len(self.doc) - 1:
            self.pagina_atual += 1
            self._desenhar()

    # ---------- desenho ----------
    def _desenhar(self):
        if self.doc is None:
            return
        self._imagem = renderizar_pagina(self._pagina(), self.escala)
        largura_area, altura_area = self._area()
        largura_img, altura_img = self._imagem.width(), self._imagem.height()

        # pagina menor que a area fica centralizada, e nao encostada no canto
        x = max(0, (largura_area - largura_img) // 2)
        y = max(0, (altura_area - altura_img) // 2)

        self.canvas.delete("all")
        self.canvas.create_image(x, y, anchor="nw", image=self._imagem)
        self.canvas.configure(scrollregion=(
            0, 0, max(largura_img + x, largura_area), max(altura_img + y, altura_area),
        ))
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)

        self.label_zoom.config(text=f"{round(self.escala * 100)}%")
        total = len(self.doc)
        self.label_pagina.config(text=f"pag. {self.pagina_atual + 1}/{total}")
        self.botao_anterior.config(state=tk.NORMAL if self.pagina_atual > 0 else tk.DISABLED)
        self.botao_proxima.config(
            state=tk.NORMAL if self.pagina_atual < total - 1 else tk.DISABLED
        )


# ===================== FASE 2: REVISAO MANUAL =====================
class RevisorApp:
    def __init__(self, fila, callback_finalizar):
        self.fila = fila
        self.indice = 0
        self.callback_finalizar = callback_finalizar

        self.root = tk.Tk()
        self.root.title("Revisao de vencimentos pendentes")
        self.root.geometry("1000x900")
        self.root.minsize(700, 600)

        self.label_nome = tk.Label(self.root, text="", font=("Segoe UI", 11, "bold"), wraplength=650, justify="center")
        self.label_nome.pack(pady=(12, 4))

        self.label_contador = tk.Label(self.root, text="", font=("Segoe UI", 9))
        self.label_contador.pack()

        self.label_erro = tk.Label(self.root, text="", fg="red")
        self.label_erro.pack(side=tk.BOTTOM, pady=(0, 10))

        frame_botoes = tk.Frame(self.root)
        frame_botoes.pack(side=tk.BOTTOM, pady=6)
        tk.Button(frame_botoes, text="Confirmar", width=14, command=self.confirmar).pack(side=tk.LEFT, padx=4)
        tk.Button(frame_botoes, text="A vista", width=14, command=self.a_vista).pack(side=tk.LEFT, padx=4)
        tk.Button(frame_botoes, text="Pular", width=14, command=self.pular).pack(side=tk.LEFT, padx=4)
        tk.Button(frame_botoes, text="Abrir PDF externo", width=16, command=self.abrir_externo).pack(side=tk.LEFT, padx=4)

        frame_parcelas = tk.Frame(self.root)
        frame_parcelas.pack(side=tk.BOTTOM, pady=(0, 6))
        tk.Label(frame_parcelas, text="Parcelas:").pack(side=tk.LEFT)
        self.entry_parcelas = tk.Spinbox(frame_parcelas, from_=1, to=36, width=3,
                                         font=("Segoe UI", 10))
        self.entry_parcelas.pack(side=tk.LEFT, padx=(4, 6))
        tk.Label(frame_parcelas, text="a cada").pack(side=tk.LEFT)
        self.entry_intervalo = tk.Entry(frame_parcelas, width=4, font=("Segoe UI", 10),
                                        justify="center")
        self.entry_intervalo.pack(side=tk.LEFT, padx=4)
        tk.Label(frame_parcelas, text="dias  (vai uma copia da nota"
                                      " para a pasta de cada vencimento)").pack(side=tk.LEFT)

        frame_entrada = tk.Frame(self.root)
        frame_entrada.pack(side=tk.BOTTOM, pady=(6, 0))
        tk.Label(frame_entrada, text="Vencimento (DD/MM/AAAA):").pack(side=tk.LEFT)
        self.entry_data = tk.Entry(frame_entrada, width=34, font=("Segoe UI", 11))
        self.entry_data.pack(side=tk.LEFT, padx=6)
        self.entry_data.bind("<Return>", lambda e: self.confirmar())
        tk.Label(frame_entrada, text="varias datas: separe por virgula",
                 fg="#666").pack(side=tk.LEFT)

        self.visualizador = VisualizadorPdf(self.root)
        self.visualizador.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        self.label_sem_preview = tk.Label(self.root, text="", fg="#666")

        self.root.bind("<Control-plus>", lambda e: self.visualizador.mais_zoom())
        self.root.bind("<Control-equal>", lambda e: self.visualizador.mais_zoom())
        self.root.bind("<Control-minus>", lambda e: self.visualizador.menos_zoom())
        self.root.bind("<Control-Key-0>", lambda e: self.visualizador.ajustar())
        self.root.protocol("WM_DELETE_WINDOW", self._fechar_janela)

        self._mostrar_item()
        self.root.mainloop()

    def _fechar_janela(self):
        self.visualizador.fechar()
        self.root.destroy()

    def _item_atual(self):
        return self.fila[self.indice]

    def _mostrar_item(self):
        item = self._item_atual()
        self.label_erro.config(text="")
        self.entry_data.delete(0, tk.END)
        self.entry_parcelas.delete(0, tk.END)
        self.entry_parcelas.insert(0, "1")
        self.entry_intervalo.delete(0, tk.END)
        self.entry_intervalo.insert(0, "30")
        self.label_nome.config(text=item["xml_path"].name)
        self.label_contador.config(text=f"{self.indice + 1} de {len(self.fila)}")
        try:
            self.visualizador.abrir(item["pdf_temp"])
            self.label_sem_preview.pack_forget()
        except Exception:
            self.visualizador.fechar()
            self.label_sem_preview.config(text="(sem preview - use 'Abrir PDF externo')")
            self.label_sem_preview.pack()
        self.entry_data.focus()

    def abrir_externo(self):
        abrir_pdf(self._item_atual()["pdf_temp"])

    def confirmar(self):
        try:
            datas = interpretar_vencimentos(
                self.entry_data.get(),
                self.entry_parcelas.get(),
                self.entry_intervalo.get(),
            )
        except ValueError as e:
            self.label_erro.config(text=str(e))
            return
        self.callback_finalizar(self._item_atual(), datas, "manual")
        self._avancar()

    def a_vista(self):
        self.callback_finalizar(self._item_atual(), [], "a_vista")
        self._avancar()

    def pular(self):
        self.callback_finalizar(self._item_atual(), [], "pular")
        self._avancar()

    def _avancar(self):
        self.indice += 1
        if self.indice >= len(self.fila):
            self._fechar_janela()
        else:
            self._mostrar_item()


# ===================== MAIN =====================
def main():
    preferencias = carregar_preferencias()
    aplicar_pastas_das_preferencias(preferencias)

    if "--desfazer-renomear" in sys.argv:
        mostrar_pastas()
        desfazer_renomeacoes(PASTA_DESTINO)
        return
    if "--renomear" in sys.argv:
        mostrar_pastas()
        renomear_existentes(PASTA_DESTINO, aplicar="--aplicar" in sys.argv)
        return
    if "--reprocessar" in sys.argv:
        mostrar_pastas()
        indice = sys.argv.index("--reprocessar") + 1
        if indice < len(sys.argv) and not sys.argv[indice].startswith("-"):
            alvo = Path(sys.argv[indice]).expanduser()
        else:
            alvo = PASTA_PENDENTES
        reprocessar_pdfs(alvo)
        return

    mostrar_pastas()
    PASTA_ORIGEM.mkdir(parents=True, exist_ok=True)
    PASTA_DESTINO.mkdir(parents=True, exist_ok=True)

    xml_paths = list(PASTA_ORIGEM.glob("*.xml"))
    if not xml_paths:
        print("Nenhum XML encontrado na pasta de origem.")
        return

    prazo_cte = perguntar_prazo_cte(preferencias.get("prazo_cte_dias", 0))
    preferencias["prazo_cte_dias"] = prazo_cte
    salvar_preferencias(preferencias)
    if prazo_cte:
        print(f"Prazo automatico dos CT-e: emissao + {prazo_cte} dia(s).")
    else:
        print("Sem prazo automatico: CT-e sem vencimento vai para a revisao manual.")

    linhas_log = []
    fila_pendentes = []
    contagem = {"auto": 0, "prazo_cte": 0, "manual": 0, "a_vista": 0,
                "pendente": 0, "resumo": 0}

    with tempfile.TemporaryDirectory(prefix="notas_pdf_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)

        # ---------- FASE 1: automatica, sem interface ----------
        for xml_path in xml_paths:
            try:
                xml_texto, root = ler_xml(xml_path)
                tipo = identificar_tipo(root)
                chave = extrair_chave(root)
                nome_pdf_temp = tmpdir / f"{chave or xml_path.stem}.pdf"

                resumo = False
                try:
                    gerar_pdf(tipo, xml_texto, nome_pdf_temp)
                except Exception as e:
                    motivo = f"{type(e).__name__}: {e}"
                    print(f"  {xml_path.name}: sem PDF oficial ({motivo})")
                    print("    -> seguindo com um resumo do XML")
                    gerar_pdf_resumo(root, tipo, chave, nome_pdf_temp, motivo)
                    resumo = True
                    contagem["resumo"] += 1

                item = {"xml_path": xml_path, "tipo": tipo, "chave": chave,
                        "pdf_temp": nome_pdf_temp, "resumo": resumo,
                        "nome_base": nome_base_arquivo(root, chave, xml_path)}

                vencimentos = []
                if tipo in ("NFe", "CTe"):
                    vencimentos = extrair_vencimentos_nfe_cte(root)
                elif tipo == "NFSe":
                    data = extrair_vencimento_nfse(root)
                    if data:
                        vencimentos = [data]

                metodo = "xml" if vencimentos else None
                if not vencimentos:
                    data_pdf = extrair_vencimento_pdf(nome_pdf_temp)
                    if data_pdf:
                        vencimentos, metodo = [data_pdf], "pdf-regex"

                # ultimo recurso, so para frete: emissao + prazo informado
                if not vencimentos:
                    data_prazo = vencimento_por_prazo(root, tipo, prazo_cte)
                    if data_prazo:
                        vencimentos, metodo = [data_prazo], f"prazo-cte-{prazo_cte}d"
                        contagem["prazo_cte"] += 1

                if vencimentos:
                    finalizar_item(item, vencimentos, metodo, linhas_log)
                    xml_path.unlink()
                    contagem["auto"] += 1
                else:
                    fila_pendentes.append(item)

            except Exception:
                print(f"Erro processando {xml_path.name}:")
                traceback.print_exc()

        # ---------- FASE 2: janela de revisao manual ----------
        if fila_pendentes:
            def callback_finalizar(item, vencimentos, metodo):
                finalizar_item(item, vencimentos, metodo, linhas_log)
                item["xml_path"].unlink()
                if metodo == "pular":
                    contagem["pendente"] += 1
                elif metodo == "a_vista":
                    contagem["a_vista"] += 1
                elif metodo == "manual":
                    contagem["manual"] += 1

            RevisorApp(fila_pendentes, callback_finalizar)

        registrar_log(linhas_log)

    print(
        f"\nConcluido: {contagem['auto']} automatico(s)"
        f" (sendo {contagem['prazo_cte']} por prazo de CT-e), "
        f"{contagem['manual']} manual(is), {contagem['a_vista']} a vista, "
        f"{contagem['pendente']} pendente(s). Log: {ARQUIVO_LOG}"
    )
    if contagem["resumo"]:
        print(
            f"Atencao: {contagem['resumo']} documento(s) sem PDF oficial - foram"
            " arquivados como resumo, com _SEM_PDF_OFICIAL no nome."
        )


if __name__ == "__main__":
    main()
