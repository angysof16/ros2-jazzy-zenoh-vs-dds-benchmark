#!/usr/bin/env python3
"""
run_exp1.py (EXPERIMENTO 1, costo de descubrimiento)

¿Cuánto tráfico genera cada middleware solo por existir?
¿Cuánto tarda el grafo en converger al crecer N?

Topologia en anillo con N namespaces.
En cada r_i corre un talker que publica en el topic t_i y un listener suscrito al topic de la maquina siguiente (t_{i+1}, y el ultimo escucha t_1).

Cada corrida tiene dos fases de 30s, "arranque" (0 a 30s) y "reposo" (30s a 60s).

USO
corre lo pendiente (reanudable)
sudo python3 src/run_exp1.py

muestra el plan y el tiempo estimado, sin correr nada
sudo python3 src/run_exp1.py --plan

prueba rapida, N=5 y 1 repeticion, en smoke.csv
sudo python3 src/run_exp1.py --smoke

agrega N=40
sudo python3 src/run_exp1.py --with-n40

captura tshark con N=20 por RMW (no entra al CSV)
sudo python3 src/run_exp1.py --capture
"""

import argparse
import csv
import random
import subprocess
import sys
import time

import common as K

C = K.C
OUT = C.RESULTS / "exp1"
# Columnas del CSV de salida.
FIELDS = [ "rmw", "N", "rep", "fase", "bytes", "paquetes", "convergencia_s", "cpu_pct", "rss_mb" ]
LAB = str( C.ROOT / "lab" / "lab_exp1.sh" )   # script que monta el laboratorio


def done_set( csv_path ):
  """
  Devuelve las corridas que ya estan completas en el CSV (tienen sus dos fases).
  Es lo que permite reanudar sin repetir trabajo.
  """
  if not csv_path.exists():
    return set()
  # end if
  fases = {}
  with open( csv_path ) as f:
    for r in csv.DictReader( f ):
      fases.setdefault( ( r[ "rmw" ], int( r[ "N" ] ), int( r[ "rep" ] ) ), set() ).add( r[ "fase" ] )
    # end for
  # end with
  return { k for k, v in fases.items() if { "arranque", "reposo" } <= v }
# end def


def snapshot(ifaces, procs):
  # snapshot del momento. Contadores del bridge, y CPU y RAM acumulados de todos los procesos
  b, p = K.read_counters_host( ifaces )
  return dict( t=time.time(), b=b, p=p, ticks=sum( K.proc_ticks( x.pid ) for x in procs ), rss_kb=sum( K.proc_rss_kb( x.pid ) for x in procs ) )
# end def


def phs(pcap, txt):
  # Resume una captura con la jerarquia de protocolos de tshark (que protocolos y cuantos bytes).
  with open( txt, "w" ) as f:
    subprocess.run( [ "tshark", "-r", str( pcap ), "-q", "-z", "io,phs" ], stdout=f, stderr=K.DEVNULL )
  # end with
# end def


