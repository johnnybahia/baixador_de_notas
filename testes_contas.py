"""
Testes do classificador (nao precisam da pasta real nem de certificado).

    python -m unittest testes_contas -v

Os testes de interface abrem janelas de verdade; se nao houver display
disponivel (servidor sem X), eles sao pulados automaticamente.
"""

import json
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import contas as c

CHAVE_CTE = "29250712345678000199570010000055551000055559"[:44].ljust(44, "0")


def cte(dh_emi="2026-07-16T08:00:00-03:00", com_vencimento=False):
    cobranca = (
        "<cobr><dup><dVenc>2026-09-01</dVenc></dup></cobr>" if com_vencimento else ""
    )
    return (
        '<cteProc xmlns="http://www.portalfiscal.inf.br/cte" versao="4.00">'
        f'<CTe><infCte Id="CTe{CHAVE_CTE}">'
        f"<ide><cUF>29</cUF><nCT>333</nCT><dhEmi>{dh_emi}</dhEmi></ide>"
        "<emit><xNome>TRANSPORTADORA TESTE LTDA</xNome></emit>"
        # o CT-e cita a NF-e transportada, mas sem data dela
        f"<infCTeNorm><infDoc><infNFe><chave>{'9' * 44}</chave></infNFe></infDoc>"
        f"</infCTeNorm>{cobranca}"
        "</infCte></CTe></cteProc>"
    )


def nfe(dh_emi="2026-07-15T10:00:00-03:00", com_vencimento=True):
    cobranca = (
        "<cobr><dup><dVenc>2026-08-14</dVenc></dup>"
        "<dup><dVenc>2026-09-13</dVenc></dup></cobr>" if com_vencimento else ""
    )
    return (
        '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">'
        f'<NFe><infNFe Id="NFe{"1" * 44}">'
        f"<ide><nNF>4521</nNF><dhEmi>{dh_emi}</dhEmi></ide>"
        "<emit><xNome>FORNECEDOR TESTE LTDA</xNome></emit>"
        f"{cobranca}"
        "</infNFe></NFe></nfeProc>"
    )


def nfse(dh_emi="2026-07-10T09:00:00-03:00"):
    return (
        '<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse">'
        f'<infNFSe Id="NFS{"3" * 50}"><dhProc>{dh_emi}</dhProc></infNFSe></NFSe>'
    )


def raiz(xml):
    return ET.fromstring(xml)


class TestTipo(unittest.TestCase):
    def test_identifica_os_tres_tipos(self):
        self.assertEqual(c.identificar_tipo(raiz(cte())), "CTe")
        self.assertEqual(c.identificar_tipo(raiz(nfe())), "NFe")
        self.assertEqual(c.identificar_tipo(raiz(nfse())), "NFSe")


class TestEmissao(unittest.TestCase):
    def test_le_a_emissao_do_cte(self):
        self.assertEqual(c.extrair_data_emissao(raiz(cte())), date(2026, 7, 16))

    def test_le_a_emissao_da_nfe(self):
        self.assertEqual(c.extrair_data_emissao(raiz(nfe())), date(2026, 7, 15))

    def test_documento_sem_data_devolve_none(self):
        self.assertIsNone(c.extrair_data_emissao(raiz("<cteProc><x>1</x></cteProc>")))


class TestPrazoCte(unittest.TestCase):
    def test_cte_vence_em_emissao_mais_prazo(self):
        # emitido em 16/07/2026 + 28 dias = 13/08/2026
        self.assertEqual(
            c.vencimento_por_prazo(raiz(cte()), "CTe", 28), date(2026, 8, 13)
        )

    def test_prazo_zero_nao_aplica(self):
        self.assertIsNone(c.vencimento_por_prazo(raiz(cte()), "CTe", 0))

    def test_nao_vale_para_nota_de_servico(self):
        self.assertIsNone(c.vencimento_por_prazo(raiz(nfse()), "NFSe", 28))

    def test_nao_vale_para_nota_de_produto(self):
        self.assertIsNone(c.vencimento_por_prazo(raiz(nfe()), "NFe", 28))

    def test_cte_sem_emissao_vai_para_o_manual(self):
        sem_data = '<cteProc xmlns="http://www.portalfiscal.inf.br/cte"><CTe/></cteProc>'
        self.assertIsNone(c.vencimento_por_prazo(raiz(sem_data), "CTe", 28))

    def test_vencimento_do_xml_tem_prioridade(self):
        # com duplicata no XML o prazo nem chega a ser consultado
        vencimentos = c.extrair_vencimentos_nfe_cte(raiz(cte(com_vencimento=True)))
        self.assertEqual(vencimentos, [date(2026, 9, 1)])

    def test_nfe_com_parcelas_mantem_todas(self):
        self.assertEqual(
            c.extrair_vencimentos_nfe_cte(raiz(nfe())),
            [date(2026, 8, 14), date(2026, 9, 13)],
        )


