# Mar de Fondo · portal (primera entrega)

Portal web del archivo audiovisual de entrevistas de memoria oral de aAntena.
Esta fase carga **una búsqueda guardada** (32 cortes de 6 entrevistas) y construye
las vistas **Inicio, Catálogo, Resultados y Entrevista** de la maqueta. El buscador
en directo está pendiente: el portal lo indica en pantalla.

Python + FastAPI + plantillas Jinja2, CSS y JavaScript sin dependencias.

## Instalación

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"              # sin pruebas: pip install -e .
cp .env.example .env                 # y edita MDF_VIDEOS_DIR
```

## Datos

| Archivo | Para qué |
|---|---|
| `datos/demo/playlist.json` | Búsqueda exportada: 32 cortes (copia del `playlist_20261022T190150790698Z.json`). |
| `datos/demo/video_mapping.json` | Entrevistas y nombre del MP4 de cada una. Sus `video_path` apuntan a Colab y **no se usan como ruta**: solo se toma el nombre del archivo. |
| `datos/demo/entrevistas.json` | **Opcional.** Capítulos y transcripción completa. Formato más abajo. |
| `MDF_VIDEOS_DIR` | Carpeta local con los MP4 (`Pilar Barrientos Conde.mp4`, `MariaJoseRoa.mp4`…). No se versionan. |

El portal busca en `MDF_VIDEOS_DIR` el nombre de archivo del mapa (tolerando
mayúsculas y acentos descompuestos de macOS) y, si no está, `<interview_id>.mp4`.
Para una excepción puntual: `MDF_VIDEOS_EXTRA='{"MariaJoseRoa": "otro.mp4"}'`.

## Arranque

```bash
uvicorn app.main:app --reload
# http://127.0.0.1:8000
```

Al arrancar, el registro dice qué vídeos encuentra y cuáles faltan:

```
vídeo OK      MariaJoseRoa          /ruta/MariaJoseRoa.mp4
vídeo FALTA   Carmen_Vileda_Rey     espera «Carmen Vileda Rey.mp4» (no se encuentra en la carpeta de vídeos)
```

Los datos JSON se leen al arrancar (reinicia para recargarlos). Los vídeos se
buscan en cada petición: puedes copiarlos con el portal en marcha.

Rutas útiles: `/api/videos` (qué vídeos hay), `/api/busqueda`, `/api/plan`,
`/api/entrevistas`, `/api/docs`.

## Prueba manual (criterio de «entrega lista»)

1. Abre `/api/videos`: las 6 entrevistas deben salir con `"disponible": true`.
2. Abre `/resultados`. Debe verse el aviso **Demostración** y «32 cortes de 6 entrevistas».
3. En el **Corte 7**, «A volta ao palillo e a sorpresa de Marina Antelo»
   (María José Roa, 17:54–19:18), pulsa **Ver corte desde 17:54**.
4. El vídeo debe arrancar en **17:54** (míralo en la barra de posición). Si el
   navegador bloquea el inicio automático, pulsa reproducir.
5. Deja que llegue a **19:18**: se detiene solo, la etiqueta pasa a «Corte terminado» y
   aparecen **Seguir viendo la entrevista completa** y **Repetir el corte**.
6. Pulsa **Seguir viendo la entrevista completa**: continúa más allá de 19:18 sin parar.
7. Pulsa **‹ Volver a resultados**: vuelves a `/resultados` situado en ese mismo corte.
8. Salto: en la entrevista, arrastra la barra del vídeo a un punto lejano. Debe responder
   de inmediato, sin descargar el archivo entero (en las herramientas de red del
   navegador, las peticiones de `/videos/...` salen como `206 Partial Content`).
9. En `/resultados` pulsa **Reproducir fragmentos**. Se encadenan los tramos, también
   al cambiar de entrevista. Abre «Ver cómo se encadenan» para ver el plan: por ejemplo,
   los cortes 7 y 8 (misma entrevista, solapados) se reproducen como un único tramo
   16:51–19:31, y el corte 1 de Carmen Vileda Rey se continúa después en 02:09 en vez
   de repetir los 01:40–02:09.
10. Vídeo ausente: renombra un MP4, recarga la entrevista. Debe aparecer «Vídeo no
    disponible» con el nombre de archivo esperado y la variable a configurar; capítulos y
    texto siguen visibles. En «Reproducir fragmentos», esos tramos se omiten con aviso.

## Pruebas automáticas

```bash
pytest                               # 40 pruebas: datos, plan, rutas, rangos, vídeo ausente
cd tests/js && npm install && npm test   # 10 pruebas del reproductor con un <video> simulado (jsdom)
```

Las pruebas JS simulan el vídeo: comprueban la lógica (parada en `end`, seguir con la
completa, encadenado, cambio de entrevista), **no** el comportamiento de un navegador real.
Por eso existe la prueba manual de arriba.

## Cómo funciona lo que pedía la fase

**Resultados → Entrevista.** Cada corte enlaza a `/entrevista/<interview_id>?clip=<clip_id>`.
El servidor busca el corte en la búsqueda cargada y fija `start`/`end`; así no se
manipulan tiempos en la URL. También acepta `?t=30&fin=45`. El parámetro `volver` solo
admite `/`, `/resultados` y `/catalogo` (con consulta y ancla); cualquier otro valor
cae en `/resultados`.

**Corte frente a entrevista completa.** El corte se detiene en `end` (comprobación cada
40 ms, error máximo de decenas de milisegundos). Al terminar, el portal lo dice y ofrece
seguir con la completa o repetir. Pulsar *play* en los controles nativos al final del
corte equivale a seguir con la completa; saltar fuera del corte con la barra también
lo deja en completa, avisando.

**Reproducir fragmentos.** Un único `<video>` cambia de fuente (así el navegador permite
reanudar al pasar de una entrevista a otra tras la primera pulsación). El plan lo calcula
`app/reproduccion.py`:

1. Cortes **consecutivos** de la misma entrevista que se solapan (o se tocan, ≤ 0,25 s) se
   unen en un tramo continuo y cronológico: no se salta hacia atrás.
2. A cada tramo se le **resta lo ya reproducido** de esa entrevista, aunque proceda de
   cortes no consecutivos. Los restos de menos de 0,5 s se descartan.

Con la lista real: 32 cortes → 30 tramos, de 30 min 52 s a 27 min 03 s (3 min 49 s evitados).
Una prueba comprueba que ningún instante queda sin cubrir ni se repite.

**Vídeo y saltos.** `/videos/<interview_id>` usa `FileResponse` de Starlette: responde
`Accept-Ranges: bytes` y `206`. El identificador se busca en el catálogo; nunca se
construye una ruta con texto de la URL. Tras cargar el metadato, el reproductor
comprueba que el navegador puede saltar y, si no, muestra un aviso. Consejo: los MP4 con
el índice (`moov`) al principio empiezan antes; se consigue con
`ffmpeg -i entrada.mp4 -c copy -movflags +faststart salida.mp4`.

## Formato opcional de `entrevistas.json`

Todo salvo `interview_id` es opcional; tiempos en segundos. Un archivo ausente o con
otro formato se ignora con un aviso en el registro.

```json
{"entrevistas": [{
  "interview_id": "Carmen_Vileda_Rey",
  "speaker": "Carmen Vileda Rey", "title": "…", "lugar": "…", "temas": ["…"], "duration": 839.0,
  "capitulos": [{"title": "…", "start": 0.0, "end": 95.2}],
  "transcripcion": [{"start": 3.1, "end": 9.8, "text": "…", "speaker": "…"}]
}]}
```

Con `lugar` y `temas` aparecen los filtros de Catálogo y las entradas por tema de Inicio.
Sin ellos no se muestra nada inventado. Sin `capitulos` ni `transcripcion`, la pantalla
Entrevista usa los cortes de la búsqueda y lo indica («no es la entrevista entera»).

## Pendiente o fuera de esta fase

Buscador en directo (la caja abre la búsqueda guardada) · filtros de Resultados por
tema y lugar (no hay esos datos en la lista) · Historias · interfaz en gallego ·
subtítulos · créditos y condiciones de uso por material · área privada.
