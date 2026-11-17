"""Prueba con el modelo REAL (BAAI/bge-m3): repite la consulta de la búsqueda guardada.

Es la prueba de aceptación del buscador en directo: si el portal codifica la consulta
igual que lo hizo el cuaderno 08, salen los mismos registros, en el mismo orden y con las
mismas puntuaciones. Necesita las dependencias y descargar el modelo (~2 GB la primera vez).

    pip install -e ".[busqueda]"
    MDF_PRUEBA_MODELO=1 pytest tests/test_busqueda_modelo.py -s            # PowerShell: $env:MDF_PRUEBA_MODELO=1
"""
from __future__ import annotations

import json
import os

import pytest

from app.busqueda import Buscador, Corpus
from app.config import Ajustes
from tests.conftest import DEMO

pytestmark = pytest.mark.skipif(os.environ.get("MDF_PRUEBA_MODELO") != "1",
                                reason="Define MDF_PRUEBA_MODELO=1 para probar con el modelo real.")

REF = json.loads((DEMO / "busqueda_referencia.json").read_text(encoding="utf-8"))
TOLERANCIA = 5e-4   # CPU frente a la GPU del cuaderno: diferencias de coma flotante


@pytest.fixture(scope="module")
def resultado():
    aj = Ajustes(_env_file=None)
    b = Buscador(aj, Corpus.cargar(aj))
    assert b.motivo_no_disponible() is None, b.motivo_no_disponible()
    p = REF["parametros"]
    return b.buscar(REF["consulta"], tipo=p["tipo"], terminos=REF["terminos_obligatorios"],
                    k_por_indice=p["k_por_indice"])


@pytest.mark.parametrize("tipo", ["tema", "historia"])
def test_mismos_candidatos_y_puntuaciones(resultado, tipo):
    obtenidos, esperados = resultado["candidatos"][tipo], REF["candidatos"][tipo]
    assert [c["id"] for c in obtenidos] == [c["id"] for c in esperados]
    for o, e in zip(obtenidos, esperados):
        assert o["score_coseno"] == pytest.approx(e["score_coseno"], abs=TOLERANCIA), o["id"]


def test_misma_seleccion_y_misma_lista(resultado):
    assert [(s["id"], s["orden_seleccion"]) for s in resultado["seleccion"]] == \
           [(s["id"], s["orden_seleccion"]) for s in REF["seleccion"]]
    assert [d["id"] for d in resultado["descartados"]] == [d["id"] for d in REF["descartados"]]
    assert [c["clip_id"] for c in resultado["playlist"]] == [c["clip_id"] for c in REF["playlist"]]