class TestNomeDoArquivo(unittest.TestCase):
    def test_usa_emitente_e_numero(self):
        xml = (
            '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe">'
            f'<NFe><infNFe Id="NFe{"1" * 44}">'
            "<ide><nNF>4521</nNF></ide>"
            "<emit><xNome>DISTRIBUIDORA EXEMPLO LTDA</xNome></emit>"
            "<dest><xNome>MINHA EMPRESA LTDA</xNome></dest>"
            "</infNFe></NFe></nfeProc>"
        )
        self.assertEqual(
            c.nome_base_arquivo(raiz(xml), "1" * 44, Path("nota.xml")),
            "DISTRIBUIDORA EXEMPLO LTDA - 4521",
        )

    def test_pega_o_emitente_e_nao_o_destinatario(self):
        xml = (
            '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe><infNFe>'
            "<dest><xNome>QUEM RECEBE</xNome></dest>"
            "<emit><xNome>QUEM EMITE</xNome></emit>"
            "<ide><nNF>9</nNF></ide></infNFe></NFe></nfeProc>"
        )
        self.assertEqual(
            c.nome_base_arquivo(raiz(xml), None, Path("x.xml")), "QUEM EMITE - 9"
        )

    def test_transportadora_no_cte(self):
        xml = (
            '<cteProc xmlns="http://www.portalfiscal.inf.br/cte"><CTe><infCte>'
            "<ide><nCT>777</nCT></ide>"
            "<emit><xNome>TRANSPORTES RAPIDOS SA</xNome></emit>"
            "</infCte></CTe></cteProc>"
        )
        self.assertEqual(
            c.nome_base_arquivo(raiz(xml), None, Path("x.xml")),
            "TRANSPORTES RAPIDOS SA - 777",
        )

    def test_tira_caracteres_que_o_windows_recusa(self):
        xml = (
            "<NFSe><emit><xNome>COMERCIO A/B: LTDA *?</xNome></emit>"
            "<nNFSe>12</nNFSe></NFSe>"
        )
        nome = c.nome_base_arquivo(raiz(xml), None, Path("x.xml"))
        self.assertEqual(nome, "COMERCIO AB LTDA - 12")
        for proibido in '<>:"/\\|?*':
            self.assertNotIn(proibido, nome)

    def test_nome_comprido_e_cortado(self):
        longo = "EMPRESA " * 30
        xml = f"<NFSe><emit><xNome>{longo}</xNome></emit><nNFSe>1</nNFSe></NFSe>"
        nome = c.nome_base_arquivo(raiz(xml), None, Path("x.xml"))
        self.assertLessEqual(len(nome), 80)

    def test_sem_emitente_cai_para_a_chave(self):
        xml = "<NFSe><x>1</x></NFSe>"
        self.assertEqual(c.nome_base_arquivo(raiz(xml), "3" * 50, Path("x.xml")), "3" * 50)

    def test_parcela_aparece_no_nome(self):
        item = {"nome_base": "FORNECEDOR - 10", "chave": None,
                "xml_path": Path("x.xml")}
        self.assertEqual(c.nome_do_arquivo(item, 0, 1), "FORNECEDOR - 10.pdf")
        self.assertEqual(
            c.nome_do_arquivo(item, 1, 3), "FORNECEDOR - 10 (parcela 2 de 3).pdf"
        )


CHAVE_NFE_REAL = "35260712345678000199550010000045211000045218"


