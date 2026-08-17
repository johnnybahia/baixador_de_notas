"""
Testes do baixador (nao acessam a rede nem precisam de certificado).

    python -m unittest testes_baixador -v
"""

import base64
import gzip
import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

import baixador as b


CHAVE_NFE = "29250712345678000199550010000012341000012349"[:44].ljust(44, "0")
CHAVE_CTE = "29250712345678000199570010000055551000055559"[:44].ljust(44, "0")
CHAVE_NFSE = "3".ljust(50, "7")


def nfe_proc(chave=CHAVE_NFE, dh_emi="2026-07-15T10:20:30-03:00"):
    return (
        '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">'
        f'<NFe><infNFe Id="NFe{chave}" versao="4.00">'
        f"<ide><nNF>123</nNF><dhEmi>{dh_emi}</dhEmi></ide></infNFe></NFe>"
        "</nfeProc>"
    )


def evento_nfe(chave=CHAVE_NFE, seq="1"):
    return (
        '<procEventoNFe xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.00">'
        f'<evento><infEvento Id="ID110111{chave}0{seq}"><chNFe>{chave}</chNFe>'
        f"<nSeqEvento>{seq}</nSeqEvento></infEvento></evento></procEventoNFe>"
    )


def cte_proc(chave=CHAVE_CTE, dh_emi="2026-07-16T08:00:00-03:00"):
    return (
        '<cteProc xmlns="http://www.portalfiscal.inf.br/cte" versao="4.00">'
        f'<CTe><infCte Id="CTe{chave}"><ide><dhEmi>{dh_emi}</dhEmi></ide></infCte></CTe>'
        "</cteProc>"
    )


def resumo_nfe(chave=CHAVE_NFE):
    return (
        '<resNFe xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01">'
        f"<chNFe>{chave}</chNFe></resNFe>"
    )


def nfse_proc(chave=CHAVE_NFSE):
    return (
        '<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse">'
        f'<infNFSe Id="NFS{chave}"><nNFSe>7</nNFSe></infNFSe></NFSe>'
    )


def zipar(texto):
    return base64.b64encode(gzip.compress(texto.encode("utf-8"))).decode()


def envelope_resposta(ns, cstat, ult, maximo, docs=()):
    """docs: lista de (nsu, xml)."""
    partes = "".join(
        f'<docZip NSU="{str(nsu).zfill(15)}" schema="procNFe_v4.00.xsd">{zipar(xml)}</docZip>'
        for nsu, xml in docs
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>'
        f'<retDistDFeInt xmlns="{ns}" versao="1.01">'
        f"<tpAmb>1</tpAmb><cStat>{cstat}</cStat><xMotivo>teste</xMotivo>"
        f"<ultNSU>{str(ult).zfill(15)}</ultNSU><maxNSU>{str(maximo).zfill(15)}</maxNSU>"
        f"<loteDistDFeInt>{partes}</loteDistDFeInt>"
        "</retDistDFeInt></soap:Body></soap:Envelope>"
    ).encode("utf-8")


class RespostaFalsa:
    def __init__(self, content=b"", status_code=200, json_data=None, text=""):
        self.content = content
        self.status_code = status_code
        self._json = json_data
        self.text = text or (content.decode("utf-8", "replace") if content else "")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if self._json is None:
            raise ValueError("sem json")
        return self._json


class SessaoFalsa:
    """Devolve as respostas na ordem em que foram programadas."""

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.chamadas = []

    def _proxima(self, url, kwargs):
        self.chamadas.append((url, kwargs))
        if not self.respostas:
            raise AssertionError(f"chamada inesperada a {url}")
        return self.respostas.pop(0)

    def post(self, url, **kwargs):
        return self._proxima(url, kwargs)

    def get(self, url, **kwargs):
        return self._proxima(url, kwargs)


class BaseTemp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = b.Config(
            cnpj="12345678000199",
            cuf="29",
            tp_amb="1",
            cert_pfx=self.tmp / "cert.pfx",
            cert_senha="x",
            pasta_saida=self.tmp / "saida",
            pasta_controle=self.tmp / "controle",
            pasta_planilhas=self.tmp / "planilhas",
            max_lotes=5,
            pausa=0,
        )
        self.cfg.pasta_saida.mkdir(parents=True)
        self.cfg.pasta_controle.mkdir(parents=True)
        self.contadores = {"documentos": 0, "eventos": 0, "duplicados": 0,
                           "fora_periodo": 0}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @property
    def eventos_dir(self):
        return self.cfg.pasta_saida / "_EVENTOS_E_RESUMOS"


