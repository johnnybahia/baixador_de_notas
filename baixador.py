"""
Baixador unificado de documentos fiscais - NF-e, CT-e e NFS-e
=============================================================
Varre os tres ambientes nacionais por NSU usando o certificado A1 e grava
os XMLs em uma unica pasta de saida (entrada do classificador de vencimentos).

  NF-e  -> NFeDistribuicaoDFe  (SOAP)  distNSU
  CT-e  -> CTeDistribuicaoDFe  (SOAP)  distNSU
  NFS-e -> API ADN NFS-e       (REST)  paginacao por NSU

Configuracao em config.ini (ao lado deste arquivo). Estado (ultNSU por
servico, bloqueios) em PASTA_CONTROLE - nunca no OneDrive.

Requisitos:
    pip install requests cryptography openpyxl
"""

import os
import re
import sys
import ssl
import json
import gzip
import time
import base64
import zipfile
import tempfile
import configparser
import xml.etree.ElementTree as ET
from io import BytesIO
from datetime import datetime, timedelta
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from cryptography.hazmat.primitives.serialization import (
    pkcs12, Encoding, PrivateFormat, NoEncryption,
)

try:
    from openpyxl import load_workbook
except ImportError:
    load_workbook = None


# ===================== CONFIGURACAO =====================
def base_execucao():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


BASE = base_execucao()
ARQUIVO_CONFIG = BASE / "config.ini"

if not ARQUIVO_CONFIG.exists():
    print(f"config.ini nao encontrado em {BASE}")
    print("Copie o config.ini.exemplo, ajuste os caminhos e rode de novo.")
    sys.exit(1)

CFG = configparser.ConfigParser()
CFG.read(ARQUIVO_CONFIG, encoding="utf-8")

PASTA_SAIDA = Path(CFG["PASTAS"]["saida"])
PASTA_CONTROLE = Path(CFG["PASTAS"]["controle"])
PASTA_PLANILHAS = Path(CFG["PASTAS"].get("planilhas", str(BASE)))

CERT_PFX = Path(CFG["CERTIFICADO"]["pfx"])
CERT_SENHA = CFG["CERTIFICADO"]["senha"]

CNPJ = re.sub(r"\D", "", CFG["EMPRESA"]["cnpj"])
CUF = CFG["EMPRESA"]["cuf"]
TP_AMB = CFG["EMPRESA"].get("ambiente", "1")

MAX_LOTES = CFG["LIMITES"].getint("max_lotes_por_execucao", 20)
PAUSA = CFG["LIMITES"].getint("pausa_segundos", 2)
BLOQUEIO_MIN = CFG["LIMITES"].getint("bloqueio_minutos", 65)

ARQUIVO_ESTADO = PASTA_CONTROLE / "estado_nsu.json"
ARQUIVO_LOCK = PASTA_CONTROLE / "baixador.lock"
ARQUIVO_RELATORIO = PASTA_CONTROLE / "relatorio_faltantes.csv"

# --------------------------------------------------------
NS_NFE = "http://www.portalfiscal.inf.br/nfe"
NS_CTE = "http://www.portalfiscal.inf.br/cte"

URL_NFE = {
    "1": "https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx",
    "2": "https://hom1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx",
}
URL_CTE = {
    "1": "https://www1.cte.fazenda.gov.br/CTeDistribuicaoDFe/CTeDistribuicaoDFe.asmx",
    "2": "https://hom1.cte.fazenda.gov.br/CTeDistribuicaoDFe/CTeDistribuicaoDFe.asmx",
}
URL_NFSE = {
    "1": "https://adn.nfse.gov.br/contribuintes",
    "2": "https://adn.producaorestrita.nfse.gov.br/contribuintes",
}

ACTION_NFE = "http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe/nfeDistDFeInteresse"
ACTION_CTE = "http://www.portalfiscal.inf.br/cte/wsdl/CTeDistribuicaoDFe/cteDistDFeInteresse"

