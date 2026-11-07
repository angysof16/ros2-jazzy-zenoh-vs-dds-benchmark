#!/usr/bin/env python3
"""
run_exp2.py (EXPERIMENTO 2, enlace degradado)

Pregunta. Con un enlace de baja calidad entre robot y estacion, que pasa con la latencia y la entrega?

Laboratorio. Dos namespaces (robot y base) unidos por un veth, con netem en ambos extremos.
El publicador (probe.py) corre en robot y el suscriptor en base. Con Zenoh el router corre en base.

Comandos
  sudo python3 src/run_exp2.py plan             carga ofrecida y tiempo estimado, sin correr nada
  sudo python3 src/run_exp2.py v1               prueba de validacion V1, se corre ANTES de la matriz
  sudo python3 src/run_exp2.py matrix           las 108 corridas, reanudable
  sudo python3 src/run_exp2.py matrix --skip-v1 salta el requisito de V1 (solo si sabes por que)
"""
import argparse
import csv
import itertools
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import common as K

C = K.C
OUT = C.RESULTS / "exp2"
RUNS = OUT / "runs.csv"                         # una fila por corrida de la matriz
PROBE = str(C.ROOT / "src" / "probe.py")        # el nodo de medicion
PY = "/usr/bin/python3"                         # Python del sistema, el que tiene rclpy de ROS
# Columnas del CSV de salida.
FIELDS = ["rmw", "profile", "size", "qos", "rep", "hz", "duration_s", "sent", "received", "delivery",
          "match_s", "match_ok", "p50_ms", "p99_ms", "min_ms", "max_ms", "bytes_r2b", "bytes_b2r",
          "pkts_r2b", "pkts_b2r", "bytes_r2b_total", "bytes_b2r_total", "shm_files"]


def pct(xs, q):
    """Percentil q (0 a 100) con interpolacion lineal, igual que numpy por defecto. xs debe venir ordenado."""
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * q / 100     # posicion exacta dentro de la lista
    f = int(k)                      # indice de abajo
    c = min(f + 1, len(xs) - 1)     # indice de arriba
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def load_sub(path):
    """Lee el CSV del suscriptor y devuelve {seq: latencia_ms}. Si una secuencia llega repetida se queda con la primera."""
    seen = {}
    with open(path) as f:
        next(f, None)   # salta el encabezado
        for line in f:
            s, _, lat = line.strip().split(",")
            seen.setdefault(int(s), float(lat))   # setdefault solo guarda si la clave no existe
    return seen


def append_row(path, row):
    """Agrega una fila al CSV, escribiendo el encabezado si el archivo es nuevo."""
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


def done_set():
    """Corridas ya registradas en runs.csv. Es lo que permite reanudar la matriz."""
    if not RUNS.exists():
        return set()
    with open(RUNS) as f:
        return {(r["rmw"], r["profile"], r["size"], r["qos"], int(r["rep"])) for r in csv.DictReader(f)}


def tshark_summary(pcap, txt):
    """Resume una captura con tshark. Jerarquia de protocolos y conversaciones UDP y TCP."""
    with open(txt, "w") as f:
        for z in ("io,phs", "conv,udp", "conv,tcp"):
            f.write(f"### tshark -r {pcap.name} -q -z {z}\n")
            f.flush()
            subprocess.run(["tshark", "-r", str(pcap), "-q", "-z", z], stdout=f, stderr=K.DEVNULL)