class TestChaveENome(unittest.TestCase):
    def test_extrai_chave_do_id(self):
        self.assertEqual(b.extrair_chave(nfe_proc()), CHAVE_NFE)

    def test_extrai_chave_nfse_com_50_digitos(self):
        self.assertEqual(b.extrair_chave(nfse_proc()), CHAVE_NFSE)

    def test_extrai_chave_solta_quando_nao_ha_id(self):
        self.assertEqual(b.extrair_chave(resumo_nfe()), CHAVE_NFE)

    def test_sem_chave_devolve_none(self):
        self.assertIsNone(b.extrair_chave("<xml><a>1</a></xml>"))

    def test_documento_nomeado_pela_chave(self):
        self.assertEqual(b.nome_arquivo(nfe_proc(), "NFE"), f"{CHAVE_NFE}.xml")

    def test_evento_inclui_raiz_e_nsu(self):
        nome = b.nome_arquivo(evento_nfe(), "NFE", "procEventoNFe", "42", documento=False)
        self.assertEqual(nome, f"{CHAVE_NFE}-procEventoNFe-42.xml")

    def test_raiz_de_xml_invalido_nao_estoura(self):
        self.assertEqual(b.raiz_do_xml("<nao fecha"), "")


class TestDescompactar(unittest.TestCase):
    def test_base64_com_gzip(self):
        self.assertEqual(b.descompactar_doczip(zipar(nfe_proc())), nfe_proc())

    def test_base64_sem_gzip(self):
        puro = base64.b64encode(nfe_proc().encode()).decode()
        self.assertEqual(b.descompactar_doczip(puro), nfe_proc())

    def test_remove_bom(self):
        com_bom = base64.b64encode(("﻿" + nfe_proc()).encode()).decode()
        self.assertEqual(b.descompactar_doczip(com_bom), nfe_proc())


class TestGravacao(BaseTemp):
    def test_documento_vai_para_a_raiz(self):
        destino = b.gravar_xml(self.cfg, nfe_proc(), "NFE", self.contadores, nsu="1")
        self.assertEqual(destino.parent, self.cfg.pasta_saida)
        self.assertEqual(destino.name, f"{CHAVE_NFE}.xml")
        self.assertEqual(self.contadores["documentos"], 1)

    def test_nfse_reconhecida_como_documento(self):
        destino = b.gravar_xml(self.cfg, nfse_proc(), "NFSE", self.contadores, nsu="3")
        self.assertEqual(destino.parent, self.cfg.pasta_saida)
        self.assertEqual(self.contadores["documentos"], 1)

    def test_evento_vai_para_subpasta(self):
        destino = b.gravar_xml(self.cfg, evento_nfe(), "NFE", self.contadores, nsu="9")
        self.assertEqual(destino.parent, self.eventos_dir)
        self.assertEqual(self.contadores["eventos"], 1)
        self.assertEqual(self.contadores["documentos"], 0)

    def test_documento_repetido_conta_como_duplicado(self):
        b.gravar_xml(self.cfg, nfe_proc(), "NFE", self.contadores, nsu="1")
        destino = b.gravar_xml(self.cfg, nfe_proc(), "NFE", self.contadores, nsu="2")
        self.assertIsNone(destino)
        self.assertEqual(self.contadores["documentos"], 1)
        self.assertEqual(self.contadores["duplicados"], 1)

    def test_eventos_da_mesma_chave_nao_se_sobrescrevem(self):
        b.gravar_xml(self.cfg, resumo_nfe(), "NFE", self.contadores, nsu="10")
        b.gravar_xml(self.cfg, evento_nfe(seq="1"), "NFE", self.contadores, nsu="11")
        b.gravar_xml(self.cfg, evento_nfe(seq="2"), "NFE", self.contadores, nsu="12")
        self.assertEqual(len(list(self.eventos_dir.glob("*.xml"))), 3)
        self.assertEqual(self.contadores["duplicados"], 0)


class TestEnvelope(BaseTemp):
    def test_nfe_usa_namespace_e_versao_certos(self):
        env = b.montar_envelope(self.cfg, "nfe", "7")
        self.assertIn("nfeDistDFeInteresse", env)
        self.assertIn('versao="1.01"', env)
        self.assertIn("<ultNSU>000000000000007</ultNSU>", env)
        self.assertIn("<CNPJ>12345678000199</CNPJ>", env)

    def test_cte_usa_namespace_e_versao_certos(self):
        env = b.montar_envelope(self.cfg, "cte", "0")
        self.assertIn("cteDistDFeInteresse", env)
        self.assertIn(b.NS_CTE, env)
        self.assertIn('versao="1.00"', env)


