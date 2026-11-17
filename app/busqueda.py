"""Buscador en directo: BGE-M3 + FAISS + selección determinista.

Es una adaptación fiel del cuaderno ``08_recuperacion_audiovisual_videos_vinculados``
de Mar de Fondo; las funciones de selección están portadas sin cambios de lógica
para que el portal y el cuaderno devuelvan lo mismo ante la misma consulta.

Flujo de una consulta:

1. BGE-M3 (1024 dimensiones) codifica el texto, en gallego o castellano, sin
   traducción previa; el vector se normaliza (L2).
2. Se consultan por separado el índice de **temas** (segmentos continuos) y el
   de **historias** (pueden tener varios intervalos discontinuos): hasta 15
   candidatos de cada uno. Si se piden términos literales, se examina todo el
   índice y se conservan los candidatos que los contienen.
3. Se intercalan las posiciones de ambos índices (primero la historia) y se
   aplican reglas explícitas: se omite un candidato cubierto en un 60 % o más
   por lo ya seleccionado de la misma entrevista, y se prefieren como mucho dos
   unidades por entrevistada en una primera pasada; las demás **no se
   descartan**, pasan a una segunda pasada.

No hay LLM, reranker ni umbral de similitud: una consulta sin respuesta también
recibe vecinos de FAISS. Los resultados son candidatos, no una garantía de
pertinencia.

Las dependencias pesadas (``faiss``, ``numpy``, ``sentence_transformers``) se
importan solo al usarlas: sin ellas el portal arranca y usa la búsqueda guardada.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import threading
import unicodedata
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any, Callable

from .config import Ajustes

log = logging.getLogger("mardefondo.busqueda")

MODELO_ID = "BAAI/bge-m3"
DIMENSION = 1024
K_POR_INDICE = 15
MAX_POR_ENTREVISTADA = 2
MAX_FRACCION_YA_CUBIERTA = 0.60
PRIORIDAD_EN_EMPATE = "historia"
TIPOS = ("ambos", "temas", "historias")
CONSULTAS_EN_CACHE = 64
MAX_CARACTERES_CONSULTA = 1000


class ErrorBusqueda(Exception):
    """Error previsto, con un mensaje que se puede mostrar a la persona."""


# ---------------------------------------------------------------------------
# Intervalos y selección (portado del cuaderno 08)
# ---------------------------------------------------------------------------

def fusionar_intervalos(pares):
    """Unión de intervalos temporales [inicio, fin), sin inventar silencios intermedios."""
    pares = sorted((float(a), float(b)) for a, b in pares if float(b) > float(a))
    union: list[tuple[float, float]] = []
    for a, b in pares:
        if union and a <= union[-1][1]:
            union[-1] = (union[-1][0], max(union[-1][1], b))
        else:
            union.append((a, b))
    return union


def duracion_total(pares):
    return sum(b - a for a, b in fusionar_intervalos(pares))


def segundos_interseccion(a, b):
    a, b = fusionar_intervalos(a), fusionar_intervalos(b)
    i = j = 0
    total = 0.0
    while i < len(a) and j < len(b):
        total += max(0.0, min(a[i][1], b[j][1]) - max(a[i][0], b[j][0]))
        if a[i][1] <= b[j][1]:
            i += 1
        else:
            j += 1
    return total


def intercalar_rangos(candidatos, primero=PRIORIDAD_EN_EMPATE):
    orden = ["historia", "tema"] if primero == "historia" else ["tema", "historia"]
    lista = []
    max_filas = max((len(candidatos.get(t, [])) for t in orden), default=0)
    for posicion in range(max_filas):
        for tipo in orden:
            if posicion < len(candidatos.get(tipo, [])):
                lista.append(candidatos[tipo][posicion])
    return lista


def seleccionar_fragmentos(candidatos, max_fragmentos=None,
                           max_por_entrevistada=MAX_POR_ENTREVISTADA,
                           max_fraccion_cubierta=MAX_FRACCION_YA_CUBIERTA):
    """Selecciona sin duplicar intervalos y sin descartar por cuota de entrevistada.

    ``max_por_entrevistada`` es una PREFERENCIA para la primera pasada.
    ``max_fragmentos=None`` conserva todos los candidatos admisibles.
    """
    if max_fragmentos is not None and (not isinstance(max_fragmentos, int) or max_fragmentos < 1):
        raise ValueError("max_fragmentos debe ser entero positivo o None.")
    if max_por_entrevistada is not None and (
        not isinstance(max_por_entrevistada, int) or max_por_entrevistada < 1
    ):
        raise ValueError("max_por_entrevistada debe ser entero positivo o None.")
    if not 0 <= max_fraccion_cubierta <= 1:
        raise ValueError("La fracción máxima cubierta debe estar entre 0 y 1.")

    seleccionados: list[dict] = []
    descartados: list[dict] = []
    usados: dict[str, list] = {}
    por_entrevista: Counter = Counter()
    ordenados = intercalar_rangos(candidatos)

    def incorporar(candidato):
        eid = candidato["interview_id"]
        tramos = [(s["start"], s["end"]) for s in candidato["fuentes"]]
        longitud = duracion_total(tramos)
        if longitud <= 0:
            descartados.append({"id": candidato["id"], "motivo": "intervalos inválidos"})
            return
        cubierto = segundos_interseccion(tramos, usados.get(eid, []))
        fraccion = min(1.0, cubierto / longitud)
        if fraccion >= max_fraccion_cubierta:
            descartados.append({"id": candidato["id"], "motivo": "solapamiento temporal",
                                "fraccion_ya_cubierta": round(fraccion, 4)})
            return
        elegido = {**candidato,
                   "orden_seleccion": len(seleccionados) + 1,
                   "duracion_total_segundos": round(longitud, 3),
                   "fraccion_ya_cubierta": round(fraccion, 4)}
        seleccionados.append(elegido)
        por_entrevista[eid] += 1
        usados[eid] = fusionar_intervalos(usados.get(eid, []) + tramos)

    # Primera pasada: diversidad, sin descartar nada por superar la cuota.
    pospuestos = []
    for candidato in ordenados:
        if max_fragmentos is not None and len(seleccionados) >= max_fragmentos:
            break
        if (max_por_entrevistada is not None
                and por_entrevista[candidato["interview_id"]] >= max_por_entrevistada):
            pospuestos.append(candidato)
        else:
            incorporar(candidato)

    # Segunda pasada: los resultados de una misma persona siguen disponibles.
    for candidato in pospuestos:
        if max_fragmentos is not None and len(seleccionados) >= max_fragmentos:
            break
        incorporar(candidato)

    return seleccionados, descartados


def fuentes(item):
    """Intervalos de un tema (uno) o de una historia (varios, discontinuos)."""
    if item["record_type"] == "topic":
        return [{
            "start": float(item["start"]), "end": float(item["end"]),
            "from_sentence": item.get("from_sentence"), "to_sentence": item.get("to_sentence"),
            "transcript_text": item.get("transcript_text", ""),
        }]
    return [{
        "start": float(s["start"]), "end": float(s["end"]),
        "from_sentence": s.get("from_sentence"), "to_sentence": s.get("to_sentence"),
        "transcript_text": s.get("transcript_text", ""),
    } for s in item["sources"]]


def normalizar_busqueda(s):
    """Solo para el filtro LITERAL opcional; los textos originales no se modifican."""
    s = unicodedata.normalize("NFKD", s.casefold())
    return "".join(c for c in s if not unicodedata.combining(c))


def contiene_terminos(item, terminos):
    if not terminos:
        return True
    original = "\n".join([
        item.get("title", ""), item.get("summary", ""),
        item.get("transcript_text", ""),
        *[s.get("transcript_text", "") for s in item.get("sources", [])],
    ])
    texto = normalizar_busqueda(original)
    return all(normalizar_busqueda(t).strip() in texto for t in terminos)


def entrada_playlist(registro: dict, n: int, fuente: dict) -> dict:
    """Un clip de la lista de reproducción (mismo formato que exporta el cuaderno 08)."""
    return {
        "clip_id": f'{registro["id"]}__clip{n:02d}',
        "selection_order": registro.get("orden_seleccion", 1),
        "source_number": n,
        "record_id": registro["id"], "record_type": registro["tipo"],
        "interview_id": registro["interview_id"], "speaker": registro["speaker"],
        "title": registro["title"], "summary": registro["summary"],
        "rank_in_index": registro.get("rank_en_su_indice"),
        "cosine_similarity": (round(registro["score_coseno"], 6)
                              if registro.get("score_coseno") is not None else None),
        "start": fuente["start"], "end": fuente["end"],
        "duration_seconds": round(fuente["end"] - fuente["start"], 3),
        "from_sentence": fuente.get("from_sentence"), "to_sentence": fuente.get("to_sentence"),
        "transcript_text": fuente.get("transcript_text", ""),
        "quality_flags": registro.get("quality_flags", []),
    }


def crear_playlist(seleccionados):
    """Un registro por intervalo, preservando historias discontinuas y el texto fuente."""
    return [entrada_playlist(s, n, f) for s in seleccionados for n, f in enumerate(s["fuentes"], start=1)]


# ---------------------------------------------------------------------------
# Corpus y buscador
# ---------------------------------------------------------------------------

def _leer_json(ruta: Path) -> Any:
    with ruta.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def _hashes_admitidos(ruta: Path) -> set[str]:
    """SHA-256 del archivo y, si es JSON, también el de su versión con saltos de línea LF.

    Git en Windows puede convertir los JSON a CRLF al descargarlos; eso cambia los bytes
    pero no el contenido. Cualquier otro cambio sigue sin coincidir con ningún hash.
    """
    datos = ruta.read_bytes()
    hashes = {hashlib.sha256(datos).hexdigest()}
    if ruta.suffix.lower() == ".json":
        hashes.add(hashlib.sha256(datos.replace(b"\r\n", b"\n")).hexdigest())
    return hashes


class Corpus:
    """Temas e historias preparados. No necesita FAISS ni el modelo."""

    def __init__(self, temas: list[dict], historias: list[dict]):
        self.temas = temas
        self.historias = historias
        self._por_id = {d["id"]: ("tema", d) for d in temas}
        self._por_id.update({d["id"]: ("historia", d) for d in historias})

    @classmethod
    def cargar(cls, ajustes: Ajustes) -> "Corpus | None":
        try:
            temas = _leer_json(ajustes.datos_ruta / ajustes.temas_archivo)
            historias = _leer_json(ajustes.datos_ruta / ajustes.historias_archivo)
        except (OSError, ValueError) as exc:
            log.warning("No se pudo leer el corpus de temas e historias: %s", exc)
            return None
        if not isinstance(temas, list) or not isinstance(historias, list):
            return None
        return cls(temas, historias)

    def clip(self, clip_id: str) -> dict | None:
        """Reconstruye un clip a partir de su identificador (``<registro>__clipNN``)."""
        registro_id, separador, numero = clip_id.rpartition("__clip")
        if not separador or not numero.isdigit() or registro_id not in self._por_id:
            return None
        tipo, doc = self._por_id[registro_id]
        lista = fuentes(doc)
        n = int(numero)
        if not 1 <= n <= len(lista):
            return None
        registro = {
            "id": doc["id"], "tipo": tipo, "interview_id": doc["interview_id"],
            "speaker": doc["speaker"], "title": doc["title"], "summary": doc.get("summary", ""),
            "quality_flags": doc.get("quality_flags", []),
        }
        return entrada_playlist(registro, n, lista[n - 1])


class Buscador:
    """Recupera y selecciona fragmentos. El modelo se carga en la primera consulta.

    ``codificador`` permite inyectar una función ``texto -> vector`` (pruebas); si
    no se da, se usa BGE-M3 con ``sentence_transformers``.
    """

    def __init__(self, ajustes: Ajustes, corpus: Corpus | None = None,
                 codificador: Callable[[str], Any] | None = None):
        self._ajustes = ajustes
        self._corpus = corpus
        self._codificador = codificador
        self._cerrojo = threading.RLock()
        self._indices: dict[str, Any] | None = None
        self._modelo: Any = None
        self._error_carga: str | None = None
        self._cache: OrderedDict = OrderedDict()

    # --- Disponibilidad ----------------------------------------------------

    def motivo_no_disponible(self) -> str | None:
        """Comprobación ligera (sin cargar nada): None si se puede intentar buscar."""
        a = self._ajustes
        if a.buscador.strip().lower() in ("off", "no", "false", "0", "desactivado"):
            return "el buscador está desactivado (MDF_BUSCADOR=off)"
        if self._error_carga:
            return self._error_carga
        if self._corpus is None:
            return "no se ha podido leer el corpus de temas e historias"
        faltan = [n for n in ("manifest_indice.json", "temas.faiss", "historias.faiss",
                              "temas_mapa.json", "historias_mapa.json")
                  if not (a.indice_ruta / n).is_file()]
        if faltan:
            return f"faltan archivos del índice en {a.indice_ruta.name}/: {', '.join(faltan)}"
        modulos = ["numpy", "faiss"] + ([] if self._codificador else ["sentence_transformers"])
        sin = [m for m in modulos if importlib.util.find_spec(m) is None]
        if sin:
            return ("faltan dependencias del buscador (" + ", ".join(sin)
                    + "); instala con: pip install -e \".[busqueda]\"")
        return None

    @property
    def disponible(self) -> bool:
        return self.motivo_no_disponible() is None

    @property
    def modelo_cargado(self) -> bool:
        return self._codificador is not None or self._modelo is not None

    @property
    def corpus(self) -> Corpus | None:
        return self._corpus

    # --- Carga de índices y modelo ------------------------------------------

    def _cargar_indices(self) -> dict[str, Any]:
        with self._cerrojo:
            if self._indices is not None:
                return self._indices
            try:
                self._indices = self._leer_indices()
            except ErrorBusqueda as exc:
                self._error_carga = str(exc)
                raise
            return self._indices

    def _leer_indices(self) -> dict[str, Any]:
        import faiss

        a = self._ajustes
        carpeta, datos = a.indice_ruta, a.datos_ruta
        manifiesto = _leer_json(carpeta / "manifest_indice.json")
        if (manifiesto.get("model_id") != MODELO_ID or manifiesto.get("dimension") != DIMENSION
                or manifiesto.get("index_type") != "IndexFlatIP"
                or manifiesto.get("vector_normalization") != "L2"):
            raise ErrorBusqueda("El índice no es BGE-M3 / 1024 / IndexFlatIP / L2.")

        # Los archivos no deben haber cambiado desde que se construyó el índice.
        comprobar = [(datos / n, h, f"corpus {n}") for n, h in manifiesto.get("input_sha256", {}).items()]
        comprobar += [(carpeta / n, h, f"índice {n}") for n, h in manifiesto.get("output_sha256", {}).items()
                      if n in ("temas.faiss", "historias.faiss", "temas_mapa.json", "historias_mapa.json")]
        for ruta, esperado, etiqueta in comprobar:
            if not ruta.is_file():
                if etiqueta.startswith("corpus") and ruta.name in ("manifest_corpus.json", "informe_validacion.json"):
                    continue  # solo documentan; no intervienen en la búsqueda
                raise ErrorBusqueda(f"Falta {etiqueta}: {ruta}")
            if esperado not in _hashes_admitidos(ruta):
                raise ErrorBusqueda(
                    f"{etiqueta} ha cambiado desde que se construyó el índice. No se mezclan índices "
                    "con un corpus distinto: hay que volver a generar los embeddings (cuaderno 07).")

        assert self._corpus is not None
        indices = {}
        for nombre, docs, esperado in (
            ("temas", self._corpus.temas, int(manifiesto["topic_count"])),
            ("historias", self._corpus.historias, int(manifiesto["story_count"])),
        ):
            idx = faiss.read_index(str(carpeta / f"{nombre}.faiss"))
            mapa = _leer_json(carpeta / f"{nombre}_mapa.json")
            if len(docs) != esperado or len(mapa) != esperado or idx.ntotal != esperado:
                raise ErrorBusqueda(f"{nombre}: discrepancia de tamaños entre índice, mapa y documentos.")
            if idx.d != DIMENSION or idx.metric_type != faiss.METRIC_INNER_PRODUCT:
                raise ErrorBusqueda(f"{nombre}: índice FAISS incompatible.")
            for posicion, (doc, ref) in enumerate(zip(docs, mapa)):
                if ref.get("faiss_row") != posicion or ref.get("id") != doc.get("id"):
                    raise ErrorBusqueda(f"{nombre}: índice y mapa desalineados en la fila {posicion}.")
            indices[nombre] = (idx, docs)
        indices["revision_modelo"] = manifiesto.get("model_revision")
        return indices

    def _cargar_modelo(self):
        with self._cerrojo:
            if self._modelo is not None:
                return self._modelo
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:  # pragma: no cover - depende del entorno
                raise ErrorBusqueda("Falta sentence-transformers: pip install -e \".[busqueda]\"") from exc
            a = self._ajustes
            log.info("Cargando %s para codificar consultas (la primera vez se descarga)…", a.modelo_consultas)
            try:
                modelo = SentenceTransformer(a.modelo_consultas, device=a.modelo_dispositivo or None)
            except Exception as exc:  # red, disco, memoria…
                raise ErrorBusqueda(f"No se pudo cargar el modelo {a.modelo_consultas}: {exc}") from exc
            modelo.eval()
            if int(modelo.get_sentence_embedding_dimension()) != DIMENSION:
                raise ErrorBusqueda("El modelo cargado no genera vectores de 1024 dimensiones.")
            revision_indice = (self._indices or {}).get("revision_modelo")
            try:
                revision_actual = modelo._first_module().auto_model.config._commit_hash
            except (AttributeError, TypeError):
                revision_actual = None
            if revision_indice and revision_actual and revision_indice != revision_actual:
                raise ErrorBusqueda(
                    f"Revisiones de BGE-M3 distintas: índice={revision_indice}, consulta={revision_actual}.")
            self._modelo = modelo
            return modelo

    def precargar(self) -> None:
        """Carga índices y modelo ahora (por defecto se hace en la primera consulta)."""
        self._cargar_indices()
        if self._codificador is None:
            self._cargar_modelo()

    # --- Consulta ------------------------------------------------------------

    def _vector(self, pregunta: str):
        import faiss
        import numpy as np

        if self._codificador is not None:
            v = self._codificador(pregunta)
        else:
            with self._cerrojo:
                v = self._cargar_modelo().encode([pregunta], convert_to_numpy=True,
                                                 normalize_embeddings=False)
        v = np.ascontiguousarray(np.asarray(v, dtype=np.float32).reshape(1, -1))
        if v.shape != (1, DIMENSION) or not np.isfinite(v).all():
            raise ErrorBusqueda("Vector de consulta no válido.")
        faiss.normalize_L2(v)
        if not np.isclose(np.linalg.norm(v[0]), 1.0, atol=1e-5):
            raise ErrorBusqueda("No se pudo normalizar el vector de consulta.")
        return v

    @staticmethod
    def validar_entrada(consulta: Any, tipo: str, terminos: Any) -> tuple[str, str, list[str]]:
        if not isinstance(consulta, str) or not consulta.strip():
            raise ErrorBusqueda("Escribe una consulta.")
        consulta = consulta.strip()
        if len(consulta) > MAX_CARACTERES_CONSULTA:
            raise ErrorBusqueda(f"La consulta es demasiado larga (máximo {MAX_CARACTERES_CONSULTA} caracteres).")
        if tipo not in TIPOS:
            raise ErrorBusqueda("El tipo debe ser «ambos», «temas» o «historias».")
        terminos = terminos or []
        if isinstance(terminos, str) or any(not isinstance(t, str) or not t.strip() for t in terminos):
            raise ErrorBusqueda("Los términos literales deben ser palabras no vacías.")
        return consulta, tipo, [t.strip() for t in terminos]

    def recuperar_candidatos(self, pregunta: str, k_por_indice: int = K_POR_INDICE,
                             tipo: str = "ambos", terminos: list[str] | None = None) -> dict[str, list[dict]]:
        """Candidatos de cada índice por separado; SIN umbral automático de aceptación."""
        indices = self._cargar_indices()
        terminos = terminos or []
        v = self._vector(pregunta)

        colecciones = []
        if tipo in ("ambos", "temas"):
            colecciones.append(("tema", *indices["temas"]))
        if tipo in ("ambos", "historias"):
            colecciones.append(("historia", *indices["historias"]))

        salidas: dict[str, list[dict]] = {"tema": [], "historia": []}
        for clase, idx, docs in colecciones:
            # Con filtro literal se examina TODO el índice: un término raro no debe
            # quedar fuera solo por no figurar entre los primeros vecinos.
            cuantos = idx.ntotal if terminos else min(k_por_indice, idx.ntotal)
            dist, filas = idx.search(v, cuantos)
            for d, fila in zip(dist[0], filas[0]):
                if int(fila) < 0:
                    continue
                doc = docs[int(fila)]
                if not contiene_terminos(doc, terminos):
                    continue
                salidas[clase].append({
                    "id": doc["id"], "tipo": clase, "interview_id": doc["interview_id"],
                    "speaker": doc["speaker"], "title": doc["title"],
                    "summary": doc.get("summary", ""),
                    "score_coseno": float(d),
                    "rank_en_su_indice": len(salidas[clase]) + 1,
                    "quality_flags": doc.get("quality_flags", []),
                    "fuentes": fuentes(doc),
                    "related_topic_ids": doc.get("related_topic_ids", []) if clase == "historia" else [doc["id"]],
                    "story_ids": doc.get("story_ids", []) if clase == "tema" else [doc["id"]],
                })
                if len(salidas[clase]) >= k_por_indice:
                    break
        return salidas

    def buscar(self, consulta: str, tipo: str = "ambos", terminos: list[str] | None = None,
               k_por_indice: int = K_POR_INDICE) -> dict:
        """Una consulta completa. Devuelve el mismo diccionario que exporta el cuaderno 08."""
        consulta, tipo, terminos = self.validar_entrada(consulta, tipo, terminos)
        clave = (consulta, tipo, tuple(terminos), k_por_indice)
        with self._cerrojo:
            if clave in self._cache:
                self._cache.move_to_end(clave)
                return self._cache[clave]
            candidatos = self.recuperar_candidatos(consulta, k_por_indice, tipo, terminos)
            elegidos, rechazados = seleccionar_fragmentos(candidatos, max_fragmentos=None)
            resultado = {
                "consulta": consulta, "terminos_obligatorios": terminos,
                "parametros": {"k_por_indice": k_por_indice, "max_fragmentos": None, "tipo": tipo,
                               "max_por_entrevistada": MAX_POR_ENTREVISTADA,
                               "max_fraccion_cubierta": MAX_FRACCION_YA_CUBIERTA,
                               "prioridad_en_empate": PRIORIDAD_EN_EMPATE},
                "candidatos": candidatos, "seleccion": elegidos,
                "descartados": rechazados, "playlist": crear_playlist(elegidos),
            }
            self._cache[clave] = resultado
            while len(self._cache) > CONSULTAS_EN_CACHE:
                self._cache.popitem(last=False)
            return resultado
