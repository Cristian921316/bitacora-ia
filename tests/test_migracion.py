"""La base creada ANTES de Alembic (la del piloto) debe actualizarse sin perder datos."""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ES_SQLITE, RAIZ

ESQUEMA_PILOTO = """
CREATE TABLE accesos (id INTEGER PRIMARY KEY AUTOINCREMENT, casa TEXT NOT NULL, nombre_visitante TEXT NOT NULL,
  tipo TEXT NOT NULL, placa TEXT, observaciones TEXT,
  estado TEXT NOT NULL DEFAULT 'PRE_AUTORIZADO'
    CHECK (estado IN ('PRE_AUTORIZADO','ADENTRO','SALIO','PENDIENTE','RECHAZADO')),
  autorizado_por TEXT, fecha_hora TEXT NOT NULL, transcripcion TEXT, origen TEXT, telefono TEXT,
  hora_ingreso TEXT, hora_salida TEXT, hora_respuesta TEXT, respondido_por TEXT);
CREATE TABLE residentes (telefono TEXT PRIMARY KEY, casa TEXT NOT NULL, nombre TEXT);
CREATE TABLE movimientos (id INTEGER PRIMARY KEY AUTOINCREMENT, acceso_id INTEGER NOT NULL,
  evento TEXT NOT NULL CHECK (evento IN ('REGISTRADO','INGRESO','SALIDA','SOLICITUD','AUTORIZADO','RECHAZADO')),
  fecha_hora TEXT NOT NULL, origen TEXT);
CREATE INDEX idx_accesos_fecha ON accesos(fecha_hora);
CREATE INDEX idx_mov_fecha ON movimientos(fecha_hora);
"""


@pytest.mark.skipif(not ES_SQLITE, reason="solo aplica a la base SQLite del piloto")
def test_base_del_piloto_se_actualiza_sin_perder_datos(tmp_path):
    archivo = tmp_path / "piloto.db"
    con = sqlite3.connect(archivo)
    con.executescript(ESQUEMA_PILOTO)
    con.execute("INSERT INTO residentes VALUES ('593988391178','Mz 6 Villa 23','Cristian'), ('593977777777','Mz  6  VILLA 23','Esposa'), ('593966666666','Mz 2 Villa 5',NULL)")
    con.execute("INSERT INTO accesos (casa,nombre_visitante,tipo,estado,fecha_hora,transcripcion) VALUES ('Mz 6 Villa 23','Juan','VISITA','ADENTRO','2026-11-06T09:00:00','audio viejo')")
    con.execute("INSERT INTO movimientos (acceso_id,evento,fecha_hora,origen) VALUES (1,'REGISTRADO','2026-11-06T09:00:00','whatsapp')")
    con.commit()
    con.close()

    codigo = (
        "import db; db.init_db(); db.init_db()\n"      # dos veces: debe ser idempotente
        "from sqlalchemy import text\n"
        "c = db.engine.connect()\n"
        "print('VERSION', c.execute(text('select version_num from alembic_version')).scalar())\n"
        "print('ACCESOS', c.execute(text('select nombre_visitante, transcripcion from accesos')).all())\n"
        "print('MOV', c.execute(text('select count(*) from movimientos')).scalar())\n"
        "print('CASAS', sorted(r[0] for r in c.execute(text('select clave from casas'))))\n"
        "print('RESIDENTES', c.execute(text('select telefono, nombre, casa, cedula from residentes order by telefono')).all())\n"
        "ddl = c.execute(text(\"select sql from sqlite_master where name='residentes'\")).scalar()\n"
        "print('AUTOINCREMENT', 'AUTOINCREMENT' in ddl, '| cedula unica', 'uq_residentes_cedula' in ddl, '| telefono unico', 'uq_residentes_telefono' in ddl)\n"
        "c.execute(text(\"insert into residentes (telefono, cedula, nombre, casa) values ('593900000001','0900000000','Repetida A','Mz 1 Villa 1'), ('593900000002','0900000000','Repetida B','Mz 1 Villa 2')\"))\n"
        "c.commit()\n"
        "print('CEDULAS_REPETIDAS', c.execute(text(\"select count(*) from residentes where cedula='0900000000'\")).scalar())\n"
        "nuevo_id = c.execute(text('select max(id) from residentes')).scalar()\n"
        "c.execute(text(\"delete from residentes where id=:i\"), {'i': nuevo_id}); c.commit()\n"
        "c.execute(text(\"insert into residentes (telefono, nombre, casa) values ('593900000003','Id nuevo','Mz 1 Villa 3')\")); c.commit()\n"
        "print('ID_NO_SE_REUSA', c.execute(text('select max(id) from residentes')).scalar() > nuevo_id)\n"
    )
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{archivo}"}
    r = subprocess.run([sys.executable, "-c", codigo], cwd=RAIZ, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    from alembic.script import ScriptDirectory
    ultima = ScriptDirectory(str(RAIZ / "alembic")).get_current_head()
    assert f"VERSION {ultima}" in r.stdout          # queda en la última versión, sea cual sea
    assert "('593988391178', 'Cristian', 'Mz 6 Villa 23', None)" in r.stdout   # los residentes se conservan intactos
    assert "('593966666666', None, 'Mz 2 Villa 5', None)" in r.stdout
    assert "AUTOINCREMENT True | cedula unica False | telefono unico True" in r.stdout   # 0005: cédula ya no es única
    assert "CEDULAS_REPETIDAS 2" in r.stdout
    assert "ID_NO_SE_REUSA True" in r.stdout
    assert "('Juan', 'audio viejo')" in r.stdout
    assert "MOV 1" in r.stdout
    assert "['mz 2 villa 5', 'mz 6 villa 23']" in r.stdout   # las casas se deducen de los residentes, sin duplicar
