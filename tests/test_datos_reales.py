"""Pruebas con los datos reales de la demo (capítulos de 6 entrevistas y la
transcripción de Pilar Barrientos Conde)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.catalogo import buscar_transcripcion, cargar_catalogo
from app.config import Ajustes
from app.resultados import cargar_busqueda
from app.tiempos import leer_fin, leer_inicio
from app.transcripcion import interpretar_transcripcion, leer_transcripcion
from tests.conftest import DEMO, hacer_cliente

PILAR = "Pilar_Barrientos_Conde_02_2"
ARCHIVO_PILAR = DEMO / "transcripciones" / "Pilar_Barrientos_Conde_transcripcion_corregida.json"


@pytest.fixture(scope="module")
def pilar_json() -> dict:
    return json.loads(ARCHIVO_PILAR.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def catalogo():
    aj = Ajustes(_env_file=None)
    return cargar_catalogo(aj, cargar_busqueda(aj).cortes)


# ---------------------------------------------------------------- transcripción

def test_transcripcion_real_integridad(pilar_json):
    tr = interpretar_transcripcion(pilar_json)
    assert tr.formato == "palabras" and len(tr.turnos) == 64
    assert tr.transcripcion_id == "BnXbNp6lyBGOsktqsWYX"
    assert (tr.idioma, tr.prob_idioma, tr.duracion_audio) == ("glg", 1.0, 447.04)
    # el texto de los turnos contiene todas las palabras, sin perder ni añadir ninguna
    esperado = "".join(w["text"] for w in pilar_json["words"] if w["type"] != "spacing")
    obtenido = "".join(t.texto for t in tr.turnos)
    assert re.sub(r"\s", "", obtenido) == re.sub(r"\s", "", esperado)
    # ordenados, dentro del audio y con el inicio real de su primera palabra
    assert all(a.inicio <= b.inicio for a, b in zip(tr.turnos, tr.turnos[1:]))
    assert all(0 <= t.inicio < t.fin <= 447.04 for t in tr.turnos)
    inicios = {w["start"] for w in pilar_json["words"] if w["type"] != "spacing"}
    assert all(t.inicio in inicios for t in tr.turnos)


def test_primeras_intervenciones_con_tiempos_reales(pilar_json):
    t = interpretar_transcripcion(pilar_json).turnos
    assert (t[0].tipo, t[0].texto, t[0].inicio, t[0].fin) == ("evento", "[música de fondo]", 0.0, 15.42)
    assert (t[1].tipo, t[1].inicio, t[1].fin) == ("habla", 15.48, 19.24)
    assert t[1].texto == "Pola mañá, levantarse, desayuno, pa escuela."
    assert (t[3].inicio, t[3].fin) == (23.28, 28.5)
    assert t[3].texto.endswith("que era na Falanxe.")


def test_eventos_dentro_de_frase_y_pausas(pilar_json):
    turnos = interpretar_transcripcion(pilar_json).turnos
    risas = [t for t in turnos if "[ríe]" in t.texto]
    assert [(t.tipo, t.inicio, t.fin) for t in risas] == [("habla", 324.56, 334.04), ("habla", 394.9, 403.2)]
    assert risas[0].texto.endswith("inchache o peito [ríe].")   # sin espacio antes del punto
    assert turnos[-1].tipo == "evento" and turnos[-1].fin == 447.0
    pausas = [t for t in turnos if t.tras_pausa]
    assert len(pausas) == 22 and all(5 < t.pausa < 8 for t in pausas[:-1])


def test_capitulos_reales_caen_en_palabras_de_la_transcripcion(catalogo, pilar_json):
    """Cada capítulo empieza y termina exactamente en una palabra: misma línea temporal."""
    palabras = [w for w in pilar_json["words"] if w["type"] != "spacing"]
    inicios, fines = {w["start"] for w in palabras}, {w["end"] for w in palabras}
    caps = catalogo.obtener(PILAR).capitulos
    assert len(caps) == 9
    for c in caps:
        assert c.inicio in inicios and c.fin in fines, c.id


# ---------------------------------------------------------------- capítulos

def test_capitulos_reales(catalogo):
    assert sum(len(e.capitulos) for e in catalogo.todas()) == 70
    c = catalogo.obtener(PILAR).capitulos
    assert [(x.id, x.inicio, x.fin) for x in c[:2]] == [
        ("Pilar_Barrientos_Conde_02_2_seg01", 15.48, 76.14),
        ("Pilar_Barrientos_Conde_02_2_seg02", 82.56, 141.44)]
    assert c[0].titulo == "A escola de palillo da Falaxe" and c[0].resumen.startswith("Pola mañá ía á escola")
    assert c[0].historias == ("Pilar_Barrientos_Conde_02_2_hist01",)
    assert catalogo.obtener(PILAR).procedencia.archivo_origen_temas == "Pilar_Barrientos_Conde_02_2.json"


def test_ids_de_capitulos_coinciden_con_los_cortes_guardados(catalogo):
    """Los cortes de tipo «tema» de la búsqueda son capítulos del archivo, con los mismos tiempos."""
    por_id = {c.id: c for e in catalogo.todas() for c in e.capitulos}
    cortes = [c for c in cargar_busqueda(Ajustes(_env_file=None)).cortes if c.tipo == "tema"]
    assert len(cortes) == 11
    for corte in cortes:
        cap = por_id[corte.record_id]
        assert (cap.inicio, cap.fin) == (corte.inicio, corte.fin), corte.record_id


def test_avisos_de_calidad_y_notas(catalogo):
    antelo = catalogo.obtener("Maria_Antelo_Lema")
    assert antelo.tramos_dudosos == (
        (145.0, 198.0, "Transcripción casi ininteligible (02:25–03:18)"),
        (367.0, 412.0, "Transcripción casi ininteligible (06:07–06:52)"))
    pilar = catalogo.obtener(PILAR)
    assert pilar.notas_transcripcion == (
        "Só se oen as respostas da entrevistada; as preguntas están cortadas na edición.",)


# ---------------------------------------------------------------- alias start_time / end_time

def test_alias_en_capitulos_turnos_y_palabras(tmp_path: Path):
    datos = tmp_path / "datos"
    (datos / "transcripciones").mkdir(parents=True)
    (datos / "temas_preparados.json").write_text(json.dumps([{
        "id": "X_seg01", "interview_id": "X", "title": "Tema", "start_time": 10.5, "end_time": 20,
        "summary": "r", "quality_flags": [{"kind": "k", "reason": "mal",
                                          "affected_interval": {"start_time": 11, "end_time": 12}}]}]),
        encoding="utf-8")
    (datos / "transcripciones" / "X.json").write_text(json.dumps({"turns": [
        {"start_time": 10.5, "end_time": 14, "speaker": "SPEAKER_00", "text": "Hola."},
        {"start_time": 14.5, "end_time": 19, "speaker": "SPEAKER_01", "text": "Adiós."}]}), encoding="utf-8")
    aj = Ajustes(_env_file=None, datos_dir=datos)
    e = cargar_catalogo(aj).obtener("X")
    assert (e.capitulos[0].inicio, e.capitulos[0].fin) == (10.5, 20.0)
    assert e.tramos_dudosos == ((11.0, 12.0, "mal"),)
    assert [(t.inicio, t.fin, t.hablante) for t in e.transcripcion] == [
        (10.5, 14.0, "SPEAKER_00"), (14.5, 19.0, "SPEAKER_01")]

    palabras = interpretar_transcripcion({"words": [
        {"text": "Hola", "start_time": 1.0, "end_time": 1.4, "type": "word", "speaker_id": "A"},
        {"text": "bos", "start_time": 1.5, "end_time": 1.9, "type": "word", "speaker_id": "B"}]})
    assert [(t.inicio, t.hablante, t.texto) for t in palabras.turnos] == [(1.0, "A", "Hola"), (1.5, "B", "bos")]


def test_alias_en_la_lista_de_cortes(datos_copia: Path):
    lista = json.loads((datos_copia / "playlist.json").read_text(encoding="utf-8"))
    for x in lista:
        x["start_time"], x["end_time"] = x.pop("start"), x.pop("end")
    (datos_copia / "playlist.json").write_text(json.dumps(lista), encoding="utf-8")
    b = cargar_busqueda(Ajustes(_env_file=None, datos_dir=datos_copia))
    assert len(b.cortes) == 32 and b.cortes[0].inicio == 100.18 and not b.avisos
    assert leer_inicio({"start_time": 3}) == 3 and leer_fin({"end": 4, "end_time": 5}) == 4


# ---------------------------------------------------------------- localizar la transcripción

def test_busqueda_del_archivo_de_transcripcion(tmp_path: Path):
    carpeta = tmp_path / "datos" / "transcripciones"
    carpeta.mkdir(parents=True)
    aj = Ajustes(_env_file=None, datos_dir=tmp_path / "datos")
    assert buscar_transcripcion(aj, PILAR) is None
    # el nombre del archivo no lleva «_02_2»: se encuentra por prefijo
    (carpeta / "Pilar_Barrientos_Conde_transcripcion_corregida.json").write_text("{}")
    assert buscar_transcripcion(aj, PILAR).name == "Pilar_Barrientos_Conde_transcripcion_corregida.json"
    assert buscar_transcripcion(aj, "Pilar") is None            # un prefijo sin «_» no vale
    # exacto gana a prefijo
    (carpeta / f"{PILAR}.json").write_text("{}")
    assert buscar_transcripcion(aj, PILAR).name == f"{PILAR}.json"


def test_transcripcion_ambigua_no_se_usa(tmp_path: Path, caplog):
    carpeta = tmp_path / "t"
    carpeta.mkdir()
    for n in ("Ana_transcripcion.json", "Ana_transcript.json"):   # las dos encajan con Ana_01
        (carpeta / n).write_text("{}")
    aj = Ajustes(_env_file=None, datos_dir=tmp_path, transcripciones_dir=carpeta)
    assert buscar_transcripcion(aj, "Ana_01") is None
    assert "varias transcripciones posibles" in caplog.text
    (carpeta / "Ana_01.json").write_text("{}")                     # un nombre exacto resuelve la duda
    assert buscar_transcripcion(aj, "Ana_01").name == "Ana_01.json"


def test_transcripcion_extra_por_configuracion(tmp_path: Path):
    carpeta = tmp_path / "t"
    carpeta.mkdir()
    (carpeta / "raro.json").write_text("{}")
    aj = Ajustes(_env_file=None, transcripciones_dir=carpeta, transcripciones_extra={"Z": "raro.json"})
    assert buscar_transcripcion(aj, "Z").name == "raro.json"


def test_transcripcion_rota_o_vacia_se_ignora(tmp_path: Path):
    mala = tmp_path / "mala.json"
    mala.write_text("{no json")
    assert leer_transcripcion(mala) is None
    assert interpretar_transcripcion({"words": []}) is None and interpretar_transcripcion(42) is None


# ---------------------------------------------------------------- HTML de la entrevista

def _filas(html: str):
    return re.findall(r'<li class="turno[^"]*"\s+id="turno-(\d+)"\s+data-inicio="([^"]+)"\s+data-fin="([^"]*)"', html)


def test_pagina_de_pilar_con_datos_reales(cliente):
    html = cliente.get(f"/entrevista/{PILAR}").text
    # capítulos: id, inicio y fin reales
    caps = re.findall(r'data-capitulo="([^"]+)"\s+data-inicio="([^"]+)" data-fin="([^"]+)"', html)
    temas = [t for t in json.loads((DEMO / "temas_preparados.json").read_text(encoding="utf-8"))
             if t["interview_id"] == PILAR]
    assert caps == [(t["id"], str(t["start"]), str(t["end"])) for t in temas]
    # intervenciones: una fila por turno y cada una con su inicio real
    tr = interpretar_transcripcion(json.loads(ARCHIVO_PILAR.read_text(encoding="utf-8")))
    filas = _filas(html)
    assert len(filas) == 64
    assert [float(f[1]) for f in filas] == [t.inicio for t in tr.turnos]
    assert "no es la entrevista entera" not in html
    # procedencia y duración para la comprobación del vídeo
    assert "BnXbNp6lyBGOsktqsWYX" in html and 'data-duracion="447.04"' in html
    assert "Pilar_Barrientos_Conde_02_2.json" in html and "probabilidad 100 %" in html
    assert "preguntas están cortadas na edición" in html
    assert "no indican el modelo" in html


def test_tramos_poco_fiables_se_marcan_en_capitulos_y_transcripcion(carpeta_videos, datos_copia):
    (datos_copia / "transcripciones").mkdir(exist_ok=True)
    (datos_copia / "transcripciones" / "Maria_Antelo_Lema_transcripcion_corregida.json").write_text(
        json.dumps({"turns": [{"start": 100, "end": 120, "text": "Antes."},
                              {"start": 150, "end": 160, "text": "Dentro."},
                              {"start": 300, "end": 310, "text": "Después."}]}), encoding="utf-8")
    with hacer_cliente(videos_dir=carpeta_videos, datos_dir=datos_copia) as c:
        html = c.get("/entrevista/Maria_Antelo_Lema").text
    assert html.count("Transcripción poco fiable") >= 3
    clases = re.findall(r'<li class="(turno[^"]*)"', html)
    assert ["dudoso" in c.split() for c in clases] == [False, True, False]


def test_sin_transcripcion_las_otras_entrevistas_siguen_con_capitulos(cliente):
    html = cliente.get("/entrevista/Carmen_Vileda_Rey").text
    assert html.count('data-capitulo="') == 10 and 'data-duracion=""' in html
    assert "no es la entrevista entera" in html


def test_datos_de_procedencia_en_la_api(cliente):
    datos = {e["interview_id"]: e for e in cliente.get("/api/entrevistas").json()}
    assert datos[PILAR]["tiene_capitulos"] and datos[PILAR]["tiene_transcripcion"]
    assert datos["Carmen_Vileda_Rey"]["tiene_capitulos"] and not datos["Carmen_Vileda_Rey"]["tiene_transcripcion"]