def nfe_completa():
    """NF-e valida o bastante para o brazilfiscalreport desenhar o DANFE."""
    ns = "http://www.portalfiscal.inf.br/nfe"
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="{ns}" versao="4.00"><NFe><infNFe Id="NFe{CHAVE_NFE_REAL}" versao="4.00">
<ide><cUF>35</cUF><cNF>10000452</cNF><natOp>VENDA</natOp><mod>55</mod><serie>1</serie>
<nNF>4521</nNF><dhEmi>2026-07-15T10:00:00-03:00</dhEmi><tpNF>1</tpNF><idDest>1</idDest>
<cMunFG>3550308</cMunFG><tpImp>1</tpImp><tpEmis>1</tpEmis><cDV>8</cDV><tpAmb>1</tpAmb>
<finNFe>1</finNFe><indFinal>0</indFinal><indPres>1</indPres><procEmi>0</procEmi>
<verProc>1.0</verProc></ide>
<emit><CNPJ>12345678000199</CNPJ><xNome>DISTRIBUIDORA EXEMPLO LTDA</xNome><xFant>EXEMPLO</xFant>
<enderEmit><xLgr>RUA UM</xLgr><nro>100</nro><xBairro>CENTRO</xBairro><cMun>3550308</cMun>
<xMun>SAO PAULO</xMun><UF>SP</UF><CEP>01000000</CEP><cPais>1058</cPais><xPais>BRASIL</xPais>
<fone>1130000000</fone></enderEmit><IE>1234567890</IE><CRT>3</CRT></emit>
<dest><CNPJ>98765432000188</CNPJ><xNome>MINHA EMPRESA LTDA</xNome>
<enderDest><xLgr>RUA DOIS</xLgr><nro>200</nro><xBairro>CENTRO</xBairro><cMun>3550308</cMun>
<xMun>SAO PAULO</xMun><UF>SP</UF><CEP>02000000</CEP><cPais>1058</cPais><xPais>BRASIL</xPais>
</enderDest><indIEDest>9</indIEDest></dest>
<det nItem="1"><prod><cProd>1</cProd><cEAN>SEM GTIN</cEAN><xProd>PRODUTO TESTE</xProd>
<NCM>84713012</NCM><CFOP>5102</CFOP><uCom>UN</uCom><qCom>1.0000</qCom><vUnCom>100.00</vUnCom>
<vProd>100.00</vProd><cEANTrib>SEM GTIN</cEANTrib><uTrib>UN</uTrib><qTrib>1.0000</qTrib>
<vUnTrib>100.00</vUnTrib><indTot>1</indTot></prod>
<imposto><ICMS><ICMS00><orig>0</orig><CST>00</CST><modBC>3</modBC><vBC>100.00</vBC>
<pICMS>18.00</pICMS><vICMS>18.00</vICMS></ICMS00></ICMS>
<PIS><PISAliq><CST>01</CST><vBC>100.00</vBC><pPIS>1.65</pPIS><vPIS>1.65</vPIS></PISAliq></PIS>
<COFINS><COFINSAliq><CST>01</CST><vBC>100.00</vBC><pCOFINS>7.60</pCOFINS><vCOFINS>7.60</vCOFINS>
</COFINSAliq></COFINS></imposto></det>
<total><ICMSTot><vBC>100.00</vBC><vICMS>18.00</vICMS><vICMSDeson>0.00</vICMSDeson>
<vFCP>0.00</vFCP><vBCST>0.00</vBCST><vST>0.00</vST><vFCPST>0.00</vFCPST><vFCPSTRet>0.00</vFCPSTRet>
<vProd>100.00</vProd><vFrete>0.00</vFrete><vSeg>0.00</vSeg><vDesc>0.00</vDesc><vII>0.00</vII>
<vIPI>0.00</vIPI><vIPIDevol>0.00</vIPIDevol><vPIS>1.65</vPIS><vCOFINS>7.60</vCOFINS>
<vOutro>0.00</vOutro><vNF>100.00</vNF></ICMSTot></total>
<transp><modFrete>9</modFrete></transp>
<cobr><fat><nFat>4521</nFat><vOrig>100.00</vOrig><vLiq>100.00</vLiq></fat>
<dup><nDup>001</nDup><dVenc>2026-08-14</dVenc><vDup>100.00</vDup></dup></cobr>
<pag><detPag><tPag>15</tPag><vPag>100.00</vPag></detPag></pag>
<infAdic><infCpl>Observacoes</infCpl></infAdic>
</infNFe></NFe><protNFe versao="4.00"><infProt><tpAmb>1</tpAmb><verAplic>SP</verAplic>
<chNFe>{CHAVE_NFE_REAL}</chNFe><dhRecbto>2026-07-15T10:05:00-03:00</dhRecbto>
<nProt>135260000000001</nProt><digVal>abc</digVal><cStat>100</cStat>
<xMotivo>Autorizado o uso da NF-e</xMotivo></infProt></protNFe></nfeProc>'''


class TestDadosDaChave(unittest.TestCase):
    def test_chave_de_44_digitos(self):
        dados = c.dados_da_chave(CHAVE_NFE_REAL)
        self.assertEqual(dados["cnpj"], "12345678000199")
        self.assertEqual(dados["numero"], "4521")

    def test_chave_de_nfse_com_50_digitos(self):
        # chave real do usuario: CNPJ 23.973.005/0001-04, NFS-e 256
        dados = c.dados_da_chave("23037091223973005000104000000000025626082809817912")
        self.assertEqual(dados["cnpj"], "23973005000104")
        self.assertEqual(dados["numero"], "256")

    def test_outra_chave_de_nfse(self):
        dados = c.dados_da_chave("23042851212968028000104000000000062126046560779793")
        self.assertEqual(dados["cnpj"], "12968028000104")
        self.assertEqual(dados["numero"], "621")

    def test_chave_de_tamanho_estranho(self):
        self.assertIsNone(c.dados_da_chave("123"))

    def test_formata_cnpj(self):
        self.assertEqual(c.formatar_cnpj("12345678000199"), "12.345.678/0001-99")
        self.assertEqual(c.formatar_cnpj("abc"), "abc")


class TestRenomearExistentes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.pasta = self.tmp / "2026" / "08 - Agosto" / "14-08-2026"
        self.pasta.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def danfe_real(self, nome):
        from brazilfiscalreport.danfe import Danfe
        destino = self.pasta / nome
        Danfe(xml=nfe_completa()).output(str(destino))
        return destino

    def pdf_com_texto(self, nome, linhas):
        from reportlab.pdfgen import canvas as rl
        destino = self.pasta / nome
        pdf = rl.Canvas(str(destino))
        y = 800
        for linha in linhas:
            pdf.drawString(40, y, linha)
            y -= 16
        pdf.showPage()
        pdf.save()
        return destino

    def nomes(self):
        return sorted(p.name for p in self.tmp.rglob("*.pdf"))

    def test_le_o_emitente_de_um_danfe_de_verdade(self):
        caminho = self.danfe_real(f"{CHAVE_NFE_REAL}.pdf")
        self.assertEqual(c.emitente_do_pdf(caminho), "DISTRIBUIDORA EXEMPLO LTDA")
        self.assertEqual(
            c.novo_nome_para(caminho), "DISTRIBUIDORA EXEMPLO LTDA - 4521.pdf"
        )

    def test_renomeia_de_verdade_com_aplicar(self):
        self.danfe_real(f"{CHAVE_NFE_REAL}.pdf")
        c.renomear_existentes(self.tmp, aplicar=True)
        self.assertEqual(self.nomes(), ["DISTRIBUIDORA EXEMPLO LTDA - 4521.pdf"])

    def test_previa_nao_mexe_em_nada(self):
        self.danfe_real(f"{CHAVE_NFE_REAL}.pdf")
        c.renomear_existentes(self.tmp, aplicar=False)
        self.assertEqual(self.nomes(), [f"{CHAVE_NFE_REAL}.pdf"])

    def test_desfaz_a_renomeacao(self):
        self.danfe_real(f"{CHAVE_NFE_REAL}.pdf")
        c.renomear_existentes(self.tmp, aplicar=True)
        c.desfazer_renomeacoes(self.tmp)
        self.assertEqual(self.nomes(), [f"{CHAVE_NFE_REAL}.pdf"])
        self.assertFalse((self.tmp / c.ARQUIVO_RENOMEACOES).exists())

    def test_nao_toca_em_quem_ja_esta_no_padrao_novo(self):
        self.pdf_com_texto("FORNECEDOR X LTDA - 10.pdf", ["qualquer coisa"])
        c.renomear_existentes(self.tmp, aplicar=True)
        self.assertEqual(self.nomes(), ["FORNECEDOR X LTDA - 10.pdf"])

    def test_preserva_sufixos_de_parcela_e_de_resumo(self):
        nome = f"{CHAVE_NFE_REAL} (parcela 2 de 3).pdf"
        self.assertEqual(
            c.novo_nome_para(self.danfe_real(nome)),
            "DISTRIBUIDORA EXEMPLO LTDA - 4521 (parcela 2 de 3).pdf",
        )
        nome2 = f"{CHAVE_NFE_REAL}_SEM_PDF_OFICIAL.pdf"
        self.assertEqual(
            c.novo_nome_para(self.danfe_real(nome2)),
            "DISTRIBUIDORA EXEMPLO LTDA - 4521_SEM_PDF_OFICIAL.pdf",
        )

    def test_sem_emitente_legivel_usa_o_cnpj_da_chave(self):
        caminho = self.pdf_com_texto(f"{CHAVE_NFE_REAL}.pdf", ["pagina sem nome algum"])
        self.assertEqual(
            c.novo_nome_para(caminho), "CNPJ 12.345.678-0001-99 - 4521.pdf"
        )

    def test_ancora_de_prestador_da_nfse(self):
        chave = "23037091223973005000104000000000025626082809817912"
        caminho = self.pdf_com_texto(f"{chave}.pdf", [
            "DANFSE - NOTA FISCAL DE SERVICO ELETRONICA",
            "PRESTADOR DE SERVICOS",
            "CLINICA EXEMPLO SERVICOS MEDICOS LTDA",
            "CNPJ 23.973.005/0001-04",
        ])
        self.assertEqual(
            c.novo_nome_para(caminho),
            "CLINICA EXEMPLO SERVICOS MEDICOS LTDA - 256.pdf",
        )

    def test_nao_sobrescreve_arquivo_existente(self):
        self.danfe_real(f"{CHAVE_NFE_REAL}.pdf")
        self.pdf_com_texto("DISTRIBUIDORA EXEMPLO LTDA - 4521.pdf", ["outro arquivo"])
        c.renomear_existentes(self.tmp, aplicar=True)
        self.assertEqual(self.nomes(), [
            "DISTRIBUIDORA EXEMPLO LTDA - 4521 [2].pdf",
            "DISTRIBUIDORA EXEMPLO LTDA - 4521.pdf",
        ])

    def test_pasta_inexistente_nao_estoura(self):
        self.assertEqual(c.renomear_existentes(self.tmp / "nao_existe"), [])

    def test_pela_linha_de_comando(self):
        """Como o usuario roda: python contas.py --renomear --aplicar"""
        import sys
        self.danfe_real(f"{CHAVE_NFE_REAL}.pdf")

        originais = (c.PASTA_DESTINO, c.ARQUIVO_PREFERENCIAS, sys.argv)
        c.PASTA_DESTINO = self.tmp
        c.ARQUIVO_PREFERENCIAS = self.tmp / "preferencias_contas.json"
        try:
            sys.argv = ["contas.py", "--renomear"]          # previa
            c.main()
            self.assertEqual(self.nomes(), [f"{CHAVE_NFE_REAL}.pdf"])

            sys.argv = ["contas.py", "--renomear", "--aplicar"]
            c.main()
            self.assertEqual(self.nomes(), ["DISTRIBUIDORA EXEMPLO LTDA - 4521.pdf"])

            sys.argv = ["contas.py", "--desfazer-renomear"]
            c.main()
            self.assertEqual(self.nomes(), [f"{CHAVE_NFE_REAL}.pdf"])
        finally:
            c.PASTA_DESTINO, c.ARQUIVO_PREFERENCIAS, sys.argv = originais

    def test_pastas_vem_do_json_e_sobrevivem_a_atualizacao(self):
        originais = (c.PASTA_ORIGEM, c.PASTA_DESTINO, c.ARQUIVO_LOG,
                     c.PASTA_PENDENTES, c.PASTA_A_VISTA)
        try:
            c.aplicar_pastas_das_preferencias({
                "pasta_origem": str(self.tmp / "entrada"),
                "pasta_destino": str(self.tmp / "destino"),
            })
            self.assertEqual(c.PASTA_ORIGEM, self.tmp / "entrada")
            self.assertEqual(c.PASTA_DESTINO, self.tmp / "destino")
            self.assertEqual(c.PASTA_PENDENTES, self.tmp / "destino" / "_PENDENTES")
            self.assertEqual(
                c.ARQUIVO_LOG, self.tmp / "destino" / "_log_classificacao.csv"
            )
        finally:
            (c.PASTA_ORIGEM, c.PASTA_DESTINO, c.ARQUIVO_LOG,
             c.PASTA_PENDENTES, c.PASTA_A_VISTA) = originais

    def test_json_sem_pastas_nao_mexe_nos_padroes(self):
        antes = (c.PASTA_ORIGEM, c.PASTA_DESTINO)
        c.aplicar_pastas_das_preferencias({"prazo_cte_dias": 28})
        self.assertEqual((c.PASTA_ORIGEM, c.PASTA_DESTINO), antes)


class TestReprocessarPdfs(unittest.TestCase):
    """PDFs que ficaram em _PENDENTES: o XML nao existe mais, so o PDF."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.pendentes = self.tmp / "_PENDENTES"
        self.pendentes.mkdir(parents=True)

        self.originais = (c.PASTA_DESTINO, c.PASTA_PENDENTES, c.PASTA_A_VISTA,
                          c.ARQUIVO_LOG, c.RevisorApp)
        c.PASTA_DESTINO = self.tmp
        c.PASTA_PENDENTES = self.pendentes
        c.PASTA_A_VISTA = self.tmp / "_A_VISTA_SEM_VENCIMENTO"
        c.ARQUIVO_LOG = self.tmp / "_log_classificacao.csv"
        c.RevisorApp = lambda fila, callback: self.fail("nao deveria abrir a janela")

    def tearDown(self):
        (c.PASTA_DESTINO, c.PASTA_PENDENTES, c.PASTA_A_VISTA,
         c.ARQUIVO_LOG, c.RevisorApp) = self.originais
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pdf_com_texto(self, nome, linhas, pasta=None):
        from reportlab.pdfgen import canvas as rl
        destino = (pasta or self.pendentes) / nome
        pdf = rl.Canvas(str(destino))
        y = 800
        for linha in linhas:
            pdf.drawString(40, y, linha)
            y -= 16
        pdf.showPage()
        pdf.save()
        return destino

    def danfe_real(self, nome):
        from brazilfiscalreport.danfe import Danfe
        destino = self.pendentes / nome
        Danfe(xml=nfe_completa()).output(str(destino))
        return destino

    def arquivados(self):
        """So o que saiu de _PENDENTES e foi parar numa pasta de vencimento."""
        return sorted(
            str(p.relative_to(self.tmp)) for p in self.tmp.rglob("*.pdf")
            if self.pendentes not in p.parents
        )

    def test_le_a_chave_impressa_no_danfe(self):
        caminho = self.danfe_real("qualquer_nome.pdf")
        texto = c.texto_do_pdf(caminho)
        # no DANFE a chave sai em grupos de 4 digitos
        self.assertEqual(c.chave_do_pdf(texto), CHAVE_NFE_REAL)

    def test_tipo_sai_do_modelo_da_chave(self):
        self.assertEqual(c.tipo_pela_chave(CHAVE_NFE_REAL), "NFe")     # modelo 55
        self.assertEqual(c.tipo_pela_chave(CHAVE_CTE), "CTe")          # modelo 57
        self.assertEqual(c.tipo_pela_chave("3" * 50), "NFSe")
        self.assertEqual(c.tipo_pela_chave(None), "DESCONHECIDO")

    def test_monta_o_item_a_partir_do_pdf(self):
        item = c.item_de_pdf(self.danfe_real("sem_nome_util.pdf"))
        self.assertEqual(item["chave"], CHAVE_NFE_REAL)
        self.assertEqual(item["tipo"], "NFe")
        self.assertEqual(item["nome_base"], "DISTRIBUIDORA EXEMPLO LTDA - 4521")

    def test_arquiva_pelo_vencimento_impresso(self):
        self.pdf_com_texto("nota.pdf", [
            "DACTE - CONHECIMENTO DE TRANSPORTE",
            "TRANSPORTES BONS LTDA",
            "Chave de acesso 2925 0712 3456 7800 0199 5700 1000 0055 5510 0005 5559",
            "Vencimento: 10/08/2026",
        ])

        c.reprocessar_pdfs(self.pendentes)

        self.assertEqual(self.arquivados(), [
            str(Path("2026") / "08 - Agosto" / "10-08-2026"
                / "TRANSPORTES BONS LTDA - 5555.pdf")
        ])
        # o original sai de _PENDENTES
        self.assertFalse(list(self.pendentes.glob("*.pdf")))

    def test_sem_vencimento_vai_para_a_revisao(self):
        self.danfe_real("sem_data.pdf")
        vistos = {}
        c.RevisorApp = lambda fila, callback: vistos.update(
            nomes=[i["nome_base"] for i in fila]
        )
        c.reprocessar_pdfs(self.pendentes)
        self.assertEqual(vistos.get("nomes"), ["DISTRIBUIDORA EXEMPLO LTDA - 4521"])

    def test_revisao_manual_arquiva_e_limpa_a_origem(self):
        self.danfe_real("sem_data.pdf")

        def revisor(fila, callback):
            callback(fila[0], [date(2026, 9, 5)], "manual")
        c.RevisorApp = revisor

        c.reprocessar_pdfs(self.pendentes)
        self.assertEqual(self.arquivados(), [
            str(Path("2026") / "09 - Setembro" / "05-09-2026"
                / "DISTRIBUIDORA EXEMPLO LTDA - 4521.pdf")
        ])
        self.assertFalse(list(self.pendentes.glob("*.pdf")))

    def test_pular_deixa_o_arquivo_onde_estava(self):
        self.danfe_real("sem_data.pdf")

        def revisor(fila, callback):
            callback(fila[0], [], "pular")
        c.RevisorApp = revisor

        c.reprocessar_pdfs(self.pendentes)
        self.assertTrue((self.pendentes / "sem_data.pdf").exists())

    def test_parcelas_na_revisao_espalham_as_copias(self):
        self.danfe_real("sem_data.pdf")

        def revisor(fila, callback):
            callback(fila[0], [date(2026, 9, 5), date(2026, 10, 5)], "manual")
        c.RevisorApp = revisor

        c.reprocessar_pdfs(self.pendentes)
        self.assertEqual(self.arquivados(), [
            str(Path("2026") / "09 - Setembro" / "05-09-2026"
                / "DISTRIBUIDORA EXEMPLO LTDA - 4521 (parcela 1 de 2).pdf"),
            str(Path("2026") / "10 - Outubro" / "05-10-2026"
                / "DISTRIBUIDORA EXEMPLO LTDA - 4521 (parcela 2 de 2).pdf"),
        ])

    def test_pdf_ilegivel_nao_derruba_o_lote(self):
        (self.pendentes / "quebrado.pdf").write_bytes(b"nao sou um PDF")
        self.pdf_com_texto("boa.pdf", [
            "NOTA", "Chave 3526 0712 3456 7800 0199 5500 1000 0045 2110 0004 5218",
            "Vencimento: 10/08/2026",
        ])
        c.reprocessar_pdfs(self.pendentes)
        self.assertEqual(len(self.arquivados()), 1)

    def test_pasta_vazia_ou_inexistente(self):
        c.reprocessar_pdfs(self.tmp / "nao_existe")
        c.reprocessar_pdfs(self.pendentes)      # vazia
        self.assertEqual(self.arquivados(), [])

    def test_pela_linha_de_comando(self):
        import sys
        self.pdf_com_texto("nota.pdf", [
            "NOTA FISCAL",
            "Chave 3526 0712 3456 7800 0199 5500 1000 0045 2110 0004 5218",
            "Vencimento: 20/08/2026",
        ])
        originais = (sys.argv, c.ARQUIVO_PREFERENCIAS)
        c.ARQUIVO_PREFERENCIAS = self.tmp / "preferencias_contas.json"
        try:
            sys.argv = ["contas.py", "--reprocessar", str(self.pendentes)]
            c.main()
        finally:
            sys.argv, c.ARQUIVO_PREFERENCIAS = originais
        self.assertEqual(len(self.arquivados()), 1)


class TestParcelasManuais(unittest.TestCase):
    def test_uma_data(self):
        self.assertEqual(
            c.interpretar_vencimentos("10/08/2026"), [date(2026, 8, 10)]
        )

    def test_varias_datas_separadas_por_virgula(self):
        self.assertEqual(
            c.interpretar_vencimentos("10/08/2026, 10/09/2026; 10/10/2026"),
            [date(2026, 8, 10), date(2026, 9, 10), date(2026, 10, 10)],
        )

    def test_gera_parcelas_a_partir_de_uma_data(self):
        self.assertEqual(
            c.interpretar_vencimentos("10/08/2026", parcelas="3", intervalo="30"),
            [date(2026, 8, 10), date(2026, 9, 9), date(2026, 10, 9)],
        )

    def test_intervalo_livre(self):
        self.assertEqual(
            c.interpretar_vencimentos("01/08/2026", parcelas="2", intervalo="15"),
            [date(2026, 8, 1), date(2026, 8, 16)],
        )

    def test_datas_repetidas_viram_uma(self):
        self.assertEqual(
            c.interpretar_vencimentos("10/08/2026, 10/08/2026"), [date(2026, 8, 10)]
        )

    def test_datas_fora_de_ordem_sao_ordenadas(self):
        self.assertEqual(
            c.interpretar_vencimentos("10/10/2026, 10/08/2026"),
            [date(2026, 8, 10), date(2026, 10, 10)],
        )

    def test_campo_vazio_reclama(self):
        with self.assertRaises(ValueError):
            c.interpretar_vencimentos("")

    def test_data_impossivel_reclama(self):
        with self.assertRaises(ValueError):
            c.interpretar_vencimentos("31/02/2026")

    def test_quantidade_de_datas_diferente_das_parcelas_reclama(self):
        with self.assertRaises(ValueError) as ctx:
            c.interpretar_vencimentos("10/08/2026, 10/09/2026", parcelas="3")
        self.assertIn("3 parcelas", str(ctx.exception))

    def test_parcela_nao_numerica_reclama(self):
        with self.assertRaises(ValueError):
            c.interpretar_vencimentos("10/08/2026", parcelas="tres")


class TestValidacaoDoPrazo(unittest.TestCase):
    def test_aceita_numero(self):
        self.assertEqual(c.validar_prazo("28"), 28)

    def test_vazio_desliga(self):
        self.assertEqual(c.validar_prazo(""), 0)
        self.assertEqual(c.validar_prazo("   "), 0)

    def test_recusa_texto(self):
        with self.assertRaises(ValueError):
            c.validar_prazo("vinte")

    def test_recusa_negativo(self):
        with self.assertRaises(ValueError):
            c.validar_prazo("-5")

    def test_recusa_absurdo(self):
        with self.assertRaises(ValueError):
            c.validar_prazo("900")


class TestPreferencias(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.original = c.ARQUIVO_PREFERENCIAS
        c.ARQUIVO_PREFERENCIAS = self.tmp / "preferencias_contas.json"

    def tearDown(self):
        c.ARQUIVO_PREFERENCIAS = self.original
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_guarda_e_recupera_o_prazo(self):
        c.salvar_preferencias({"prazo_cte_dias": 28})
        self.assertEqual(c.carregar_preferencias()["prazo_cte_dias"], 28)

    def test_arquivo_ausente_nao_estoura(self):
        self.assertEqual(c.carregar_preferencias(), {})

    def test_arquivo_corrompido_nao_estoura(self):
        c.ARQUIVO_PREFERENCIAS.write_text("{isso nao e json", encoding="utf-8")
        self.assertEqual(c.carregar_preferencias(), {})


NS_NFSE = "http://www.sped.fazenda.gov.br/nfse"


def nfse_com_ibscbs(c_ind_op=""):
    """NFS-e com o grupo IBS/CBS da reforma tributaria; vazio derruba a lib."""
    return (
        f'<NFSe xmlns="{NS_NFSE}"><infNFSe Id="NFS{"3" * 50}">'
        "<nNFSe>77</nNFSe><dhProc>2026-07-10T09:00:00-03:00</dhProc>"
        "<valores><vLiq>1234.56</vLiq></valores>"
        f"<IBSCBS><finNFSe>0</finNFSe><cIndOp>{c_ind_op}</cIndOp>"
        "<indDest>0</indDest></IBSCBS>"
        "</infNFSe></NFSe>"
    )


class TestGrupoIbsCbs(unittest.TestCase):
    def test_remove_o_grupo(self):
        limpo = c.remover_ibscbs(nfse_com_ibscbs())
        self.assertNotIn("IBSCBS", limpo)
        self.assertIn("1234.56", limpo)       # o resto do XML continua la

    def test_sem_o_grupo_devolve_none(self):
        self.assertIsNone(c.remover_ibscbs(nfse()))

    def test_lib_falha_com_campo_vazio_e_passa_sem_o_grupo(self):
        """O bug de origem: a excecao da lib escapa do proprio except dela."""
        from pynfse_nacional.response_parsers import parse_ibscbs
        from pynfse_nacional.exceptions import NFSeError

        with self.assertRaises(NFSeError):
            parse_ibscbs(nfse_com_ibscbs())
        self.assertIsNone(parse_ibscbs(c.remover_ibscbs(nfse_com_ibscbs())))

    def test_gerar_pdf_tenta_de_novo_sem_o_grupo(self):
        chamadas = []

        def gerador_falso(xml_content, output_path):
            chamadas.append(xml_content)
            if "IBSCBS" in xml_content:
                raise RuntimeError("cIndOp invalido (valor redigido (0 caracteres)).")
            Path(output_path).write_bytes(b"%PDF-1.4")

        import pynfse_nacional.pdf_generator as gerador
        original = gerador.generate_danfse_from_xml
        gerador.generate_danfse_from_xml = gerador_falso
        try:
            tmp = Path(tempfile.mkdtemp())
            c.gerar_pdf("NFSe", nfse_com_ibscbs(), tmp / "nota.pdf")
            self.assertEqual(len(chamadas), 2)          # falhou, tirou o grupo, passou
            self.assertNotIn("IBSCBS", chamadas[1])
            self.assertTrue((tmp / "nota.pdf").exists())
            shutil.rmtree(tmp, ignore_errors=True)
        finally:
            gerador.generate_danfse_from_xml = original

    def test_erro_sem_relacao_com_o_grupo_continua_estourando(self):
        def sempre_falha(xml_content, output_path):
            raise RuntimeError("outro problema qualquer")

        import pynfse_nacional.pdf_generator as gerador
        original = gerador.generate_danfse_from_xml
        gerador.generate_danfse_from_xml = sempre_falha
        try:
            with self.assertRaises(RuntimeError):
                c.gerar_pdf("NFSe", nfse(), Path(tempfile.mkdtemp()) / "x.pdf")
        finally:
            gerador.generate_danfse_from_xml = original


class TestPdfResumo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def texto_do_pdf(self, caminho):
        import pdfplumber
        with pdfplumber.open(caminho) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)

    def test_resumo_traz_os_dados_da_nota(self):
        destino = self.tmp / "resumo.pdf"
        c.gerar_pdf_resumo(
            raiz(nfse_com_ibscbs()), "NFSe", "3" * 50, destino,
            motivo="NFSeValidationError: cIndOp invalido",
        )
        texto = self.texto_do_pdf(destino)
        self.assertIn("RESUMO DO DOCUMENTO", texto)
        self.assertIn("3" * 20, texto)          # a chave aparece
        self.assertIn("1234.56", texto)         # o valor aparece
        self.assertIn("cIndOp", texto)          # e o motivo da falha

    def test_resumo_de_xml_pobre_nao_estoura(self):
        destino = self.tmp / "vazio.pdf"
        c.gerar_pdf_resumo(raiz("<NFSe><x>1</x></NFSe>"), "NFSe", None, destino)
        self.assertTrue(destino.exists())
        self.assertIn("nao encontrada", self.texto_do_pdf(destino))


class TestFluxoCompleto(unittest.TestCase):
    """Fase automatica de ponta a ponta, sem gerar PDF de verdade."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.origem = self.tmp / "entrada"
        self.destino = self.tmp / "destino"
        self.origem.mkdir()

        self.originais = (
            c.PASTA_ORIGEM, c.PASTA_DESTINO, c.PASTA_PENDENTES,
            c.PASTA_A_VISTA, c.ARQUIVO_LOG, c.ARQUIVO_PREFERENCIAS,
            c.gerar_pdf, c.extrair_vencimento_pdf, c.perguntar_prazo_cte,
        )
        c.PASTA_ORIGEM = self.origem
        c.PASTA_DESTINO = self.destino
        c.PASTA_PENDENTES = self.destino / "_PENDENTES"
        c.PASTA_A_VISTA = self.destino / "_A_VISTA_SEM_VENCIMENTO"
        c.ARQUIVO_LOG = self.destino / "_log_classificacao.csv"
        c.ARQUIVO_PREFERENCIAS = self.tmp / "preferencias_contas.json"

        # PDF de mentira: o que importa aqui e a classificacao
        c.gerar_pdf = lambda tipo, xml_texto, destino: Path(destino).write_bytes(b"%PDF-1.4")
        c.extrair_vencimento_pdf = lambda caminho: None
        # a fila manual nunca deve ser aberta nestes testes
        c.RevisorApp = lambda fila, callback: self.fail(
            f"nao deveria abrir a revisao manual: {[i['xml_path'].name for i in fila]}"
        )

    def tearDown(self):
        (c.PASTA_ORIGEM, c.PASTA_DESTINO, c.PASTA_PENDENTES, c.PASTA_A_VISTA,
         c.ARQUIVO_LOG, c.ARQUIVO_PREFERENCIAS, c.gerar_pdf,
         c.extrair_vencimento_pdf, c.perguntar_prazo_cte) = self.originais
        shutil.rmtree(self.tmp, ignore_errors=True)

    def escrever(self, nome, xml):
        (self.origem / nome).write_text(xml, encoding="utf-8")

    def pastas_de_vencimento(self):
        return sorted(p.name for p in self.destino.rglob("*.pdf"))

    def caminhos(self):
        return sorted(
            str(p.relative_to(self.destino)) for p in self.destino.rglob("*.pdf")
        )

    def test_cte_sem_vencimento_usa_o_prazo_informado(self):
        c.perguntar_prazo_cte = lambda padrao=0: 28
        self.escrever("cte.xml", cte())
        c.main()

        # emissao 16/07/2026 + 28 dias = 13/08/2026
        self.assertEqual(
            self.caminhos(),
            [str(Path("2026") / "08 - Agosto" / "13-08-2026"
                 / "TRANSPORTADORA TESTE LTDA - 333.pdf")],
        )
        self.assertFalse(list(self.origem.glob("*.xml")))   # XML consumido

    def test_prazo_fica_guardado_para_a_proxima_vez(self):
        c.perguntar_prazo_cte = lambda padrao=0: 21
        self.escrever("cte.xml", cte())
        c.main()
        self.assertEqual(c.carregar_preferencias()["prazo_cte_dias"], 21)

    def test_sem_prazo_o_cte_vai_para_a_revisao_manual(self):
        c.perguntar_prazo_cte = lambda padrao=0: 0
        vistos = {}
        c.RevisorApp = lambda fila, callback: vistos.update(qtd=len(fila))
        self.escrever("cte.xml", cte())
        c.main()
        self.assertEqual(vistos.get("qtd"), 1)

    def test_nota_de_servico_nao_pega_o_prazo_do_frete(self):
        c.perguntar_prazo_cte = lambda padrao=0: 28
        vistos = {}
        c.RevisorApp = lambda fila, callback: vistos.update(
            tipos=[i["tipo"] for i in fila]
        )
        self.escrever("nfse.xml", nfse())
        c.main()
        self.assertEqual(vistos.get("tipos"), ["NFSe"])
        self.assertFalse(list(self.destino.rglob("*.pdf")))

    def test_duplicata_da_nfe_continua_mandando(self):
        c.perguntar_prazo_cte = lambda padrao=0: 28
        self.escrever("nfe.xml", nfe())
        c.main()
        # duas parcelas, duas pastas, cada copia dizendo qual parcela e
        self.assertEqual(
            self.caminhos(),
            [str(Path("2026") / "08 - Agosto" / "14-08-2026"
                 / "FORNECEDOR TESTE LTDA - 4521 (parcela 1 de 2).pdf"),
             str(Path("2026") / "09 - Setembro" / "13-09-2026"
                 / "FORNECEDOR TESTE LTDA - 4521 (parcela 2 de 2).pdf")],
        )

    def test_nota_sem_pdf_oficial_e_arquivada_como_resumo(self):
        c.perguntar_prazo_cte = lambda padrao=0: 0
        # o PDF oficial falha; o classificador nao pode perder a nota
        c.gerar_pdf = self.originais[6]   # devolve o gerar_pdf de verdade

        import pynfse_nacional.pdf_generator as gerador
        original_gerador = gerador.generate_danfse_from_xml
        gerador.generate_danfse_from_xml = self._sempre_falha

        vistos = {}
        c.RevisorApp = lambda fila, callback: vistos.update(
            nomes=[i["pdf_temp"].name for i in fila], resumo=[i["resumo"] for i in fila]
        )
        try:
            self.escrever("nfse.xml", nfse_com_ibscbs())
            c.main()
        finally:
            gerador.generate_danfse_from_xml = original_gerador

        self.assertEqual(vistos.get("resumo"), [True])

    @staticmethod
    def _sempre_falha(xml_content, output_path):
        raise RuntimeError("falha simulada da biblioteca")

    def test_nota_parcelada_vai_uma_copia_para_cada_pasta(self):
        item = {"xml_path": Path("nota.xml"), "tipo": "NFSe", "chave": "3" * 50,
                "pdf_temp": self.tmp / "x.pdf", "nome_base": "PRESTADOR - 88"}
        (self.tmp / "x.pdf").write_bytes(b"%PDF-1.4")
        linhas = []

        c.finalizar_item(
            item,
            [date(2026, 8, 10), date(2026, 9, 9), date(2026, 10, 9)],
            "manual", linhas,
        )

        self.assertEqual(self.caminhos(), [
            str(Path("2026") / "08 - Agosto" / "10-08-2026" / "PRESTADOR - 88 (parcela 1 de 3).pdf"),
            str(Path("2026") / "09 - Setembro" / "09-09-2026" / "PRESTADOR - 88 (parcela 2 de 3).pdf"),
            str(Path("2026") / "10 - Outubro" / "09-10-2026" / "PRESTADOR - 88 (parcela 3 de 3).pdf"),
        ])
        self.assertEqual(len(linhas), 3)   # uma linha de log por parcela

    def test_parcela_unica_nao_ganha_rotulo(self):
        item = {"xml_path": Path("nota.xml"), "tipo": "NFe", "chave": "1" * 44,
                "pdf_temp": self.tmp / "x.pdf", "nome_base": "FORNECEDOR - 5"}
        (self.tmp / "x.pdf").write_bytes(b"%PDF-1.4")
        c.finalizar_item(item, [date(2026, 8, 10)], "manual", [])
        self.assertEqual(self.caminhos(), [
            str(Path("2026") / "08 - Agosto" / "10-08-2026" / "FORNECEDOR - 5.pdf")
        ])

    def test_nota_diferente_com_nome_igual_nao_sobrescreve(self):
        (self.tmp / "x.pdf").write_bytes(b"%PDF-1.4")
        for _ in range(2):
            c.finalizar_item(
                {"xml_path": Path("nota.xml"), "tipo": "NFe", "chave": None,
                 "pdf_temp": self.tmp / "x.pdf", "nome_base": "MESMO NOME - 1"},
                [date(2026, 8, 10)], "manual", [],
            )
        self.assertEqual(self.caminhos(), [
            str(Path("2026") / "08 - Agosto" / "10-08-2026" / "MESMO NOME - 1 [2].pdf"),
            str(Path("2026") / "08 - Agosto" / "10-08-2026" / "MESMO NOME - 1.pdf"),
        ])

    def test_resumo_ganha_sufixo_no_nome_do_arquivo(self):
        item = {"xml_path": Path("nota.xml"), "tipo": "NFSe",
                "chave": "3" * 50, "pdf_temp": self.tmp / "x.pdf", "resumo": True}
        (self.tmp / "x.pdf").write_bytes(b"%PDF-1.4")
        linhas = []
        c.finalizar_item(item, [date(2026, 8, 10)], "manual", linhas)
        self.assertEqual(self.caminhos(), [
            str(Path("2026") / "08 - Agosto" / "10-08-2026" / f"{'3' * 50}_SEM_PDF_OFICIAL.pdf")
        ])

    def test_log_registra_o_metodo_do_prazo(self):
        c.perguntar_prazo_cte = lambda padrao=0: 28
        self.escrever("cte.xml", cte())
        c.main()
        linhas = c.ARQUIVO_LOG.read_text(encoding="utf-8-sig").splitlines()
        self.assertIn("prazo-cte-28d", linhas[1])


# ===================== INTERFACE =====================
def tem_display():
    import tkinter
    try:
        janela = tkinter.Tk()
    except Exception:
        return False
    janela.destroy()
    return True


def pdf_de_teste(caminho, paginas=2):
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    doc = fitz.open()
    for n in range(paginas):
        pagina = doc.new_page(width=595, height=842)   # A4
        pagina.insert_text((80, 100), f"DACTE de teste - pagina {n + 1}")
    doc.save(caminho)
    doc.close()


@unittest.skipUnless(tem_display(), "sem display grafico")
class TestVisualizador(unittest.TestCase):
    def setUp(self):
        import tkinter
        self.tmp = Path(tempfile.mkdtemp())
        self.pdf = self.tmp / "nota.pdf"
        pdf_de_teste(self.pdf)
        self.root = tkinter.Tk()
        self.visual = c.VisualizadorPdf(self.root, largura=600, altura=400)
        self.visual.pack()
        self.root.update()

    def tearDown(self):
        self.visual.fechar()
        self.root.destroy()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_abre_e_ajusta_a_pagina(self):
        self.visual.abrir(self.pdf)
        self.root.update()
        self.root.update_idletasks()
        self.assertIsNotNone(self.visual._imagem)
        self.assertIn("%", self.visual.label_zoom.cget("text"))
        self.assertIn("1/2", self.visual.label_pagina.cget("text"))

    def test_zoom_manual_desliga_o_ajuste_automatico(self):
        self.visual.abrir(self.pdf)
        self.root.update()
        self.assertEqual(self.visual._modo_ajuste, "largura")
        self.visual.mais_zoom()
        self.assertIsNone(self.visual._modo_ajuste)
        self.visual.ajustar()
        self.assertEqual(self.visual._modo_ajuste, "pagina")

    def test_zoom_aumenta_e_diminui_a_imagem(self):
        self.visual.abrir(self.pdf)
        self.root.update()
        largura_inicial = self.visual._imagem.width()

        self.visual.mais_zoom()
        self.root.update()
        self.assertGreater(self.visual._imagem.width(), largura_inicial)

        self.visual.menos_zoom()
        self.visual.menos_zoom()
        self.root.update()
        self.assertLess(self.visual._imagem.width(), largura_inicial)

    def test_zoom_respeita_os_limites(self):
        self.visual.abrir(self.pdf)
        for _ in range(40):
            self.visual.mais_zoom()
        self.assertLessEqual(self.visual.escala, c.ZOOM_MAXIMO)
        for _ in range(60):
            self.visual.menos_zoom()
        self.assertGreaterEqual(self.visual.escala, c.ZOOM_MINIMO)

    def test_ajustar_largura_ocupa_a_area(self):
        self.visual.abrir(self.pdf)
        self.root.update()
        self.visual.ajustar_largura()
        self.root.update()
        largura_area = self.visual.canvas.winfo_width()
        self.assertAlmostEqual(self.visual._imagem.width(), largura_area, delta=3)

    def test_navega_entre_as_paginas(self):
        self.visual.abrir(self.pdf)
        self.root.update()
        self.assertEqual(str(self.visual.botao_anterior.cget("state")), "disabled")

        self.visual.proxima_pagina()
        self.root.update()
        self.assertIn("2/2", self.visual.label_pagina.cget("text"))
        self.assertEqual(str(self.visual.botao_proxima.cget("state")), "disabled")

        self.visual.pagina_anterior()
        self.root.update()
        self.assertIn("1/2", self.visual.label_pagina.cget("text"))

    def test_nao_passa_do_fim(self):
        self.visual.abrir(self.pdf)
        for _ in range(5):
            self.visual.proxima_pagina()
        self.assertEqual(self.visual.pagina_atual, 1)

    def test_area_de_rolagem_acompanha_o_zoom(self):
        self.visual.abrir(self.pdf)
        self.visual.mais_zoom()
        self.root.update()
        _, _, largura, altura = self.visual.canvas.cget("scrollregion").split()
        self.assertGreaterEqual(int(largura), self.visual._imagem.width())
        self.assertGreaterEqual(int(altura), self.visual._imagem.height())

    def test_sem_documento_os_comandos_nao_estouram(self):
        self.visual.mais_zoom()
        self.visual.ajustar()
        self.visual.proxima_pagina()   # nao deve levantar excecao


if __name__ == "__main__":
    unittest.main()
