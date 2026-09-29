#!/usr/bin/env python3
"""linmon - realtime CPU and memory monitor for Linux.

Reads /proc directly (no dependencies) and serves a live-graph dashboard.

    python3 linmon.py                 # http://127.0.0.1:8080
    python3 linmon.py --port 9000 --interval 0.5
    python3 linmon.py --host 0.0.0.0  # expose on your network (no auth!)
"""
import argparse, json, os, threading, time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def read_cpu():
    """Return {'cpu': (busy, total), 'cpu0': (...), ...} from /proc/stat."""
    out = {}
    with open("/proc/stat") as f:
        for line in f:
            if not line.startswith("cpu"):
                break
            p = line.split()
            v = list(map(int, p[1:9]))  # user nice system idle iowait irq softirq steal
            idle = v[3] + v[4]
            total = sum(v)
            out[p[0]] = (total - idle, total)
    return out


def read_mem():
    m = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":")
            m[k] = int(v.split()[0]) * 1024  # kB -> bytes
    total = m["MemTotal"]
    avail = m.get("MemAvailable", m["MemFree"])
    swap_t = m.get("SwapTotal", 0)
    swap_u = swap_t - m.get("SwapFree", 0)
    return {
        "mem_total": total,
        "mem_used": total - avail,
        "mem_pct": round((total - avail) / total * 100, 1),
        "cached": m.get("Cached", 0) + m.get("Buffers", 0),
        "swap_total": swap_t,
        "swap_used": swap_u,
        "swap_pct": round(swap_u / swap_t * 100, 1) if swap_t else 0.0,
    }


class Sampler(threading.Thread):
    def __init__(self, interval, keep):
        super().__init__(daemon=True)
        self.interval = interval
        self.history = deque(maxlen=keep)
        self.lock = threading.Lock()
        self.prev = read_cpu()
        self.cores = len(self.prev) - 1

    def run(self):
        while True:
            time.sleep(self.interval)
            cur = read_cpu()
            pct = {}
            for k, (b, t) in cur.items():
                pb, pt = self.prev.get(k, (b, t))
                pct[k] = round((b - pb) / (t - pt) * 100, 1) if t > pt else 0.0
            self.prev = cur
            sample = {
                "t": time.time(),
                "cpu": pct["cpu"],
                "cores": [pct[f"cpu{i}"] for i in range(self.cores)],
                "load": os.getloadavg(),
                **read_mem(),
            }
            with self.lock:
                self.history.append(sample)

    def snapshot(self):
        with self.lock:
            return list(self.history)


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>linmon</title>
<style>
:root{--bg:#0e1720;--panel:#15212d;--line:#243646;--text:#e6edf3;--dim:#8ba0b2;--cpu:#4cc9f0;--mem:#ffb454;--swap:#c792ea}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 "IBM Plex Sans","Segoe UI",system-ui,sans-serif}
header{display:flex;flex-wrap:wrap;gap:8px 24px;align-items:baseline;padding:18px 24px 6px}
h1{font-size:20px;margin:0;font-weight:600}
.meta{color:var(--dim);font-size:13px}
main{display:grid;gap:16px;padding:12px 24px 28px;grid-template-columns:repeat(auto-fit,minmax(min(100%,440px),1fr))}
section{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px}
.head{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px}
.head h2{margin:0;font-size:15px;font-weight:600;color:var(--dim)}
.big{font-size:34px;font-weight:600;font-variant-numeric:tabular-nums}
canvas{width:100%;height:190px;display:block}
.stats{display:flex;flex-wrap:wrap;gap:6px 22px;margin-top:10px;color:var(--dim);font-size:13px}
.stats b{color:var(--text);font-weight:500;font-variant-numeric:tabular-nums}
.cores{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:8px 14px;margin-top:14px}
.core{font-size:12px;color:var(--dim)}
.bar{height:6px;background:var(--line);border-radius:3px;margin-top:3px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--cpu);transition:width .3s}
#err{display:none;margin:0 24px;padding:10px 14px;border-radius:8px;background:#3a1d22;color:#ffb3bd}
</style></head><body>
<header><h1>linmon</h1><span class="meta" id="host"></span><span class="meta" id="win"></span></header>
<div id="err">Lost connection to the monitor. Retrying…</div>
<main>
 <section><div class="head"><h2>CPU usage</h2><span class="big" id="cpuv">–</span></div>
  <canvas id="cpu"></canvas>
  <div class="stats"><span>Load <b id="load">–</b></span><span>Cores <b id="nc">–</b></span><span>Peak <b id="cpk">–</b></span></div>
  <div class="cores" id="cores"></div></section>
 <section><div class="head"><h2>Memory used</h2><span class="big" id="memv">–</span></div>
  <canvas id="mem"></canvas>
  <div class="stats"><span>Used <b id="mu">–</b></span><span>Total <b id="mt">–</b></span><span>Cache <b id="mc">–</b></span><span>Swap <b id="sw">–</b></span></div></section>
</main>
<script>
const $=id=>document.getElementById(id);
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
let hist=[],interval=1,MAXPTS=120,coresBuilt=false;
const gb=b=>b>=2**30?(b/2**30).toFixed(2)+" GB":(b/2**20).toFixed(0)+" MB";