def run_one(rmw, profile, netem, size_key, qos, rep, outdir, hz, duration, pcap=None):
    """
    Ejecuta UNA corrida y devuelve un diccionario que es una fila de runs.csv.
    Si se pasa 'pcap', ademas captura el trafico del enlace con tshark (lo usa la prueba V1).
    """
    raw = outdir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    key = f"{rmw}_{profile}_{size_key}_{qos}_r{rep}"        # nombre unico de la corrida
    sub_csv, pub_json = raw / f"{key}.sub.csv", raw / f"{key}.pub.json"
    for f in (sub_csv, pub_json, Path(str(pub_json) + ".pubstart")):
        f.unlink(missing_ok=True)                            # borra restos de un intento anterior
    drain = C.DRAIN[size_key]

    # 1) Limpieza y degradacion del enlace ANTES de arrancar. Asi el descubrimiento tambien sufre el enlace.
    K.cleanup(netem=True)
    K.set_netem(netem)
    procs, cap = [], None
    try:
        # 2) Con Zenoh, el router va en base (la estacion).
        if rmw == "zenoh":
            procs.append(K.spawn("base", [K.exe("rmw_zenoh_cpp", "rmw_zenohd")], K.router_env(), raw / f"{key}.router.log"))
            time.sleep(3)
        qos_args = ["--size", C.SIZES[size_key], "--qos", qos, "--depth", C.DEPTH]
        # 3) El suscriptor arranca primero, en base. Su duracion es larga, se detiene con SIGTERM al final.
        sub = K.spawn("base", [PY, PROBE, "--role", "sub", "--out", sub_csv, "--duration", 86400] + qos_args,
                      K.node_env(rmw), raw / f"{key}.sub.log")
        procs.append(sub)
        time.sleep(3)
        # 4) Captura de tshark en el extremo vb (solo en V1). Dura 45 s para cubrir descubrimiento y datos.
        if pcap:
            cap = subprocess.Popen(["ip", "netns", "exec", "base", "tshark", "-i", "vb", "-n", "-q",
                                    "-a", "duration:45", "-w", str(pcap)], stdout=K.DEVNULL, stderr=K.DEVNULL)
            time.sleep(2)
        # 5) Contadores del enlace antes de lanzar el publicador.
        c_before = K.link_counters()
        # 6) El publicador va en robot. Con Zenoh se le pasa la IP del router, que esta en base.
        pub = K.spawn("robot", [PY, PROBE, "--role", "pub", "--out", pub_json, "--hz", hz, "--duration", duration,
                                "--match-timeout", C.MATCH_TIMEOUT, "--settle", C.SETTLE] + qos_args,
                      K.node_env(rmw, router_ip=C.BASE_IP if rmw == "zenoh" else None), raw / f"{key}.pub.log")
        procs.append(pub)
        # 7) Se espera a que el publicador termine. Cuando aparece el archivo .pubstart se leen los
        #    contadores otra vez, para medir solo los bytes de la fase de publicacion.
        c_start, deadline = None, time.time() + C.MATCH_TIMEOUT + C.SETTLE + duration + 30
        while pub.poll() is None and time.time() < deadline:
            if c_start is None and os.path.exists(str(pub_json) + ".pubstart"):
                c_start = K.link_counters()
            time.sleep(0.2)
        if pub.poll() is None or not pub_json.exists():
            raise RuntimeError("el publicador no termino bien (revisa el log .pub.log)")
        # 8) Se deja vaciar la cola del enlace y se leen los contadores finales.
        time.sleep(drain)
        c_end = K.link_counters()
        shm_files = len(os.listdir("/dev/shm"))              # cuantos archivos hay en /dev/shm, para detectar fugas
        K.stop([sub], grace=10)                              # SIGTERM, el suscriptor escribe su CSV al salir
        # 9) Calculo de metricas.
        meta = json.load(open(pub_json))                     # lo que escribio el publicador
        got = load_sub(sub_csv) if sub_csv.exists() else {}  # {seq: latencia}
        lats = sorted(got.values())
        c_s = c_start or c_before
        sent = meta["sent"]
        return dict(
            rmw=rmw, profile=profile, size=size_key, qos=qos, rep=rep, hz=hz, duration_s=duration,
            sent=sent, received=len(got),
            delivery=round(len(got) / sent, 4) if sent else 0,      # secuencias unicas recibidas entre enviadas
            match_s=round(meta["match_s"], 2), match_ok=int(meta["match_ok"]),
            p50_ms=round(pct(lats, 50), 3), p99_ms=round(pct(lats, 99), 3),
            min_ms=round(lats[0], 3) if lats else float("nan"), max_ms=round(lats[-1], 3) if lats else float("nan"),
            bytes_r2b=c_end[0] - c_s[0], bytes_b2r=c_end[1] - c_s[1],    # bytes en cada sentido durante la publicacion
            pkts_r2b=c_end[2] - c_s[2], pkts_b2r=c_end[3] - c_s[3],
            bytes_r2b_total=c_end[0] - c_before[0], bytes_b2r_total=c_end[1] - c_before[1],  # incluye descubrimiento
            shm_files=shm_files)
    finally:
        # 10) Limpieza pase lo que pase.
        K.stop(procs)
        if cap is not None:
            cap.terminate()
            try:
                cap.wait(timeout=10)   # espera a que tshark termine de escribir el archivo
            except subprocess.TimeoutExpired:
                cap.kill()
        K.cleanup(netem=True)


