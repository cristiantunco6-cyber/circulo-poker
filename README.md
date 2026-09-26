# Círculo · Texas Hold’em — Mac, Windows, celulares e Internet

## Mesas privadas con código y clave (v6)

En el navegador elige **Crear mesa**, escribe tu nombre, el nombre de la mesa y una clave de al menos 6 caracteres. Recibirás un código de 8 caracteres y el botón **Copiar invitación**. Comparte el enlace y envía la clave aparte. Los invitados pueden abrirlo o elegir **Entrar con código** e introducir código, nombre y clave.

Cada mesa tiene su propio chat, jugadores, apuestas y anfitrión. Una salida solo termina la partida de esa mesa. El siguiente anfitrión puede iniciar otra. Usa **Salir** antes de cambiar de mesa; un navegador comparte su sesión entre pestañas. El saldo inicial es S/ 10,000 virtuales por jugador. Hay un máximo de 12 mesas simultáneas y 10 jugadores por mesa; las mesas vacías caducan tras 30 minutos. Reiniciar el servidor elimina las mesas y sus códigos. Las instrucciones de abajo sobre entrar sin código corresponden a la versión anterior o al escritorio.

Para jugar desde conexiones diferentes y con la Mac apagada, consulta **PUBLICAR_EN_INTERNET.md**. El modo `--online` está preparado para alojamiento HTTPS y pasa el control a otro jugador cuando sale el anfitrión. Requiere publicar el servidor; ejecutar el archivo solo en tu Mac sigue siendo acceso local.

Todo el juego está en **poker_lan.py**: motor de póker, servidor TCP, cliente, interfaz Tkinter y pruebas. Este documento es opcional para ejecutar el juego.

## Empezar con la mesa circular (recomendado)

1. Cierra el servidor de la versión anterior antes de iniciar este.
2. En la Mac anfitriona ejecuta `python3 poker_lan.py`. Se abre automáticamente la nueva mesa en el navegador. También puedes abrir `http://127.0.0.1:5051` en esa Mac.
3. Entra primero con tu nombre: serás el anfitrión y podrás repartir y reiniciar.
4. Terminal muestra el enlace para los demás: por ejemplo `http://192.168.1.25:5051`. Usa la IP que muestre tu Mac, no la del ejemplo.
5. Todos deben estar en el mismo Wi-Fi. Tus amigos abren ese enlace en Safari o Chrome, desde iPhone, Android, Windows o Mac, escriben su nombre y pulsan **Entrar a la mesa**. No necesitan instalar Python ni copiar el script.
6. Cuando estén todos, pulsa **Repartir mano**. Mantén la Mac despierta, Terminal abierto y las páginas de los jugadores abiertas.

El modo navegador usa el puerto **5051**; **5050** queda para clientes de escritorio. Si el puerto está ocupado, usa `python3 poker_lan.py --web-port 5052` y comparte el enlace con ese puerto. Si macOS pide permitir conexiones entrantes para Python, permite el acceso para la partida. La interfaz anterior sigue disponible con `python3 poker_lan.py --desktop`.

La mesa del navegador incluye una mesa circular verde con borde dorado, crupier visual, reparto animado y fichas que viajan desde cada jugador hacia el bote al apostar o igualar. Las fichas de la animación son decorativas: los importes exactos aparecen en soles virtuales. Se conservan chat, perfiles, avisos de subidas, resultados con la mano ganadora y S/ 10,000.00 iniciales.

La página adapta el diseño a pantallas pequeñas y respeta la preferencia del dispositivo de reducir movimiento. Las pruebas se hicieron en navegador con un tamaño de pantalla móvil; todavía debes comprobar la conexión Wi-Fi real de tus equipos.

Salir con **Salir** termina la partida para todos. Si el navegador se cierra, pierde la red o el celular suspende la página, el servidor lo detecta tras aproximadamente **20 segundos sin actividad** y devuelve las apuestas de la mano interrumpida. Volver a la misma página puede recuperar el asiento mediante la cookie; el anfitrión debe iniciar una nueva partida. Evita bloquear la pantalla o cambiar de aplicación durante la mano.

Las instrucciones siguientes describen principalmente el modo de escritorio opcional.