class TestSoap(BaseTemp):
    def test_para_no_maxnsu_e_grava_documentos(self):
        sessao = SessaoFalsa([
            RespostaFalsa(envelope_resposta(b.NS_NFE, "138", 2, 3, [(1, nfe_proc()), (2, evento_nfe())])),
            RespostaFalsa(envelope_resposta(b.NS_NFE, "138", 3, 3, [(3, nfse_proc())])),
        ])
        info = {"ultNSU": "0", "maxNSU": "0", "bloqueado_ate": None}
        b.rodar_soap(self.cfg, "nfe", sessao, info, self.contadores)

        self.assertEqual(info["ultNSU"], "000000000000003")
        self.assertEqual(self.contadores["documentos"], 2)
        self.assertEqual(self.contadores["eventos"], 1)
        self.assertEqual(len(sessao.chamadas), 2)

    def test_cstat_137_encerra(self):
        sessao = SessaoFalsa([RespostaFalsa(envelope_resposta(b.NS_NFE, "137", 5, 5))])
        info = {"ultNSU": "5", "maxNSU": "5", "bloqueado_ate": None}
        b.rodar_soap(self.cfg, "nfe", sessao, info, self.contadores)
        self.assertEqual(len(sessao.chamadas), 1)
        self.assertIsNone(info["bloqueado_ate"])

    def test_consumo_indevido_bloqueia(self):
        sessao = SessaoFalsa([RespostaFalsa(envelope_resposta(b.NS_NFE, "656", 0, 0))])
        info = {"ultNSU": "0", "maxNSU": "0", "bloqueado_ate": None}
        b.rodar_soap(self.cfg, "nfe", sessao, info, self.contadores)
        self.assertIsNotNone(b.bloqueado(info))
        self.assertIn("656", info["motivo_bloqueio"])

    def test_max_lotes_limita_as_chamadas(self):
        self.cfg.max_lotes = 2
        sessao = SessaoFalsa([
            RespostaFalsa(envelope_resposta(b.NS_NFE, "138", n, 99, [(n, nfe_proc(str(n).ljust(44, "1")))]))
            for n in (1, 2)
        ])
        info = {"ultNSU": "0", "maxNSU": "0", "bloqueado_ate": None}
        b.rodar_soap(self.cfg, "nfe", sessao, info, self.contadores)
        self.assertEqual(len(sessao.chamadas), 2)

    def test_nsu_parado_interrompe_o_laco(self):
        # resposta repetindo o mesmo ultNSU: seguir pedindo so gastaria cota
        sessao = SessaoFalsa([
            RespostaFalsa(envelope_resposta(b.NS_NFE, "138", 5, 99, [(5, nfe_proc())])),
        ])
        info = {"ultNSU": "5", "maxNSU": "99", "bloqueado_ate": None}
        b.rodar_soap(self.cfg, "nfe", sessao, info, self.contadores)
        self.assertEqual(len(sessao.chamadas), 1)

    def test_erro_de_rede_nao_propaga(self):
        class Explode(SessaoFalsa):
            def post(self, url, **kwargs):
                raise OSError("sem rede")

        info = {"ultNSU": "0", "maxNSU": "0", "bloqueado_ate": None}
        b.rodar_soap(self.cfg, "nfe", Explode([]), info, self.contadores)
        self.assertEqual(info["ultNSU"], "0")


