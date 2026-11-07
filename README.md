# Descubrimiento y enlaces degradados en ROS 2 Jazzy

Experimentos para la charla del ROS Meetup en UTB Cartagena. Comparan tres middlewares de ROS 2 Jazzy.

- `rmw_fastrtps_cpp` (Fast DDS 2.14.7)
- `rmw_cyclonedds_cpp` (Cyclone DDS 0.10.5)
- `rmw_zenoh_cpp` 0.2.11 (con router `rmw_zenohd`)

## Qué mide

**Experimento 1, costo de descubrimiento.** N pares talker/listener, cada uno en su propio namespace de red, conectados en anillo por un bridge. Se varía N (5, 10, 20 y 40) y se mide bytes en el cable, tiempo de convergencia, CPU y RAM. Cada configuración se repite 3 veces.

**Experimento 2, enlace degradado.** Un robot y una base unidos por un enlace virtual con `tc netem`. Se combinan tres perfiles (ideal, celular, malo), tres tamaños de mensaje (1 KB, 128 KB, 1 MB) y dos QoS (reliable, best_effort). Son 162 corridas. Se mide latencia (p50 y p99), tasa de entrega y bytes por el enlace.

| Perfil | netem |
|---|---|
| ideal | sin degradación |
| celular | delay 50ms 10ms, loss 2%, rate 5mbit |
| malo | delay 100ms 30ms, loss 5%, rate 2mbit |

## Resultados principales

- El tráfico de descubrimiento en reposo de Fast DDS crece cerca de N². El de Zenoh crece casi lineal. Cyclone queda en medio.
- Con mensajes pequeños los tres siguen al enlace. Zenoh tiene más latencia con el perfil malo por usar TCP.
- Con mensajes grandes y pérdida, Cyclone aguanta mejor que Zenoh.
- A N=40 en este montaje, Fast DDS y Cyclone no cerraron el grafo (28 a 30 de 40 listeners). Zenoh sí. No se determinó la causa, puede ser un límite del laboratorio.

Los números completos están en `resultados.txt` y en `figures/`.

## Estructura

```
config/     parámetros de los experimentos (config.py)
lab/        scripts que montan y desmontan la red (namespaces, veth, netem)
src/        run_exp1.py, run_exp2.py, analyze.py, common.py, probe.py
results/    datos crudos (exp1/exp1.csv, exp2/runs.csv, env.txt)
figures/    gráficas generadas por analyze.py
resultados.txt   salida de analyze.py (tablas y comprobaciones)
```

## Requisitos

- Linux con permisos de `sudo`. Los scripts crean namespaces, interfaces y reglas de red.
- ROS 2 Jazzy con los tres RMW instalados.
- `iproute2` (`ip`, `tc`), `tshark` (solo para `--capture`) y Python 3 con las librerías que importa `src/analyze.py`.
- `tmux` y `systemd-inhibit` son opcionales. Sirven para dejar corridas largas sin que el equipo se suspenda.

## Cómo reproducir

Ejecuta todo desde la raíz del repositorio.

```bash
# Experimento 1 (aprox 35 min)
sudo python3 src/run_exp1.py --plan        # muestra el plan sin correr nada
sudo python3 src/run_exp1.py               # N = 5, 10, 20
sudo python3 src/run_exp1.py --with-n40    # agrega N = 40 (aprox 12 min)
sudo python3 src/run_exp1.py --capture     # pcapng de N=20, uno por RMW

# Experimento 2 (varias horas)
sudo python3 src/run_exp2.py matrix

# Análisis, genera figuras y resultados.txt
sudo python3 src/analyze.py
```

Prueba rápida con `sudo python3 src/run_exp1.py --smoke`.

Ambos experimentos son reanudables. Si se interrumpen, al relanzar solo hacen las corridas pendientes. Si una corrida falla y deja restos de red, limpia con:

```bash
sudo bash lab/lab_exp1.sh down
```

## Cómo leer los resultados

- `analyze.py` imprime comprobaciones de sanidad. La latencia debe respetar los perfiles de netem y el orden ideal < celular < malo.
- Las celdas marcadas como saturadas tienen latencia creciente por acumulación de cola. En ellas conviene mirar la tasa de entrega, no el p50.
- Con 1 MB el p99 usa unas 90 muestras y se acerca al máximo.

## Limitaciones

- Zenoh usa TCP por defecto y los DDS usan UDP. La latencia con pérdida mezcla el efecto del RMW con el del transporte.
- Las corridas de N=40 de Fast DDS y Cyclone no convergieron por completo. No tratar ese tamaño como curva.
- El exponente de bytes de arranque incluye datos de usuario, por eso no separa el costo de descubrimiento. Usar los bytes en reposo.
- Los pcapng pueden estar incompletos si el equipo perdió paquetes al capturar (revisar `Dropped packets` en Wireshark). Los totales salen de los contadores de las interfaces, no de las capturas.
- Los resultados corresponden a una sola máquina (Intel i5-1035G4) y no se generalizan a otro hardware sin repetirlos.