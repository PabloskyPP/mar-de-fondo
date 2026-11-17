from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import RAIZ, Ajustes
from app.main import crear_app

DEMO = RAIZ / "datos" / "demo"
TAMANO_VIDEO = 300_000


def _contenido(i: int) -> bytes:
    """Bytes reconocibles por posición, para comprobar los rangos."""
    return bytes((j * 7 + i) % 251 for j in range(TAMANO_VIDEO))


@pytest.fixture
def mapa() -> dict:
    return json.loads((DEMO / "video_mapping.json").read_text(encoding="utf-8"))


@pytest.fixture
def carpeta_videos(tmp_path: Path, mapa: dict) -> Path:
    """Un «MP4» por entrevista, con el nombre de archivo del mapa de vídeos."""
    carpeta = tmp_path / "videos"
    carpeta.mkdir()
    for i, datos in enumerate(mapa["interviews"].values()):
        nombre = datos["video_path"].rsplit("/", 1)[-1]
        (carpeta / nombre).write_bytes(_contenido(i))
    return carpeta


@pytest.fixture
def datos_copia(tmp_path: Path) -> Path:
    destino = tmp_path / "datos"
    shutil.copytree(DEMO, destino)
    return destino


def hacer_cliente(buscador=None, **kw) -> TestClient:
    """Cliente de pruebas. El buscador real queda desactivado (no debe descargar el modelo);
    se puede inyectar uno con un codificador simulado."""
    kw.setdefault("buscador", "off")
    ajustes = Ajustes(_env_file=None, **kw)
    return TestClient(crear_app(ajustes, buscador=buscador))


@pytest.fixture
def cliente(carpeta_videos: Path):
    with hacer_cliente(videos_dir=carpeta_videos) as c:
        yield c


@pytest.fixture
def cliente_sin_videos(tmp_path: Path):
    vacia = tmp_path / "vacia"
    vacia.mkdir()
    with hacer_cliente(videos_dir=vacia) as c:
        yield c