class TestNfse(BaseTemp):
    def _lote(self, itens, status="DOCUMENTOS_LOCALIZADOS"):
        return RespostaFalsa(
            status_code=200,
            json_data={
                "StatusProcessamento": status,
                "LoteDFe": [
                    {"NSU": nsu, "ChaveAcesso": CHAVE_NFSE, "TipoDocumento": "NFSE",
                     "ArquivoXml": zipar(xml)}
                    for nsu, xml in itens
                ],
            },
        )

    def test_usa_endpoint_do_adn_com_nsu_no_caminho(self):
        sessao = SessaoFalsa([self._lote([(1, nfse_proc())], "NENHUM_DOCUMENTO_LOCALIZADO")])
        info = {"ultNSU": "0", "maxNSU": "0", "bloqueado_ate": None}
        b.rodar_nfse(self.cfg, sessao, info, self.contadores)

        url, kwargs = sessao.chamadas[0]
        self.assertEqual(url, "https://adn.nfse.gov.br/contribuintes/DFe/0")
        self.assertEqual(kwargs["params"]["lote"], "true")
        self.assertEqual(kwargs["params"]["cnpjConsulta"], "12345678000199")
        self.assertEqual(info["ultNSU"], "1")
        self.assertEqual(self.contadores["documentos"], 1)

    def test_pagina_ate_o_lote_vazio(self):
        sessao = SessaoFalsa([
            self._lote([(1, nfse_proc(CHAVE_NFSE))]),
            self._lote([(2, nfse_proc("4".ljust(50, "2")))]),
            self._lote([]),
        ])
        info = {"ultNSU": "0", "maxNSU": "0", "bloqueado_ate": None}
        b.rodar_nfse(self.cfg, sessao, info, self.contadores)

        self.assertEqual([c[0].rsplit("/", 1)[-1] for c in sessao.chamadas], ["0", "1", "2"])
        self.assertEqual(info["ultNSU"], "2")
        self.assertEqual(self.contadores["documentos"], 2)

    def test_404_encerra_sem_erro(self):
        sessao = SessaoFalsa([RespostaFalsa(status_code=404, text="")])
        info = {"ultNSU": "9", "maxNSU": "9", "bloqueado_ate": None}
        b.rodar_nfse(self.cfg, sessao, info, self.contadores)
        self.assertEqual(info["ultNSU"], "9")

    def test_429_bloqueia(self):
        sessao = SessaoFalsa([RespostaFalsa(status_code=429, text="")])
        info = {"ultNSU": "0", "maxNSU": "0", "bloqueado_ate": None}
        b.rodar_nfse(self.cfg, sessao, info, self.contadores)
        self.assertIsNotNone(b.bloqueado(info))

    def test_nsu_parado_interrompe_o_laco(self):
        # o ADN devolveu itens com NSU menor que o pedido: nao pode repetir para sempre
        sessao = SessaoFalsa([self._lote([(1, nfse_proc())])])
        info = {"ultNSU": "5", "maxNSU": "5", "bloqueado_ate": None}
        b.rodar_nfse(self.cfg, sessao, info, self.contadores)
        self.assertEqual(len(sessao.chamadas), 1)
        self.assertEqual(info["ultNSU"], "5")


class TestEstadoELock(BaseTemp):
    def test_estado_vai_e_volta(self):
        estado = {"nfe": {"ultNSU": "12", "maxNSU": "20", "bloqueado_ate": None}}
        b.salvar_estado(self.cfg, estado)
        self.assertEqual(b.carregar_estado(self.cfg), estado)

    def test_estado_corrompido_vira_dicionario_vazio(self):
        self.cfg.arquivo_estado.write_text("{isso nao e json", encoding="utf-8")
        self.assertEqual(b.carregar_estado(self.cfg), {})

    def test_lock_impede_segunda_execucao(self):
        self.assertTrue(b.adquirir_lock(self.cfg))
        self.assertFalse(b.adquirir_lock(self.cfg))
        b.liberar_lock(self.cfg)
        self.assertTrue(b.adquirir_lock(self.cfg))

    def test_lock_antigo_e_assumido(self):
        antigo = {"pid": 1, "inicio": (b.datetime.now() - b.timedelta(hours=3)).isoformat()}
        self.cfg.arquivo_lock.write_text(json.dumps(antigo), encoding="utf-8")
        self.assertTrue(b.adquirir_lock(self.cfg))


