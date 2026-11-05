"""
DEPOT-GUARD dashboard matched to the uploaded STM32 firmware.

STM32 output format:
{"temperature":28,"humidity":60,"vibration":0,"vibration_level":0,
 "vibration_events":0,"mq_raw":350,"mq_voltage":0.282,
 "mq_alert":0,"anomaly":0,"dht_ok":1,"buzzer_active":0,"reason":"Normal"}

Install:
    python -m pip install flask pyserial

Run:
    python dashboard_depot_guard_stm32.py

Open:
    http://127.0.0.1:5000
"""

import json
import threading
import time
from collections import deque
from datetime import datetime

import serial
from serial import SerialException
from flask import Flask, jsonify, render_template_string

# Change this if Windows assigns a different COM port.
SERIAL_PORT = "COM7"
BAUD_RATE = 115200
RECONNECT_SECONDS = 2
STALE_SECONDS = 5

app = Flask(__name__)
lock = threading.Lock()
stop_event = threading.Event()

FIELDS = (
    "temperature", "humidity", "vibration", "vibration_level",
    "vibration_events", "mq_raw", "mq_voltage", "mq_alert",
    "anomaly", "dht_ok", "buzzer_active", "reason"
)

data = {key: None for key in FIELDS}
data.update({
    "connected": False,
    "valid_packet": False,
    "last_packet_time": None,
    "last_packet_text": "Never",
    "raw_line": "",
    "last_error": "",
    "packet_count": 0,
    "bad_line_count": 0,
})
history = deque(maxlen=100)
activity = deque(maxlen=25)


def parse_packet(line):
    """Parse a JSON object and accept the exact keys sent by this firmware."""
    try:
        obj = json.loads(line.strip())
    except json.JSONDecodeError:
        # Also allow a startup/debug prefix before the JSON object.
        start = line.find("{")
        end = line.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            obj = json.loads(line[start:end + 1])
        except json.JSONDecodeError:
            return None

    if not isinstance(obj, dict):
        return None

    # Require at least one of the actual firmware telemetry keys.
    if not any(k in obj for k in FIELDS if k != "reason"):
        return None

    packet = {}
    for key in FIELDS:
        if key not in obj:
            packet[key] = None
        elif key == "reason":
            packet[key] = str(obj[key])
        else:
            try:
                packet[key] = float(obj[key])
            except (TypeError, ValueError):
                packet[key] = None

    return packet


def serial_reader():
    while not stop_event.is_set():
        port = None
        try:
            port = serial.Serial(
                SERIAL_PORT,
                BAUD_RATE,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=1,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )

            with lock:
                data["connected"] = True
                data["last_error"] = ""
                activity.append({
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "type": "INFO",
                    "message": f"Connected to {SERIAL_PORT} at {BAUD_RATE} baud"
                })

            while not stop_event.is_set():
                raw = port.readline()
                if not raw:
                    continue

                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                packet = parse_packet(line)
                with lock:
                    data["raw_line"] = line
                    if packet is None:
                        data["bad_line_count"] += 1
                        activity.append({
                            "time": datetime.now().strftime("%H:%M:%S"),
                            "type": "RX",
                            "message": line[:350]
                        })
                        continue

                    for key, value in packet.items():
                        if value is not None:
                            data[key] = value

                    now = time.time()
                    data["valid_packet"] = True
                    data["last_packet_time"] = now
                    data["last_packet_text"] = datetime.now().strftime("%H:%M:%S")
                    data["packet_count"] += 1
                    data["last_error"] = ""

                    history.append({
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "temperature": data["temperature"] if data["dht_ok"] == 1 else None,
                        "humidity": data["humidity"] if data["dht_ok"] == 1 else None,
                        "mq_raw": data["mq_raw"],
                        "vibration_events": data["vibration_events"],
                    })
                    activity.append({
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "type": "JSON",
                        "message": line[:350]
                    })

        except (SerialException, OSError) as exc:
            with lock:
                data["connected"] = False
                data["last_error"] = str(exc)
                activity.append({
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "type": "ERROR",
                    "message": str(exc)[:350]
                })
        except Exception as exc:
            with lock:
                data["connected"] = False
                data["last_error"] = f"Serial reader error: {exc}"
        finally:
            if port is not None:
                try:
                    port.close()
                except Exception:
                    pass

        stop_event.wait(RECONNECT_SECONDS)


