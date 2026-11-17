"""Buscador en directo con un codificador simulado.

El codificador devuelve vectores REALES del índice (no aleatorios), de modo que se
ejercitan de verdad FAISS, los mapas fila→id, la verificación de hashes, el filtro
literal y la selección. Lo único que no se prueba aquí es BGE-M3 codificando texto:
eso lo cubre tests/test_busqueda_modelo.py, que necesita el modelo.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

faiss = pytest.importorskip("faiss")
np = pytest.importorskip("numpy")

from app import busqueda as bq
from app.config import Ajustes
from tests.conftest import DEMO, hacer_cliente

REFERENCIA = json.loads((DEMO / "busqueda_referencia.json").read_text(encoding="utf-8"))


def _ajustes(**kw) -> Ajustes:
    return Ajustes(_env_file=None, **kw)


@pytest.fixture(scope="module")
def vectores() -> dict[str, "np.ndarray"]:
    """Vector real de cada registro del índice, por id."""
    salida = {}
    for nombre in ("temas", "historias"):
        idx = faiss.read_index(str(DEMO / "indice" / f"{nombre}.faiss"))
        mapa = json.loads((DEMO / "indice" / f"{nombre}_mapa.json").read_text(encoding="utf-8"))
        v = idx.reconstruct_n(0, idx.ntotal)
        salida.update({m["id"]: v[m["faiss_row"]] for m in mapa})
    return salida


def codificador_para(vectores):
    def codificar(texto: str):
        if texto.startswith("como:"):            # «como:<id>» → el vector de ese registro
            return vectores[texto[5:]]
        semilla = int(hashlib.sha256(texto.encode()).hexdigest()[:8], 16)
        v = np.random.default_rng(semilla).standard_normal(bq.DIMENSION).astype("float32")
        return v / np.linalg.norm(v)
    return codificar


@pytest.fixture
def buscador(vectores) -> bq.Buscador:
    aj = _ajustes()
    return bq.Buscador(aj, bq.Corpus.cargar(aj), codificador_para(vectores))


# ---------------------------------------------------------------- lógica portada del cuaderno 08

def test_el_selector_reproduce_la_busqueda_guardada_de_referencia():
    """Con los candidatos reales de la búsqueda guardada, sale la misma selección y la misma lista."""
    sel, desc = bq.seleccionar_fragmentos(REFERENCIA["candidatos"], max_fragmentos=None)
    assert sel == REFERENCIA["seleccion"] and desc == REFERENCIA["descartados"]
    assert (len(sel), len(desc)) == (24, 6)
    ignorar = {"video_path", "drive_url", "video_mapping_status", "start_hms", "end_hms"}
    esperado = [{k: v for k, v in c.items() if k not in ignorar} for c in REFERENCIA["playlist"]]
    assert bq.crear_playlist(sel) == esperado


def test_playlist_del_repositorio_es_la_de_la_busqueda_de_referencia():
    assert json.loads((DEMO / "playlist.json").read_text(encoding="utf-8")) == REFERENCIA["playlist"]


def test_cuota_por_entrevistada_aplaza_pero_no_descarta():
    """El tercer resultado de una entrevistada pasa a la segunda pasada, no se pierde."""
    pos = {c["id"]: c["orden_seleccion"] for c in REFERENCIA["seleccion"]}
    assert pos["MariaJoseLiresMartinez_seg06"] == 12 and pos["MariaJoseRoa_seg08"] == 13
    assert sum(1 for c in REFERENCIA["seleccion"] if c["interview_id"] == "MariaJoseRoa") == 10


def test_filtro_literal_ignora_mayusculas_y_acentos():
    doc = {"title": "Palilladas con luz de carburo", "summary": "", "sources": []}
    assert bq.contiene_terminos(doc, ["CARBURÓ"]) and bq.contiene_terminos(doc, ["luz", "palilladas"])
    assert not bq.contiene_terminos(doc, ["luz", "ausente"]) and bq.contiene_terminos(doc, [])


# ---------------------------------------------------------------- índices reales

def test_los_indices_se_cargan_y_pasan_las_verificaciones(buscador):
    assert buscador.motivo_no_disponible() is None
    indices = buscador._cargar_indices()
    assert indices["temas"][0].ntotal == 70 and indices["historias"][0].ntotal == 42


def test_cada_registro_se_recupera_a_si_mismo_el_primero(buscador, vectores):
    for rid, tipo in [(r, "tema") for r in vectores if "_seg" in r] + [(r, "historia") for r in vectores if "_hist" in r]:
        cand = buscador.recuperar_candidatos(f"como:{rid}", k_por_indice=3)
        primero = cand[tipo][0]
        assert primero["id"] == rid and primero["rank_en_su_indice"] == 1, rid
        assert primero["score_coseno"] == pytest.approx(1.0, abs=1e-4)


def test_tipo_restringe_el_indice_consultado(buscador):
    assert buscador.buscar("como:Carmen_Vileda_Rey_hist02", tipo="temas")["candidatos"]["historia"] == []
    assert buscador.buscar("como:Carmen_Vileda_Rey_hist02", tipo="historias")["candidatos"]["tema"] == []


def test_buscar_devuelve_el_formato_del_cuaderno(buscador):
    r = buscador.buscar("como:Carmen_Vileda_Rey_hist02")
    assert set(r) == {"consulta", "terminos_obligatorios", "parametros", "candidatos", "seleccion",
                      "descartados", "playlist"}
    assert r["parametros"] == {"k_por_indice": 15, "max_fragmentos": None, "tipo": "ambos",
                               "max_por_entrevistada": 2, "max_fraccion_cubierta": 0.6,
                               "prioridad_en_empate": "historia"}
    assert (len(r["candidatos"]["tema"]), len(r["candidatos"]["historia"])) == (15, 15)
    assert len(r["seleccion"]) + len(r["descartados"]) == 30
    assert r["seleccion"][0]["id"] == "Carmen_Vileda_Rey_hist02"      # rango 1, la historia primero


@pytest.mark.parametrize("consulta", ["como:Carmen_Vileda_Rey_hist02", "una pregunta cualquiera", "otra distinta"])
def test_invariantes_de_la_seleccion(buscador, consulta):
    r = buscador.buscar(consulta)
    previos: dict[str, list] = {}
    for i, s in enumerate(r["seleccion"], start=1):
        assert s["orden_seleccion"] == i
        tramos = [(f["start"], f["end"]) for f in s["fuentes"]]
        cubierto = bq.segundos_interseccion(tramos, previos.get(s["interview_id"], []))
        fraccion = min(1.0, cubierto / bq.duracion_total(tramos))
        assert fraccion < 0.6 and s["fraccion_ya_cubierta"] == pytest.approx(fraccion, abs=1e-4)
        previos[s["interview_id"]] = bq.fusionar_intervalos(previos.get(s["interview_id"], []) + tramos)
    assert all(d["fraccion_ya_cubierta"] >= 0.6 for d in r["descartados"] if "fraccion_ya_cubierta" in d)
    ids = [c["id"] for c in r["seleccion"]] + [d["id"] for d in r["descartados"]]
    assert len(ids) == len(set(ids)) == 30


def test_terminos_literales_examinan_todo_el_indice(buscador):
    r = buscador.buscar("una pregunta cualquiera", terminos=["carburo"])
    todos = [c for lista in r["candidatos"].values() for c in lista]
    assert todos
    corpus = buscador.corpus
    docs = {d["id"]: d for d in corpus.temas + corpus.historias}
    assert all(bq.contiene_terminos(docs[c["id"]], ["carburo"]) for c in todos)
    esperados = sum(bq.contiene_terminos(d, ["carburo"]) for d in corpus.historias)
    assert len(r["candidatos"]["historia"]) == min(15, esperados)
    # el rango se cuenta entre los que cumplen el filtro
    assert [c["rank_en_su_indice"] for c in r["candidatos"]["historia"]] == list(range(1, len(r["candidatos"]["historia"]) + 1))
    assert buscador.buscar("una pregunta cualquiera", terminos=["zzzxxyy"])["playlist"] == []


def test_cache_de_consultas(buscador):
    a = buscador.buscar("otra distinta")
    assert buscador.buscar("  otra distinta  ") is a                  # misma consulta tras recortar
    assert buscador.buscar("otra distinta", tipo="temas") is not a


@pytest.mark.parametrize("consulta,tipo,terminos,mensaje", [
    ("", "ambos", None, "Escribe una consulta"), ("   ", "ambos", None, "Escribe una consulta"),
    ("x" * 1001, "ambos", None, "demasiado larga"), ("hola", "raro", None, "tipo"),
    ("hola", "ambos", "Venezuela", "términos literales"), ("hola", "ambos", [" "], "términos literales"),
])
def test_entradas_no_validas(buscador, consulta, tipo, terminos, mensaje):
    with pytest.raises(bq.ErrorBusqueda, match=mensaje):
        buscador.buscar(consulta, tipo=tipo, terminos=terminos)


# ---------------------------------------------------------------- integridad y disponibilidad

def test_un_corpus_modificado_se_detecta_por_hash(tmp_path, vectores):
    datos = tmp_path / "datos"
    shutil.copytree(DEMO, datos)
    (datos / "indice" / "temas_mapa.json").write_text(
        (datos / "indice" / "temas_mapa.json").read_text(encoding="utf-8") + " ", encoding="utf-8")
    aj = _ajustes(datos_dir=datos)
    b = bq.Buscador(aj, bq.Corpus.cargar(aj), codificador_para(vectores))
    with pytest.raises(bq.ErrorBusqueda, match="ha cambiado"):
        b.buscar("hola")
    assert "ha cambiado" in b.motivo_no_disponible()                  # y el motivo queda registrado


def test_saltos_de_linea_crlf_de_git_en_windows_no_invalidan_los_hashes(tmp_path, vectores):
    datos = tmp_path / "datos"
    shutil.copytree(DEMO, datos)
    for ruta in datos.rglob("*.json"):                       # como haría git con autocrlf=true
        ruta.write_bytes(ruta.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    aj = _ajustes(datos_dir=datos)
    b = bq.Buscador(aj, bq.Corpus.cargar(aj), codificador_para(vectores))
    assert b.buscar("como:Carmen_Vileda_Rey_hist02")["seleccion"][0]["id"] == "Carmen_Vileda_Rey_hist02"
    # …pero un cambio real de contenido sí se detecta, también con CRLF
    ruta = datos / "historias_preparadas.json"
    ruta.write_bytes(ruta.read_bytes().replace(b"palillo", b"palilho", 1))
    b2 = bq.Buscador(aj, bq.Corpus.cargar(aj), codificador_para(vectores))
    with pytest.raises(bq.ErrorBusqueda, match="ha cambiado"):
        b2.buscar("hola")


def test_faltan_archivos_del_indice(tmp_path, vectores):
    datos = tmp_path / "datos"
    shutil.copytree(DEMO, datos)
    (datos / "indice" / "historias.faiss").unlink()
    aj = _ajustes(datos_dir=datos)
    b = bq.Buscador(aj, bq.Corpus.cargar(aj), codificador_para(vectores))
    assert "faltan archivos del índice" in b.motivo_no_disponible() and "historias.faiss" in b.motivo_no_disponible()


def test_sin_dependencias_se_explica_como_instalarlas(monkeypatch):
    aj = _ajustes()
    real = bq.importlib.util.find_spec
    monkeypatch.setattr(bq.importlib.util, "find_spec", lambda n, *a: None if n == "sentence_transformers" else real(n, *a))
    motivo = bq.Buscador(aj, bq.Corpus.cargar(aj)).motivo_no_disponible()
    assert "sentence_transformers" in motivo and 'pip install -e ".[busqueda]"' in motivo


def test_desactivado_por_configuracion():
    aj = _ajustes(buscador="off")
    assert "MDF_BUSCADOR=off" in bq.Buscador(aj, bq.Corpus.cargar(aj)).motivo_no_disponible()


# ---------------------------------------------------------------- corpus: clips por identificador

def test_todo_clip_de_la_lista_guardada_se_reconstruye_desde_el_corpus():
    corpus = bq.Corpus.cargar(_ajustes())
    for c in REFERENCIA["playlist"]:
        r = corpus.clip(c["clip_id"])
        for k in ("clip_id", "record_id", "record_type", "interview_id", "speaker", "title", "start", "end",
                  "transcript_text", "source_number", "from_sentence", "to_sentence"):
            assert r[k] == c[k], (c["clip_id"], k)


def test_clips_inexistentes_no_se_reconstruyen():
    corpus = bq.Corpus.cargar(_ajustes())
    for malo in ("nope", "Carmen_Vileda_Rey_hist02__clip99", "Carmen_Vileda_Rey_hist02__clip00",
                 "Carmen_Vileda_Rey_hist02__clipxx", "__clip01", ""):
        assert corpus.clip(malo) is None


# ---------------------------------------------------------------- rutas con el buscador

@pytest.fixture
def cliente_vivo(carpeta_videos, vectores):
    aj = _ajustes(videos_dir=carpeta_videos)
    b = bq.Buscador(aj, bq.Corpus.cargar(aj), codificador_para(vectores))
    with hacer_cliente(buscador=b, videos_dir=carpeta_videos) as c:
        c.buscador = b
        yield c


def test_resultados_en_directo(cliente_vivo):
    html = cliente_vivo.get("/resultados?q=como:Carmen_Vileda_Rey_hist02").text
    r = cliente_vivo.buscador.buscar("como:Carmen_Vileda_Rey_hist02")
    assert 'class="aviso-vivo"' in html and "Búsqueda en directo" in html and "aviso-demo" not in html
    assert "no garantizan que respondan a la pregunta" in html
    assert html.count('<li class="corte"') == len(r["playlist"])
    assert "Cómo se ha construido esta lista" in html and "15 temas y 15 historias" in html
    assert "Omitidos por solapamiento" in html
    assert 'id="plan-datos"' in html and "Reproducir fragmentos" in html


def test_un_corte_en_directo_abre_la_entrevista_en_su_segundo_y_vuelve(cliente_vivo):
    q = "como:MariaJoseRoa_hist09"
    html = cliente_vivo.get("/resultados", params={"q": q, "tipo": "historias", "terminos": "palillo"}).text
    assert "solo en historias" in html and "palillo" in html
    api = {c["clip_id"]: c for c in cliente_vivo.get("/api/buscar", params={"q": q, "tipo": "historias", "terminos": "palillo"}).json()["cortes"]}
    enlaces = [unescape(h) for h in re.findall(r'<a class="boton" href="([^"]+)">Ver corte', html)]
    assert enlaces and len(enlaces) == len(api)
    for href in enlaces:
        clip = parse_qs(urlsplit(href).query)["clip"][0]
        pagina = cliente_vivo.get(href)
        assert pagina.status_code == 200 and f'data-inicio="{api[clip]["inicio"]}"' in pagina.text
        volver = unescape(re.search(r'<a href="([^"]+)">‹ Volver a resultados', pagina.text).group(1))
        destino = urlsplit(volver)
        assert destino.path == "/resultados" and destino.fragment == f"corte-{clip}"
        assert parse_qs(destino.query) == {"q": [q], "tipo": ["historias"], "terminos": ["palillo"]}
        # la página de la entrevista lista los cortes de ESTA búsqueda, no los de la demostración
        propios = [c for c in api.values() if c["interview_id"] == api[clip]["interview_id"]]
        assert pagina.text.count('data-accion="corte"') >= len(propios)


def test_clip_fuera_de_la_busqueda_se_abre_igualmente_desde_el_corpus(cliente_vivo):
    # seg06 de Carmen se descartó de la búsqueda guardada, pero existe en el corpus
    html = cliente_vivo.get("/entrevista/Carmen_Vileda_Rey?clip=Carmen_Vileda_Rey_seg06__clip01").text
    assert 'data-inicio="357.86"' in html and 'data-fin="469.63"' in html
    assert "no está en la búsqueda cargada" not in html


def test_sin_resultados_por_terminos_ausentes(cliente_vivo):
    html = cliente_vivo.get("/resultados", params={"q": "hola", "terminos": "zzzxxyy"}).text
    assert "Ningún candidato cumple los filtros" in html and "no demuestra que nadie hable del asunto" in html
    assert 'class="aviso-vivo"' in html


def test_consulta_demasiado_larga_vuelve_a_la_demostracion_con_aviso(cliente_vivo):
    html = cliente_vivo.get("/resultados", params={"q": "x" * 1001}).text
    assert "No se ha podido buscar" in html and "demasiado larga" in html and "aviso-demo" in html


def test_sin_consulta_se_muestra_la_guardada_e_invita_a_buscar(cliente_vivo):
    html = cliente_vivo.get("/resultados").text
    assert "aviso-demo" in html and "Escribe una consulta para buscar en directo" in html
    # con un codificador ya disponible no se avisa de que la primera consulta tarde
    assert "La primera consulta en directo tarda unos segundos" not in html


def test_api_del_buscador(cliente_vivo, cliente):
    estado = cliente_vivo.get("/api/buscador").json()
    assert estado["disponible"] is True and estado["motivo"] is None and estado["modelo"] == "BAAI/bge-m3"
    datos = cliente_vivo.get("/api/buscar?q=como:Carmen_Vileda_Rey_hist02").json()
    assert datos["es_demo"] is False and datos["n_candidatos"] == {"tema": 15, "historia": 15}
    assert datos["n_cortes"] == len(datos["cortes"]) and datos["cortes"][0]["clip_id"] == "Carmen_Vileda_Rey_hist02__clip01"
    assert cliente_vivo.get("/api/buscar").status_code == 400
    assert cliente_vivo.get("/api/buscar?q=x&tipo=raro").status_code == 400
    assert cliente_vivo.get("/api/buscar?q=" + "x" * 1001).status_code == 422
    # sin buscador disponible
    assert cliente.get("/api/buscar?q=hola").status_code == 503
    assert cliente.get("/api/buscador").json()["disponible"] is False
