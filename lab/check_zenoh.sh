#!/usr/bin/env bash
# check_zenoh.sh (se usa en los dos experimentos)
#
# Prueba CUATRO formas de conectar nodos de rmw_zenoh a un router que vive en otro namespace, y dice cual funciona.
# Sirve para decidir ZENOH_TOPOLOGY en config/config.py, que afecta al experimento 1 y al experimento 2.
#
# Por que existe. En la primera prueba el talker publicaba pero el listener no recibia
# nada. La explicacion probable es que, con la configuracion por defecto, los nodos
# son "peers" que escuchan solo en localhost y el router no reenvia entre peers.
#
# Uso   sudo lab/check_zenoh.sh   (puede demorar unos 2 minutos)

set +u   # no abortar por variables sin definir, el entorno de ROS define muchas

# sudo borra el entorno, asi que cargamos ROS aqui para tener ros2 y las bibliotecas.
source /opt/ros/jazzy/setup.bash

# Carpeta donde esta este script, para poder llamar a lab_exp2.sh sin importar desde donde se corra.
HERE=$(cd "$(dirname "$0")" && pwd)

# Rutas directas a los ejecutables. Asi lanzamos el programa real y no el lanzador "ros2 run".
RZ=$(ros2 pkg prefix rmw_zenoh_cpp)/lib/rmw_zenoh_cpp/rmw_zenohd   # el router de Zenoh
DN=$(ros2 pkg prefix demo_nodes_cpp)/lib/demo_nodes_cpp             # talker y listener de ejemplo

# Todos los nodos de esta prueba usan Zenoh como RMW.
export RMW_IMPLEMENTATION=rmw_zenoh_cpp
# Se borran variables que podrian cambiar la configuracion por defecto sin que lo notes.
unset ZENOH_CONFIG_OVERRIDE ZENOH_SESSION_CONFIG_URI ZENOH_ROUTER_CONFIG_URI ROS_LOCALHOST_ONLY ZENOH_ROUTER_CHECK_ATTEMPTS

# ZENOH_CONFIG_OVERRIDE cambia campos de la configuracion de Zenoh sin editar archivos.
# Cada campo se escribe como ruta/del/campo=valor y varios campos se separan con punto y coma.
SHM='transport/shared_memory/enabled=false'      # apaga la memoria compartida (viene activa por defecto en tu 0.2.11)
EP='connect/endpoints=["tcp/10.20.0.2:7447"]'    # direccion del router, que vive en el namespace base
ALL='listen/endpoints=["tcp/0.0.0.0:0"]'         # escuchar en todas las interfaces y no solo en localhost

# caso <nombre> <namespace del talker> <override del talker> <override del listener>
# Arranca un router y un listener en base, un talker donde se indique, y revisa si el listener recibe.
caso() {
  local name=$1 tns=$2 tov=$3 lov=$4
  pkill -f 'lib/demo_nodes_cpp'; pkill -f rmw_zenohd; sleep 1      # limpiar restos del caso anterior
  # El router va en base. "&" lo deja corriendo en segundo plano.
  ip netns exec base env ZENOH_CONFIG_OVERRIDE="$SHM" "$RZ" >/tmp/zr.log 2>&1 &
  sleep 3                                                          # el router debe estar arriba antes que los nodos
  # El listener tambien va en base, junto al router.
  ip netns exec base env ZENOH_CONFIG_OVERRIDE="$lov" "$DN/listener" >/tmp/zl.log 2>&1 &
  # El talker va en el namespace que indique el caso (base o robot).
  ip netns exec "$tns" env ZENOH_CONFIG_OVERRIDE="$tov" "$DN/talker" >/tmp/zt.log 2>&1 &
  sleep 10                                                         # tiempo para que se descubran y fluyan mensajes
  echo "=== caso $name"
  echo "conexiones TCP establecidas hacia el puerto 7447 (si no hay ninguna, el override no llego al router)"
  ip netns exec base ss -tn state established | grep 7447 || echo "  ninguna"
  # El listener de demo_nodes_cpp imprime "I heard" cada vez que recibe un mensaje. Ese es el criterio de exito.
  if grep -q "I heard" /tmp/zl.log; then echo "RESULTADO, PASA"; else echo "RESULTADO, FALLA"; fi
  pkill -f 'lib/demo_nodes_cpp'; pkill -f rmw_zenohd; sleep 1
}

"$HERE/lab_exp2.sh" up    # monta los namespaces robot y base
echo "--- el router debe escuchar en 7447 dentro de base"
caso "1 local, todo en base"                          base  "$SHM"                    "$SHM"
caso "2 peer con endpoint (el que fallo)"             robot "$EP;$SHM"                "$SHM"
caso "3 cliente con endpoint (receta del README)"     robot "mode=\"client\";$EP;$SHM" "$SHM"
caso "4 peer escuchando en todas las interfaces"      robot "$EP;$ALL;$SHM"           "$ALL;$SHM"
"$HERE/lab_exp2.sh" down  # desmonta todo