def get_snapshot():
    with lock:
        result = dict(data)
        result["history"] = list(history)
        result["activity"] = list(activity)
    if result["last_packet_time"] is None:
        result["valid_packet"] = False
        result["packet_age"] = None
    else:
        result["packet_age"] = round(time.time() - result["last_packet_time"], 1)
        if result["packet_age"] > STALE_SECONDS:
            result["valid_packet"] = False
    return result


PAGE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DEPOT-GUARD | STM32 Monitoring</title>
<style>
:root{--bg:#07111e;--panel:#101f32;--panel2:#142840;--line:#29425e;--txt:#edf5ff;--muted:#9bb0c8;--cyan:#43d9ed;--green:#48dc9b;--yellow:#ffd166;--red:#ff6b7f}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 50% -20%,#1a3a5b 0,#07111e 65%);font-family:Segoe UI,Arial,sans-serif;color:var(--txt)}
header{padding:23px 4%;display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap;border-bottom:1px solid var(--line)}
.logo{font-size:24px;font-weight:800;letter-spacing:2px}.subtitle{font-size:11px;color:var(--muted);letter-spacing:2px;margin-top:5px}
.connection{padding:10px 15px;border:1px solid var(--line);background:#0b1929;border-radius:30px;color:var(--muted);font-size:12px}
main{max-width:1450px;margin:auto;padding:24px 3% 45px}.notice{padding:13px 16px;border-radius:11px;background:#302714;border:1px solid #755b28;color:#ffe2a3;font-size:13px;line-height:1.5;margin-bottom:20px}
.notice.good{background:#102c26;border-color:#28694f;color:#b4f6d5}.notice.bad{background:#321a24;border-color:#7b3548;color:#ffc1cc}
.heading{font-size:12px;letter-spacing:2px;color:var(--muted);text-transform:uppercase;margin:25px 0 12px}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:13px}
.card{background:linear-gradient(145deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:15px;padding:18px;min-width:0;box-shadow:0 10px 28px #0002}
.label{font-size:11px;color:var(--muted);letter-spacing:1.1px;text-transform:uppercase}
.value{font-size:32px;font-weight:750;margin:13px 0 7px;overflow-wrap:anywhere}.unit{font-size:13px;color:var(--muted);font-weight:500}
.small{font-size:11px;color:var(--muted);line-height:1.5}.statusgrid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:13px}
.status{font-size:18px;font-weight:700;margin-top:12px;overflow-wrap:anywhere}
.twocol{display:grid;grid-template-columns:1fr 1fr;gap:13px;margin-top:13px}
.paneltitle{font-size:14px;font-weight:700;margin-bottom:14px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#07101b;border:1px solid var(--line);border-radius:9px;padding:12px;color:#a8edcf;min-height:65px;max-height:180px;overflow:auto;font-size:12px}
table{width:100%;border-collapse:collapse;font-size:12px}td,th{text-align:left;padding:9px 6px;border-bottom:1px solid #29425e}th{color:var(--muted);font-weight:500}
.log{max-height:210px;overflow:auto}.logitem{font-size:11px;line-height:1.5;padding:8px 0;border-bottom:1px solid #29425e;overflow-wrap:anywhere}.tag{display:inline-block;background:#29425e;border-radius:4px;padding:2px 6px;margin-right:6px;color:#c6d9ed}
canvas{width:100%;height:220px}.footer{text-align:center;color:#7188a4;font-size:11px;padding-top:25px}
@media(max-width:900px){.grid,.statusgrid{grid-template-columns:repeat(2,minmax(0,1fr))}.twocol{grid-template-columns:1fr}}
@media(max-width:480px){.grid,.statusgrid{grid-template-columns:1fr 1fr}.value{font-size:25px}.logo{font-size:20px}}
</style>
</head>
<body>
<header>
 <div><div class="logo">DEPOT-GUARD</div><div class="subtitle">SMART ENVIRONMENTAL MONITORING SYSTEM</div></div>
 <div class="connection" id="connection">Connecting to STM32…</div>
</header>
<main>
 <div class="notice" id="notice">Waiting for the first STM32 JSON packet…</div>

 <div class="heading">Live sensor readings</div>
 <section class="grid">
  <div class="card"><div class="label">Temperature · DHT11</div><div class="value" id="temp">-- <span class="unit">°C</span></div><div class="small" id="tempNote">Waiting for DHT11</div></div>
  <div class="card"><div class="label">Humidity · DHT11</div><div class="value" id="hum">-- <span class="unit">%</span></div><div class="small" id="humNote">Waiting for DHT11</div></div>
  <div class="card"><div class="label">MQ sensor · ADC raw</div><div class="value" id="mq">--</div><div class="small">Demo alert threshold: 800</div></div>
  <div class="card"><div class="label">MQ sensor · voltage</div><div class="value" id="mqv">-- <span class="unit">V</span></div><div class="small">Calculated by STM32 firmware</div></div>
  <div class="card"><div class="label">Vibration state · SW-420</div><div class="value" id="vib">--</div><div class="small">0 = normal · 1 = detected</div></div>
  <div class="card"><div class="label">Vibration level</div><div class="value" id="vlevel">-- <span class="unit">/ 100</span></div><div class="small">Firmware event-count indicator</div></div>
  <div class="card"><div class="label">Vibration events</div><div class="value" id="vevents">--</div><div class="small">Count in current 10-second window</div></div>
  <div class="card"><div class="label">Buzzer</div><div class="value" id="buzzer">--</div><div class="small">Turns on for 3 seconds at 3 events</div></div>
 </section>

 <div class="heading">System status</div>
 <section class="statusgrid">
  <div class="card"><div class="label">Overall anomaly</div><div class="status" id="anomaly">Waiting</div></div>
  <div class="card"><div class="label">MQ alert</div><div class="status" id="mqalert">Waiting</div></div>
  <div class="card"><div class="label">DHT11 sensor</div><div class="status" id="dht">Waiting</div></div>
  <div class="card"><div class="label">Firmware reason</div><div class="status" id="reason">Waiting</div></div>
 </section>

 <section class="twocol">
  <div class="card"><div class="paneltitle">Latest STM32 serial line</div><pre id="raw">No serial line received yet.</pre><div class="small" id="lastseen">Last valid packet: Never</div></div>
  <div class="card"><div class="paneltitle">Serial diagnostics</div><table>
   <tr><th>COM port</th><td id="port">COM7</td></tr><tr><th>Baud rate</th><td>115200</td></tr>
   <tr><th>Valid JSON packets</th><td id="packets">0</td></tr><tr><th>Other lines</th><td id="bad">0</td></tr>
   <tr><th>Last error</th><td id="error">None</td></tr>
  </table></div>
 </section>
 <section class="twocol">
  <div class="card"><div class="paneltitle">Sensor history</div><canvas id="chart" width="850" height="230"></canvas><div class="small">Temperature and humidity are plotted only when DHT11 reports dht_ok = 1. MQ raw ADC is shown on the same illustrative plot.</div></div>
  <div class="card"><div class="paneltitle">Serial activity</div><div class="log" id="log">Waiting for serial activity…</div></div>
 </section>
 <div class="footer">DEPOT-GUARD · Live readings come directly from the STM32 UART stream. Thresholds are demonstration values, not certified safety limits.</div>
</main>
<script>
const el=id=>document.getElementById(id);
const number=(v,d=0)=>v===null||v===undefined||!Number.isFinite(Number(v))?"--":Number(v).toFixed(d);
const isOn=v=>Number(v)===1;
function metric(id,v,unit="",digits=0){el(id).innerHTML=number(v,digits)+(unit?` <span class="unit">${unit}</span>`:"");}
function yesNo(v,yes,no){return v===null||v===undefined?"--":(isOn(v)?yes:no);}
function safe(s){return String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
function plot(points){
 const c=el("chart"),x=c.getContext("2d"),w=c.width,h=c.height;x.clearRect(0,0,w,h);
 x.strokeStyle="#29425e";x.fillStyle="#9bb0c8";x.font="12px Arial";
 for(let i=0;i<5;i++){let y=12+i*(h-35)/4;x.beginPath();x.moveTo(40,y);x.lineTo(w-8,y);x.stroke();}
 if(!points.length){x.fillText("Waiting for sensor history…",52,110);return;}
 let keys=[["temperature","#43d9ed"],["humidity","#48dc9b"],["mq_raw","#ffd166"]];
 let vals=points.flatMap(p=>keys.map(k=>p[k[0]]).filter(v=>v!==null&&v!==undefined&&Number.isFinite(Number(v))).map(Number));
 if(!vals.length)return;let min=Math.min(...vals),max=Math.max(...vals);if(min===max){min-=1;max+=1;}
 keys.forEach(([key,color])=>{x.beginPath();x.strokeStyle=color;x.lineWidth=2;let started=false;
  points.forEach((p,i)=>{let v=p[key];if(v===null||v===undefined||!Number.isFinite(Number(v)))return;
   let px=42+(w-55)*(points.length===1?0:i/(points.length-1)),py=12+(h-35)*(1-(Number(v)-min)/(max-min));
   if(!started){x.moveTo(px,py);started=true;}else x.lineTo(px,py);
  });x.stroke();});
 x.fillStyle="#43d9ed";x.fillText("Temp",45,h-5);x.fillStyle="#48dc9b";x.fillText("Humidity",105,h-5);x.fillStyle="#ffd166";x.fillText("MQ raw",190,h-5);
}
function render(d){
 let fresh=d.valid_packet;
 el("connection").textContent=fresh?"● STM32 DATA RECEIVING":(d.connected?"● COM PORT OPEN · WAITING FOR DATA":"● SERIAL DISCONNECTED");
 el("connection").style.color=fresh?"#48dc9b":(d.connected?"#ffd166":"#ff6b7f");
 let n=el("notice");
 if(!d.connected){n.className="notice bad";n.textContent="Cannot open "+d.serial_port+". Check the COM port and close Tera Term/other serial programs. "+(d.last_error||"");}
 else if(!fresh){n.className="notice";n.textContent="COM port is open, but no valid JSON packet has arrived. The uploaded firmware should send a JSON line every 500 ms. Check the raw serial line and activity log below.";}
 else{n.className="notice good";n.textContent="Live STM32 readings received. Last packet: "+d.packet_age+" seconds ago.";}

 const dhtOK=isOn(d.dht_ok);
 if(d.dht_ok===null){metric("temp",null,"°C");metric("hum",null,"%");}
 else if(!dhtOK){el("temp").innerHTML="SENSOR ERROR";el("hum").innerHTML="SENSOR ERROR";}
 else{metric("temp",d.temperature,"°C");metric("hum",d.humidity,"%");}
 el("tempNote").textContent=dhtOK?"DHT11 reading valid":"DHT11 status: "+(d.dht_ok===null?"waiting":"read failed");
 el("humNote").textContent=dhtOK?"DHT11 reading valid":"Check DHT11 wiring and pull-up";
 metric("mq",d.mq_raw,"",0);metric("mqv",d.mq_voltage,"V",3);
 el("vib").textContent=yesNo(d.vibration,"VIBRATION","NORMAL");
 metric("vlevel",d.vibration_level,"/ 100",0);metric("vevents",d.vibration_events,"",0);
 el("buzzer").textContent=yesNo(d.buzzer_active,"ON","OFF");
 el("anomaly").textContent=yesNo(d.anomaly,"ALERT","NORMAL");
 el("mqalert").textContent=yesNo(d.mq_alert,"ALERT","NORMAL");
 el("dht").textContent=d.dht_ok===null?"Waiting":(dhtOK?"OK":"READ ERROR");
 el("reason").textContent=d.reason??"Waiting";
 el("raw").textContent=d.raw_line||"No serial line received yet.";
 el("lastseen").textContent="Last valid packet: "+d.last_packet_text+(d.packet_age===null?"":" · "+d.packet_age+" sec ago");
 el("port").textContent=d.serial_port;el("packets").textContent=d.packet_count;
 el("bad").textContent=d.bad_line_count;el("error").textContent=d.last_error||"None";
 plot(d.history||[]);
 el("log").innerHTML=(d.activity||[]).slice().reverse().map(a=>`<div class="logitem"><span class="tag">${safe(a.type)}</span><span style="color:#9bb0c8">${safe(a.time)}</span><br>${safe(a.message)}</div>`).join("")||"Waiting for serial activity…";
}
async function refresh(){try{let r=await fetch("/api/data",{cache:"no-store"});render(await r.json());}catch(e){el("connection").textContent="Dashboard API error";}}
refresh();setInterval(refresh,1000);
</script>
</body>
</html>
"""


@app.route("/")
def home():
    return render_template_string(PAGE)


@app.route("/api/data")
def api_data():
    result = get_snapshot()
    result["serial_port"] = SERIAL_PORT
    return jsonify(result)


@app.route("/health")
def health():
    s = get_snapshot()
    return jsonify({
        "web": "ok",
        "serial_connected": s["connected"],
        "valid_packet": s["valid_packet"],
        "packet_count": s["packet_count"],
        "last_error": s["last_error"],
    })


if __name__ == "__main__":
    reader = threading.Thread(target=serial_reader, daemon=True)
    reader.start()
    print("-" * 60)
    print("DEPOT-GUARD STM32 dashboard")
    print(f"Serial port: {SERIAL_PORT} | Baud: {BAUD_RATE}")
    print("Open: http://127.0.0.1:5000")
    print("Close Tera Term before starting this dashboard.")
    print("-" * 60)
    try:
        app.run(host="127.0.0.1", port=5000, debug=False, threaded=True, use_reloader=False)
    finally:
        stop_event.set()