## 1. Instalar Python

1. Descarga el instalador estable de Python 3 para macOS desde [python.org](https://www.python.org/downloads/macos/) e instálalo en cada Mac. El script requiere Python 3.10 o posterior.
2. Abre Terminal y comprueba:

   ```sh
   python3 --version
   python3 -m tkinter
   ```

   El segundo comando debe abrir una ventana de demostración. Ciérrala después.
3. **No necesitas ejecutar `pip install`**: solo se utiliza la biblioteca estándar. Los instaladores actuales de python.org incluyen Tcl/Tk para macOS; véase [la documentación oficial](https://www.python.org/download/mac/tcltk/). Si aparece `No module named tkinter`, utiliza el Python instalado desde python.org, no una distribución que omita Tkinter. Tkinter no se instala mediante `pip install tkinter`.

## 2. Abrir el juego

Copia `poker_lan.py` en cada ordenador. Por ejemplo, si lo guardaste en Descargas:

```sh
cd ~/Downloads
python3 poker_lan.py --desktop
```

También puedes escribir `python3 ` en Terminal, arrastrar el archivo hasta la ventana, añadir ` --desktop` y pulsar Enter. No necesitas copiar ningún otro archivo ni descargar imágenes.

## 3. Crear la mesa en la Mac anfitriona

1. Conecta todos los ordenadores a la misma red Wi-Fi o Ethernet.
2. Abre el juego y escribe tu nombre.
3. Deja el puerto **5050** o elige otro puerto libre entre 1024 y 65535.
4. Introduce una clave de mesa compartida, si quieres restringir quién entra. La clave puede quedar vacía.
5. Pulsa **Crear mesa**. La Mac inicia el servidor y conecta automáticamente al anfitrión como jugador.
6. Comparte con los demás la **IP para tus amigos**, el puerto y la clave. Si se muestran varias IP, utiliza la correspondiente a la red que comparten. Puedes consultar la IP en los detalles de la conexión de red de macOS.
7. Espera a que todos aparezcan y pulsa **Repartir mano**. Solo el anfitrión puede repartir.

Si macOS solicita aceptar conexiones entrantes para Python, permítelas para esta partida. También puedes revisar **Ajustes del Sistema → Red → Firewall → Opciones** y permitir Python, conservando el firewall activado. [Instrucciones de Apple](https://support.apple.com/en-hk/guide/mac-help/mh34041/mac).

## 4. Conectar los otros jugadores

1. Ejecuta el mismo script en cada ordenador.
2. Escribe un nombre distinto.
3. En **IP anfitrión**, introduce la dirección del anfitrión, por ejemplo `192.168.1.25`.
4. Introduce el mismo puerto y clave de mesa.
5. Pulsa **Conectar**.

`127.0.0.1` solo sirve para conectarte a un servidor que corre en tu propio ordenador. Para probar el juego sin otra Mac, abre dos Terminales, ejecuta el script en ambas, crea la mesa en una y conecta la otra a `127.0.0.1` con un nombre diferente.

Esta versión utiliza **IP local por Wi-Fi/Ethernet**, la alternativa solicitada a Bluetooth. Los celulares y clientes de navegador solo necesitan Safari o Chrome. Python y Tkinter solo son necesarios para los clientes que elijan el modo de escritorio. El anfitrión necesita Python; Tkinter es opcional en el modo navegador.

## 5. Jugar

- **Mesa:** Texas Hold’em sin límite, de 2 a 10 jugadores, baraja de 52 cartas sin comodines. Cada jugador recibe dos cartas privadas.
- **Dinero virtual:** saldo inicial predeterminado S/ 10,000.00 por jugador. El anfitrión puede cambiarlo en «Saldo inicial por jugador» antes de crear la mesa (mínimo S/ 0.20). Ciega pequeña S/ 0.10 y grande S/ 0.20, fijas. Se mantienen los saldos entre manos. No hay depósitos, retiros, pagos ni dinero real.
- **Rondas:** preflop, flop de tres cartas, turn y river de una carta cada uno. Se quema una carta antes de cada calle. El ganador tiene la mejor combinación de cinco cartas entre sus dos cartas y las cinco comunitarias; puede usar cero, una o dos cartas privadas.
- **Retirarse:** abandonar la mano y las fichas ya comprometidas.
- **Pasar / Igualar:** pasar si no debes fichas o pagar lo que falta, limitado a tus fichas disponibles.
- **Apostar / subir a:** el campo numérico indica tu **TOTAL de esta ronda**, no la cantidad adicional. Si llevas S/ 0.20 y escribes 0.60, añades S/ 0.40. Introduce hasta dos decimales; se admite punto o coma decimal (0.10 o 0,10). No introduzcas separadores de miles. La apuesta mínima de apertura es S/ 0.20, salvo un all-in menor; la ciega pequeña es S/ 0.10. La interfaz muestra el mínimo permitido y tu máximo.
- **All-in:** apostar todas tus fichas cuando las reglas permitan hacerlo. Una subida incompleta no reabre por sí sola las apuestas para quien ya actuó. Las subidas incompletas acumuladas sí reabren si alcanzan una subida completa.
- **Botes:** se calculan botes principal y secundarios según las contribuciones. Las apuestas no igualadas se devuelven. Los empates reparten el bote; las fichas indivisibles se asignan en orden desde la izquierda del botón.
- **Dos jugadores:** el botón pone la ciega pequeña y actúa primero preflop; la ciega grande actúa primero después del flop.
- **Posiciones:** D = botón, SB = ciega pequeña, BB = ciega grande. Se rota el botón entre los participantes de cada mano.
- **Privacidad:** cada cliente recibe solo sus cartas, las comunitarias y los datos públicos. En el showdown se muestran las cartas de todos los jugadores que siguen en la mano. Las cartas retiradas permanecen ocultas para los demás. El ordenador servidor administra toda la baraja.
- **Tiempo:** cada decisión tiene 60 segundos. Si vence, el jugador pasa si es gratis o se retira si debe igualar. No hay pausa de mesa.
- **Siguiente mano:** el anfitrión pulsa **Repartir mano**. Quien entra durante una mano espera a la siguiente. Los desconectados y quienes no tienen fichas no reciben cartas nuevas.

Cuando solo queda un jugador con fichas, se muestra **GANADOR DE LA PARTIDA**, su saldo y cuánto apostó en total. El anfitrión pulsa **Nueva partida**, confirma el reinicio y después **Repartir mano**. Todos vuelven al saldo inicial configurado sin desconectarse, manteniendo nombres y asientos. El botón también permite empezar desde cero entre manos; no se puede reiniciar una mano en curso.

Al finalizar cada mano se abre **Resultado de la mano**: muestra cuánto apostó cada jugador (incluidas las ciegas), cuánto ganó en botes, cuánto se devolvió sin igualar, su ganancia neta y saldo. **Apostado partida** suma todas las fichas puestas durante la partida, incluyendo las que después se devuelven. La ganancia neta es botes ganados + devoluciones − fichas apostadas. Los ganadores de cada bote aparecen destacados; ganar un bote no siempre implica acabar con ganancia neta positiva.

La interfaz de escritorio incluye tema oscuro, letras grandes, turnos resaltados, barra de tiempo, cartas con sombra, bote central y pestañas para mesa, resultados, historial, chat global y perfiles. El bote de la última mano y las apuestas permanecen visibles al finalizar. **Repartir mano** continúa con los saldos actuales; **Nueva partida** reinicia saldos, resultados e historial.

## 6. Desconexiones y límites de sesión

- Si pierdes la red, deja abierta la ventana y pulsa **Reconectar** al recuperarla. Se utiliza un identificador privado conservado en memoria para recuperar el mismo asiento y saldo.
- Si cierras la aplicación cliente, ese identificador se pierde; no se puede recuperar el asiento simplemente escribiendo el mismo nombre. Los asientos permanecen reservados durante toda la sesión, con un máximo de 10.
- Si un jugador se desconecta durante una partida, la partida termina inmediatamente para todos. Las apuestas de la mano interrumpida se devuelven, aparece un aviso grande y el anfitrión debe pulsar **Nueva partida** para continuar.
- El servidor no transfiere el rol de anfitrión. Si el proceso servidor se cierra, termina toda la mesa.
- La sesión se guarda únicamente en memoria. Se pierden saldos e historial al cerrar el servidor.
- Está diseñado para una LAN de confianza: la conexión TCP y la clave compartida no tienen cifrado TLS. No lo publiques mediante redirección de puertos hacia Internet.

## 7. Si no se conectan

- Comprueba IP, puerto y clave; los nombres deben ser distintos.
- No uses `127.0.0.1` desde otro ordenador.
- Mantén abierta la aplicación del anfitrión y su Mac despierta durante la partida.
- Revisa que el firewall permita conexiones a Python. No es necesario desactivar el firewall.
- Algunas redes de invitados impiden que los dispositivos se comuniquen entre sí; utiliza una red privada compartida sin aislamiento de clientes.
- Si el puerto está ocupado, el anfitrión puede escoger otro y comunicarlo a todos.
- Si el proceso servidor se cerró y volvió a abrir, reinicia los clientes. Si el anfitrión solo pulsó Nueva partida, los clientes siguen conectados automáticamente.

## 8. Pruebas incluidas

```sh
python3 poker_lan.py --test
```

Las pruebas no necesitan abrir una ventana. Verifican clasificación y desempates, heads-up, opción de la ciega grande, apuestas inválidas, cartas privadas, all-ins y reapertura, botes secundarios, devolución de excedentes, fichas impares, tiempo agotado, partidas aleatorias con conservación de fichas, importes exactos en céntimos, anuncios, chat, perfiles, desconexión que termina la partida y conexiones TCP. Las pruebas de red usan un puerto efímero local.

La comprobación TCP automatizada es entre hilos del mismo proceso mediante loopback; no sustituye la comprobación de conectividad Wi-Fi entre vuestros equipos.

## 9. Servidor dedicado opcional

Para iniciar el servidor sin ventana:

```sh
python3 poker_lan.py --server --port 5050 --pin amigos
```

Abre después `http://127.0.0.1:5051` en el navegador de la Mac. El primer jugador que entra será el anfitrión y controlará el reparto. Los demás abren el enlace con la IP local que imprime Terminal. Introduce la clave `amigos` al entrar. `Ctrl+C` detiene el servidor. También puedes usar `--pin amigos` al iniciar la versión habitual del navegador; en modo `--desktop`, la clave se escribe en la ventana.

## Referencias de reglas

[Texas Hold’em en PokerStars](https://www.pokerstars.com/poker/games/texas-holdem/), [subidas y botes secundarios](https://www.pokerstars.com/help/articles/poker-rules-master/) y [apuesta all-in menor a la mínima, explicación de Poker TDA](https://www.pokertda.com/forum/index.php?topic=1040.0).


## Avisos grandes, chat y perfiles

Cuando alguien se retira, todos los clientes conectados ven un aviso central grande con su nombre, lo apostado y su saldo. Cuando alguien apuesta o sube, todos ven otro aviso con el aumento y el total de la ronda. Al terminar una mano aparece un aviso de ganador con la combinación ganadora, sus cinco cartas, botes ganados, importe apostado y ganancia neta, durante unos 9 segundos. Si hay varios ganadores de botes, aparecen todos; el detalle se puede desplazar. Al quedar un único jugador con saldo se anuncia al ganador de la partida.

Puedes cerrar el aviso con **Continuar en la mesa**. Los turnos siguen contando durante los avisos. El resultado permanece en su pestaña y en el historial. Los avisos llegan desde el servidor, no se repiten por recibir el mismo estado y se retiran si empieza otra mano o partida. Un cliente que entra tarde no reproduce todas las retiradas anteriores.

La pestaña **Chat global** permite mensajes de hasta 300 caracteres y conserva los últimos 100. **Perfiles** muestra saldo, total ganado, total perdido, balance neto, manos jugadas, manos ganadas y total apostado; haz doble clic en un jugador de la mesa para abrirlo. La mesa tiene bordes dorados, cartas con sombra, bote central grande y formato monetario. Internamente todo se calcula con céntimos enteros.

El anfitrión debe ejecutar este archivo v5. Los jugadores del navegador siempre reciben la interfaz desde la Mac; no instalan un archivo aparte. Los clientes del escritorio deben usar una versión compatible. El reinicio conserva las conexiones y el saldo inicial elegido.

## Abrir en Windows

Lo más fácil es abrir en el navegador de Windows el enlace que imprime la Mac (puerto 5051). No necesitas instalar nada. Para usar la interfaz de escritorio opcional, instala Python desde python.org, guarda el archivo en Descargas y ejecuta en CMD:

```bat
cd %USERPROFILE%\Downloads
py poker_lan.py --desktop
```

Para unirse: mismo Wi-Fi, IP de la Mac anfitriona, mismo puerto y clave, nombre diferente y **Conectar**. El campo de saldo inicial lo determina el anfitrión, no cada cliente.

## Blackjack (navegador, versión 7)

En **Crear mesa**, elige **Blackjack 21**, indica un nombre de mesa y clave de al menos seis caracteres. Comparte la invitación y la clave por separado. La misma web funciona en Mac, Windows y celular. Las mesas existentes de póker siguen jugando Texas Hold’em.

Hasta seis participantes por mesa de Blackjack, cada uno con S/ 10,000 virtuales. El anfitrión abre las apuestas. Cada jugador apuesta en su turno, desde S/ 0.10; usa céntimos pares para que el pago 3:2 sea exacto. Luego elige **Pedir carta**, **Plantarse** o **Doblar ×2**. Se usan seis barajas nuevas barajadas por el servidor en cada mano. El crupier pide hasta 16 y se planta en todo 17, incluido suave. Blackjack natural paga beneficio 3:2, una victoria normal 1:1 y el empate devuelve la apuesta. No se incluyen dividir, seguro ni rendición; las reglas también aparecen en la mesa.

Entre manos, cualquier participante puede pulsar **Ser crupier** si el puesto está libre. Ese jugador dirige la banca virtual, puede abrir apuestas y pulsa **Avanzar crupier** durante su turno. No apuesta ni financia la banca con su saldo personal, no elige las cartas y tampoco ve la carta oculta antes del turno del crupier. El crupier automático muestra giro de baraja, dribble y cascada de cartas entre manos. Los trucos se desactivan y se cancelan cuando un humano ocupa el puesto. Son animaciones ornamentales; el reparto real se baraja con aleatoriedad del sistema. **Dejar de ser crupier** vuelve a activar la banca automática. Si el crupier humano agota su tiempo, el servidor avanza por él. Si un jugador deja de apostar a tiempo, la mano se cancela y se devuelven las apuestas pendientes, sin apostar automáticamente en su nombre.

Al terminar, todos ven cartas, resultado y balance; Resultados y Perfiles conservan el detalle durante la sesión. Nueva partida restablece saldos. Si alguien se desconecta, finaliza esa partida y se devuelven apuestas pendientes; otras mesas siguen funcionando. Render gratuito pierde las mesas y perfiles al reiniciarse.

## El Causa: chat local y conexión de IA opcional

Escribe **@crupier hola**, **@crupier reglas** o **@crupier chiste**. Por defecto es un bot de respuestas preparadas, gratis y sin IA conectada; la interfaz lo identifica explícitamente.

La conexión opcional usa la [Responses API de OpenAI](https://developers.openai.com/api/docs/guides/text). Para activarla, el propietario del servidor debe configurar en Render → Environment:

- `OPENAI_API_KEY`: clave privada de un proyecto de API propio. Nunca pegarla en el chat del juego ni subirla a GitHub.
- `POKER_AI_MODEL`: identificador de un modelo con acceso a Responses API en ese proyecto.

No se ha contratado ni activado un servicio de pago. Una suscripción a ChatGPT no configura esta conexión. La IA puede tener coste según el proveedor/modelo; configura los límites de gasto en tu cuenta antes de activarla. Se envía exclusivamente el texto dirigido a @crupier, sin historial de la mesa, nombres, cartas, claves ni sesiones. Se solicitan respuestas breves con `store: false`; consulta la política del proveedor para la retención aplicable. El juego limita a una consulta cada ocho segundos y treinta por hora por mesa; no sustituye un límite de gasto de cuenta. El hilo del juego no espera a la IA; si falla, responde el bot local. La IA no tiene herramientas ni acceso a modificar saldos o resultados.

Validación: `python3 poker_lan.py --test` ejecuta 35 pruebas, incluidas pagos, ases, dobles, banca humana por red, privacidad, desconexiones, aislamiento de mesas y regresión de póker. La conexión a IA requiere credenciales y no ha sido probada contra un servicio real.


## Actualización de efectos y protección (versión 8)

Las florituras están inspiradas en los movimientos explicados en [Top 3 Trucos de CARDISTRY para Principiantes](https://www.youtube.com/watch?v=EK5Hz9me6kM): giro del paquete, dribble y cascada. Son ilustraciones originales animadas, no clips ni archivos descargados del video. Se sincronizan con los eventos de la mesa; respetan la preferencia de movimiento reducido y se paran al ocultar la página. Ningún truco cambia el orden de las cartas del motor.

Para jugar online entra solamente en https://circulo-poker.onrender.com/; tu Mac es un cliente del sitio, no el servidor de los demás jugadores. No necesitas abrir puertos, compartir pantalla, activar acceso remoto ni ejecutar Python en tu Mac para que tus amigos jueguen.

El Dockerfile actualizado ejecuta Python 3.12 con usuario sin privilegios y `--server --online`. Render publica el puerto HTTP indicado por PORT detrás de HTTPS; los sockets del motor solo escuchan en 127.0.0.1 del contenedor. La entrada antigua sin sala `/api/join` está deshabilitada en modo online; se usa código y clave por mesa.

Protecciones adicionales: scripts autorizados mediante nonce aleatorio por respuesta, política que bloquea recursos ajenos y marcos, cookies HttpOnly/SameSite/Secure bajo HTTPS, HSTS, bloqueo de cámara/micrófono/ubicación/USB/Bluetooth, límite de 32 solicitudes HTTP concurrentes, límite global de 60 intentos de entrada por minuto además del límite de claves erróneas por mesa. Los textos de usuarios y de IA se muestran como texto, nunca se ejecutan como código. No hay endpoints de archivos, consola ni comandos del sistema. La IA no tiene herramientas y solo recibe el mensaje escrito a @crupier, no el estado ni credenciales de la mesa. Su límite agregado es de 120 consultas por hora por proceso, además de los límites por mesa. Los límites en memoria se reinician con el servicio y no sustituyen los controles de gasto del proveedor.

Esto reduce superficies de ataque; no constituye una garantía de invulnerabilidad ni una auditoría externa completa. El servidor HTTP de biblioteca estándar sigue siendo una implementación pequeña, alojada detrás del proxy de Render. Las mesas permanecen en memoria y pueden perderse al reiniciar.

### Activar la IA cuando tengas una clave

1. Crea una clave privada de API en tu cuenta de OpenAI y habilita la facturación/saldo necesarios. No compartas la clave por chat ni GitHub.
2. En Render, abre el servicio circulo-poker → Environment → Add Environment Variable.
3. Añade `OPENAI_API_KEY` con tu clave, y `POKER_AI_MODEL` con un modelo habilitado para Responses API (por ejemplo `gpt-4o-mini` si tu cuenta tiene acceso).
4. Guarda y despliega. El reinicio cerrará las mesas actuales.
5. Entra en una mesa nueva y escribe `@crupier hola`. Una respuesta etiquetada `El Causa · IA` confirma que respondió el proveedor. Si aparece `bot local`, revisa la configuración y saldo; no pegues claves en el juego.

La IA real sigue pendiente: el propietario confirmó que todavía no tiene clave. No se crearon cuentas de pago ni se configuraron credenciales. El transporte se probó con una respuesta simulada, no con una llamada facturada.


## Endurecimiento v9

Nombres normalizados y reservados, importes validados contra el saldo antes del motor, estilos con nonce sin unsafe-inline y registros de entradas rechazadas sin secretos. Se conservan las animaciones y el diseño. Consulta `AUDITORIA_SEGURIDAD.md` para alcance, pruebas y límites. La IA real sigue pendiente de la clave privada de API.
