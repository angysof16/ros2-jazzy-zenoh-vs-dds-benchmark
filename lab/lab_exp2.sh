#!/usr/bin/env bash
# lab_exp2.sh (EXPERIMENTO 2, enlace degradado)
#
# Construye el laboratorio del experimento 2. Dos namespaces unidos por UN solo cable
# virtual, sin bridge, para que nada pueda saltarse el enlace.
#
#   robot (10.20.0.1, interfaz vr) ═══ enlace con netem en ambos extremos ═══ (interfaz vb, 10.20.0.2) base
#   publicador, y nodos de Zenoh como clientes                                   suscriptor y router de Zenoh
#
# netem (Network Emulator) es un modulo del kernel que agrega retardo, perdida y tope
# de velocidad a lo que SALE por una interfaz. Por eso se aplica en los DOS extremos.
#
# Uso
#   sudo lab/lab_exp2.sh up
#   sudo lab/lab_exp2.sh set "delay 50ms 10ms loss 2% rate 5mbit"   aplica un perfil en ambos extremos
#   sudo lab/lab_exp2.sh clear                                      quita el netem
#   sudo lab/lab_exp2.sh show      muestra todo, util para la captura de la diapositiva 7
#   sudo lab/lab_exp2.sh down      desmonta todo

# down borra los dos namespaces. Es seguro correrlo aunque no existan.
down() {
  for ns in robot base; do
    ip netns pids "$ns" 2>/dev/null | xargs -r kill -9   # primero se matan sus procesos
    ip netns del "$ns" 2>/dev/null                        # luego se borra el namespace
  done
  return 0
}

# up monta los namespaces, el cable y las direcciones IP.
up() {
  down
  ip netns add robot
  ip netns add base
  # Un par veth es un cable con dos extremos. Lo que entra por vr sale por vb y al reves.
  ip link add vr type veth peer name vb
  ip link set vr netns robot     # el extremo vr se mueve al namespace robot
  ip link set vb netns base      # el extremo vb se mueve al namespace base
  ip -n robot addr add 10.20.0.1/24 dev vr
  ip -n base  addr add 10.20.0.2/24 dev vb
  for pair in "robot vr" "base vb"; do
    read -r ns dev <<< "$pair"          # separa "robot vr" en ns=robot y dev=vr
    ip -n "$ns" link set "$dev" up      # enciende la interfaz
    ip -n "$ns" link set lo up          # enciende el loopback (localhost) del namespace
    # TSO, GSO y GRO juntan varios paquetes en uno de hasta 64 KB antes de enviarlos.
    # Con eso netem vería "paquetes" gigantes y la perdida y la velocidad saldrian
    # distorsionadas. Se apagan para que netem trabaje paquete por paquete.
    if command -v ethtool >/dev/null; then
      ip netns exec "$ns" ethtool -K "$dev" tso off gso off gro off >/dev/null 2>&1
    else
      echo "aviso, falta ethtool (sudo apt install ethtool), los resultados de netem pueden distorsionarse"
    fi
  done
}

# clear_netem quita el netem de los dos extremos.
clear_netem() {
  ip netns exec robot tc qdisc del dev vr root 2>/dev/null
  ip netns exec base  tc qdisc del dev vb root 2>/dev/null
  return 0
}

# set_netem aplica el mismo perfil en AMBOS extremos. Recibe el texto del perfil, por
# ejemplo "delay 50ms 10ms loss 2% rate 5mbit". Sin argumento quita el netem.
set_netem() {
  if [ -z "$1" ]; then clear_netem; return 0; fi
  # Como netem actua sobre lo que SALE de cada interfaz
  #   los datos robot->base ven el netem de vr (extremo del robot),
  #   los acuses y parte del descubrimiento base->robot ven el netem de vb.
  # Por eso un perfil de "100 ms" da 100 ms por sentido y 200 ms de ida y vuelta,
  # como viste en tu comando 2.
  # "replace" crea la regla si no existe o la cambia si ya existe. $1 va sin comillas
  # a proposito, para que el texto se separe en palabras (delay, 50ms, ...).
  ip netns exec robot tc qdisc replace dev vr root netem $1
  ip netns exec base  tc qdisc replace dev vb root netem $1
}

# show imprime el estado del laboratorio.
show() {
  ip netns list
  ip -n robot -br addr
  ip -n base -br addr
  ip netns exec robot tc qdisc show dev vr   # muestra el perfil activo del lado robot
  ip netns exec base  tc qdisc show dev vb   # y del lado base
}

# Despacha segun el primer argumento.
case "$1" in
  up) up ;;
  set) set_netem "$2" ;;
  clear) clear_netem ;;
  show) show ;;
  down) down ;;
  *) echo "uso, $0 up | set \"<args netem>\" | clear | show | down"; exit 1 ;;
esac
