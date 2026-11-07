"""
common.py (COMPARTIDO por los dos orquestadores)

Funciones de apoyo que usan run_exp1.py y run_exp2.py.
Todo lo que lance procesos o toque la red se corre con sudo, porque "ip netns" y "tc" lo exigen.
"""
import datetime
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Se agrega la carpeta config al camino de busqueda de Python para poder hacer "import config".
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "config"))
import config as C  # noqa: E402

DEVNULL = subprocess.DEVNULL   # atajo para descartar la salida de un comando

# CLK es cuantos "ticks" de CPU tiene un segundo en Linux (normalmente 100).
# /proc/<pid>/stat cuenta el tiempo de CPU en ticks, y se divide entre CLK para pasar a segundos.
CLK = os.sysconf("SC_CLK_TCK")

_ROS = None   # cache del entorno de ROS, se calcula una sola vez

# Override de Zenoh que apaga la memoria compartida (SHM). En tu 0.2.11 viene activa por
# defecto, y con ella los namespaces podrian intercambiar datos por /dev/shm saltandose el enlace.
ZENOH_SHM_OFF = "transport/shared_memory/enabled=false"

# Variables de entorno que podrian cambiar la configuracion por defecto sin que lo notes.
# Se borran del entorno de cada nodo para que todos los middlewares corran "de fabrica".
_UNSET = ["ROS_LOCALHOST_ONLY", "ROS_AUTOMATIC_DISCOVERY_RANGE", "ROS_STATIC_PEERS", "ROS_DISCOVERY_SERVER",
          "FASTRTPS_DEFAULT_PROFILES_FILE", "FASTDDS_DEFAULT_PROFILES_FILE", "RMW_FASTRTPS_USE_QOS_FROM_XML",
          "FASTDDS_BUILTIN_TRANSPORTS", "CYCLONEDDS_URI", "ZENOH_CONFIG_OVERRIDE", "ZENOH_SESSION_CONFIG_URI",
          "ZENOH_ROUTER_CONFIG_URI", "ZENOH_ROUTER_CHECK_ATTEMPTS", "RMW_IMPLEMENTATION"]

# Patrones de procesos que se matan entre corridas. pkill -f los busca en la linea de comando completa.
_KILL = [r"lib/demo_nodes_cpp/(talker|listener)", r"lib/rmw_zenoh_cpp/rmw_zenohd", r"src/probe\.py"]
# Archivos que Fast DDS deja en /dev/shm. Los nombres exactos hay que verificarlos en tu maquina
# con "ls -la /dev/shm" mientras corre un nodo Fast DDS (pendiente F.6 de la guia).
_SHM_GLOBS = ["/dev/shm/fastrtps_*", "/dev/shm/sem.fastrtps_*", "/dev/shm/fast_datasharing_*"]


def require_root():
    """Aborta si no se corre con sudo."""
    if os.geteuid() != 0:
        sys.exit("Ejecuta con sudo (ip netns y tc lo requieren).")


def ros_env():
    """
    Devuelve el entorno que resulta de hacer 'source /opt/ros/jazzy/setup.bash'.
    sudo borra el entorno del usuario, asi que lo reconstruimos aqui. Se calcula una sola vez.
    """
    global _ROS
    if _ROS is None:
        # Abre un bash, carga ROS e imprime todas las variables separadas por \0 (env -0),
        # que es la forma segura de leerlas aunque tengan saltos de linea.
        out = subprocess.run(["bash", "-c", "source /opt/ros/jazzy/setup.bash && env -0"],
                             capture_output=True, check=True).stdout.decode()
        _ROS = dict(kv.split("=", 1) for kv in out.split("\0") if "=" in kv)
    return dict(_ROS)   # se devuelve una copia para que nadie modifique el cache


def exe(pkg, name):
    """
    Ruta directa al ejecutable de un paquete de ROS, por ejemplo demo_nodes_cpp/talker.
    Se lanza el programa real y no 'ros2 run', porque 'ros2 run' crea un proceso intermedio
    y entonces el PID que medimos (CPU y RAM del experimento 1) no seria el del nodo.
    """
    prefix = subprocess.run(["ros2", "pkg", "prefix", pkg], env=ros_env(),
                            capture_output=True, text=True, check=True).stdout.strip()
    return f"{prefix}/lib/{pkg}/{name}"


def zenoh_overrides(router_ip):
    """
    Construye el texto de ZENOH_CONFIG_OVERRIDE para un nodo de Zenoh.
    Los campos van separados por punto y coma. router_ip es None si el nodo comparte
    namespace con el router y entonces usa el localhost por defecto.
    """
    ov = [ZENOH_SHM_OFF]   # siempre se apaga la memoria compartida (desviacion declarada)
    if router_ip:
        # Apunta el nodo al router, que esta en otro namespace.
        ov.append(f'connect/endpoints=["tcp/{router_ip}:7447"]')
        if C.ZENOH_TOPOLOGY == "client":
            ov.append('mode="client"')   # cliente puro, solo habla con el router
    if C.ZENOH_TOPOLOGY == "peer_mesh":
        ov.append('listen/endpoints=["tcp/0.0.0.0:0"]')   # peer que escucha en todas las interfaces
    return ";".join(ov)