def offered_load():
    """Imprime la carga ofrecida por tamano contra el enlace mas lento (riesgo 1, enlace saturado)."""
    print("Carga ofrecida (payload mas 40 bytes de cabecera aproximada) contra el enlace mas lento")
    for s, n in C.SIZES.items():
        mbit = (n + 40) * 8 * C.HZ[s] / 1e6      # bytes por mensaje, por 8 bits, por mensajes por segundo
        print(f"  {s}, {C.HZ[s]} Hz, {mbit:.2f} Mbit/s, {100 * mbit / 5:.0f}% de 5 Mbit/s, {100 * mbit / 2:.0f}% de 2 Mbit/s, "
              f"{int(C.HZ[s] * C.DURATION[s])} muestras por corrida")


def estimate_s(size_key):
    """Segundos estimados de una corrida. Montaje, emparejamiento, publicacion y vaciado de cola."""
    return 15 + C.SETTLE + 4 + C.DURATION[size_key] + C.DRAIN[size_key]


def plan():
    """Imprime la carga ofrecida y el tiempo total estimado de la matriz."""
    offered_load()
    total = sum(len(C.RMWS) * len(C.PROFILES) * len(C.QOS) * C.EXP2_REPS * estimate_s(s) for s in C.SIZES)
    n = len(C.RMWS) * len(C.PROFILES) * len(C.SIZES) * len(C.QOS) * C.EXP2_REPS
    print(f"{n} corridas, tiempo estimado aprox {total / 3600:.1f} h (mas hasta {n * C.MATCH_TIMEOUT / 3600:.1f} h "
          f"si el descubrimiento agota el tope de {C.MATCH_TIMEOUT} s)")


def v1():
    """
    Prueba de validacion V1 (regla 4). Para cada RMW corre el perfil ideal y otro con 100 ms y 20 % de
    perdida. Si la latencia no sube al menos 80 ms, algo se salta el enlace y no se corre la matriz.
    """
    out = OUT / "v1"
    out.mkdir(parents=True, exist_ok=True)
    K.write_env(["## V1 (100 ms de retardo y 20% de perdida)"])
    flag = OUT / "v1_ok.flag"          # archivo que habilita la matriz
    flag.unlink(missing_ok=True)
    v = C.V1
    lines, all_ok = [], True
    for rmw in C.RMWS:
        res = {}
        for name, netem in (("ideal", ""), ("v1", C.V1_NETEM)):
            pcap = out / f"v1_{rmw}_{name}.pcapng"
            try:
                row = run_one(rmw, name, netem, v["size"], v["qos"], 1, out, v["hz"], v["duration"], pcap)
            except Exception as e:
                print(f"  ERROR {rmw} {name}, {e}")
                row = None
            if row:
                append_row(out / "runs_v1.csv", row)
                tshark_summary(pcap, out / f"v1_{rmw}_{name}_tshark.txt")
            res[name] = row
        # Criterio. El p50 con V1 debe superar al p50 ideal en al menos V1_MIN_RISE_MS.
        ok = False
        if res["ideal"] and res["v1"] and res["ideal"]["received"] > 0 and res["v1"]["received"] > 0:
            rise = res["v1"]["p50_ms"] - res["ideal"]["p50_ms"]
            ok = rise >= C.V1_MIN_RISE_MS
            lines.append(f"{rmw:20s} p50 ideal {res['ideal']['p50_ms']:8.2f} ms | p50 v1 {res['v1']['p50_ms']:8.2f} ms | "
                         f"subida {rise:8.2f} ms | entrega ideal {res['ideal']['delivery']:.2f} v1 {res['v1']['delivery']:.2f} | "
                         f"match_ok {res['v1']['match_ok']} | shm_files {res['v1']['shm_files']} | {'PASA' if ok else 'FALLA'}")
        else:
            lines.append(f"{rmw:20s} sin datos suficientes | FALLA")
        all_ok &= ok
    text = "\n".join(lines)
    (out / "v1.txt").write_text(text + "\n")
    print("\n" + text)
    if all_ok:
        flag.write_text("ok\n")        # deja la marca que permite correr la matriz
        print("\nV1 aprobada. Ya puedes correr la matriz.")
    else:
        print("\nV1 FALLO. No corras la matriz. Algo se salta el enlace o el emparejamiento falla, revisa los .pcapng y los logs.")
    K.fix_owner()


