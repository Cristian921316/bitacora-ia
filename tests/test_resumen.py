"""Resumen por WhatsApp para administradores: hoy, ayer y semana (solo cifras)."""
from datetime import datetime, timedelta

from sqlalchemy import insert

import config
import main
from conftest import registrar, texto, webhook
from db import accesos, engine
from resumen import detectar_periodo

ADMIN_TEL = "593991112222"


def poner(estado, tipo="VISITA", origen="whatsapp", hace_dias=0, casa="Mz 6 Villa 23", nombre="Persona Secreta"):
    f = (datetime.now() - timedelta(days=hace_dias)).replace(hour=0, minute=1).isoformat(timespec="seconds") if hace_dias else \
        datetime.now().replace(hour=0, minute=0, second=1).isoformat(timespec="seconds")
    with engine.begin() as c:
        c.execute(insert(accesos).values(casa=casa, nombre_visitante=nombre, tipo=tipo, estado=estado, fecha_hora=f, origen=origen))


def test_detecta_el_periodo_solo_si_piden_un_resumen():
    assert detectar_periodo("Reporte de hoy") == "hoy"
    assert detectar_periodo("reporte") == "hoy"
    assert detectar_periodo("¿Cuántas visitas tuvimos ayer?") == "ayer"
    assert detectar_periodo("resumen de la semana") == "semana"
    assert detectar_periodo("semana") == "semana"
    assert detectar_periodo("Hoy viene Juan Pérez en taxi") is None          # sigue siendo un anuncio de visita
    assert detectar_periodo("viene mi suegra ayer") is None
    assert detectar_periodo("") is None


def test_el_admin_pide_el_resumen_por_texto(admin, wsp, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TELEFONOS", "0991112222")
    poner("ADENTRO", "DELIVERY"); poner("RECHAZADO", "VISITA", "garita"); poner("PENDIENTE", "VISITA", "garita")
    poner("PRE_AUTORIZADO", "TAXI_UBER")
    r = texto(admin, ADMIN_TEL, "reporte de hoy")
    assert r["resumen"] == "hoy"
    t = wsp[-1][1]
    assert wsp[-1][0] == ADMIN_TEL and "Visitas: 4" in t and "Ingresaron: 1" in t and "Rechazadas: 1" in t
    assert "Sin aviso en garita: 2" in t and "1 rechazadas" in t and "1 sin respuesta" in t
    assert "Persona Secreta" not in t and "Mz 6" not in t                      # solo cifras


def test_ayer_y_semana(admin, wsp, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TELEFONOS", "0991112222")
    poner("ADENTRO", hace_dias=1); poner("ADENTRO", hace_dias=3); poner("ADENTRO", hace_dias=20)
    texto(admin, ADMIN_TEL, "reporte de ayer");   assert "Visitas: 1" in wsp[-1][1] and "Ayer" in wsp[-1][1]
    texto(admin, ADMIN_TEL, "reporte de la semana"); assert "Visitas: 2" in wsp[-1][1]
    texto(admin, ADMIN_TEL, "hoy");               assert "No hay visitas" in wsp[-1][1]


def test_el_admin_tambien_puede_pedirlo_por_nota_de_voz(admin, wsp, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TELEFONOS", "0991112222")
    monkeypatch.setattr(main, "transcribir", lambda *a, **k: "Dame el reporte de hoy por favor")
    poner("ADENTRO")
    assert webhook(admin, ADMIN_TEL, {"audioMessage": {}, "base64": "AAAA"})["resumen"] == "hoy"
    assert "Visitas: 1" in wsp[-1][1]


def test_un_numero_que_no_es_admin_no_recibe_el_resumen(admin, wsp, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TELEFONOS", "0991112222")
    poner("ADENTRO")
    registrar(admin, "0988391178")
    r = texto(admin, "593988391178", "reporte de hoy")        # un residente cualquiera
    assert "resumen" not in r and "Visitas: " not in wsp[-1][1]
    r = texto(admin, "593955555555", "reporte de hoy")        # un número desconocido
    assert "resumen" not in r and "no está registrado" in wsp[-1][1]


def test_un_admin_residente_puede_seguir_anunciando_visitas(admin, wsp, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TELEFONOS", "0988391178")
    registrar(admin, "0988391178")
    assert texto(admin, "593988391178", "Hoy viene Pedro Gómez en moto, placa MOT456")["registros"] == 1


def test_un_admin_que_no_es_residente_recibe_ayuda_si_no_pide_nada(admin, wsp, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TELEFONOS", "0991112222")
    texto(admin, ADMIN_TEL, "hola")
    assert "reporte de hoy" in wsp[-1][1]