def run_one( rmw, n, rep, capture=False ):
  # Ejecuta una corrida (un RMW, un N, una repeticion). Devuelve las dos filas del CSV
  tag = f"{ rmw }_N{ n }_r{ rep }"
  logdir = OUT / "logs" / tag
  logdir.mkdir( parents=True, exist_ok=True )
  pcap = OUT / f"cap_{ tag }.pcapng"   # archivo de la captura (solo se usa con --capture)
  caplog = OUT / f"cap_{ tag }.log"    # mensajes de error de tshark, si los hay
  capfh = caplogfh = None              # manejadores de esos dos archivos

  # 1. Limpieza, para que nada de la corrida anterior contamine esta.
  K.cleanup()

  # 2. Monta el laboratorio. Solo Zenoh necesita el namespace extra del router.
  subprocess.run( [ LAB, "up", str( n ) ] + ( [ "router" ] if rmw == "zenoh" else [] ), check=True )
  procs, cap, rows = [], None, None
  try:
    # Interfaces del host cuyos contadores se suman (una por namespace)
    ifaces = [ f"vh{ i }" for i in range( 1, n + 1 ) ]
    if rmw == "zenoh":
      ifaces.append( "vhz" ) # el veth del router tambien cuenta como trafico de Zenoh
      # El router va en el namespace rz. Se le da 3s de ventaja para que ya escuche cuando arranquen los nodos
      procs.append( K.spawn( "rz", [ K.exe( "rmw_zenoh_cpp", "rmw_zenohd" ) ], K.router_env(), logdir / "router.log" ) )
      time.sleep( 3 )
    # end if
    talker = K.exe( "demo_nodes_cpp", "talker" )
    listener = K.exe( "demo_nodes_cpp", "listener" )
    # Entorno de los nodos. Con Zenoh se les pasa la IP del router.
    env = K.node_env( rmw, router_ip=C.EXP1_ROUTER_IP if rmw == "zenoh" else None )

    if capture:
      # Captura en el bridge, ve todo el trafico entre namespaces. Dura 2 fases mas 5 s.
      # tshark escribe el pcapng por la salida estandar ("-w -") y es Python quien abre el archivo.
      # Asi el archivo lo crea este proceso y no dumpcap, al que el sistema le negaba escribir
      # en esta carpeta ("Permission denied"), por eso antes no quedaba ningun pcapng.
      capfh = open( pcap, "wb" )
      caplogfh = open( caplog, "w" )
      cap = subprocess.Popen( [ "tshark", "-i", "br-exp1", "-n", "-q", "-B", "512", "-a", f"duration:{ 2 * C.PHASE_S + 5 }", "-w", "-" ], stdout=capfh, stderr=caplogfh )
      time.sleep( 2 )
      if cap.poll() is not None:
        print( f"  AVISO, tshark murio al arrancar, mira { caplog }" )
      # end if
    # end if

    # 3. Lectura inicial. Contadores en cero y hora de lanzamiento t0
    b0, p0 = K.read_counters_host( ifaces )
    t0 = time.time()
    # 4. Lanza el ciclo (topología en anillo). Para el namespace i, nxt es el siguiente (el ultimo vuelve al primero).
    for i in range( 1, n + 1 ):
      nxt = i % n + 1
      # "-r __node:=" renombra el nodo y "-r chatter:=" cambia el topic en el que publica o escucha.
      procs.append( K.spawn( f"r{ i }", [ talker, "--ros-args", "-r", f"__node:=talker_{ i }", "-r", f"chatter:=t_{ i }" ], env, logdir / f"talker_{ i }.log" ) )
      procs.append( K.spawn( f"r{ i }", [ listener, "--ros-args", "-r", f"__node:=listener_{ i }", "-r", f"chatter:=t_{ nxt }" ], env, logdir / f"listener_{ i }.log" ) )
    # end for

    # 5. Bucle de medicion durante las dos fases
    first, snap_a = {}, None # first guarda la hora del primer mensaje de cada listener
    t_a = t0 + C.PHASE_S
    t_b = t0 + 2 * C.PHASE_S
    while time.time() < t_b:
      now = time.time()
      for i in range( 1, n + 1 ):
        if i not in first:
          # La convergencia es la hora en que cada listener imprime su primer "I heard".
          try:
            if b"I heard" in ( logdir / f"listener_{ i }.log" ).read_bytes():
              first[ i ] = now
            # end if
          # end try
          except OSError:
            pass
          # end except
        # end if
      # end for
      if snap_a is None and now >= t_a:
        snap_a = snapshot( ifaces, procs ) # foto al final del arranque
      # end if
      time.sleep( 0.25 )
    # end while
    snap_b = snapshot( ifaces, procs ) # foto al final del reposo

    # Aviso si algun proceso murio durante la corrida
    alive = sum( 1 for x in procs if x.poll() is None )
    if alive < len( procs ):
      print(f"  AVISO { tag }, solo { alive }/{ len( procs ) } procesos vivos al final (revisa logs)" )
    # end if

    # 6. Cálculo de metricas
    # Convergencia, máximo entre listeners menos t0. Si algun listener nunca recibio, "NA".
    conv = ( max( first.values() ) - t0 ) if len( first ) == n else None
    conv_s = "NA" if conv is None else f"{ conv:.2f}"
    da, db = snap_a[ "t" ] - t0, snap_b[ "t" ] - snap_a[ "t" ]# duracion real de cada fase
    rows = [
      # Fase arranque. Bytes y paquetes son la diferencia contra el cero inicial.
      # CPU es tiempo de CPU entre tiempo transcurrido (100 % equivale a un nucleo completo).
      dict( rmw=rmw, N=n, rep=rep, fase="arranque", bytes=snap_a[ "b" ] - b0, paquetes=snap_a[ "p" ] - p0,
          convergencia_s=conv_s, cpu_pct=round(100 * snap_a[ "ticks" ] / K.CLK / da, 1 ),
          rss_mb=round( snap_a[ "rss_kb" ] / 1024, 1 ) ),
      # Fase reposo. Diferencia entre la foto del final y la del final del arranque.
      dict( rmw=rmw, N=n, rep=rep, fase="reposo", bytes=snap_b[ "b" ] - snap_a[ "b" ], paquetes=snap_b[ "p" ] - snap_a[ "p" ],
          convergencia_s=conv_s,
          cpu_pct=round( 100 * ( snap_b[ "ticks" ] - snap_a[ "ticks" ] ) / K.CLK / db, 1 ),
          rss_mb=round( snap_b[ "rss_kb" ] / 1024, 1 ) ),
    ]
    if cap is not None:
      cap.wait( timeout=30 )
      capfh.close()
      if pcap.stat().st_size == 0:
        print( f"  AVISO, { pcap.name } quedo vacio, mira { caplog }" )
      # end if
      phs( pcap, OUT / f"phs_{ rmw }_N{ n }.txt" )
    # end if
  # end try
  finally:
    # 7. Limpieza final, pase lo que pase (incluso si hubo error o Ctrl+C).
    K.stop( procs )
    if cap is not None and cap.poll() is None:
      cap.terminate()
    # end if
    for fh in ( capfh, caplogfh ):
      if fh is not None:
        fh.close()
      # end if
    # end for
    subprocess.run( [ LAB, "down" ] )
    # Los archivos lttng-ust-wait y snap.* siempre estan en /dev/shm y no son de la corrida, se ignoran.
    left = [ x for x in K.cleanup() if not x.startswith( ( "lttng-ust", "snap." ) ) ]
    if left:
      print( f" /dev/shm no quedo vacio tras { tag }, { left[ :5 ] }" )
    # end if
  # end finally
  return None if capture else rows
