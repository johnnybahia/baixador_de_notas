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
        f"<ide><cUF>29</cUF><dhEmi>{dh_emi}</dhEmi></ide>"
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
        f"<ide><dhEmi>{dh_emi}</dhEmi></ide>{cobranca}"
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
            [str(Path("2026") / "08 - Agosto" / "13-08-2026" / f"{CHAVE_CTE}.pdf")],
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
        # duas parcelas, duas pastas
        self.assertEqual(
            self.caminhos(),
            [str(Path("2026") / "08 - Agosto" / "14-08-2026" / f"{'1' * 44}.pdf"),
             str(Path("2026") / "09 - Setembro" / "13-09-2026" / f"{'1' * 44}.pdf")],
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