def matrix(skip_v1):
    """Corre las 108 corridas, 3 RMW x 3 perfiles x 2 tamanos x 2 QoS x 3 repeticiones."""
    if not skip_v1 and not (OUT / "v1_ok.flag").exists():
        sys.exit("Falta aprobar V1 (run_exp2.py v1). Usa --skip-v1 solo si sabes por que.")
    K.write_env()
    done, rng = done_set(), random.Random(11)
    todo = []
    for rep in range(1, C.EXP2_REPS + 1):
        # Todas las combinaciones de esta repeticion que aun no estan hechas.
        keys = [(r, p, s, q, rep) for r, p, s, q in itertools.product(C.RMWS, C.PROFILES, C.SIZES, C.QOS)
                if (r, p, s, q, rep) not in done]
        rng.shuffle(keys)       # orden aleatorio dentro de cada repeticion, para repartir la deriva de la maquina
        todo += keys
    left = sum(estimate_s(k[2]) for k in todo)
    print(f"{len(todo)} corridas pendientes, aprox {left / 3600:.1f} h")
    try:
        for i, (r, p, s, q, rep) in enumerate(todo, 1):
            t = time.time()
            try:
                row = run_one(r, p, C.PROFILES[p], s, q, rep, OUT, C.HZ[s], C.DURATION[s])
            except Exception as e:
                # No se escribe nada, asi que la corrida se reintenta al reanudar.
                print(f"  ERROR en {r} {p} {s} {q} rep{rep}, {e}")
                continue
            append_row(RUNS, row)   # se guarda de inmediato, si se corta la luz no se pierde lo hecho
            left -= estimate_s(s)
            print(f"[{i}/{len(todo)}] {r} {p} {s} {q} rep{rep}, entrega {row['delivery']:.2f}, p50 {row['p50_ms']} ms, "
                  f"{time.time() - t:.0f} s, faltan aprox {left / 3600:.1f} h")
    except KeyboardInterrupt:
        print("interrumpido, se puede reanudar")
    finally:
        K.clear_netem()
        K.cleanup(netem=True)
        K.fix_owner()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "v1", "matrix"])
    ap.add_argument("--skip-v1", action="store_true")
    a = ap.parse_args()
    if a.cmd == "plan":
        plan()
        return
    K.require_root()
    OUT.mkdir(parents=True, exist_ok=True)
    # Monta el laboratorio una sola vez para toda la sesion y lo desmonta al final.
    subprocess.run([str(C.ROOT / "lab" / "lab_exp2.sh"), "up"], check=True)
    try:
        v1() if a.cmd == "v1" else matrix(a.skip_v1)
    finally:
        subprocess.run([str(C.ROOT / "lab" / "lab_exp2.sh"), "down"])
        K.fix_owner()


if __name__ == "__main__":
    main()