class TestPeriodo(BaseTemp):
    def test_le_formatos_de_data(self):
        self.assertEqual(b.ler_data("15/07/2026"), date(2026, 7, 15))
        self.assertEqual(b.ler_data("2026-07-15"), date(2026, 7, 15))
        self.assertEqual(b.ler_data("15-07-2026"), date(2026, 7, 15))
        self.assertIsNone(b.ler_data("  "))
        with self.assertRaises(ValueError):
            b.ler_data("31/31/2026")

    def test_data_do_documento_prefere_emissao(self):
        self.assertEqual(b.data_do_documento(nfe_proc()), date(2026, 7, 15))

    def test_data_de_evento(self):
        xml = evento_nfe().replace(
            "<nSeqEvento>1</nSeqEvento>",
            "<nSeqEvento>1</nSeqEvento><dhEvento>2026-08-02T09:00:00-03:00</dhEvento>",
        )
        self.assertEqual(b.data_do_documento(xml), date(2026, 8, 2))

    def test_documento_sem_data_nao_estoura(self):
        self.assertIsNone(b.data_do_documento("<resNFe><chNFe>1</chNFe></resNFe>"))

    def test_periodo_invalido_reclama(self):
        self.cfg.periodo_de = date(2026, 8, 1)
        self.cfg.periodo_ate = date(2026, 7, 1)
        with self.assertRaises(ValueError):
            self.cfg.validar_periodo()

    def test_grava_so_o_que_esta_no_periodo(self):
        self.cfg.periodo_de = date(2026, 7, 1)
        self.cfg.periodo_ate = date(2026, 7, 31)

        dentro = nfe_proc(CHAVE_NFE, "2026-07-15T10:00:00-03:00")
        antes = nfe_proc("1".ljust(44, "3"), "2026-06-20T10:00:00-03:00")
        depois = nfe_proc("2".ljust(44, "4"), "2026-08-05T10:00:00-03:00")

        self.assertIsNotNone(b.gravar_xml(self.cfg, dentro, "NFE", self.contadores))
        self.assertIsNone(b.gravar_xml(self.cfg, antes, "NFE", self.contadores))
        self.assertIsNone(b.gravar_xml(self.cfg, depois, "NFE", self.contadores))

        self.assertEqual(self.contadores["documentos"], 1)
        self.assertEqual(self.contadores["fora_periodo"], 2)
        self.assertEqual(len(list(self.cfg.pasta_saida.glob("*.xml"))), 1)

    def test_documento_sem_data_e_gravado(self):
        self.cfg.periodo_de = date(2026, 7, 1)
        self.assertIsNotNone(b.gravar_xml(self.cfg, resumo_nfe(), "NFE", self.contadores))

    def test_consulta_por_chave_ignora_o_periodo(self):
        self.cfg.periodo_de = date(2026, 7, 1)
        antiga = nfe_proc(CHAVE_NFE, "2020-01-01T10:00:00-03:00")
        self.assertIsNotNone(
            b.gravar_xml(self.cfg, antiga, "NFE", self.contadores, ignorar_periodo=True)
        )

    def test_varredura_para_ao_passar_da_data_final(self):
        self.cfg.periodo_ate = date(2026, 7, 31)
        sessao = SessaoFalsa([
            RespostaFalsa(envelope_resposta(
                b.NS_NFE, "138", 1, 999,
                [(1, nfe_proc(CHAVE_NFE, "2026-08-10T10:00:00-03:00"))])),
        ])
        info = {"ultNSU": "0", "maxNSU": "0", "bloqueado_ate": None}
        b.rodar_soap(self.cfg, "nfe", sessao, info, self.contadores)
        self.assertEqual(len(sessao.chamadas), 1)
        self.assertEqual(self.contadores["fora_periodo"], 1)


class TestAvancarNsu(BaseTemp):
    """Busca binaria do NSU onde o periodo comeca."""

    def resposta(self, nsu_base, dia, maximo=10000):
        return RespostaFalsa(envelope_resposta(
            b.NS_NFE, "138", nsu_base + 1, maximo,
            [(nsu_base + 1, nfe_proc(CHAVE_NFE, f"{dia}T10:00:00-03:00"))],
        ))

    def test_pula_para_perto_do_inicio_do_periodo(self):
        self.cfg.periodo_de = date(2026, 7, 1)
        # NSU 0..5000 = junho; acima disso, julho
        def para(nsu):
            return "2026-06-15" if nsu < 5000 else "2026-07-10"

        class SessaoCalendario(SessaoFalsa):
            def post(self, url, **kwargs):
                self.chamadas.append((url, kwargs))
                corpo = kwargs["data"].decode()
                nsu = int(corpo.split("<ultNSU>")[1].split("</ultNSU>")[0])
                return RespostaFalsa(envelope_resposta(
                    b.NS_NFE, "138", nsu + 1, 10000,
                    [(nsu + 1, nfe_proc(CHAVE_NFE, f"{para(nsu)}T10:00:00-03:00"))],
                ))

        sessao = SessaoCalendario([])
        info = {"ultNSU": "0", "maxNSU": "0", "bloqueado_ate": None}
        b.avancar_ate_periodo(self.cfg, "nfe", sessao, info, margem=100)

        avancado = int(info["ultNSU"])
        self.assertGreater(avancado, 4000)
        self.assertLess(avancado, 5000)   # ficou antes do inicio, com margem
        self.assertLess(len(sessao.chamadas), 20)

    def test_nao_pula_quando_ja_esta_no_periodo(self):
        self.cfg.periodo_de = date(2026, 7, 1)
        sessao = SessaoFalsa([self.resposta(100, "2026-07-20")])
        info = {"ultNSU": "100", "maxNSU": "10000", "bloqueado_ate": None}
        b.avancar_ate_periodo(self.cfg, "nfe", sessao, info)
        self.assertEqual(info["ultNSU"], "100")
        self.assertEqual(len(sessao.chamadas), 1)

    def test_sem_periodo_nao_faz_requisicao(self):
        sessao = SessaoFalsa([])
        info = {"ultNSU": "0", "maxNSU": "0", "bloqueado_ate": None}
        b.avancar_ate_periodo(self.cfg, "nfe", sessao, info)
        self.assertEqual(sessao.chamadas, [])

    def test_consumo_indevido_durante_a_sondagem_bloqueia(self):
        self.cfg.periodo_de = date(2026, 7, 1)
        sessao = SessaoFalsa([RespostaFalsa(envelope_resposta(b.NS_NFE, "656", 0, 0))])
        info = {"ultNSU": "0", "maxNSU": "0", "bloqueado_ate": None}
        b.avancar_ate_periodo(self.cfg, "nfe", sessao, info)
        self.assertIsNotNone(b.bloqueado(info))
        self.assertEqual(info["ultNSU"], "0")


