# Lucky Bet Picks: Telegram → borrador en X

Prototipo configurado para Ciudad de México (`America/Mexico_City`, UTC−6), el canal gratuito https://t.me/luckybetpicksfree y la llamada «Premium: escribe por DM».

**Estado:** lógica probada localmente; no conectado a cuentas ni desplegado. Docker no estaba disponible en el entorno de desarrollo. Se adaptó el flujo a las capturas compartidas: imagen → enlace Playdoit → Free Pick/Free Parlay. Faltan los IDs y el token del bot y una prueba con mensajes reales de la Bot API. No se ha validado la integración real con Telegram, el botón de X ni la API de X.

## Imágenes y mensajes separados

El bot agrupa una foto (o documento JPEG/PNG/WebP), el enlace HTTPS de `playdoit.mx` con `shareCode`, y la etiqueta gratuita del mismo remitente y tema del grupo. Admite un pie de foto que ya incluya todo. Conserva el enlace exacto, incluido el código sensible a mayúsculas, y reutiliza el archivo de Telegram sin OCR ni modificación de la apuesta.

La asociación sigue el orden de las capturas: **primero la imagen, después el enlace y la etiqueta**, en un máximo de cinco minutos (`BUNDLE_WINDOW_SECONDS=300`). Espera 90 segundos después del último mensaje (`BUNDLE_SETTLE_SECONDS=90`). Si hay dos imágenes antes de completar el ticket, varios enlaces, un álbum o una respuesta dirigida a otro mensaje, deja el conjunto pendiente por ambigüedad. No adivina asociaciones. Los mensajes fuera de la ventana o en otro orden requieren revisión/adaptación. La ventana temporal es una heurística: comprobar también la correspondencia al revisar el borrador.

