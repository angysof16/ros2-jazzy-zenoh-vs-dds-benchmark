#!/usr/bin/env python3
"""
analyze.py (COMPARTIDO, lee los CSV de los dos experimentos)

Genera las figuras, las tablas y las comprobaciones de cordura. Se corre SIN sudo.
python3 src/analyze.py

Los titulos de las figuras son descriptivos y neutros a proposito. El mensaje de cada
grafica (lo que quieres decir al verla) lo escribes tu cuando tengas los datos.
"""
import sys
import textwrap
from pathlib import Path

# Permite hacer "import config" aunque se corra desde otra carpeta.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "config"))
import config as C  # noqa: E402

import matplotlib  # noqa: E402
matplotlib.use("Agg")   # modo sin ventana, solo guarda archivos PNG
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import ticker  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

# Estilo comun. Fuente de 13 pt o mas para que se lea proyectado, y 200 dpi al guardar.
plt.rcParams.update({"font.size": 13, "axes.titlesize": 14, "axes.labelsize": 13, "legend.fontsize": 12,
                     "axes.spines.top": False, "axes.spines.right": False, "savefig.dpi": 200})
LAB = {k: v[1] for k, v in C.RMWS.items()}   # clave corta -> etiqueta legible
COL = {k: v[2] for k, v in C.RMWS.items()}   # clave corta -> color fijo
C.FIGS.mkdir(exist_ok=True)


# ============================ EXPERIMENTO 1 ============================
def plot_bytes(d, fase, log, name, title):
    """
    Grafica de bytes contra N, una linea por RMW. La linea es la media, la banda va del minimo al
    maximo de las repeticiones, y los puntos son cada repeticion. Con log=True usa ejes logaritmicos,
    donde una ley de potencias se ve como una recta y su pendiente es el exponente.
    """
    a = d[d.fase == fase]
    fig, ax = plt.subplots(figsize=(8.5, 5.5))
    for r in C.RMWS:
        s = a[a.rmw == r]
        if s.empty:
            continue
        g = s.groupby("N")["bytes"].agg(["mean", "min", "max"]) / 1e6   # se pasa a megabytes
        ax.plot(g.index, g["mean"], "-o", color=COL[r], label=LAB[r], lw=2)
        ax.fill_between(g.index, g["min"], g["max"], color=COL[r], alpha=0.2)
        ax.scatter(s["N"], s["bytes"] / 1e6, color=COL[r], s=14, alpha=0.6)
    ax.set_xlabel("N, numero de nodos en el anillo")
    ax.set_ylabel("Bytes emitidos en 30 s (MB)")
    ax.set_title(title)
    ax.set_xticks(sorted(a["N"].unique()))
    if log:
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xticks(sorted(a["N"].unique()))
        ax.get_xaxis().set_major_formatter(ticker.ScalarFormatter())   # muestra 5, 10, 20 y no 10^1
    ax.legend()
    fig.tight_layout()
    fig.savefig(C.FIGS / name)
    plt.close(fig)