class TestChaves(BaseTemp):
    def test_identifica_o_servico_pelo_modelo(self):
        self.assertEqual(b.servico_da_chave(CHAVE_NFE), "nfe")     # modelo 55
        self.assertEqual(b.servico_da_chave(CHAVE_CTE), "cte")     # modelo 57
        self.assertEqual(b.servico_da_chave(CHAVE_NFSE), "nfse")   # 50 digitos
        self.assertIsNone(b.servico_da_chave("123"))

    def test_aceita_chave_com_espacos_do_portal(self):
        espacada = " ".join(CHAVE_NFE[i:i + 4] for i in range(0, 44, 4))
        validas, erros = b.normalizar_chaves([espacada])
        self.assertEqual(validas, [CHAVE_NFE])
        self.assertEqual(erros, [])

    def test_aceita_varias_separadas_por_virgula_e_ignora_repetida(self):
        validas, erros = b.normalizar_chaves([f"{CHAVE_NFE}, {CHAVE_CTE};{CHAVE_NFE}"])
        self.assertEqual(validas, [CHAVE_NFE, CHAVE_CTE])
        self.assertEqual(erros, [])

    def test_aceita_varias_separadas_por_espaco(self):
        validas, _ = b.normalizar_chaves([CHAVE_NFE, CHAVE_CTE])
        self.assertEqual(validas, [CHAVE_NFE, CHAVE_CTE])

    def test_recusa_chave_incompleta(self):
        validas, erros = b.normalizar_chaves(["123456", CHAVE_NFE])
        self.assertEqual(validas, [CHAVE_NFE])
        self.assertEqual(erros, ["123456"])

    def test_envelope_de_consulta_por_chave(self):
        env = b.montar_envelope(self.cfg, "nfe", chave=CHAVE_NFE)
        self.assertIn(f"<consChNFe><chNFe>{CHAVE_NFE}</chNFe></consChNFe>", env)
        self.assertNotIn("distNSU", env)

        env_cte = b.montar_envelope(self.cfg, "cte", chave=CHAVE_CTE)
        self.assertIn(f"<consChCTe><chCTe>{CHAVE_CTE}</chCTe></consChCTe>", env_cte)

    def test_baixa_nfe_e_cte_por_chave(self):
        sessao = SessaoFalsa([
            RespostaFalsa(envelope_resposta(b.NS_NFE, "138", 0, 0, [(1, nfe_proc())])),
            RespostaFalsa(envelope_resposta(b.NS_CTE, "138", 0, 0, [(2, cte_proc())])),
        ])
        atendidas = b.baixar_por_chave(self.cfg, sessao, [CHAVE_NFE, CHAVE_CTE], self.contadores)
        self.assertEqual(atendidas, 2)
        self.assertEqual(self.contadores["documentos"], 2)

    def test_chave_fora_do_periodo_ainda_e_baixada(self):
        self.cfg.periodo_de = date(2026, 7, 1)
        antiga = nfe_proc(CHAVE_NFE, "2019-01-01T10:00:00-03:00")
        sessao = SessaoFalsa([
            RespostaFalsa(envelope_resposta(b.NS_NFE, "138", 0, 0, [(1, antiga)])),
        ])
        b.baixar_por_chave(self.cfg, sessao, [CHAVE_NFE], self.contadores)
        self.assertEqual(self.contadores["documentos"], 1)
        self.assertEqual(self.contadores["fora_periodo"], 0)

    def test_nfe_nao_encontrada_nao_conta(self):
        sessao = SessaoFalsa([RespostaFalsa(envelope_resposta(b.NS_NFE, "137", 0, 0))])
        atendidas = b.baixar_por_chave(self.cfg, sessao, [CHAVE_NFE], self.contadores)
        self.assertEqual(atendidas, 0)
        self.assertEqual(self.contadores["documentos"], 0)

    def test_baixa_nfse_pelo_sefin(self):
        sessao = SessaoFalsa([
            RespostaFalsa(status_code=200, json_data={"nfseXmlGZipB64": zipar(nfse_proc())}),
        ])
        atendidas = b.baixar_por_chave(self.cfg, sessao, [CHAVE_NFSE], self.contadores)

        url, _ = sessao.chamadas[0]
        self.assertEqual(
            url, f"https://sefin.nfse.gov.br/SefinNacional/nfse/{CHAVE_NFSE}"
        )
        self.assertEqual(atendidas, 1)
        self.assertTrue((self.cfg.pasta_saida / f"{CHAVE_NFSE}.xml").exists())

    def test_nfse_com_xml_inline(self):
        sessao = SessaoFalsa([
            RespostaFalsa(status_code=200, json_data={"xmlNFSe": nfse_proc()}),
        ])
        self.assertEqual(
            b.baixar_por_chave(self.cfg, sessao, [CHAVE_NFSE], self.contadores), 1
        )

    def test_nfse_inexistente_devolve_zero(self):
        sessao = SessaoFalsa([RespostaFalsa(status_code=404)])
        self.assertEqual(
            b.baixar_por_chave(self.cfg, sessao, [CHAVE_NFSE], self.contadores), 0
        )


