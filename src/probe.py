#!/usr/bin/env python3
"""
probe.py (EXPERIMENTO 2, nodo de medicion)

Un mismo programa con dos roles.
  pub  publica std_msgs/String con data = f"{seq}|{time.time_ns()}|" + "x" * size a frecuencia fija.
       Antes espera a que haya un suscriptor emparejado (con tiempo maximo) y guarda ese tiempo.
  sub  recibe los mensajes y calcula la latencia de UN SOLO SENTIDO (hora de llegada menos hora
       de envio). Es valido porque los namespaces comparten el reloj del kernel. Guarda en CSV
       el numero de secuencia, la hora de llegada y la latencia.

El QoS es IDENTICO en publicador y suscriptor. Un publicador best_effort con un suscriptor
reliable es incompatible y no se conectarian.
"""
import argparse
import json
import signal
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String


def make_qos(kind, depth):
    """Construye el perfil de QoS. 'kind' es reliable o best_effort."""
    rel = ReliabilityPolicy.RELIABLE if kind == "reliable" else ReliabilityPolicy.BEST_EFFORT
    # KEEP_LAST con 'depth' guarda solo los ultimos 'depth' mensajes. VOLATILE significa que
    # un suscriptor nuevo no recibe mensajes viejos.
    return QoSProfile(reliability=rel, history=HistoryPolicy.KEEP_LAST, depth=depth,
                      durability=DurabilityPolicy.VOLATILE)


def run_pub(node, a, stop):
    """Rol publicador. Corre en el namespace robot."""
    pub = node.create_publisher(String, a.topic, make_qos(a.qos, a.depth))

    # 1) Espera de emparejamiento, con tope. Con perdida alta el descubrimiento tarda, y
    #    publicar antes de tiempo contaria como perdida falsa. Se guarda cuanto tardo (match_s).
    #    Si con algun RMW el conteo nunca sube, se agota el tope, match_ok queda en falso y
    #    se sigue adelante, lo que equivale a una espera fija de match_timeout segundos.
    t_wait = time.monotonic()
    while pub.get_subscription_count() == 0 and not stop["v"] and time.monotonic() - t_wait < a.match_timeout:
        rclpy.spin_once(node, timeout_sec=0.05)    # spin_once deja que el nodo procese descubrimiento
    match_s = time.monotonic() - t_wait
    match_ok = pub.get_subscription_count() > 0

    # 2) Espera fija extra, para que termine el descubrimiento en el otro sentido (los acuses).
    t = time.monotonic()
    while time.monotonic() - t < a.settle and not stop["v"]:
        rclpy.spin_once(node, timeout_sec=0.05)

    # 3) Publicacion a frecuencia fija.
    pad = "x" * a.size                 # el relleno se arma una sola vez
    period = 1.0 / a.hz                # segundos entre mensajes
    open(a.out + ".pubstart", "w").close()   # archivo vacio, avisa al orquestador que ya empezo a publicar
    t_start = time.monotonic()
    next_t, seq = t_start, 0
    while not stop["v"] and time.monotonic() - t_start < a.duration:
        now = time.monotonic()
        if now >= next_t:
            msg = String()
            ts = time.time_ns()                      # hora de envio en nanosegundos, comparable entre namespaces
            msg.data = f"{seq}|{ts}|" + pad          # seq permite contar entregas unicas, ts calcula la latencia
            pub.publish(msg)
            seq += 1
            next_t += period
            if time.monotonic() - next_t > period:   # si se atraso mucho, no acumula una rafaga de mensajes
                next_t = time.monotonic()
        else:
            rclpy.spin_once(node, timeout_sec=min(0.01, next_t - now))   # espera poco para no gastar CPU

    # Resumen que lee el orquestador. sent es la cantidad de mensajes enviados.
    with open(a.out, "w") as f:
        json.dump({"sent": seq, "match_s": match_s, "match_ok": match_ok, "hz": a.hz, "size": a.size}, f)


def run_sub(node, a, stop):
    """Rol suscriptor. Corre en el namespace base."""
    recs = []   # lista de (seq, hora de llegada en ns, latencia en ms)

    def cb(msg):
        rn = time.time_ns()                          # hora de llegada, lo primero que se hace
        try:
            # Solo se lee el prefijo (64 caracteres) porque el resto es relleno. Asi no se
            # procesa 1 MB en cada mensaje. Formato "seq|ts|xxxxx...".
            s, t, _ = msg.data[:64].split("|", 2)
            recs.append((int(s), rn, (rn - int(t)) / 1e6))   # latencia en milisegundos
        except ValueError:
            pass

    node.create_subscription(String, a.topic, cb, make_qos(a.qos, a.depth))
    t_end = time.monotonic() + a.duration
    try:
        while not stop["v"] and time.monotonic() < t_end:
            rclpy.spin_once(node, timeout_sec=0.05)
    finally:
        # Se escribe el CSV aunque llegue SIGTERM. El orquestador detiene al suscriptor con SIGTERM.
        with open(a.out, "w") as f:
            f.write("seq,recv_ns,latency_ms\n")
            for s, r, lat in recs:
                f.write(f"{s},{r},{lat:.4f}\n")


def main():
    # Argumentos de linea de comando. Los pide el diseno (role, size, qos, hz, duration).
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", choices=["pub", "sub"], required=True)
    ap.add_argument("--size", type=int, default=1024, help="bytes de relleno")
    ap.add_argument("--qos", choices=["reliable", "best_effort"], default="reliable")
    ap.add_argument("--hz", type=float, default=10.0, help="mensajes por segundo (solo pub)")
    ap.add_argument("--duration", type=float, default=60.0, help="segundos de publicacion")
    ap.add_argument("--out", required=True, help="archivo de salida")
    ap.add_argument("--topic", default="probe")
    ap.add_argument("--depth", type=int, default=10)
    ap.add_argument("--match-timeout", type=float, default=30.0)
    ap.add_argument("--settle", type=float, default=2.0)
    a = ap.parse_args()

    rclpy.init()
    node = Node(f"probe_{a.role}")
    stop = {"v": False}   # bandera que se activa al recibir SIGTERM
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("v", True))
    try:
        (run_pub if a.role == "pub" else run_sub)(node, a, stop)
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