# cStat que encerram a varredura de forma normal ou definitiva
CSTAT_SEM_DOCUMENTO = {"137", "138"}
CSTAT_CONSUMO_INDEVIDO = "656"


# ===================== LOCK =====================
def adquirir_lock():
    PASTA_CONTROLE.mkdir(parents=True, exist_ok=True)
    if ARQUIVO_LOCK.exists():
        try:
            dados = json.loads(ARQUIVO_LOCK.read_text(encoding="utf-8"))
            inicio = datetime.fromisoformat(dados["inicio"])
            if datetime.now() - inicio < timedelta(hours=2):
                print(f"Execucao em andamento (PID {dados.get('pid')}). Saindo.")
                return False
            print("Lock antigo (>2h), assumindo travado.")
        except Exception:
            pass
    ARQUIVO_LOCK.write_text(
        json.dumps({"pid": os.getpid(), "inicio": datetime.now().isoformat()}),
        encoding="utf-8",
    )
    return True


def liberar_lock():
    try:
        ARQUIVO_LOCK.unlink(missing_ok=True)
    except Exception:
        pass


# ===================== ESTADO =====================
def carregar_estado():
    if ARQUIVO_ESTADO.exists():
        try:
            return json.loads(ARQUIVO_ESTADO.read_text(encoding="utf-8"))
        except Exception:
            print("Estado corrompido, recriando do zero.")
    return {}