def node_env(rmw, router_ip=None):
    """
    Entorno completo de un nodo para el RMW indicado (clave corta de config.RMWS).
    Solo se toca lo estrictamente necesario para que nada se salte el enlace (regla 3).
    """
    env = ros_env()
    for v in _UNSET:
        env.pop(v, None)                    # quita variables que cambiarian los valores por defecto
    env["ROS_DOMAIN_ID"] = "0"              # mismo dominio para todos
    env["RCUTILS_LOGGING_BUFFERED_STREAM"] = "0"   # logs sin buffer, para que "I heard" aparezca al instante
    env["PYTHONUNBUFFERED"] = "1"
    env["RMW_IMPLEMENTATION"] = C.RMWS[rmw][0]     # selecciona el middleware
    if rmw == "fastdds":
        # Fast DDS crea por defecto UDPv4 y memoria compartida, y prefiere la memoria compartida.
        # En namespaces la memoria compartida (/dev/shm) NO esta aislada y saltaria el enlace.
        env["FASTDDS_BUILTIN_TRANSPORTS"] = "UDPv4"
    elif rmw == "zenoh":
        key = router_ip or "local"
        if key in C.ZENOH_SESSION_FILES:
            # Plan B, un archivo de sesion completo en lugar del override.
            env["ZENOH_SESSION_CONFIG_URI"] = C.ZENOH_SESSION_FILES[key]
        else:
            env["ZENOH_CONFIG_OVERRIDE"] = zenoh_overrides(router_ip)
    return env


def router_env():
    """Entorno del router de Zenoh. Tambien se le apaga la memoria compartida."""
    env = ros_env()
    for v in _UNSET:
        env.pop(v, None)
    if C.ZENOH_ROUTER_FILE:
        env["ZENOH_ROUTER_CONFIG_URI"] = C.ZENOH_ROUTER_FILE
    else:
        env["ZENOH_CONFIG_OVERRIDE"] = ZENOH_SHM_OFF
    return env


def spawn(ns, cmd, env, log):
    """
    Lanza 'cmd' dentro del namespace 'ns' y guarda su salida en el archivo 'log'.
    'ip netns exec' reemplaza su propio proceso por el comando, asi que el PID que devuelve
    Popen ES el del comando (necesario para medir CPU y RAM).
    start_new_session crea un grupo de procesos propio, para poder matar al nodo y a todos
    sus hijos de una vez con killpg.
    """
    with open(log, "w") as f:
        return subprocess.Popen(["ip", "netns", "exec", ns] + [str(c) for c in cmd], env=env,
                                stdout=f, stderr=subprocess.STDOUT, start_new_session=True)


def stop(procs, grace=3.0):
    """
    Detiene una lista de procesos. Primero pide amablemente (SIGTERM, senal 15) y, si despues
    de 'grace' segundos siguen vivos, los mata a la fuerza (SIGKILL, senal 9).
    """
    for p in procs:
        try:
            os.killpg(p.pid, 15)   # SIGTERM al grupo completo
        except (ProcessLookupError, PermissionError):
            pass
    limit = time.time() + grace
    for p in procs:
        try:
            p.wait(timeout=max(0.1, limit - time.time()))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, 9)   # SIGKILL, no se puede ignorar
            except ProcessLookupError:
                pass
    for p in procs:   # recoge los procesos terminados para que no queden zombis
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def clear_netem():
    """EXPERIMENTO 2. Quita el netem de los dos extremos del enlace."""
    for ns, dev in (("robot", "vr"), ("base", "vb")):
        subprocess.run(["ip", "netns", "exec", ns, "tc", "qdisc", "del", "dev", dev, "root"], stderr=DEVNULL)


def set_netem(args):
    """EXPERIMENTO 2. Aplica un perfil de netem en ambos extremos llamando a lab_exp2.sh."""
    lab = str(C.ROOT / "lab" / "lab_exp2.sh")
    subprocess.run([lab, "set", args] if args else [lab, "clear"], check=True)


def cleanup(netem=False):
    """
    Regla 6, residuos entre corridas. Mata procesos de ROS y Zenoh, detiene el daemon de ROS,
    borra restos de Fast DDS en /dev/shm y, si se pide, quita el netem.
    Devuelve lo que quede en /dev/shm para poder registrarlo.
    """
    for pat in _KILL:
        subprocess.run(["pkill", "-9", "-f", pat], stderr=DEVNULL)
    try:
        # El daemon de ROS guarda el grafo de una corrida anterior y podria contaminar la siguiente.
        subprocess.run(["ros2", "daemon", "stop"], env=ros_env(), stdout=DEVNULL, stderr=DEVNULL, timeout=30)
    except subprocess.TimeoutExpired:
        pass
    for g in _SHM_GLOBS:
        for f in glob.glob(g):
            try:
                os.remove(f)
            except OSError:
                pass
    if netem:
        clear_netem()
    return os.listdir("/dev/shm")


