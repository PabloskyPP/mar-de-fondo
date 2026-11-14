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
Para instalar todas las dependencias necesarias ejecutar en la terminal: 
py --version
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .

## Datos

| Archivo | Para qué |
|---|---|
| `datos/demo/playlist.json` | Búsqueda exportada: 32 cortes (copia del `playlist_20261022T190150790698Z.json`). |
| `datos/demo/video_mapping.json` | Entrevistas y nombre del MP4 de cada una. Sus `video_path` apuntan a Colab y **no se usan como ruta**: solo se toma el nombre del archivo. |
| `datos/demo/temas_preparados.json` | **Capítulos** reales: 70 segmentos de las 6 entrevistas (`id`, `title`, `summary`, `start`/`end`, historias, avisos de calidad, notas). |
| `datos/demo/transcripciones/*.json` | **Transcripción** con marcas temporales, una por entrevista. Hoy solo está la de Pilar Barrientos Conde. |
| `datos/demo/entrevistas.json` | **Opcional.** Título, lugar y temas; y capítulos/transcripción solo si no hay otra fuente. |
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

Para ejecutar programa ejecuta en la terminal:
.\.venv\Scripts\python.exe -m uvicorn app.main:app
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

## Comprobar que capítulos e intervenciones saltan al instante correcto

Con la entrevista de Pilar (`/entrevista/Pilar_Barrientos_Conde_02_2?depurar=1`). El
parámetro `depurar=1` muestra bajo el vídeo una línea con el instante **pedido** y la
posición **real** del vídeo tras saltar.

1. Pulsa el capítulo **«Como era a escola de palillo»** (03:34, `Pilar_Barrientos_Conde_02_2_seg05`).
   La línea de depuración debe decir `pedido 214.38 s (03:34) · posición real 214.38 s ·
   diferencia 0.00 s ✓` (se admite hasta 0,25 s) y el vídeo debe seguir sin pararse.
2. Pulsa una intervención de la transcripción, por ejemplo la de **00:23**
   («Tamén había escuela para as… Tiñamos a escuela de palillo, que era na Falanxe.»).
   Debe decir `pedido 23.28 s`, y **lo que oyes debe ser esa frase**.
3. Ese segundo paso es la comprobación que importa: el salto puede ser exacto y aun así
   la transcripción estar desplazada si el vídeo no es el mismo material que el audio
   transcrito. Si lo que oyes no coincide con el texto, no es un fallo del portal sino de
   los tiempos o del archivo de vídeo.
4. La duración se comprueba sola: si el MP4 dura más de 2 s de diferencia respecto al audio
   transcrito (7:27), aparece un aviso amarillo bajo el vídeo. El MP4 de Pilar debe durar
   unos 07:27.
5. Con el vídeo en marcha, el capítulo y la intervención actuales se resaltan; la
   transcripción se desplaza sola.
6. Pulsa el capítulo y luego, en «Cortes de esta búsqueda», un corte: el capítulo salta y
   sigue sin límite; el corte se detiene en su final.

## Pruebas automáticas

```bash
pytest                               # 59 pruebas: datos reales, plan, rutas, rangos, vídeo ausente
cd tests/js && npm install && npm test   # 17 pruebas del reproductor con un <video> simulado (jsdom)
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

## Capítulos y transcripción reales

**Capítulos** (`temas_preparados.json`). Una lista de segmentos con `id` (el mismo
`record_id` que usa la búsqueda guardada: los 11 cortes de tipo «tema» tienen los mismos
tiempos), `interview_id`, `title`, `summary`, `start`/`end`, `story_ids`,
`quality_flags` y `transcription_notes`. Se muestran con su resumen (en el capítulo activo)
y los tramos de «transcripción casi ininteligible» quedan marcados como poco fiables.
No se usan `from_sentence`/`to_sentence`: no se pueden reproducir desde la transcripción
cargada (otra segmentación en frases), así que todo se apoya en los tiempos, que sí
coinciden con las palabras (comprobado en `tests/test_datos_reales.py`).

**Transcripción** (`datos/demo/transcripciones/`). Admite dos formatos:

1. *Palabra a palabra* (el de `Pilar_Barrientos_Conde_transcripcion_corregida.json`):
   `words` con `text`, `start`, `end`, `type` (`word`/`spacing`/`audio_event`) y, si hay,
   `speaker_id`; más `transcription_id`, `language_code`, `language_probability` y
   `audio_duration_secs`. No trae hablantes ni frases, así que las intervenciones se forman
   aquí: terminan en `.` `?` `!` (no en «...», que ahí marca una duda), tras 2 s de pausa o
   al cambiar de hablante. Los eventos como `[ríe]` se quedan dentro de su frase; los
   que están solos (`[música de fondo]`) son su propia fila. Las pausas de 3 s o más se
   marcan con una línea. Pulsar una intervención salta al `start` real de su primera palabra.
2. *Por turnos* (el de la guía técnica): una lista, o un objeto con `turns`/`turnos`/
   `segments`/`transcripcion`, de `{start, end, speaker, text}`.

En todo lo que lleve tiempos (capítulos, turnos, palabras, cortes) se admiten
`start_time`/`end_time` como alternativa a `start`/`end`.

**Nombre del archivo.** Se busca `<interview_id>.json`, `<interview_id>_transcripcion_corregida.json`
y, si no, un archivo cuyo nombre sea un prefijo del identificador (por eso
`Pilar_Barrientos_Conde_transcripcion_corregida.json` sirve para `Pilar_Barrientos_Conde_02_2`),
siempre que haya uno solo. Si hay varios, no se usa ninguno y se avisa en el registro.
Excepciones: `MDF_TRANSCRIPCIONES_EXTRA='{"MariaJoseRoa": "roa.json"}'`.

**Procedencia.** «Contexto y uso» muestra lo que traen los archivos: archivo e
identificador de la transcripción, idioma detectado y su probabilidad, duración del audio,
archivo de origen y número de capítulos, y las notas de transcripción (p. ej. «Muxía aparece
transcrito ás veces como “Musía”»). Los archivos **no** traen modelo, versión del
procesador ni fecha de generación; el portal lo dice en pantalla en vez de inventarlo.

Si falta la transcripción de una entrevista, su pantalla muestra los capítulos y usa el
texto de los cortes de la búsqueda, rotulado como «no es la entrevista entera».

## Formato opcional de `entrevistas.json`

Fuente de último recurso para título, lugar, temas y, solo si no hay capítulos ni
transcripción reales, para estos. Todo salvo `interview_id` es opcional; tiempos en
segundos. Un archivo ausente o con otro formato se ignora con un aviso en el registro.

```json
{"entrevistas": [{
  "interview_id": "Carmen_Vileda_Rey",
  "speaker": "Carmen Vileda Rey", "title": "…", "lugar": "…", "temas": ["…"], "duration": 839.0,
  "capitulos": [{"title": "…", "start": 0.0, "end": 95.2}],
  "transcripcion": [{"start": 3.1, "end": 9.8, "text": "…", "speaker": "…"}]
}]}
```

Con `lugar` y `temas` aparecen los filtros de Catálogo y las entradas por tema de Inicio.
Sin ellos no se muestra nada inventado.

## Pendiente o fuera de esta fase

Buscador en directo (la caja abre la búsqueda guardada) · filtros de Resultados por
tema y lugar (no hay esos datos en la lista) · Historias · interfaz en gallego ·
subtítulos · créditos y condiciones de uso por material · área privada.
