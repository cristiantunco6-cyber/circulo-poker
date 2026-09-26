# Revisión de seguridad — Círculo v9

Fecha: 26 de septiembre de 2026. Juego con dinero virtual.

## Alcance y resultado

Revisión del archivo `poker_lan.py`, sus rutas HTTP, protocolo TCP, validación del motor, sesiones, interfaz y configuración Docker. Los tres cambios del informe externo no estaban presentes en esta copia del proyecto; se implementaron y verificaron aquí. Esto es una revisión de código con pruebas de regresión, no una certificación ni un pentest independiente.

El cliente no dispone de una operación para fijar saldos. El motor calcula apuestas, pagos y permisos. Las pruebas comprueban que campos inventados de saldo/anfitrión no otorgan dinero ni permisos, las apuestas inválidas no cambian el estado y repetir una acción con revisión antigua se rechaza. No es una garantía de ausencia de vulnerabilidades.

## Cambios realizados

- Nombres normalizados con NFKC; se eliminan caracteres de control, bidi y privados. Se validan tipo y longitud y se reservan nombres que empiezan por identidades del sistema. La comprobación cubre póker, Blackjack y admisión HTTP. No pretende resolver todos los homógrafos entre alfabetos.
- Importes de acciones validados antes del motor: entero estricto (no booleanos, flotantes, NaN, cadenas u objetos), no negativo, hasta el saldo más la apuesta ya comprometida, y dentro del rango entero exacto de JavaScript. La validación del motor sigue comprobando turno, apuesta mínima y disponibilidad. Se evita una cota fija que impida apostar ganancias legítimas acumuladas.
- CSP de estilos con nonce aleatorio por respuesta, compartido con el bloque de script; `style-src-attr 'none'`. Se trasladaron los estilos HTML y cssText a clases CSS. Las animaciones conservan sus propiedades CSS asignadas por JavaScript autorizado. No se utiliza `unsafe-inline`.
- Registro estructurado de admisiones rechazadas: evento, hora y dirección del socket. No registra nombres, claves, códigos ni cookies. Detrás de Render esa dirección puede ser del proxy: NO se interpreta un X-Forwarded-For arbitrario como identidad fiable del jugador. Los registros se consultan en Render; no se crean archivos de datos en la Mac.

## Protecciones conservadas

Mesas con código y clave, sesiones aleatorias de 32 bytes (256 bits antes de codificar), cookies HttpOnly/SameSite/ Secure bajo HTTPS, comprobación de origen, restricciones de tamaño y frecuencia, validación de turnos y permisos, reparto privado de cartas, rechazo de mensajes HTTP ambiguos y contenedor sin privilegios. HttpOnly impide leer la cookie desde JavaScript, pero por sí solo NO elimina los efectos de un XSS.

La IA opcional no tiene herramientas ni acceso a comandos, archivos, saldos o cartas. Sin clave configurada se utiliza el bot local. No se activó ningún servicio de pago.

## Validación

`python3 poker_lan.py --test`: 35 pruebas. Incluye póker, Blackjack, botes, conservación de fichas, privacidad, separación de mesas, permisos, repeticiones de acciones, importes manipulados, nombres y logs sin secretos.

Comprobación de navegador: formulario, mesa Blackjack, animación automática y ausencia de errores CSP. Se verifica también la versión y cabeceras en la web publicada. Las pruebas de ataque se ejecutan localmente; no se realizan ataques de carga contra Render.

## Límites pendientes

- Los perfiles y mesas se guardan en memoria; un reinicio los elimina. Render gratuito puede suspender el servicio.
- La revisión anti-repetición cubre acciones y reinicios; no sustituye identificadores idempotentes universales para todas las operaciones.
- El límite global de admisiones y de conexiones reduce abuso, pero un atacante puede consumir esa capacidad. No es una defensa completa contra DDoS.
- La clave de mesa compartida no autentica personas reales; compartirla permite entrar. Los permisos del repositorio y de Render también importan y quedan fuera de esta revisión.
- El HTTP de biblioteca estándar está detrás del proxy HTTPS de Render. No abrir los puertos TCP locales al Internet.
- No hay garantías de invulnerabilidad. Para dinero real, cuentas permanentes o gran escala se requiere otra arquitectura y auditoría especializada.

Referencias: https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy/style-src-attr y https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy/style-src