def salvar_estado(estado):
    ARQUIVO_ESTADO.write_text(
        json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def estado_servico(estado, servico):
    return estado.setdefault(servico, {"ultNSU": "0", "maxNSU": "0", "bloqueado_ate": None})


def bloqueado(info):
    if not info.get("bloqueado_ate"):
        return None
    try:
        ate = datetime.fromisoformat(info["bloqueado_ate"])
        return ate if datetime.now() < ate else None
    except Exception:
        return None


def bloquear(info, motivo):
    ate = datetime.now() + timedelta(minutes=BLOQUEIO_MIN)
    info["bloqueado_ate"] = ate.isoformat()
    info["motivo_bloqueio"] = motivo
    print(f"    !! BLOQUEADO ate {ate:%d/%m %H:%M} ({motivo})")


# ===================== CERTIFICADO =====================
class TLSAdapter(HTTPAdapter):
    """Alguns endpoints da SEFAZ exigem renegociacao legada no OpenSSL 3."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = True
        try:
            ctx.options |= 0x4  # OP_LEGACY_SERVER_CONNECT
        except Exception:
            pass
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def preparar_certificado():
    if not CERT_PFX.exists():
        raise FileNotFoundError(f"Certificado nao encontrado: {CERT_PFX}")
    chave, cert, cadeia = pkcs12.load_key_and_certificates(
        CERT_PFX.read_bytes(), CERT_SENHA.encode("utf-8")
    )
    if cert is None or chave is None:
        raise ValueError("Nao consegui extrair certificado/chave do .pfx")

    validade = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after
    validade = validade.replace(tzinfo=None)
    agora = datetime.now()
    if validade < agora:
        raise ValueError(f"Certificado VENCIDO em {validade:%d/%m/%Y}")
    if (validade - agora).days < 15:
        print(f"AVISO: certificado vence em {validade:%d/%m/%Y}")

    fd_c, pem_cert = tempfile.mkstemp(suffix=".pem")
    fd_k, pem_key = tempfile.mkstemp(suffix=".pem")
    with os.fdopen(fd_c, "wb") as f:
        f.write(cert.public_bytes(Encoding.PEM))
        for extra in (cadeia or []):
            f.write(extra.public_bytes(Encoding.PEM))
    with os.fdopen(fd_k, "wb") as f:
        f.write(chave.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    return pem_cert, pem_key


def nova_sessao(cert_par):
    s = requests.Session()
    s.cert = cert_par
    s.mount("https://", TLSAdapter())
    return s


# ===================== GRAVACAO =====================
def nome_por_chave(conteudo, prefixo):
    m = re.search(r"\d{44,50}", conteudo)
    if m:
        return f"{m.group(0)}.xml"
    return f"{prefixo}_{datetime.now():%Y%m%d%H%M%S%f}.xml"


def gravar_xml(conteudo, prefixo, contadores):
    """Grava apenas documentos processaveis; eventos/resumos vao para subpasta."""
    PASTA_SAIDA.mkdir(parents=True, exist_ok=True)

    principal = any(
        marca in conteudo
        for marca in ("<nfeProc", "<cteProc", "<CTeOSProc", "<NFSe", "<nfseProc")
    )
    if principal:
        destino = PASTA_SAIDA / nome_por_chave(conteudo, prefixo)
        contadores["documentos"] += 1
    else:
        pasta_eventos = PASTA_SAIDA / "_EVENTOS_E_RESUMOS"
        pasta_eventos.mkdir(parents=True, exist_ok=True)
        destino = pasta_eventos / nome_por_chave(conteudo, prefixo)
        contadores["eventos"] += 1

    if destino.exists():
        contadores["duplicados"] += 1
        return
    destino.write_text(conteudo, encoding="utf-8")


# ===================== MODULO SOAP (NF-e / CT-e) =====================
def montar_envelope(servico, ult_nsu):
    if servico == "nfe":
        ns = NS_NFE
        wsdl = "http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe"
        metodo = "nfeDistDFeInteresse"
        tag_dados = "nfeDadosMsg"
        versao = "1.01"
    else:
        ns = NS_CTE
        wsdl = "http://www.portalfiscal.inf.br/cte/wsdl/CTeDistribuicaoDFe"
        metodo = "cteDistDFeInteresse"
        tag_dados = "cteDadosMsg"
        versao = "1.00"

    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">'
        "<soap12:Body>"
        f'<{metodo} xmlns="{wsdl}">'
        f"<{tag_dados}>"
        f'<distDFeInt xmlns="{ns}" versao="{versao}">'
        f"<tpAmb>{TP_AMB}</tpAmb>"
        f"<cUFAutor>{CUF}</cUFAutor>"
        f"<CNPJ>{CNPJ}</CNPJ>"
        f"<distNSU><ultNSU>{str(ult_nsu).zfill(15)}</ultNSU></distNSU>"
        "</distDFeInt>"
        f"</{tag_dados}>"
        f"</{metodo}>"
        "</soap12:Body>"
        "</soap12:Envelope>"
    )


def descompactar_doczip(texto_b64):
    bruto = base64.b64decode(texto_b64)
    try:
        return gzip.decompress(bruto).decode("utf-8", errors="replace")
    except Exception:
        return bruto.decode("utf-8", errors="replace")


def rodar_soap(servico, sessao, info, contadores):
    ns = NS_NFE if servico == "nfe" else NS_CTE
    url = (URL_NFE if servico == "nfe" else URL_CTE)[TP_AMB]
    action = ACTION_NFE if servico == "nfe" else ACTION_CTE
    prefixo = servico.upper()

    for lote in range(1, MAX_LOTES + 1):
        ult = info["ultNSU"]
        print(f"  lote {lote}/{MAX_LOTES} a partir do NSU {ult}")
        try:
            resp = sessao.post(
                url,
                data=montar_envelope(servico, ult).encode("utf-8"),
                headers={"Content-Type": f'application/soap+xml; charset=utf-8; action="{action}"'},
                timeout=90,
            )
            resp.raise_for_status()
        except Exception as e:
            print(f"    falha de rede: {e}")
            return

        try:
            raiz = ET.fromstring(resp.content)
        except ET.ParseError as e:
            print(f"    resposta ilegivel: {e}")
            return

        def txt(tag):
            el = raiz.find(f".//{{{ns}}}{tag}")
            return el.text if el is not None and el.text else ""

        cstat, xmotivo = txt("cStat"), txt("xMotivo")
        novo_ult, max_nsu = txt("ultNSU"), txt("maxNSU")
        docs = raiz.findall(f".//{{{ns}}}docZip")
        print(f"    cStat {cstat} - {xmotivo} | {len(docs)} doc(s)")

        if cstat == CSTAT_CONSUMO_INDEVIDO:
            bloquear(info, f"{cstat} {xmotivo}")
            return

        for doc in docs:
            try:
                gravar_xml(descompactar_doczip(doc.text), prefixo, contadores)
            except Exception as e:
                print(f"    erro ao descompactar doc: {e}")

        if novo_ult:
            info["ultNSU"] = novo_ult
        if max_nsu:
            info["maxNSU"] = max_nsu

        if cstat in CSTAT_SEM_DOCUMENTO and not docs:
            print("    nada novo, varredura concluida.")
            return
        if max_nsu and novo_ult and int(novo_ult) >= int(max_nsu):
            print("    alcancou o maxNSU, varredura concluida.")
            return
        if not docs:
            return
        time.sleep(PAUSA)

    print(f"    limite de {MAX_LOTES} lote(s) atingido; continua na proxima execucao.")


# ===================== MODULO REST (NFS-e / ADN) =====================
def rodar_nfse(sessao, info, contadores):
    base_url = URL_NFSE[TP_AMB]

    for lote in range(1, MAX_LOTES + 1):
        ult = info["ultNSU"]
        url = f"{base_url}/nfse"
        print(f"  lote {lote}/{MAX_LOTES} a partir do NSU {ult}")
        try:
            resp = sessao.get(
                url,
                params={"NSU": str(ult), "tipoNSU": "1", "lote": "true"},
                headers={"Accept": "application/json"},
                timeout=90,
            )
        except Exception as e:
            print(f"    falha de rede: {e}")
            return

        if resp.status_code == 404:
            print("    nada novo, varredura concluida.")
            return
        if resp.status_code in (401, 403):
            print(f"    acesso negado ({resp.status_code}). Confira se o CNPJ do")
            print("    certificado e ator (tomador) das notas e se o ADN esta habilitado.")
            return
        if resp.status_code == 429:
            bloquear(info, "429 too many requests")
            return
        if resp.status_code >= 400:
            print(f"    HTTP {resp.status_code}: {resp.text[:300]}")
            return

        try:
            dados = resp.json()
        except ValueError:
            print(f"    resposta nao-JSON: {resp.text[:300]}")
            return

        documentos = dados.get("LoteDFe") or dados.get("loteDFe") or []
        print(f"    {len(documentos)} documento(s)")

        for item in documentos:
            conteudo_b64 = (
                item.get("ArquivoXml") or item.get("arquivoXml")
                or item.get("DocumentoXmlGZipB64") or item.get("documentoXmlGZipB64")
            )
            if not conteudo_b64:
                continue
            try:
                gravar_xml(descompactar_doczip(conteudo_b64), "NFSE", contadores)
            except Exception as e:
                print(f"    erro ao processar documento: {e}")

        novo_ult = str(
            dados.get("UltimoNSU") or dados.get("ultimoNSU")
            or (documentos[-1].get("NSU") if documentos else "") or ""
        )
        max_nsu = str(dados.get("MaximoNSU") or dados.get("maximoNSU") or "")
        if novo_ult:
            info["ultNSU"] = novo_ult
        if max_nsu:
            info["maxNSU"] = max_nsu

        if not documentos:
            return
        if max_nsu and novo_ult and int(novo_ult) >= int(max_nsu):
            print("    alcancou o maxNSU, varredura concluida.")
            return
        time.sleep(PAUSA)

    print(f"    limite de {MAX_LOTES} lote(s) atingido; continua na proxima execucao.")


# ===================== CONFERENCIA COM A PLANILHA =====================
def conferir_planilhas():
    if load_workbook is None or not PASTA_PLANILHAS.exists():
        return
    arquivos = [c for c in PASTA_PLANILHAS.glob("*.xls*") if not c.name.startswith("~$")]
    if not arquivos:
        return

    baixadas = {p.stem for p in PASTA_SAIDA.glob("*.xml")}
    faltantes = []

    for caminho in arquivos:
        try:
            wb = load_workbook(caminho, read_only=True, data_only=True)
        except Exception as e:
            print(f"  nao consegui ler {caminho.name}: {e}")
            continue
        try:
            ws = wb[wb.sheetnames[0]]
            linhas = ws.iter_rows(values_only=True)
            cab = next(linhas, None)
            if cab is None:
                continue
            idx = {str(n).strip(): i for i, n in enumerate(cab) if n is not None}
            if "Chave" not in idx:
                continue

            def campo(l, n):
                i = idx.get(n)
                return "" if i is None or i >= len(l) or l[i] is None else str(l[i]).strip()

            for linha in linhas:
                if not linha or not any(v is not None and str(v).strip() for v in linha):
                    continue
                chave = re.sub(r"\D", "", campo(linha, "Chave"))
                if len(chave) != 44 or chave in baixadas:
                    continue
                faltantes.append([
                    caminho.name, chave, campo(linha, "Tipo"), campo(linha, "Num"),
                    campo(linha, "DtAut"), campo(linha, "Valor"), campo(linha, "Emissor Nome"),
                ])
        finally:
            wb.close()

    if not faltantes:
        print("\nConferencia: todas as chaves das planilhas estao na pasta de saida.")
        return

    import csv
    with open(ARQUIVO_RELATORIO, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["planilha", "chave", "tipo", "numero", "data_aut", "valor", "emissor"])
        w.writerows(faltantes)
    print(f"\nConferencia: {len(faltantes)} chave(s) da planilha NAO chegaram.")
    print(f"Detalhes em: {ARQUIVO_RELATORIO}")
    print("Causa comum: documento fora da janela de 90 dias do Ambiente Nacional.")


# ===================== MAIN =====================
def main():
    PASTA_SAIDA.mkdir(parents=True, exist_ok=True)
    PASTA_CONTROLE.mkdir(parents=True, exist_ok=True)

    if not adquirir_lock():
        return

    pem_cert = pem_key = None
    try:
        try:
            pem_cert, pem_key = preparar_certificado()
        except Exception as e:
            print(f"ERRO no certificado: {e}")
            return

        sessao = nova_sessao((pem_cert, pem_key))
        estado = carregar_estado()
        contadores = {"documentos": 0, "eventos": 0, "duplicados": 0}

        servicos = [
            ("nfe", "NF-e", lambda i, c: rodar_soap("nfe", sessao, i, c)),
            ("cte", "CT-e", lambda i, c: rodar_soap("cte", sessao, i, c)),
            ("nfse", "NFS-e", lambda i, c: rodar_nfse(sessao, i, c)),
        ]

        for chave_cfg, rotulo, executar in servicos:
            if CFG["SERVICOS"].get(chave_cfg, "sim").strip().lower() not in ("sim", "true", "1"):
                print(f"\n== {rotulo}: desativado no config.ini ==")
                continue

            info = estado_servico(estado, chave_cfg)
            print(f"\n== {rotulo} ==")
            ate = bloqueado(info)
            if ate:
                restante = int((ate - datetime.now()).total_seconds() // 60) + 1
                print(f"  bloqueado por ~{restante} min (ate {ate:%H:%M}). Pulando.")
                continue
            info["bloqueado_ate"] = None

            try:
                executar(info, contadores)
            except Exception as e:
                print(f"  erro inesperado: {e}")
            finally:
                salvar_estado(estado)

        salvar_estado(estado)
        print(
            f"\n---\nDocumentos gravados: {contadores['documentos']}"
            f" | eventos/resumos: {contadores['eventos']}"
            f" | ja existentes: {contadores['duplicados']}"
        )
        print(f"Pasta de saida: {PASTA_SAIDA}")

        conferir_planilhas()
    finally:
        for caminho in (pem_cert, pem_key):
            if caminho:
                try:
                    os.unlink(caminho)
                except Exception:
                    pass
        liberar_lock()


if __name__ == "__main__":
    main()