def exp1():
    """Figuras y tablas del experimento 1."""
    p = C.RESULTS / "exp1" / "exp1.csv"
    if not p.exists():
        print("exp1.csv no existe, se omite el experimento 1")
        return
    d = pd.read_csv(p, na_values=["NA"])   # "NA" (no convergio) se lee como dato faltante

    # Figuras de bytes. Arranque en escala lineal y log-log, y reposo.
    plot_bytes(d, "arranque", False, "fig_exp1_bytes_arranque.png",
               "Bytes emitidos en los primeros 30 s segun N (banda, minimo y maximo de 3 repeticiones)")
    plot_bytes(d, "arranque", True, "fig_exp1_bytes_arranque_loglog.png",
               "Bytes de arranque segun N, ejes logaritmicos")
    plot_bytes(d, "reposo", False, "fig_exp1_bytes_reposo.png", "Bytes emitidos en reposo (30 s) segun N")

    # Convergencia contra N. Un punto por repeticion y una linea con la mediana.
    # La convergencia esta repetida en las dos fases, se usan solo las filas de arranque.
    conv = d[d.fase == "arranque"].copy()
    fig, ax = plt.subplots(figsize=(8.5, 5.5))
    for r in C.RMWS:
        s = conv[conv.rmw == r]
        if s.empty:
            continue
        ax.scatter(s["N"], s["convergencia_s"], color=COL[r], s=22, alpha=0.7)
        ax.plot(sorted(s["N"].unique()), s.groupby("N")["convergencia_s"].median(), "-o",
                color=COL[r], label=LAB[r], lw=2)
    ax.set_xlabel("N, numero de nodos en el anillo")
    ax.set_ylabel("Convergencia, hasta que el ultimo listener recibe (s)")
    ax.set_title("Tiempo de convergencia segun N (linea, mediana; puntos, repeticiones)")
    ax.set_xticks(sorted(conv["N"].unique()))
    ax.legend()
    fig.tight_layout()
    fig.savefig(C.FIGS / "fig_exp1_convergencia.png")
    plt.close(fig)
    n_na = int(conv["convergencia_s"].isna().sum())
    print(f"\n[exp1] corridas sin convergencia (NA), {n_na}")

    # Exponente aparente. Pendiente log-log entre pares de N consecutivos, log(y2/y1) / log(x2/x1).
    # Con solo 3 valores de N es una TENDENCIA, no el ajuste de una ley. Sirve para contrastar
    # "lo que se dice" (crecimiento cercano al cuadrado) con tus datos.
    print("\n[exp1] exponente aparente de bytes de arranque contra N (pendiente log-log entre pares de N)")
    a = d[d.fase == "arranque"]
    for r in C.RMWS:
        g = a[a.rmw == r].groupby("N")["bytes"].mean()
        ns = sorted(g.index)
        pares = [f"N={x}->{y}, {np.log(g[y] / g[x]) / np.log(y / x):.2f}" for x, y in zip(ns, ns[1:])]
        print(f"  {LAB[r]}, " + " | ".join(pares))
    print("  Los bytes incluyen datos de usuario (los talkers publican), que crecen con N. Es tendencia, no ley.")

    # Tabla de CPU y RAM en reposo, media y desviacion por RMW y N, en formato Markdown.
    rep = d[d.fase == "reposo"]
    t = rep.groupby(["rmw", "N"]).agg(cpu_mean=("cpu_pct", "mean"), cpu_std=("cpu_pct", "std"),
                                      rss_mean=("rss_mb", "mean"), rss_std=("rss_mb", "std")).round(1).reset_index()
    t["rmw"] = t["rmw"].map(LAB)
    md = "| RMW | N | CPU total (%) | RAM total (MB, RSS) |\n|---|---|---|---|\n"
    for _, r in t.iterrows():
        md += f"| {r['rmw']} | {int(r['N'])} | {r['cpu_mean']} ± {r['cpu_std']} | {r['rss_mean']} ± {r['rss_std']} |\n"
    (C.RESULTS / "exp1" / "tabla_cpu_ram.md").write_text(md)
    print("\n[exp1] CPU y RAM en reposo (100 % = un nucleo; RSS suma paginas compartidas varias veces)\n" + md)