# --------------------------- contadores ---------------------------
def read_counters_host(ifaces):
    """
    EXPERIMENTO 1. Suma rx_bytes y rx_packets de los veth del lado del host.
    Lo que el host RECIBE por vhK es exactamente lo que el namespace K EMITE, asi que cada
    trama se cuenta una sola vez y el multicast que el bridge replica no se multiplica.
    """
    b = p = 0
    for i in ifaces:
        s = Path(f"/sys/class/net/{i}/statistics")
        b += int((s / "rx_bytes").read_text())
        p += int((s / "rx_packets").read_text())
    return b, p


def link_counters():
    """
    EXPERIMENTO 2. Lee los contadores de la interfaz vr dentro del namespace robot.
    Devuelve (tx_bytes, rx_bytes, tx_paquetes, rx_paquetes). tx es robot->base y rx es base->robot.
    Cuentan lo que sale DESPUES de la perdida de netem, es decir, lo que realmente cruza el enlace.
    """
    cmd = "cd /sys/class/net/vr/statistics && cat tx_bytes rx_bytes tx_packets rx_packets"
    out = subprocess.run(["ip", "netns", "exec", "robot", "sh", "-c", cmd],
                         capture_output=True, text=True, check=True).stdout.split()
    return tuple(int(x) for x in out)


def proc_ticks(pid):
    """
    EXPERIMENTO 1. Tiempo de CPU acumulado de un proceso (utime + stime, en ticks).
    En /proc/<pid>/stat son los campos 14 y 15. Se corta despues del ')' porque el nombre
    del proceso puede tener espacios y desordenaria el conteo de campos.
    """
    try:
        s = Path(f"/proc/{pid}/stat").read_text()
        rest = s[s.rindex(")") + 2:].split()
        return int(rest[11]) + int(rest[12])
    except (OSError, ValueError):
        return 0   # si el proceso ya murio, no suma


def proc_rss_kb(pid):
    """EXPERIMENTO 1. Memoria residente (RSS) de un proceso, en kB."""
    try:
        for line in open(f"/proc/{pid}/status"):
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except OSError:
        pass
    return 0


def fix_owner():
    """
    Como se corre con sudo, los archivos de results/ quedan de root. Aqui se devuelven a tu usuario
    (sudo guarda su id en SUDO_UID) para que analyze.py se pueda correr sin sudo.
    """
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if uid:
        subprocess.run(["chown", "-R", f"{uid}:{gid}", str(C.RESULTS)])


def write_env(extra=()):
    """
    Agrega una seccion nueva a results/env.txt con versiones, kernel, CPU, parametros y
    desviaciones del default. Es el "registro de versiones" del repositorio.
    """
    C.RESULTS.mkdir(parents=True, exist_ok=True)
    # Cada par es (etiqueta, comando de shell cuya salida se guarda).
    items = [
        ("fecha", "date -Is"), ("kernel", "uname -srvm"), ("sistema", "lsb_release -ds"),
        ("cpu", "lscpu | grep -E 'Model name|^CPU\\(s\\)'"), ("memoria", "free -h | head -2"),
        ("governor", "cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
        ("paquetes", "dpkg-query -W -f='${Package} ${Version}\\n' 'ros-jazzy-rmw-*' ros-jazzy-fastrtps "
                     "ros-jazzy-cyclonedds ros-jazzy-zenoh-cpp-vendor ros-jazzy-demo-nodes-cpp"),
        ("tshark", "tshark --version | head -1"), ("iproute2", "ip -V"), ("tc", "tc -V"),
        ("python", "python3 --version"), ("docker", "docker --version"),
        ("iptables FORWARD", "iptables -S FORWARD | head -3"),
        ("zenoh shared_memory por defecto",
         "grep -n -A4 'shared_memory' /opt/ros/jazzy/share/rmw_zenoh_cpp/config/DEFAULT_RMW_ZENOH_SESSION_CONFIG.json5 | head -12"),
    ]
    # Parametros del experimento y las desviaciones de la configuracion por defecto, que hay que declarar.
    params = dict(PROFILES=C.PROFILES, V1_NETEM=C.V1_NETEM, SIZES=C.SIZES, QOS=C.QOS, HZ=C.HZ,
                  DURATION=C.DURATION, DRAIN=C.DRAIN, DEPTH=C.DEPTH, EXP1_N=C.EXP1_N, PHASE_S=C.PHASE_S,
                  desviaciones=["FASTDDS_BUILTIN_TRANSPORTS=UDPv4", ZENOH_SHM_OFF,
                                f"topologia Zenoh={C.ZENOH_TOPOLOGY} con endpoint al router"])
    with open(C.RESULTS / "env.txt", "a") as f:   # "a" agrega al final, no borra sesiones anteriores
        f.write(f"\n===== sesion {datetime.datetime.now().isoformat(timespec='seconds')} =====\n")
        for label, cmd in items:
            out = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True).stdout.strip()
            f.write(f"## {label}\n{out}\n")
        f.write("## parametros\n" + json.dumps(params, indent=1) + "\n")
        for line in extra:
            f.write(line + "\n")
