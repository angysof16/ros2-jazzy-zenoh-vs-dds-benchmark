"""
config.py (COMPARTIDO por el experimento 1 y el experimento 2)

Aqui viven TODOS los parametros del proyecto. Si quieres cambiar una frecuencia,
un perfil de red, un color o un umbral de las comprobaciones de cordura, se cambia
en este archivo y no en los demas scripts. Los demas archivos lo importan con
"import config as C".
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results" # CSV, logs, capturas y env.txt
FIGS = ROOT / "figures" # graficas PNG


# Los tres middlewares (RMW) que se comparan
RMWS = {
    "fastdds": ( "rmw_fastrtps_cpp", "Fast DDS", "#0072B2") , # azul
    "cyclone": ( "rmw_cyclonedds_cpp", "Cyclone DDS", "#E69F00" ), # naranja
    "zenoh": ( "rmw_zenoh_cpp", "rmw_zenoh", "#009E73" ), # verde
}

# ---------------------------------------------------------------------------
# Zenoh. Como se conectan los nodos al router.
#   "client"    los nodos remotos al router son clientes. Es la receta documentada
#               para nodos que estan en otro host (aqui, otro namespace).
#   "peer_mesh" los nodos son peers que escuchan en todas las interfaces, para que
#               puedan conectarse entre si por gossip.
# Por que hace falta. Con la configuracion por defecto (peer que escucha solo en
# localhost) el router de Zenoh NO reenvia mensajes entre dos peers, y dos nodos en
# namespaces distintos no se ven. Eso fue lo que paso en tu primera prueba.
# Decide cual usar con lab/check_zenoh.sh.
# ---------------------------------------------------------------------------
ZENOH_TOPOLOGY = "client"
# Plan B si ZENOH_CONFIG_OVERRIDE no funcionara en tu version. Se llena con rutas a
# archivos de sesion completos. Claves "local", "10.10.0.254" y "10.20.0.2".
ZENOH_SESSION_FILES = {}
ZENOH_ROUTER_FILE = None   # ruta a un archivo de router con shared_memory apagada

# ---------------------------------------------------------------------------
# EXPERIMENTO 1, costo de descubrimiento
# ---------------------------------------------------------------------------
EXP1_N = [5, 10, 20]   # tamanos del anillo. La opcion --with-n40 agrega 40
EXP1_REPS = 3  # repeticiones por combinacion (RMW, N)
PHASE_S = 30  # segundos de cada fase (arranque y reposo)
EXP1_ROUTER_IP = "10.10.0.254"   # IP del namespace del router de Zenoh

# ---------------------------------------------------------------------------
# EXPERIMENTO 2, enlace degradado
# ---------------------------------------------------------------------------
ROBOT_IP = "10.20.0.1"
# el router de Zenoh vive en base, en esta IP
BASE_IP = "10.20.0.2"

# Perfiles de red.
#  delay 50ms 10ms - retardo de 50 ms con variacion (jitter) de mas o menos 10 ms
#  loss 2%  -  pierde el 2 % de los paquetes
#  rate 5mbit   -   limita la velocidad a 5 megabits por segundo
PROFILES = {
    "ideal":   "",     # sin netem, es la linea base
    "celular": "delay 50ms 10ms loss 2% rate 5mbit",
    "malo":    "delay 100ms 30ms loss 5% rate 2mbit",
}
# Perfil de la prueba de validacion V1. Es mas agresivo a proposito, para que
# cualquier camino que se salte el enlace se note.
V1_NETEM = "delay 100ms loss 20%"

# Tamanos de mensaje, en bytes de relleno ("x") que lleva el String.
SIZES = {"1KB": 1024, "128KB": 128 * 1024, "1MB": 1024 * 1024}
QOS = ["reliable", "best_effort"]
EXP2_REPS = 3

# Frecuencia de publicacion por tamano. Se elige para que la carga ofrecida
# quede por debajo del enlace mas lento (2 Mbit/s).
#   1 KB a 20 Hz   = 0.19 Mbit/s   (10 % del enlace de 2 Mbit/s)
#   1 MB a 0.2 Hz  = 1.68 Mbit/s   (84 % del enlace de 2 Mbit/s)
# Con 1 MB incluso 1 Hz (8.4 Mbit/s) ya saturaria los dos enlaces.
HZ = {"1KB": 20.0, "128KB": 0.5, "1MB": 0.2}
# Segundos que dura la publicacion. Con 0.2 Hz y 150 s salen solo 30 mensajes por
# corrida, por eso el p99 de 1 MB se acerca al maximo y hay que declararlo.
DURATION = {"1KB": 60, "128KB": 120, "1MB": 150}
# Segundos que se espera despues de publicar para que la cola del enlace se vacie.
DRAIN = {"1KB": 5, "128KB": 15, "1MB": 20}
# tope de espera a que el suscriptor se empareje (segundos)
MATCH_TIMEOUT = 30
# espera fija extra despues del emparejamiento (segundos)
SETTLE = 2
# profundidad KEEP_LAST, igual en publicador y suscriptor
DEPTH = 10

# Prueba V1. Un mensaje de 1 KB a 10 Hz durante 20 s con 100 ms por sentido.
V1 = dict(size="1KB", qos="reliable", hz=10.0, duration=20)
# Con 100 ms por sentido el p50 debe subir al menos 80 ms respecto al perfil ideal.
V1_MIN_RISE_MS = 80

# Umbrales de las comprobaciones de cordura (los usa analyze.py).
# netem con "delay 50ms 10ms" reparte el retardo de forma uniforme entre 40 y 60 ms.
# Por eso el minimo esperado es 40 ms y no 50 ms (se deja 1 ms de margen).
SANITY = dict(
    ideal_p50_ms={"1KB": 10.0, "128KB": 100.0, "1MB": 100.0},   # en ideal la latencia debe ser baja
    celular_min_ms=39.0, celular_p50_ms=48.0,   # 50 ms con 10 ms de jitter
    malo_min_ms=69.0, malo_p50_ms=95.0,         # 100 ms con 30 ms de jitter
)