# ============================ EXPERIMENTO 2 ============================
def exp2():
    """Figuras, tabla de celdas y comprobaciones de cordura del experimento 2."""
    base = C.RESULTS / "exp2"
    p = base / "runs.csv"
    if not p.exists():
        print("runs.csv no existe, se omite el experimento 2")
        return
    runs = pd.read_csv(p)

    # 1) Se cargan las latencias de cada corrida (un CSV por corrida en raw/) y se detecta saturacion.
    lat, sat = {}, {}
    for _, r in runs.iterrows():
        key = f"{r['rmw']}_{r['profile']}_{r['size']}_{r['qos']}_r{r['rep']}"
        f = base / "raw" / f"{key}.sub.csv"
        arr, flag = np.array([]), False
        if f.exists():
            df = pd.read_csv(f).drop_duplicates("seq").sort_values("seq")   # secuencias unicas, en orden
            arr = df["latency_ms"].to_numpy()
            if len(arr) >= 6:
                # Saturacion. Si la latencia del ultimo tercio es mas del doble que la del primer tercio
                # y ademas crece mas de 500 ms, la cola del enlace se esta llenando durante la corrida.
                # En ese caso el p99 mide la cola y no el middleware.
                k = len(arr) // 3
                first, last = np.median(arr[:k]), np.median(arr[-k:])
                flag = bool(last > 2 * first and last - first > 500)
        lat[key], sat[key] = arr, flag

    # 2) Se agrupan las 3 repeticiones de cada celda (RMW, perfil, tamano, QoS) y se calculan
    #    p50, p99 y minimo sobre todas las muestras juntas. El p99 con mas muestras es mas estable.
    cells = {}
    for (rmw, prof, size, qos), g in runs.groupby(["rmw", "profile", "size", "qos"]):
        keys = [f"{rmw}_{prof}_{size}_{qos}_r{rep}" for rep in g["rep"]]
        pool = np.concatenate([lat[k] for k in keys]) if keys else np.array([])
        cells[(rmw, prof, size, qos)] = dict(
            n=len(pool), p50=np.percentile(pool, 50) if len(pool) else np.nan,
            p99=np.percentile(pool, 99) if len(pool) else np.nan, min=pool.min() if len(pool) else np.nan,
            sat=sum(sat[k] for k in keys) >= 2,   # se marca saturada si al menos 2 de 3 repeticiones lo estan
            deliv=g["delivery"].tolist(),
            nbytes=(g["bytes_r2b"] + g["bytes_b2r"]).tolist())
    pd.DataFrame([dict(rmw=k[0], profile=k[1], size=k[2], qos=k[3], n=v["n"], p50_ms=v["p50"], p99_ms=v["p99"],
                       min_ms=v["min"], entrega_media=np.mean(v["deliv"]), saturada=v["sat"])
                  for k, v in cells.items()]).round(3).to_csv(base / "cells.csv", index=False)

    profiles, sizes, qoss = list(C.PROFILES), list(C.SIZES), C.QOS

    def grid(title, ylabel, draw, name, ylog=False, ylim=None):
        """
        Dibuja una cuadricula de paneles, una fila por tamano y una columna por QoS. En cada panel el
        eje x son los perfiles y hay un marcador por RMW, desplazado un poco para que no se solapen.
        'draw' es la funcion que dibuja una celda concreta.
        """
        fig, axes = plt.subplots(len(sizes), len(qoss), figsize=(13, 4.3 * len(sizes)), sharex=True)
        for i, s in enumerate(sizes):
            for j, q in enumerate(qoss):
                ax = axes[i][j]
                for k, r in enumerate(C.RMWS):
                    for x, pr in enumerate(profiles):
                        c = cells.get((r, pr, s, q))
                        if c and c["n"] + len(c["deliv"]) > 0:
                            draw(ax, x + (k - 1) * 0.22, r, c)   # (k-1)*0.22 reparte los 3 RMW alrededor de x
                ax.set_title(f"{s}, QoS {q}")
                ax.set_xticks(range(len(profiles)))
                ax.set_xticklabels(profiles)
                ax.set_ylabel(ylabel)
                if ylog:
                    ax.set_yscale("log")
                if ylim:
                    ax.set_ylim(*ylim)
        handles = [Line2D([0], [0], color=COL[r], lw=4, label=LAB[r]) for r in C.RMWS]
        fig.legend(handles=handles, loc="upper right", ncol=3)
        fig.suptitle("\n".join(textwrap.wrap(title, 70)), y=0.995, x=0.30, fontsize=13)   # titulo partido en lineas
        fig.tight_layout(rect=(0, 0, 1, 0.92))
        fig.savefig(C.FIGS / name)
        plt.close(fig)

    # Latencia. Circulo lleno es el p50, triangulo vacio es el p99, y la linea que los une es la cola.
    def draw_lat(ax, x, r, c):
        if c["n"] == 0:
            return
        ax.plot([x, x], [c["p50"], c["p99"]], color=COL[r], lw=2)
        ax.plot(x, c["p50"], "o", color=COL[r], ms=8)
        ax.plot(x, c["p99"], "^", color=COL[r], mfc="white", ms=9)
        if c["sat"]:
            ax.annotate("sat.", (x, c["p99"]), textcoords="offset points", xytext=(0, 7), ha="center", fontsize=9)

    grid("Latencia de un sentido por perfil (circulo, p50; triangulo, p99; sat., latencia creciente en la corrida)",
         "Latencia (ms, escala log)", draw_lat, "fig_exp2_latencia.png", ylog=True)

    # Entrega. Barra con la media y puntos negros con cada repeticion.
    def draw_deliv(ax, x, r, c):
        ax.bar(x, np.mean(c["deliv"]), width=0.2, color=COL[r], alpha=0.85)
        ax.scatter([x] * len(c["deliv"]), c["deliv"], color="black", s=10, zorder=3)

    grid("Razon de entrega por perfil (secuencias unicas recibidas / enviadas; barra, media; puntos, repeticiones)",
         "Razon de entrega", draw_deliv, "fig_exp2_entrega.png", ylim=(0, 1.05))

    # Bytes que cruzan el enlace, suma de los dos sentidos.
    def draw_bytes(ax, x, r, c):
        ax.bar(x, np.mean(c["nbytes"]) / 1e6, width=0.2, color=COL[r], alpha=0.85)

    grid("Bytes que cruzan el enlace al publicar (suma de ambos sentidos, media de repeticiones)",
         "Bytes (MB, escala log)", draw_bytes, "fig_exp2_bytes_enlace.png", ylog=True)

    sanity(runs, cells)