# end def


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument( "--with-n40", action="store_true", help="agrega N=40" )
  ap.add_argument( "--plan", action="store_true", help="solo muestra el plan" )
  ap.add_argument( "--capture", action="store_true", help="captura tshark con N=20" )
  ap.add_argument( "--smoke", action="store_true", help="prueba rapida, N=5, 1 repeticion, smoke.csv" )
  ap.add_argument( "--reps", type=int, default=C.EXP1_REPS )
  a = ap.parse_args()

  ns = C.EXP1_N + ( [40] if a.with_n40 else [] )
  reps = a.reps
  csv_path = OUT / "exp1.csv"
  if a.smoke:
    # La prueba rapida usa otro CSV para no mezclar datos de prueba con los reales.
    ns, reps, csv_path = [5], 1, OUT / "smoke.csv"
  # end if

  per_run = 2 * C.PHASE_S + 18   # 60 s de fases mas mas o menos 18 s de montaje y limpieza
  total = len( C.RMWS ) * len( ns ) * reps
  if a.plan:
    print( f"{ total } corridas, aprox { per_run } s cada una, total aprox { total * per_run / 60:.0f} min" )
    return
  # end if

  K.require_root()
  OUT.mkdir( parents=True, exist_ok=True )
  K.write_env() # registra versiones y parametros en results/env.txt
  try:
    if a.capture:
      for r in C.RMWS:
        print( f"captura { r } N=20" )
        try:
          run_one( r, 20, 1, capture=True )
        # end try
        except Exception as e:
          # Si falla la captura de un RMW, se sigue con el siguiente.
          print( f"  ERROR en la captura de { r }, { e }" )
        # end except
      # end for
      return
    # end if

    # Arma la lista de corridas pendientes. Las repeticiones son el bucle externo, y dentro
    # de cada N se baraja el orden de los RMW para repartir la deriva de la maquina
    # (calentamiento, otros procesos) en vez de favorecer siempre al mismo.
    done, rng, todo = done_set( csv_path ), random.Random( 7 ), []
    for rep in range( 1, reps + 1 ):
      for n in ns:
        order = list( C.RMWS )
        rng.shuffle( order )
        todo += [ ( r, n, rep ) for r in order if ( r, n, rep ) not in done ]
      # end for
    # end for
    print( f"{ len( todo ) } corridas pendientes de { total }, aprox { len( todo ) * per_run / 60:.0f} min" )

    new = not csv_path.exists()  # si el CSV no existe hay que escribir primero el encabezado
    for k, ( r, n, rep ) in enumerate( todo, 1 ):
      t = time.time()
      try:
        rows = run_one( r, n, rep )
      # end try
      except Exception as e:
        # Una corrida fallida no detiene las demas. No se escribe nada en el CSV, asi que
        # se reintenta sola la proxima vez que corras el comando.
        print( f"  ERROR en { r } N={ n } rep={ rep }, { e }" )
        continue
      # end except
      with open(csv_path, "a", newline="") as f:
          w = csv.DictWriter(f, FIELDS)
          if new:
              w.writeheader()
              new = False
          w.writerows(rows)
      print( f"[ { k } / { len( todo ) } ] { r } N={ n } rep={ rep } ok en { time.time() - t:.0f} s, " f"conv={ rows[ 0 ][ 'convergencia_s' ] } s" )
    # end for
  # end try
  except KeyboardInterrupt:
    print( "interrumpido, limpiando" )
  # end except
  finally:
    subprocess.run( [ LAB, "down" ] )
    K.cleanup()
    K.fix_owner()
  # end finally
# end def


if __name__ == "__main__":
  main()