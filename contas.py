"""
Classificador de notas fiscais por data de vencimento (NF-e, NFS-e, CT-e)
Gera o PDF (DANFE/DACTE/DANFSe) localmente a partir do XML - a pasta de
origem so precisa conter os XMLs.

Requisitos:
    pip install pdfplumber pymupdf pillow "brazilfiscalreport[dacte]" pynfse-nacional
    (se pynfse-nacional nao estiver no PyPI: pip install git+https://github.com/roberto-mello/pynfse-nacional)
"""

import os
import re
import csv
import shutil
import tempfile
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
import tkinter as tk

import pdfplumber

# ===================== CONFIGURACAO =====================
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


def extrair_vencimento_nfse(root):
    for el in root.iter():
        tag_local = el.tag.split("}")[-1]
        if "venc" in tag_local.lower() and el.text:
            data = _parse_data_iso_ou_br(el.text.strip())
            if data:
                return data
    return None


# ===================== GERACAO DE PDF =====================
def gerar_pdf(tipo, xml_texto, destino_pdf):
    if tipo == "NFe":
        from brazilfiscalreport.danfe import Danfe
        Danfe(xml=xml_texto).output(str(destino_pdf))
    elif tipo == "CTe":
        from brazilfiscalreport.dacte import Dacte
        Dacte(xml_texto).output(str(destino_pdf))
    elif tipo == "NFSe":
        from pynfse_nacional.pdf_generator import generate_danfse_from_xml
        generate_danfse_from_xml(xml_content=xml_texto, output_path=str(destino_pdf))
    else:
        raise ValueError(f"Tipo desconhecido, nao sei gerar PDF: {tipo}")


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


def renderizar_pagina1(caminho_pdf, largura_max=620, altura_max=520):
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    from PIL import Image, ImageTk
    doc = fitz.open(caminho_pdf)
    pagina = doc[0]
    zoom = min(largura_max / pagina.rect.width, altura_max / pagina.rect.height)
    pix = pagina.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    return ImageTk.PhotoImage(img)


# ===================== ORGANIZACAO EM PASTAS =====================
def pasta_destino_para_data(data):
    pasta = PASTA_DESTINO / str(data.year) / MESES_PT[data.month] / data.strftime("%d-%m-%Y")
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def finalizar_item(item, vencimentos, metodo, linhas_log):
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    nome_pdf = (item["chave"] or item["xml_path"].stem) + ".pdf"

    if metodo == "pular":
        PASTA_PENDENTES.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item["pdf_temp"], PASTA_PENDENTES / nome_pdf)
        linhas_log.append([agora, item["xml_path"].name, item["tipo"], item["chave"] or "", "", "pular", str(PASTA_PENDENTES)])
        return

    if metodo == "a_vista":
        PASTA_A_VISTA.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item["pdf_temp"], PASTA_A_VISTA / nome_pdf)
        linhas_log.append([agora, item["xml_path"].name, item["tipo"], item["chave"] or "", "A_VISTA", "a_vista", str(PASTA_A_VISTA)])
        return

    for data in vencimentos:
        destino = pasta_destino_para_data(data)
        shutil.copy2(item["pdf_temp"], destino / nome_pdf)
        linhas_log.append([agora, item["xml_path"].name, item["tipo"], item["chave"] or "", data.strftime("%d/%m/%Y"), metodo, str(destino)])


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


# ===================== FASE 2: REVISAO MANUAL =====================
class RevisorApp:
    def __init__(self, fila, callback_finalizar):
        self.fila = fila
        self.indice = 0
        self.callback_finalizar = callback_finalizar

        self.root = tk.Tk()
        self.root.title("Revisao de vencimentos pendentes")
        self.root.geometry("700x820")

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

        frame_entrada = tk.Frame(self.root)
        frame_entrada.pack(side=tk.BOTTOM, pady=6)
        tk.Label(frame_entrada, text="Vencimento (DD/MM/AAAA):").pack(side=tk.LEFT)
        self.entry_data = tk.Entry(frame_entrada, width=12, font=("Segoe UI", 11))
        self.entry_data.pack(side=tk.LEFT, padx=6)
        self.entry_data.bind("<Return>", lambda e: self.confirmar())

        self.label_imagem = tk.Label(self.root)
        self.label_imagem.pack(pady=6)

        self._imagem_atual = None
        self._mostrar_item()
        self.root.mainloop()

    def _item_atual(self):
        return self.fila[self.indice]

    def _mostrar_item(self):
        item = self._item_atual()
        self.label_erro.config(text="")
        self.entry_data.delete(0, tk.END)
        self.label_nome.config(text=item["xml_path"].name)
        self.label_contador.config(text=f"{self.indice + 1} de {len(self.fila)}")
        try:
            self._imagem_atual = renderizar_pagina1(item["pdf_temp"])
            self.label_imagem.config(image=self._imagem_atual, text="")
        except Exception:
            self._imagem_atual = None
            self.label_imagem.config(image="", text="(sem preview - use 'Abrir PDF externo')")
        self.entry_data.focus()

    def abrir_externo(self):
        abrir_pdf(self._item_atual()["pdf_temp"])

    def confirmar(self):
        m = REGEX_DATA.match(self.entry_data.get().strip())
        if not m:
            self.label_erro.config(text="Data invalida. Use DD/MM/AAAA.")
            return
        d, mth, a = m.groups()
        try:
            data = datetime(int(a), int(mth), int(d)).date()
        except ValueError:
            self.label_erro.config(text="Data invalida.")
            return
        self.callback_finalizar(self._item_atual(), [data], "manual")
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
            self.root.destroy()
        else:
            self._mostrar_item()


# ===================== MAIN =====================
def main():
    PASTA_ORIGEM.mkdir(parents=True, exist_ok=True)
    PASTA_DESTINO.mkdir(parents=True, exist_ok=True)

    xml_paths = list(PASTA_ORIGEM.glob("*.xml"))
    if not xml_paths:
        print("Nenhum XML encontrado na pasta de origem.")
        return

    linhas_log = []
    fila_pendentes = []
    contagem = {"auto": 0, "manual": 0, "a_vista": 0, "pendente": 0}

    with tempfile.TemporaryDirectory(prefix="notas_pdf_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)

        # ---------- FASE 1: automatica, sem interface ----------
        for xml_path in xml_paths:
            try:
                xml_texto, root = ler_xml(xml_path)
                tipo = identificar_tipo(root)
                chave = extrair_chave(root)
                nome_pdf_temp = tmpdir / f"{chave or xml_path.stem}.pdf"
                gerar_pdf(tipo, xml_texto, nome_pdf_temp)

                item = {"xml_path": xml_path, "tipo": tipo, "chave": chave, "pdf_temp": nome_pdf_temp}

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
        f"\nConcluido: {contagem['auto']} automatico(s), {contagem['manual']} manual(is), "
        f"{contagem['a_vista']} a vista, {contagem['pendente']} pendente(s). Log: {ARQUIVO_LOG}"
    )


if __name__ == "__main__":
    main()