def sanity(runs, cells):
    """
    Comprobaciones de cordura. Verifican que el laboratorio mide lo que se cree. Si fallan,
    el problema esta en el laboratorio y no en el middleware. Escribe results/sanity.txt.
    """
    S, res = C.SANITY, []

    def add(name, ok, detail, info=False):
        res.append((name, ok, detail, info))   # info=True significa solo informativa, no cuenta como falla

    for r in C.RMWS:
        for s in C.SIZES:
            for q in C.QOS:
                tag = f"{LAB[r]} {s} {q}"
                ci, cc, cm = (cells.get((r, p, s, q)) for p in ("ideal", "celular", "malo"))
                # Ideal. La latencia debe ser baja, del orden de milisegundos.
                if ci and ci["n"]:
                    lim = S["ideal_p50_ms"][s]
                    add(f"Ideal, p50 < {lim} ms | {tag}", ci["p50"] < lim, f"p50 = {ci['p50']:.2f} ms")
                # Celular. Con 50 ms de retardo base, el minimo y el p50 deben estar cerca de 50 ms.
                if cc and cc["n"]:
                    add(f"Celular, minimo >= {S['celular_min_ms']} ms y p50 >= {S['celular_p50_ms']} ms | {tag}",
                        cc["min"] >= S["celular_min_ms"] and cc["p50"] >= S["celular_p50_ms"],
                        f"min = {cc['min']:.1f}, p50 = {cc['p50']:.1f} ms")
                # Malo. Con 100 ms de retardo base.
                if cm and cm["n"]:
                    add(f"Malo, minimo >= {S['malo_min_ms']} ms y p50 >= {S['malo_p50_ms']} ms | {tag}",
                        cm["min"] >= S["malo_min_ms"] and cm["p50"] >= S["malo_p50_ms"],
                        f"min = {cm['min']:.1f}, p50 = {cm['p50']:.1f} ms")
                # Orden esperado, ideal < celular < malo.
                if ci and cc and cm and ci["n"] and cc["n"] and cm["n"]:
                    add(f"Orden de p50, ideal < celular < malo | {tag}", ci["p50"] < cc["p50"] < cm["p50"],
                        f"{ci['p50']:.1f} < {cc['p50']:.1f} < {cm['p50']:.1f}")
    # Best_effort con 2 % de perdida debe entregar menos del 100 % (solo 1 KB, celular).
    # En Zenoh es informativa, porque Zenoh viaja por TCP y TCP retransmite lo perdido.
    for r in C.RMWS:
        c = cells.get((r, "celular", "1KB", "best_effort"))
        if c:
            m = float(np.mean(c["deliv"]))
            es_tcp = (r == "zenoh")
            add(f"Best_effort 1KB celular, entrega < 100% | {LAB[r]}", m < 1.0,
                f"entrega media = {m:.4f}" + (" (informativa, Zenoh usa TCP por defecto)" if es_tcp else ""), info=es_tcp)
    
    esperadas = len(C.RMWS) * len(C.PROFILES) * len(C.SIZES) * len(C.QOS) * C.EXP2_REPS
    add(f"Matriz completa, {len(runs)} de {esperadas} corridas", len(runs) == esperadas, f"{len(runs)} filas")
    
    add("Emparejamiento observable (match_ok = 1) en todas las corridas", bool((runs["match_ok"] == 1).all()),
        f"{int((runs['match_ok'] == 0).sum())} corridas con match_ok = 0 (se uso la espera con tope)")
    saturadas = sorted(k for k, v in cells.items() if v["sat"])

    # Se arma el texto. Cada linea queda como PASA, FALLA o INFO.
    lines, passed, total = [], 0, 0
    for name, ok, detail, info in res:
        label = "PASA" if ok else ("INFO" if info else "FALLA")
        if not info:
            total += 1
            passed += int(ok)
        lines.append(f"[{label}] {name} ({detail})")
    lines.append(f"\nComprobaciones, {passed} de {total} aprobadas (las INFO no cuentan)")
    lines.append(f"Celdas marcadas como saturadas (latencia creciente), {len(saturadas)}")
    for k in saturadas:
        lines.append(f"  {k}")
    lines.append("Con 1 MB el p99 usa muy pocas muestras (aprox 90 por celda) y se acerca al maximo.")
    text = "\n".join(lines)
    (C.RESULTS / "sanity.txt").write_text(text + "\n")
    print("\n" + text)


if __name__ == "__main__":
    exp1()
    exp2()
