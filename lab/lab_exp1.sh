#!/usr/bin/env bash
# lab_exp1.sh (EXPERIMENTO 1, costo de descubrimiento)
#
# Construye el laboratorio del experimento 1, todo dentro de tu computador.
#
#   r1 ─┐
#   r2 ─┤
#   ... ├─ br-exp1 (un switch virtual)
#   rN ─┤
#   rz ─┘  (solo en corridas de Zenoh, aloja el router, IP 10.10.0.254)
#
# Cada "rK" es un namespace de red. Un namespace es una copia aislada de la pila de red
# de Linux (sus propias interfaces e IP), y aqui hace de "maquina" independiente.
#
# Uso
#   sudo lab/lab_exp1.sh up N [router]   crea el bridge y los namespaces r1..rN (y rz si pones "router")
#   sudo lab/lab_exp1.sh down            lo desmonta todo
#   sudo lab/lab_exp1.sh show            muestra namespaces, bridge y direcciones

BR=br-exp1                # nombre del bridge (el switch virtual)
SUBNET=10.10.0            # los namespaces reciben 10.10.0.1, 10.10.0.2, ...
ROUTER_IP=${SUBNET}.254   # IP fija del namespace del router de Zenoh

# mk_ns crea UN namespace y lo conecta al bridge.
# Argumentos  <namespace>  <nombre de la interfaz en el host>  <IP>
mk_ns() {
  ip netns add "$1"                                   # crea el namespace vacio
  # Crea un "cable virtual" (veth) de dos extremos. Un extremo se llama eth0 y nace
  # DENTRO del namespace. El otro extremo queda en el host con el nombre $2.
  ip link add "$2" type veth peer name eth0 netns "$1"
  # Enchufa el extremo del host al bridge y lo enciende. Es como conectar un cable a un switch.
  ip link set "$2" master $BR up
  # Dentro del namespace se le asigna IP a eth0, se enciende, y se enciende tambien lo (loopback).
  ip -n "$1" addr add "$3/24" dev eth0
  ip -n "$1" link set eth0 up
  ip -n "$1" link set lo up
}

# down desmonta todo. Es seguro correrlo aunque no haya nada montado.
down() {
  # Solo se tocan namespaces llamados r<numero> o rz, para no borrar los de otros programas.
  for ns in $(ip netns list | awk '{print $1}' | grep -E '^(r[0-9]+|rz)$'); do
    # El manual de ip-netns recomienda matar los procesos antes de borrar el namespace.
    ip netns pids "$ns" 2>/dev/null | xargs -r kill -9
    # Al borrar el namespace desaparece tambien su veth del lado del host.
    ip netns del "$ns"
  done
  ip link del $BR 2>/dev/null                                   # borra el bridge
  iptables -D FORWARD -i $BR -o $BR -j ACCEPT 2>/dev/null       # quita la regla de iptables si existia
  return 0
}

# up N [router] monta el laboratorio con N namespaces (y el del router si se pide).
up() {
  local n=$1 router=$2
  down   # siempre se empieza desde cero
  # Crea el bridge con el "snooping" de multicast apagado. Con snooping activo el bridge
  # podria no repartir el multicast de descubrimiento de DDS a todos. Apagado, se comporta
  # como un switch simple y reparte todo, que es lo que queremos para medir el descubrimiento.
  ip link add $BR type bridge mcast_snooping 0
  ip link set $BR up
  # Regla defensiva para que Docker no bloquee el trafico interno del bridge.
  # En tu maquina FORWARD ya esta en ACCEPT y no hacia falta, pero no estorba.
  iptables -I FORWARD 1 -i $BR -o $BR -j ACCEPT 2>/dev/null || echo "aviso, no se pudo agregar la regla de iptables"
  # Crea los N namespaces. El host ve las interfaces vh1, vh2, ... y sus contadores
  # de bytes miden lo que cada namespace emite.
  for i in $(seq 1 "$n"); do mk_ns "r$i" "vh$i" "${SUBNET}.$i"; done
  # El namespace extra del router solo se crea para las corridas de Zenoh.
  if [ "$router" = "router" ]; then mk_ns rz vhz "$ROUTER_IP"; fi
  return 0
}

# show lista lo que hay montado. Es la captura de la diapositiva 7.
show() {
  ip netns list
  bridge link show 2>/dev/null | grep "master $BR" | head -n 8
  for ns in $(ip netns list | awk '{print $1}' | head -n 3); do ip -n "$ns" -br addr; done
}

# Despacha segun el primer argumento.
case "$1" in
  up) up "$2" "$3" ;;
  down) down ;;
  show) show ;;
  *) echo "uso, $0 up N [router] | down | show"; exit 1 ;;
esac