function draw(canvas,series){ // series: [{data,color}], y-axis fixed 0-100
  const dpr=window.devicePixelRatio||1,w=canvas.clientWidth,h=canvas.clientHeight;
  canvas.width=w*dpr;canvas.height=h*dpr;
  const g=canvas.getContext("2d");g.scale(dpr,dpr);
  const L=34,B=16,pw=w-L,ph=h-B;
  g.font="11px system-ui";g.fillStyle=css("--dim");g.strokeStyle=css("--line");g.lineWidth=1;
  for(const v of [0,25,50,75,100]){
    const y=ph-ph*v/100+.5;
    g.beginPath();g.moveTo(L,y);g.lineTo(w,y);g.stroke();
    g.fillText(v+"%",0,Math.min(Math.max(y+4,10),h-B));
  }
  for(const s of series){
    const d=s.data.slice(-MAXPTS),step=pw/(MAXPTS-1),x0=L+pw-(d.length-1)*step;
    if(!d.length)continue;
    g.beginPath();
    d.forEach((v,i)=>{const x=x0+i*step,y=ph-ph*Math.min(v,100)/100;i?g.lineTo(x,y):g.moveTo(x,y)});
    g.strokeStyle=s.color;g.lineWidth=2;g.lineJoin="round";g.stroke();
    if(s.fill){g.lineTo(x0+(d.length-1)*step,ph);g.lineTo(x0,ph);g.closePath();
      g.globalAlpha=.14;g.fillStyle=s.color;g.fill();g.globalAlpha=1}
  }
  g.fillStyle=css("--dim");g.fillText("-"+Math.round(MAXPTS*interval)+"s",L,h-3);
  g.textAlign="right";g.fillText("now",w,h-3);g.textAlign="left";
}

function render(){
  const s=hist[hist.length-1];if(!s)return;
  $("cpuv").textContent=s.cpu.toFixed(1)+"%";
  $("memv").textContent=s.mem_pct.toFixed(1)+"%";
  $("load").textContent=s.load.map(x=>x.toFixed(2)).join("  ");
  $("nc").textContent=s.cores.length;
  $("cpk").textContent=Math.max(...hist.slice(-MAXPTS).map(x=>x.cpu)).toFixed(1)+"%";
  $("mu").textContent=gb(s.mem_used);$("mt").textContent=gb(s.mem_total);$("mc").textContent=gb(s.cached);
  $("sw").textContent=s.swap_total?gb(s.swap_used)+" / "+gb(s.swap_total):"none";
  if(!coresBuilt){
    $("cores").innerHTML=s.cores.map((_,i)=>`<div class="core">Core ${i} <span id="cv${i}"></span><div class="bar"><i id="cb${i}"></i></div></div>`).join("");
    coresBuilt=true;
  }
  s.cores.forEach((v,i)=>{$("cv"+i).textContent=v.toFixed(0)+"%";$("cb"+i).style.width=v+"%"});
  draw($("cpu"),[{data:hist.map(x=>x.cpu),color:css("--cpu"),fill:true}]);
  draw($("mem"),[{data:hist.map(x=>x.mem_pct),color:css("--mem"),fill:true},
                 {data:hist.map(x=>x.swap_pct),color:css("--swap")}]);
}

async function init(){
  const r=await (await fetch("/api/history")).json();
  hist=r.samples;interval=r.interval;MAXPTS=Math.max(60,Math.round(120/Math.max(interval,.5)));
  $("host").textContent=r.hostname+" · "+r.kernel;
  $("win").textContent="last "+Math.round(MAXPTS*interval)+"s, updating every "+interval+"s";
  render();
  setInterval(async()=>{
    try{
      const s=await (await fetch("/api/latest")).json();
      if(s&&(!hist.length||s.t>hist[hist.length-1].t)){hist.push(s);if(hist.length>600)hist.shift()}
      $("err").style.display="none";render();
    }catch(e){$("err").style.display="block"}
  },interval*1000);
}
init().catch(()=>$("err").style.display="block");
addEventListener("resize",render);
</script></body></html>"""


def make_handler(sampler, interval):
    info = {"hostname": os.uname().nodename, "kernel": os.uname().release}

    class H(BaseHTTPRequestHandler):
        def _send(self, body, ctype):
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/":
                self._send(PAGE, "text/html; charset=utf-8")
            elif self.path == "/api/history":
                self._send(json.dumps({**info, "interval": interval,
                                       "samples": sampler.snapshot()}), "application/json")
            elif self.path == "/api/latest":
                h = sampler.snapshot()
                self._send(json.dumps(h[-1] if h else None), "application/json")
            else:
                self.send_error(404)

        def log_message(self, *a):
            pass

    return H


def main():
    ap = argparse.ArgumentParser(description="Realtime Linux CPU/memory monitor")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between samples")
    a = ap.parse_args()
    if not os.path.exists("/proc/stat"):
        raise SystemExit("linmon needs Linux (/proc not found)")
    sampler = Sampler(a.interval, keep=600)
    sampler.start()
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(sampler, a.interval))
    print(f"linmon running at http://{a.host}:{a.port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