class TestConferencia(BaseTemp):
    def planilha(self, chaves):
        from openpyxl import Workbook

        self.cfg.pasta_planilhas.mkdir(parents=True, exist_ok=True)
        wb = Workbook()
        ws = wb.active
        ws.append(["Chave", "Tipo", "Num", "DtAut", "Valor", "Emissor Nome"])
        for chave in chaves:
            ws.append([chave, "NFe", "1", "01/08/2026", "10,00", "Fulano LTDA"])
        caminho = self.cfg.pasta_planilhas / "notas.xlsx"
        wb.save(caminho)
        return caminho

    def test_relatorio_lista_apenas_o_que_falta(self):
        outra = "8".ljust(44, "9")
        self.planilha([CHAVE_NFE, outra, CHAVE_NFSE])
        b.gravar_xml(self.cfg, nfe_proc(), "NFE", self.contadores, nsu="1")
        b.gravar_xml(self.cfg, nfse_proc(), "NFSE", self.contadores, nsu="2")

        b.conferir_planilhas(self.cfg)

        linhas = self.cfg.arquivo_relatorio.read_text(encoding="utf-8-sig").splitlines()
        self.assertEqual(len(linhas), 2)  # cabecalho + a chave faltante
        self.assertIn(outra, linhas[1])

    def test_sem_faltantes_nao_gera_relatorio(self):
        self.planilha([CHAVE_NFE])
        b.gravar_xml(self.cfg, nfe_proc(), "NFE", self.contadores, nsu="1")
        b.conferir_planilhas(self.cfg)
        self.assertFalse(self.cfg.arquivo_relatorio.exists())


