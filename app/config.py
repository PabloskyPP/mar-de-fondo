"""Configuración del portal.

Todo se puede definir con variables de entorno con prefijo ``MDF_`` o en un
archivo ``.env`` en la raíz del repositorio (ver ``.env.example``).

Las rutas relativas se resuelven respecto a la raíz del repositorio, no
respecto al directorio desde el que se lanza ``uvicorn``.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

RAIZ = Path(__file__).resolve().parent.parent

# Nombre de exportación del cuaderno: playlist_20261022T190150790698Z.json
_PATRON_FECHA_ARCHIVO = re.compile(r"(\d{8})T(\d{6})")


class Ajustes(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MDF_",
        env_file=RAIZ / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Datos versionables de la demo.
    datos_dir: Path = Path("datos/demo")
    playlist_archivo: str = "playlist.json"
    mapa_videos_archivo: str = "video_mapping.json"
    # Capítulos (segmentos temáticos) de todas las entrevistas.
    temas_archivo: str = "temas_preparados.json"
    # Carpeta con una transcripción con marcas temporales por entrevista. Por
    # defecto, «transcripciones» dentro de la carpeta de datos.
    transcripciones_dir: Path | None = None
    # Excepciones: {"interview_id": "archivo.json"} (relativo a la carpeta de
    # transcripciones, o ruta absoluta). En .env va como JSON en una línea.
    transcripciones_extra: dict[str, str] = Field(default_factory=dict)
    # Opcional: metadatos (título, lugar, temas) y, si no hay otra fuente,
    # capítulos y transcripción en el formato propio del portal.
    entrevistas_archivo: str = "entrevistas.json"

    # Carpeta local con los MP4. Sin ella el portal arranca igualmente, pero
    # cada entrevista mostrará «vídeo no disponible».
    videos_dir: Path | None = None

    # Excepciones por entrevista: {"interview_id": "otro nombre.mp4"} o ruta
    # absoluta. En .env va como JSON en una línea.
    videos_extra: dict[str, str] = Field(default_factory=dict)

    # La lista exportada no incluye el texto de la consulta ni la fecha.
    # Si los conoces, indícalos aquí para mostrarlos en pantalla.
    demo_consulta: str = ""
    demo_fecha: str = ""

    def _absoluta(self, ruta: Path) -> Path:
        ruta = ruta.expanduser()
        return ruta if ruta.is_absolute() else RAIZ / ruta

    @property
    def datos_ruta(self) -> Path:
        return self._absoluta(self.datos_dir)

    @property
    def transcripciones_ruta(self) -> Path:
        if self.transcripciones_dir is not None:
            return self._absoluta(self.transcripciones_dir)
        return self.datos_ruta / "transcripciones"

    @property
    def videos_ruta(self) -> Path | None:
        return self._absoluta(self.videos_dir) if self.videos_dir else None


@lru_cache
def obtener_ajustes() -> Ajustes:
    return Ajustes()


def fecha_desde_nombre(nombre_archivo: str) -> str:
    """Intenta leer la fecha de exportación del nombre original del archivo."""
    m = _PATRON_FECHA_ARCHIVO.search(nombre_archivo)
    if not m:
        return ""
    try:
        dt = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return ""
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