El envío al operador contiene **la imagen adjunta, el texto con enlace de Playdoit y un botón para abrir el texto en X**, en un único mensaje de Telegram. Para documentos de imagen conserva el documento. Utiliza `sendPhoto`/`sendDocument` con el `file_id` del mismo bot; no descarga archivos ni expone URLs que contengan el token. [Referencia Telegram](https://core.telegram.org/bots/api#sendphoto).

**En X, la imagen todavía se adjunta manualmente:** guardar la imagen recibida, abrir el borrador con el botón, adjuntarla y confirmar Publicar. El botón no transfiere archivos. No se implementa publicación automática de imágenes con la API de pago; los tickets de imagen se omiten en modo `x_api` para no publicar sin imagen ni horario verificado.

Los tickets de imagen **no tienen horario verificado automáticamente**. Se envían para revisión manual el mismo día de recepción; el operador debe comprobar fecha, inicio del primer evento y cuota. Esto también aplica al respaldo de las 15:00. No se promete filtrar tickets ya iniciados cuando la hora solo está en la imagen. El aviso aparece en el pie del borrador y no en el texto para X.

`status` muestra `image_bundles`, incluidos conjuntos incompletos/ambiguos. Los fragmentos y referencias de archivo persisten en SQLite; la migración agrega `media_json` al registro de publicaciones sin borrar los datos existentes. Ediciones previas al envío reconstruyen el conjunto; las posteriores no corrigen automáticamente el borrador ya enviado. Telegram no informa todas las eliminaciones de mensajes a bots, por lo que una eliminación no garantiza retirar el ticket. Las imágenes antiguas anteriores a instalar esta versión no se pueden recuperar de registros que no guardaron sus archivos.

## Costo y alcance

El modo predeterminado `DELIVERY=telegram_draft` no llama a X: detecta y selecciona el pick, compone el texto y lo manda al chat privado del operador con un botón para abrir el compositor de X. **El operador confirma la publicación en X**. Si el botón no abre en su cliente, puede copiar el texto. Debe revisar que la cuota siga vigente y el evento no haya empezado al abrir un borrador antiguo.

No requiere una API de IA ni una API de X de pago. Requiere una computadora encendida o alojamiento; no se promete alojamiento gratuito. La imagen incluye un adaptador opcional `x_api`, desactivado por defecto. No se han realizado compras ni creado cuentas.

La documentación oficial de X consultada el 4 de octubre de 2026 indica pago por uso: crear un post cuesta USD 0.015 y crear uno con URL USD 0.200. Por ejemplo, 90 posts con enlace serían USD 18, sin lecturas, impuestos ni alojamiento. Confirmar tarifas en la consola antes de habilitarlo. Los créditos promocionales por primera tarjeta son temporales y dependen de elegibilidad; no equivalen a un plan gratuito permanente.

Fuentes: [precios](https://docs.x.com/x-api/getting-started/pricing), [créditos promocionales](https://docs.x.com/x-api/getting-started/free-credits), [automatización](https://help.x.com/en/rules-and-policies/x-automation). X prohíbe automatizar su web mediante scripts y las respuestas automáticas no solicitadas; las respuestas con IA requieren aprobación previa. No se incluyen automatización de navegador, respuestas masivas, follows automáticos ni simulación de actividad humana.

## Reglas implementadas

- Solo acepta el grupo configurado y los IDs de remitentes autorizados. Para un canal acepta sus publicaciones. Un administrador anónimo de grupo puede requerir adaptar el filtro tras examinar el mensaje real.
- Reconoce `Free Pick`, `Free Pick MLB ⚾️☘️☘️☘️`, `Free Pick☘️☘️☘️` y `Free Parlay`, sin importar mayúsculas. La detección es literal: también puede coincidir en una frase que mencione esos términos; adaptar con mensajes completos antes de producción.
- Los gratuitos válidos se preparan al recibirse. Opcionalmente se usan horarios configurables con `FREE_MODE=slots`.
- A las 15:00, si no observó contenido gratuito ese día, selecciona el primer pick normal válido recibido desde las 00:00 y antes de las 15:00. Máximo un respaldo diario, con 15 minutos de margen para ejecutar la tarea. Un gratuito posterior también se procesa, sujeto al límite diario.
- Si hubo un gratuito ilegible, vencido o luego editado, no supone que «no hubo gratuito» y no lo sustituye automáticamente por contenido de pago.
- El respaldo necesita observación continua desde medianoche. La primera tarde tras instalarlo puede no generar respaldo. Una interrupción de lectura superior a tres minutos invalida esa cobertura: no puede afirmar qué se publicó mientras estuvo desconectado.
- En picks de texto estructurado deja al menos 30 minutos antes del inicio. Omite picks recibidos en días anteriores. El inicio debe ser del evento; para un parlay, del primer evento. En tickets de imagen, el horario se revisa manualmente. No verifica cuotas actuales contra una casa de apuestas.
- Límite inicial configurable: tres entregas diarias, separadas por 30 minutos, incluyendo editoriales. Puede omitir gratuitos si se agota el límite o vence su ventana. `Free Parlay` se considera gratuito y conserva todas las selecciones visibles en su imagen; el parser de texto estructurado cubre una selección.
- SQLite persiste mensajes, edición más reciente, deduplicación y estados. Nunca recorta una selección para hacerla caber en 280 caracteres ponderados.
- Incluye 500 combinaciones de 25 aperturas y 20 cierres; no son 500 análisis originales. La frase se omite si no cabe. No altera mercados, cuotas o equipos ni garantiza resultados.
- Los textos de deportes se pueden programar después de revisión humana. No genera noticias ni estadísticas sin una fuente.

## Preparación de Telegram sin administrador

1. Crear un bot en `@BotFather` con `/newbot` y guardar el token **solo en `.env`**, nunca en el chat ni en Git.
2. En BotFather, usar `/setprivacy`, seleccionar el bot y elegir `Disable`.
3. Pedir al responsable del grupo que quite y vuelva a añadir el bot para aplicar el cambio. No necesita convertirlo en administrador. [Documentación de privacidad](https://core.telegram.org/bots/features#privacy-mode).
4. Desde el chat privado del operador, enviar `/start` al bot. El bot no puede iniciar por sí solo una conversación privada.
5. Pedir al tipster que envíe un nuevo mensaje al grupo. Con el worker detenido, ejecutar `discover` para ver `chat_id` y `sender_id`. Configurar el ID del grupo en `TELEGRAM_CHAT_ID`, el del tipster en `TELEGRAM_SENDER_IDS` y el del chat privado en `TELEGRAM_REVIEW_CHAT_ID`.
6. Un bot con privacidad desactivada puede recibir mensajes de otros participantes; el script los descarta antes de guardarlos si su ID no está autorizado.

La Bot API recibe actualizaciones nuevas; no descarga el historial completo. Telegram conserva actualizaciones pendientes por un máximo de 24 horas. Usar un bot dedicado: no compartir `getUpdates` con otro proceso ni mantener un webhook activo. [Bot API](https://core.telegram.org/bots/api#getupdates).

## Formato de texto estructurado (alternativa a las imágenes)

Ejemplo ficticio, no recomendación deportiva:

```text
Free Pick MLB ⚾️☘️☘️☘️
Evento: Equipo A vs Equipo B
Pick: Más de 7.5 carreras
Cuota: 1.90
Inicio: 2026-10-05T19:00:00-06:00
```

Para un pick normal se omite la primera línea. Los mensajes de texto necesitan estos campos; si faltan, quedan en `pending_format`. Las fotos/documentos de imagen siguen el flujo de agrupación descrito arriba, sin exigir extraer esos campos. Sus fragmentos individuales pueden aparecer en `pending_format`; consultar `image_bundles` para el estado del conjunto. No incluye OCR. Una etiqueta «Free Pick» sin imagen y enlace asociados no es suficiente para enviar un ticket.

## Ejecutar con Docker Compose

Desde esta carpeta, en PowerShell, con Docker y Compose instalados:

```powershell
Copy-Item .env.example .env
```

Editar `.env` localmente. El permiso de republicación ya confirmado aparece como `SOURCE_AUTHORIZED=true`. `DRY_RUN=true` conserva la simulación; `DELIVERY=telegram_draft` evita usar X incluso al salir de simulación.

```powershell
docker compose build
docker compose run --rm bot python -m unittest discover -s tests -v
docker compose run --rm bot python app.py discover
docker compose up -d
docker compose logs -f --tail 100
docker compose exec bot python app.py status
```

Después de validar el formato, cambiar `DRY_RUN=false` y ejecutar `docker compose up -d --force-recreate`. En este modo solo envía borradores al Telegram configurado. Los mensajes consumidos en simulación no se reenvían al cambiar de modo: probar con un mensaje nuevo. El volumen conserva esa decisión para prevenir duplicados.

Para detener: `docker compose stop`. No ejecutar `docker compose down -v` salvo que se quiera borrar expresamente el historial de deduplicación. Mantener una sola réplica del worker. El contenedor no expone puertos y corre sin root. No importar `.env` en una imagen.

Exportar registro:

```powershell
docker compose exec -T bot python app.py export > publicaciones.csv
```

`draft_sent` significa enviado a Telegram, **no publicado en X**. Registrar manualmente el URL y hora efectiva de publicación para medir resultados. `dry_run` significa simulación. `sending` tras un reinicio, o `unknown`, exige comprobar el destino antes de reintentar. Los timeouts y errores de publicación no se reintentan automáticamente porque podría duplicarse una publicación ya aceptada. Un `failed` también requiere corregir y revisar; el prototipo no tiene panel de reintentos. El límite diario cuenta intentos para evitar bucles.

La lectura de Telegram reintenta cada 30 segundos sin imprimir tokens. Consultar logs y `status`; no se incluye monitor externo ni alerta de caída. Los borradores fallidos requieren intervención. Respaldar el volumen con el servicio detenido.

## Contenido deportivo revisado

Guardar una opinión o análisis verificado, sin menciones automáticas, en `editorial.txt`. Copiarlo al contenedor en `/tmp` y programarlo:

```powershell
docker compose cp editorial.txt bot:/tmp/editorial.txt
docker compose exec bot python app.py editorial --file /tmp/editorial.txt --due "2026-10-05T11:00:00-06:00" --expires "2026-10-05T14:00:00-06:00"
```

Cambiar las fechas. Prioriza picks disponibles; las editoriales comparten el límite de entregas. En modo gratuito también se envían como borrador. Para responder a otras cuentas, redactar una respuesta específica y publicarla manualmente. Este prototipo no busca cuentas ni escribe respuestas por IA.

## Horarios y conversión: experimento pendiente de datos

No hay una «mejor hora» demostrada para esta cuenta. Publicar picks rápido es el punto de partida porque la cuota y la utilidad caducan. Para contenido no urgente, probar 11:00, 13:00 y 14:30 de Ciudad de México; son hipótesis, no una recomendación basada en audiencia medida. No retrasar un pick si se acerca el evento.

Durante cuatro semanas alternar las franjas en días comparables y registrar en `medicion.csv`: hora real, deporte, antelación, enlace, impresiones si están disponibles, clics si se miden, DMs sobre premium y ventas atribuibles. Comparar tasas y cantidades; no declarar ganador por un único resultado. El CSV es manual: no hay extracción de métricas de X ni optimizador automático todavía.

Prioridad comercial: ventas de premium y DMs cualificados. Seguidores e impresiones son señales secundarias. Embudo propuesto: post útil → canal gratuito → historial completo y condiciones → DM → premium. Dejar claro en el perfil qué DM usar; no se proporcionó aún el usuario de contacto. Llevar un registro verificable de todos los picks, incluidas pérdidas y anulaciones, sin promesas de beneficios.

Las reglas de [publicidad de apuestas](https://help.x.com/en/business-and-advertising/gambling-content) se aplican a productos publicitarios y monetización; no confundirlas con una autorización general para cualquier promoción orgánica. Las [asociaciones remuneradas](https://help.x.com/en/rules-and-policies/paid-partnerships-policy) tienen restricciones propias sobre apuestas. No se han configurado anuncios ni afiliación. Cualquier revisión legal local depende del modelo comercial concreto y queda fuera de este prototipo.

## API de X opcional

Solo si posteriormente se decide usarla: crear una app con permisos de escritura y tokens de usuario OAuth 1.0a, completar las cuatro variables `X_*`, establecer límites de gasto y cambiar `DELIVERY=x_api`. El adaptador usa `POST https://api.x.com/2/tweets`; no sirve un bearer token de aplicación sin autorización del usuario. [Referencia oficial](https://docs.x.com/x-api/posts/manage-tweets/introduction). La autenticación y publicación reales están pendientes de prueba.