class TestCertificado(BaseTemp):
    """Gera um A1 auto-assinado de mentira para exercitar a leitura do .pfx."""

    def gerar_pfx(self, senha=b"1234", dias_validade=365):
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        import datetime as dt

        chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TESTE:12345678000199")])
        agora = dt.datetime.now(dt.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(nome).issuer_name(nome)
            .public_key(chave.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(agora - dt.timedelta(days=800))
            .not_valid_after(agora + dt.timedelta(days=dias_validade))
            .sign(chave, hashes.SHA256())
        )
        blob = serialization.pkcs12.serialize_key_and_certificates(
            b"teste", chave, cert, None,
            serialization.BestAvailableEncryption(senha) if senha else serialization.NoEncryption(),
        )
        caminho = self.tmp / "teste.pfx"
        caminho.write_bytes(blob)
        return caminho

    def test_extrai_pem_do_pfx(self):
        self.cfg.cert_pfx = self.gerar_pfx()
        self.cfg.cert_senha = "1234"
        pem_cert, pem_key = b.preparar_certificado(self.cfg)
        try:
            self.assertIn("BEGIN CERTIFICATE", Path(pem_cert).read_text())
            self.assertIn("BEGIN PRIVATE KEY", Path(pem_key).read_text())
        finally:
            for c in (pem_cert, pem_key):
                Path(c).unlink(missing_ok=True)

    def test_senha_errada_da_mensagem_clara(self):
        self.cfg.cert_pfx = self.gerar_pfx()
        self.cfg.cert_senha = "errada"
        with self.assertRaises(ValueError) as ctx:
            b.preparar_certificado(self.cfg)
        self.assertIn("pfx", str(ctx.exception))

    def test_certificado_vencido_e_recusado(self):
        self.cfg.cert_pfx = self.gerar_pfx(dias_validade=-1)
        self.cfg.cert_senha = "1234"
        with self.assertRaises(ValueError) as ctx:
            b.preparar_certificado(self.cfg)
        self.assertIn("VENCIDO", str(ctx.exception))

    def test_arquivo_ausente_e_recusado(self):
        self.cfg.cert_pfx = self.tmp / "nao_existe.pfx"
        with self.assertRaises(FileNotFoundError):
            b.preparar_certificado(self.cfg)


class TestConfig(BaseTemp):
    def escrever(self, texto):
        caminho = self.tmp / "config.ini"
        caminho.write_text(texto, encoding="utf-8")
        return caminho

    def base_ini(self, **trocas):
        valores = {
            "cnpj": "12.345.678/0001-99", "cuf": "29", "ambiente": "1",
            "pfx": str(self.tmp / "c.pfx"), "senha": "s",
            "saida": str(self.tmp / "s"), "controle": str(self.tmp / "c"),
            "nfse": "sim",
        }
        valores.update(trocas)
        return (
            "[EMPRESA]\ncnpj = {cnpj}\ncuf = {cuf}\nambiente = {ambiente}\n"
            "[CERTIFICADO]\npfx = {pfx}\nsenha = {senha}\n"
            "[PASTAS]\nsaida = {saida}\ncontrole = {controle}\n"
            "[SERVICOS]\nnfe = sim\ncte = nao\nnfse = {nfse}\n"
            "[LIMITES]\nmax_lotes_por_execucao = 3\n"
        ).format(**valores)

    def test_le_e_normaliza(self):
        cfg = b.Config.de_arquivo(self.escrever(self.base_ini()))
        self.assertEqual(cfg.cnpj, "12345678000199")
        self.assertEqual(cfg.max_lotes, 3)
        self.assertEqual(cfg.servicos, {"nfe": True, "cte": False, "nfse": True})

    def test_cnpj_invalido_reclama(self):
        with self.assertRaises(ValueError):
            b.Config.de_arquivo(self.escrever(self.base_ini(cnpj="123")))

    def test_ambiente_invalido_reclama(self):
        with self.assertRaises(ValueError):
            b.Config.de_arquivo(self.escrever(self.base_ini(ambiente="9")))

    def test_pasta_obrigatoria_reclama(self):
        with self.assertRaises(ValueError):
            b.Config.de_arquivo(self.escrever(self.base_ini(saida="")))

    def test_opcao_numerica_vazia_usa_o_padrao(self):
        ini = self.base_ini() + "[PERIODO]\nde =\nate =\nultimos_dias =\n"
        cfg = b.Config.de_arquivo(self.escrever(ini))
        self.assertIsNone(cfg.periodo_de)
        self.assertIsNone(cfg.periodo_ate)

    def test_opcao_numerica_invalida_reclama(self):
        ini = self.base_ini() + "[PERIODO]\nultimos_dias = trinta\n"
        with self.assertRaises(ValueError):
            b.Config.de_arquivo(self.escrever(ini))

    def test_periodo_vindo_do_config(self):
        ini = self.base_ini() + "[PERIODO]\nde = 01/07/2026\nate = 31/07/2026\n"
        cfg = b.Config.de_arquivo(self.escrever(ini))
        self.assertEqual(cfg.periodo_de, date(2026, 7, 1))
        self.assertEqual(cfg.periodo_ate, date(2026, 7, 31))

    def test_ultimos_dias_vira_data_inicial(self):
        ini = self.base_ini() + "[PERIODO]\nultimos_dias = 10\n"
        cfg = b.Config.de_arquivo(self.escrever(ini))
        self.assertEqual(cfg.periodo_de, date.today() - b.timedelta(days=10))

    def test_exemplo_do_repositorio_e_valido(self):
        exemplo = Path(__file__).parent / "config.ini.exemplo"
        cfg = b.Config.de_arquivo(exemplo)
        self.assertEqual(cfg.cnpj, "00000000000000")
        self.assertTrue(all(cfg.servicos.values()))


if __name__ == "__main__":
    unittest.main()
