from __future__ import annotations

import json
import unicodedata
from pathlib import Path

from app.catalogo import cargar_catalogo, localizar_video
from app.config import Ajustes, fecha_desde_nombre
from app.resultados import cargar_busqueda, extracto, formatear_tiempo


def test_lectura_de_la_busqueda_real():
    b = cargar_busqueda(Ajustes(_env_file=None))
    assert len(b.cortes) == 32 and b.n_entrevistas == 6 and not b.avisos
    assert b.es_demo
    primero = b.cortes[0]
    assert (primero.clip_id, primero.inicio, primero.fin) == (
        "Carmen_Vileda_Rey_hist02__clip01", 100.18, 129.04)
    assert (primero.parte, primero.partes) == (1, 2)


def test_datos_no_validos_se_omiten_con_aviso(datos_copia: Path):
    lista = json.loads((datos_copia / "playlist.json").read_text(encoding="utf-8"))
    lista[0]["end"] = lista[0]["start"]            # intervalo vacío
    lista[1]["clip_id"] = lista[2]["clip_id"]      # repetido
    lista.append({"clip_id": "sin_entrevista"})
    (datos_copia / "playlist.json").write_text(json.dumps(lista), encoding="utf-8")
    b = cargar_busqueda(Ajustes(_env_file=None, datos_dir=datos_copia))
    assert len(b.cortes) == 30 and len(b.avisos) == 3


def test_falta_el_archivo_de_busqueda(tmp_path: Path):
    b = cargar_busqueda(Ajustes(_env_file=None, datos_dir=tmp_path))
    assert b.cortes == () and b.avisos


def test_formatos():
    assert formatear_tiempo(100.18) == "01:40"
    assert formatear_tiempo(3725) == "1:02:05"
    assert formatear_tiempo(None) == "--:--"
    assert extracto("a " * 400).endswith("…") and len(extracto("a " * 400)) <= 281
    assert extracto("corto") == "corto"


def test_fecha_en_el_nombre_original():
    assert fecha_desde_nombre("playlist_20261022T190150790698Z.json") == "2026-10-22T19:01:50Z"
    assert fecha_desde_nombre("playlist.json") == ""


def test_resolucion_de_videos_por_nombre_del_mapa(carpeta_videos: Path):
    aj = Ajustes(_env_file=None, videos_dir=carpeta_videos)
    cat = cargar_catalogo(aj, cargar_busqueda(aj).cortes)
    # el id lleva «_02_2» pero el archivo se llama «Pilar Barrientos Conde.mp4»
    e = cat.estado_video("Pilar_Barrientos_Conde_02_2")
    assert e.disponible and e.ruta.name == "Pilar Barrientos Conde.mp4"
    assert all(cat.estado_video(x.interview_id).disponible for x in cat.todas())


def test_nombres_con_acentos_en_otra_normalizacion(tmp_path: Path):
    nfd = unicodedata.normalize("NFD", "María.mp4")
    (tmp_path / nfd).write_bytes(b"x")
    aj = Ajustes(_env_file=None, videos_dir=tmp_path)
    estado = localizar_video(aj, "Maria", unicodedata.normalize("NFC", "María.mp4"))
    assert estado.disponible


def test_override_por_entrevista(tmp_path: Path):
    (tmp_path / "otro.mp4").write_bytes(b"x")
    aj = Ajustes(_env_file=None, videos_dir=tmp_path, videos_extra={"Z": "otro.mp4"})
    assert localizar_video(aj, "Z", "no_existe.mp4").disponible


def test_sin_carpeta_configurada_explica_el_motivo():
    e = localizar_video(Ajustes(_env_file=None), "Z", "Z.mp4")
    assert not e.disponible and "MDF_VIDEOS_DIR" in e.motivo


def test_entrevistas_json_opcional(datos_copia: Path):
    (datos_copia / "entrevistas.json").write_text(json.dumps({"entrevistas": [{
        "interview_id": "Carmen_Vileda_Rey", "title": "El palillado", "lugar": "Camariñas",
        "temas": ["Oficios"], "duration": 839,
        "capitulos": [{"title": "Aprender", "start": 0, "end": 95}, {"title": "Vender", "start": 95}],
        "transcripcion": [{"start": 3.1, "end": 9, "text": "Hola", "speaker": "Carmen"},
                          {"start": "mal", "text": "x"}],
    }]}), encoding="utf-8")
    aj = Ajustes(_env_file=None, datos_dir=datos_copia, temas_archivo="no_existe.json")
    cat = cargar_catalogo(aj, cargar_busqueda(aj).cortes)
    e = cat.obtener("Carmen_Vileda_Rey")
    assert e.encabezado == "El palillado" and e.lugar == "Camariñas"
    assert [c.titulo for c in e.capitulos] == ["Aprender", "Vender"]
    assert e.capitulos[1].fin is None and len(e.transcripcion) == 1
    assert cat.temas == ["Oficios"] and cat.lugares == ["Camariñas"]
    assert [x.persona for x in cat.filtrar(lugar="Camariñas")] == ["Carmen Vileda Rey"]


def test_entrevistas_json_roto_se_ignora(datos_copia: Path):
    (datos_copia / "entrevistas.json").write_text("{no es json", encoding="utf-8")
    aj = Ajustes(_env_file=None, datos_dir=datos_copia)
    assert len(cargar_catalogo(aj, cargar_busqueda(aj).cortes)) == 6


def test_temas_reales_tienen_prioridad_sobre_entrevistas_json(datos_copia: Path):
    (datos_copia / "entrevistas.json").write_text(json.dumps({"entrevistas": [{
        "interview_id": "Carmen_Vileda_Rey", "title": "El palillado",
        "capitulos": [{"title": "Otro", "start": 0}]}]}), encoding="utf-8")
    aj = Ajustes(_env_file=None, datos_dir=datos_copia)
    e = cargar_catalogo(aj, cargar_busqueda(aj).cortes).obtener("Carmen_Vileda_Rey")
    assert e.encabezado == "El palillado"            # metadatos de entrevistas.json
    assert len(e.capitulos) == 10 and e.capitulos[0].id == "Carmen_Vileda_Rey_seg01"
