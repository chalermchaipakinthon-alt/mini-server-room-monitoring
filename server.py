
from flask import Flask, request, jsonify, render_template_string
from datetime import datetime
import csv
import html as html_escape
import json
import os
import time
import urllib.parse
import urllib.request

app = Flask(__name__)

LOG_FILE = "sensor_log.csv"
INCIDENT_FILE = "incident_log.csv"

TEMP_WARNING_THRESHOLD = 33.0
TEMP_CRITICAL_THRESHOLD = 36.0
WATER_LEAK_THRESHOLD = 1000
MOVING_AVERAGE_WINDOW = 5
WATER_DEBOUNCE_COUNT = 2
ANOMALY_MIN_SAMPLES = 10
Z_SCORE_THRESHOLD = 2.5
TEMP_RATE_THRESHOLD = 1.5
HUMIDITY_RATE_THRESHOLD = 6.0
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_COOLDOWN_SECONDS = 60

temperature_samples = []
humidity_samples = []
water_leak_counter = 0
last_telegram_sent_at = {}

latest_data = {
    "temperature_raw": None,
    "temperature_filtered": None,
    "humidity_raw": None,
    "humidity_filtered": None,
    "water_value": None,
    "water_leak_raw": False,
    "water_leak": False,
    "water_debounce_count": 0,
    "status": "NO DATA",
    "ai_anomaly": False,
    "anomaly_score": 0,
    "anomaly_reason": "Waiting for enough data",
    "temperature_rate": 0,
    "humidity_rate": 0,
    "time": "-"
}

history = []
incidents = []


def create_log_files():
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "time",
                "temperature_raw",
                "temperature_filtered",
                "humidity_raw",
                "humidity_filtered",
                "water_value",
                "water_leak_raw",
                "water_leak_confirmed",
                "water_debounce_count",
                "status",
                "ai_anomaly",
                "anomaly_score",
                "anomaly_reason",
                "temperature_rate",
                "humidity_rate"
            ])

    if not os.path.exists(INCIDENT_FILE):
        with open(INCIDENT_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "time",
                "status",
                "message",
                "temperature_raw",
                "temperature_filtered",
                "humidity_raw",
                "humidity_filtered",
                "water_value",
                "ai_anomaly",
                "anomaly_score",
                "anomaly_reason"
            ])


def to_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except:
        return None


def to_int(value):
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except:
        return None


def moving_average(samples, new_value, window_size):
    if new_value is None:
        return None

    samples.append(new_value)

    if len(samples) > window_size:
        samples.pop(0)

    return round(sum(samples) / len(samples), 2)


def mean(values):
    return sum(values) / len(values)


def std(values):
    avg = mean(values)
    variance = sum((value - avg) ** 2 for value in values) / len(values)
    return variance ** 0.5


def detect_anomaly(history, current_data):
    if len(history) < ANOMALY_MIN_SAMPLES:
        return {
            "ai_anomaly": False,
            "anomaly_score": 0,
            "anomaly_reason": "Learning normal pattern",
            "temperature_rate": 0,
            "humidity_rate": 0
        }

    temp_values = [
        item["temperature_filtered"]
        for item in history
        if item.get("temperature_filtered") is not None
    ]

    humidity_values = [
        item["humidity_filtered"]
        for item in history
        if item.get("humidity_filtered") is not None
    ]

    temp_now = current_data["temperature_filtered"]
    humidity_now = current_data["humidity_filtered"]

    if temp_now is None or len(temp_values) < ANOMALY_MIN_SAMPLES:
        return {
            "ai_anomaly": False,
            "anomaly_score": 0,
            "anomaly_reason": "Not enough valid temperature data",
            "temperature_rate": 0,
            "humidity_rate": 0
        }

    temp_prev = temp_values[-1]
    temperature_rate = round(temp_now - temp_prev, 2)

    if humidity_now is not None and len(humidity_values) > 0:
        humidity_prev = humidity_values[-1]
        humidity_rate = round(humidity_now - humidity_prev, 2)
    else:
        humidity_rate = 0

    temp_avg = mean(temp_values)
    temp_std = std(temp_values)

    if temp_std == 0:
        temp_z_score = 0
    else:
        temp_z_score = abs((temp_now - temp_avg) / temp_std)

    temp_z_score = round(temp_z_score, 2)
    reasons = []

    if temp_z_score >= Z_SCORE_THRESHOLD:
        reasons.append("Temperature is far from normal pattern")

    if abs(temperature_rate) >= TEMP_RATE_THRESHOLD:
        reasons.append("Temperature changed too quickly")

    if abs(humidity_rate) >= HUMIDITY_RATE_THRESHOLD:
        reasons.append("Humidity changed too quickly")

    ai_anomaly = len(reasons) > 0

    return {
        "ai_anomaly": ai_anomaly,
        "anomaly_score": temp_z_score,
        "anomaly_reason": " / ".join(reasons) if ai_anomaly else "Sensor pattern is stable",
        "temperature_rate": temperature_rate,
        "humidity_rate": humidity_rate
    }


def detect_raw_water_leak(data, water_value):
    if data.get("water_leak") is True:
        return True

    if data.get("water_leak") is False:
        return False

    if water_value is None:
        return False

    return water_value >= WATER_LEAK_THRESHOLD


def determine_status(temp_filtered, water_leak_raw, water_leak_confirmed):
    if temp_filtered is None:
        return "SENSOR_ERROR"

    if water_leak_confirmed or temp_filtered >= TEMP_CRITICAL_THRESHOLD:
        return "CRITICAL"

    if water_leak_raw or temp_filtered >= TEMP_WARNING_THRESHOLD:
        return "WARNING"

    return "NORMAL"


def make_incident_message(data):
    if data["water_leak"]:
        return "Water leak confirmed after debounce filtering"

    if data["water_leak_raw"]:
        return "Possible water leak detected"

    if data["status"] == "CRITICAL":
        return "Filtered temperature exceeded critical threshold"

    if data["status"] == "WARNING":
        return "Filtered temperature exceeded warning threshold"

    return "System event"


def append_sensor_log(data):
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            data["time"],
            data["temperature_raw"],
            data["temperature_filtered"],
            data["humidity_raw"],
            data["humidity_filtered"],
            data["water_value"],
            data["water_leak_raw"],
            data["water_leak"],
            data["water_debounce_count"],
            data["status"],
            data["ai_anomaly"],
            data["anomaly_score"],
            data["anomaly_reason"],
            data["temperature_rate"],
            data["humidity_rate"]
        ])


def append_incident_log(incident):
    with open(INCIDENT_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            incident["time"],
            incident["status"],
            incident["message"],
            incident["temperature_raw"],
            incident["temperature_filtered"],
            incident["humidity_raw"],
            incident["humidity_filtered"],
            incident["water_value"],
            incident["ai_anomaly"],
            incident["anomaly_score"],
            incident["anomaly_reason"]
        ])


def build_telegram_message(data, incident):
    status = data["status"]
    if status == "CRITICAL":
        title = "CRITICAL ALERT"
    elif status == "WARNING":
        title = "WARNING ALERT"
    elif data["ai_anomaly"]:
        title = "AI ANOMALY DETECTED"
    else:
        title = "SYSTEM NOTICE"

    message = html_escape.escape(str(incident["message"]))
    reason = html_escape.escape(str(data["anomaly_reason"]))

    return "\n".join([
        f"<b>{title}</b>",
        "<b>Mini Server Room Monitoring</b>",
        "",
        f"<b>Status:</b> <code>{status}</code>",
        f"<b>Time:</b> <code>{data['time']}</code>",
        "",
        "<b>Sensor Summary</b>",
        f"Temp: <b>{data['temperature_filtered']} C</b> <code>(raw {data['temperature_raw']} C)</code>",
        f"Humidity: <b>{data['humidity_filtered']} %</b> <code>(raw {data['humidity_raw']} %)</code>",
        f"Water: <b>{data['water_value']}</b> | Leak: <code>{data['water_leak']}</code>",
        "",
        "<b>AI Check</b>",
        f"Anomaly: <code>{data['ai_anomaly']}</code>",
        f"Score: <code>{data['anomaly_score']}</code>",
        f"Rate: <code>{data['temperature_rate']} C</code> temp, <code>{data['humidity_rate']} %</code> humidity",
        "",
        f"<b>Reason:</b> {reason}",
        f"<b>Event:</b> {message}",
    ])


def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram is not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    try:
        request = urllib.request.Request(url, data=payload, method="POST")
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
        return bool(result.get("ok"))
    except Exception as error:
        print("Telegram send failed:", error)
        return False


def should_send_telegram(event_key):
    now = time.time()
    last_sent = last_telegram_sent_at.get(event_key, 0)

    if now - last_sent < TELEGRAM_COOLDOWN_SECONDS:
        return False

    last_telegram_sent_at[event_key] = now
    return True


def notify_telegram_if_needed(data, incident):
    event_key = "AI_ANOMALY" if data["ai_anomaly"] else data["status"]

    if should_send_telegram(event_key):
        message = build_telegram_message(data, incident)
        send_telegram_message(message)


create_log_files()


@app.route("/data", methods=["POST"])
def receive_data():
    global latest_data, history, incidents, water_leak_counter

    data = request.get_json(silent=True) or {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    temperature_raw = to_float(data.get("temperature"))
    humidity_raw = to_float(data.get("humidity"))
    water_value = to_int(data.get("water_value"))

    temperature_filtered = moving_average(
        temperature_samples,
        temperature_raw,
        MOVING_AVERAGE_WINDOW
    )

    humidity_filtered = moving_average(
        humidity_samples,
        humidity_raw,
        MOVING_AVERAGE_WINDOW
    )

    water_leak_raw = detect_raw_water_leak(data, water_value)

    if water_leak_raw:
        water_leak_counter += 1
    else:
        water_leak_counter = 0

    water_leak_confirmed = water_leak_counter >= WATER_DEBOUNCE_COUNT

    status = determine_status(
        temperature_filtered,
        water_leak_raw,
        water_leak_confirmed
    )

    latest_data = {
        "temperature_raw": temperature_raw,
        "temperature_filtered": temperature_filtered,
        "humidity_raw": humidity_raw,
        "humidity_filtered": humidity_filtered,
        "water_value": water_value,
        "water_leak_raw": water_leak_raw,
        "water_leak": water_leak_confirmed,
        "water_debounce_count": min(water_leak_counter, WATER_DEBOUNCE_COUNT),
        "status": status,
        "time": now
    }

    anomaly_result = detect_anomaly(history, latest_data)
    latest_data.update(anomaly_result)

    history.append(latest_data.copy())

    if len(history) > 60:
        history = history[-60:]

    append_sensor_log(latest_data)

    if status in ["WARNING", "CRITICAL"] or latest_data["ai_anomaly"]:
        incident = {
            "time": now,
            "status": status,
            "message": (
                "AI anomaly detected: " + latest_data["anomaly_reason"]
                if latest_data["ai_anomaly"]
                else make_incident_message(latest_data)
            ),
            "temperature_raw": temperature_raw,
            "temperature_filtered": temperature_filtered,
            "humidity_raw": humidity_raw,
            "humidity_filtered": humidity_filtered,
            "water_value": water_value,
            "ai_anomaly": latest_data["ai_anomaly"],
            "anomaly_score": latest_data["anomaly_score"],
            "anomaly_reason": latest_data["anomaly_reason"]
        }

        incidents.insert(0, incident)
        incidents = incidents[:15]
        append_incident_log(incident)
        notify_telegram_if_needed(latest_data, incident)

    print(latest_data)
    return jsonify({"message": "received", "data": latest_data})


@app.route("/api/latest")
def api_latest():
    return jsonify(latest_data)


@app.route("/api/history")
def api_history():
    return jsonify(history)


@app.route("/api/incidents")
def api_incidents():
    return jsonify(incidents)


@app.route("/")
def dashboard():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Mini Server Room Monitoring</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">

        <style>
            * {
                box-sizing: border-box;
            }

            body {
                margin: 0;
                font-family: Arial, sans-serif;
                background: #0f172a;
                color: #e5e7eb;
            }

            .page {
                padding: 28px;
            }

            .topbar {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 22px;
            }

            h1 {
                margin: 0;
                font-size: 30px;
            }

            .subtitle {
                margin-top: 6px;
                color: #94a3b8;
                font-size: 14px;
            }

            .pill {
                padding: 10px 14px;
                border: 1px solid #334155;
                border-radius: 999px;
                color: #cbd5e1;
                background: #111827;
            }

            .grid {
                display: grid;
                grid-template-columns: 1.2fr 0.8fr;
                gap: 20px;
                margin-bottom: 20px;
            }

            .cards {
                display: grid;
                grid-template-columns: repeat(5, 1fr);
                gap: 16px;
                margin-bottom: 20px;
            }

            .card {
                background: #111827;
                border: 1px solid #1f2937;
                border-radius: 18px;
                padding: 20px;
                box-shadow: 0 16px 40px rgba(0,0,0,0.25);
            }

            .status-main {
                font-size: 42px;
                font-weight: 800;
                margin-top: 10px;
                margin-bottom: 8px;
            }

            .status-label,
            .metric-title {
                color: #94a3b8;
                font-size: 13px;
            }

            .status-desc,
            .metric-unit {
                color: #cbd5e1;
                font-size: 14px;
            }

            .normal .status-main { color: #22c55e; }
            .warning .status-main { color: #f59e0b; }
            .critical .status-main { color: #ef4444; }
            .error .status-main { color: #94a3b8; }
            .nodata .status-main { color: #cbd5e1; }

            .metric-value {
                font-size: 30px;
                font-weight: 800;
                margin: 10px 0 6px;
            }

            .temp-value {
                color: #38bdf8;
            }

            .hum-value {
                color: #fb7185;
            }

            .water-safe {
                color: #22c55e;
            }

            .water-danger {
                color: #ef4444;
            }

            .ai-normal {
                color: #22c55e;
            }

            .ai-anomaly {
                color: #f59e0b;
            }

            .sub-value {
                display: block;
                margin-top: 4px;
                color: #64748b;
                font-size: 13px;
            }

            .section-title {
                margin: 0 0 16px 0;
                font-size: 20px;
            }

            .chart-grid {
                display: grid;
                grid-template-columns: 1fr;
                gap: 20px;
            }

            .chart-card {
                min-height: 390px;
            }

            canvas {
                width: 100% !important;
                height: 300px !important;
            }

            .info-list {
                display: flex;
                flex-direction: column;
                gap: 14px;
            }

            .info-item {
                display: flex;
                justify-content: space-between;
                gap: 14px;
                padding-bottom: 14px;
                border-bottom: 1px solid #1f2937;
            }

            .info-item:last-child {
                border-bottom: none;
                padding-bottom: 0;
            }

            .info-label {
                color: #94a3b8;
            }

            .info-value {
                font-weight: 700;
                text-align: right;
            }

            table {
                width: 100%;
                border-collapse: collapse;
            }

            th {
                color: #94a3b8;
                text-align: left;
                padding: 12px;
                border-bottom: 1px solid #334155;
                font-size: 13px;
            }

            td {
                padding: 12px;
                border-bottom: 1px solid #1f2937;
                font-size: 14px;
            }

            .badge {
                padding: 5px 9px;
                border-radius: 999px;
                font-size: 12px;
                font-weight: 800;
            }

            .badge-warning {
                color: #fbbf24;
                background: rgba(245, 158, 11, 0.14);
            }

            .badge-critical {
                color: #fca5a5;
                background: rgba(239, 68, 68, 0.16);
            }

            .empty-row {
                text-align: center;
                color: #64748b;
                padding: 28px;
            }

            @media (max-width: 950px) {
                .grid,
                .cards {
                    grid-template-columns: 1fr;
                }

                .topbar {
                    flex-direction: column;
                    align-items: flex-start;
                    gap: 12px;
                }
            }
        </style>
    </head>

    <body>
        <div class="page">
            <div class="topbar">
                <div>
                    <h1>Mini Server Room Monitoring</h1>
                    <div class="subtitle">Raw sensor data compared with moving average filtered data</div>
                </div>
                <div class="pill">Last update: <span id="lastUpdate">-</span></div>
            </div>

            <div class="grid">
                <div id="statusCard" class="card nodata">
                    <div class="status-label">CURRENT SYSTEM STATUS</div>
                    <div id="statusMain" class="status-main">NO DATA</div>
                    <div id="statusDesc" class="status-desc">Waiting for ESP32 data.</div>
                </div>

                <div class="card">
                    <h2 class="section-title">Filter Setup</h2>
                    <div class="info-list">
                        <div class="info-item">
                            <span class="info-label">Raw data</span>
                            <span class="info-value">Direct from sensor</span>
                        </div>
                        <div class="info-item">
                            <span class="info-label">Filtered data</span>
                            <span class="info-value">Moving average 5 samples</span>
                        </div>
                        <div class="info-item">
                            <span class="info-label">Water leak</span>
                            <span class="info-value">2 readings debounce</span>
                        </div>
                        <div class="info-item">
                            <span class="info-label">AI anomaly</span>
                            <span class="info-value">Z-score + rate change</span>
                        </div>
                        <div class="info-item">
                            <span class="info-label">Telegram alert</span>
                            <span class="info-value">Warning / Critical / AI</span>
                        </div>
                    </div>
                </div>
            </div>

            <div class="cards">
                <div class="card">
                    <div class="metric-title">Temperature Filtered</div>
                    <div id="tempFiltered" class="metric-value temp-value">-°C</div>
                    <div class="metric-unit">
                        Filtered output
                        <span id="tempRaw" class="sub-value">Raw: -°C</span>
                    </div>
                </div>

                <div class="card">
                    <div class="metric-title">Humidity Filtered</div>
                    <div id="humFiltered" class="metric-value hum-value">-%</div>
                    <div class="metric-unit">
                        Filtered output
                        <span id="humRaw" class="sub-value">Raw: -%</span>
                    </div>
                </div>

                <div class="card">
                    <div class="metric-title">Water Sensor</div>
                    <div id="waterValue" class="metric-value">-</div>
                    <div class="metric-unit">
                        Threshold >= 1000
                        <span id="debounceValue" class="sub-value">Debounce: 0/2</span>
                    </div>
                </div>

                <div class="card">
                    <div class="metric-title">Water Leak Confirmed</div>
                    <div id="waterLeak" class="metric-value water-safe">false</div>
                    <div class="metric-unit">
                        Debounced alarm
                        <span id="waterRaw" class="sub-value">Raw leak: false</span>
                    </div>
                </div>

                <div class="card">
                    <div class="metric-title">AI Anomaly</div>
                    <div id="aiAnomaly" class="metric-value ai-normal">NORMAL</div>
                    <div class="metric-unit">
                        Score: <span id="anomalyScore">0</span>
                        <span id="anomalyReason" class="sub-value">Learning normal pattern</span>
                        <span id="rateValue" class="sub-value">Temp rate: 0°C | Hum rate: 0%</span>
                    </div>
                </div>
            </div>

            <div class="chart-grid">
                <div class="card chart-card">
                    <h2 class="section-title">Temperature: Raw vs Filtered</h2>
                    <canvas id="tempChart"></canvas>
                </div>

                <div class="card chart-card">
                    <h2 class="section-title">Humidity: Raw vs Filtered</h2>
                    <canvas id="humChart"></canvas>
                </div>
            </div>

            <div class="card" style="margin-top:20px;">
                <h2 class="section-title">Incident Log</h2>
                <table>
                    <thead>
                        <tr>
                            <th>Time</th>
                            <th>Status</th>
                            <th>Message</th>
                            <th>Temp Raw</th>
                            <th>Temp Filtered</th>
                            <th>AI Score</th>
                            <th>Water</th>
                        </tr>
                    </thead>
                    <tbody id="incidentBody">
                        <tr>
                            <td colspan="7" class="empty-row">No incidents recorded yet</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

        <script>
            let tempChart = null;
            let humChart = null;

            function formatValue(value, suffix) {
                return value !== null && value !== undefined ? value + suffix : "-" + suffix;
            }

            function getStatusConfig(status) {
                const configs = {
                    "NORMAL": {
                        className: "normal",
                        label: "SYSTEM NORMAL",
                        desc: "Filtered readings are within safe range."
                    },
                    "WARNING": {
                        className: "warning",
                        label: "WARNING",
                        desc: "Filtered reading or raw water signal needs attention."
                    },
                    "CRITICAL": {
                        className: "critical",
                        label: "CRITICAL ALERT",
                        desc: "Immediate action required."
                    },
                    "SENSOR_ERROR": {
                        className: "error",
                        label: "SENSOR ERROR",
                        desc: "Sensor reading failed."
                    },
                    "NO DATA": {
                        className: "nodata",
                        label: "NO DATA",
                        desc: "Waiting for ESP32 data."
                    }
                };

                return configs[status] || configs["NO DATA"];
            }

            async function updateCurrentData() {
                const res = await fetch("/api/latest");
                const data = await res.json();

                const config = getStatusConfig(data.status);
                const statusCard = document.getElementById("statusCard");

                statusCard.className = "card " + config.className;

                document.getElementById("statusMain").innerText = config.label;
                document.getElementById("statusDesc").innerText = config.desc;
                document.getElementById("lastUpdate").innerText = data.time || "-";

                document.getElementById("tempFiltered").innerText =
                    formatValue(data.temperature_filtered, "°C");
                document.getElementById("tempRaw").innerText =
                    "Raw: " + formatValue(data.temperature_raw, "°C");

                document.getElementById("humFiltered").innerText =
                    formatValue(data.humidity_filtered, "%");
                document.getElementById("humRaw").innerText =
                    "Raw: " + formatValue(data.humidity_raw, "%");

                document.getElementById("waterValue").innerText =
                    data.water_value !== null && data.water_value !== undefined ? data.water_value : "-";

                document.getElementById("debounceValue").innerText =
                    "Debounce: " + (data.water_debounce_count || 0) + "/2";

                const waterLeak = document.getElementById("waterLeak");
                waterLeak.innerText = data.water_leak;
                waterLeak.className = data.water_leak
                    ? "metric-value water-danger"
                    : "metric-value water-safe";

                document.getElementById("waterRaw").innerText =
                    "Raw leak: " + data.water_leak_raw;

                const aiAnomaly = document.getElementById("aiAnomaly");
                aiAnomaly.innerText = data.ai_anomaly ? "DETECTED" : "NORMAL";
                aiAnomaly.className = data.ai_anomaly
                    ? "metric-value ai-anomaly"
                    : "metric-value ai-normal";

                document.getElementById("anomalyScore").innerText = data.anomaly_score;
                document.getElementById("anomalyReason").innerText = data.anomaly_reason;
                document.getElementById("rateValue").innerText =
                    "Temp rate: " + data.temperature_rate + "°C | Hum rate: " + data.humidity_rate + "%";
            }

            function chartOptions() {
                return {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    plugins: {
                        legend: {
                            labels: {
                                color: "#cbd5e1"
                            }
                        }
                    },
                    scales: {
                        x: {
                            ticks: {
                                color: "#94a3b8",
                                maxRotation: 45,
                                minRotation: 45
                            },
                            grid: {
                                color: "rgba(148, 163, 184, 0.12)"
                            }
                        },
                        y: {
                            ticks: {
                                color: "#94a3b8"
                            },
                            grid: {
                                color: "rgba(148, 163, 184, 0.12)"
                            }
                        }
                    }
                };
            }

            async function updateCharts() {
                const res = await fetch("/api/history");
                const data = await res.json();

                const labels = data.map(x => x.time.split(" ")[1]);

                const tempRaw = data.map(x => x.temperature_raw);
                const tempFiltered = data.map(x => x.temperature_filtered);

                const humRaw = data.map(x => x.humidity_raw);
                const humFiltered = data.map(x => x.humidity_filtered);

                if (tempChart === null) {
                    tempChart = new Chart(document.getElementById("tempChart"), {
                        type: "line",
                        data: {
                            labels: labels,
                            datasets: [
                                {
                                    label: "Temperature Raw",
                                    data: tempRaw,
                                    borderColor: "rgba(56, 189, 248, 0.35)",
                                    backgroundColor: "rgba(56, 189, 248, 0.06)",
                                    borderDash: [7, 5],
                                    pointRadius: 2,
                                    borderWidth: 2,
                                    tension: 0.15
                                },
                                {
                                    label: "Temperature Filtered",
                                    data: tempFiltered,
                                    borderColor: "#38bdf8",
                                    backgroundColor: "rgba(56, 189, 248, 0.12)",
                                    pointRadius: 3,
                                    borderWidth: 4,
                                    tension: 0.35
                                }
                            ]
                        },
                        options: chartOptions()
                    });
                } else {
                    tempChart.data.labels = labels;
                    tempChart.data.datasets[0].data = tempRaw;
                    tempChart.data.datasets[1].data = tempFiltered;
                    tempChart.update();
                }

                if (humChart === null) {
                    humChart = new Chart(document.getElementById("humChart"), {
                        type: "line",
                        data: {
                            labels: labels,
                            datasets: [
                                {
                                    label: "Humidity Raw",
                                    data: humRaw,
                                    borderColor: "rgba(251, 113, 133, 0.35)",
                                    backgroundColor: "rgba(251, 113, 133, 0.06)",
                                    borderDash: [7, 5],
                                    pointRadius: 2,
                                    borderWidth: 2,
                                    tension: 0.15
                                },
                                {
                                    label: "Humidity Filtered",
                                    data: humFiltered,
                                    borderColor: "#fb7185",
                                    backgroundColor: "rgba(251, 113, 133, 0.12)",
                                    pointRadius: 3,
                                    borderWidth: 4,
                                    tension: 0.35
                                }
                            ]
                        },
                        options: chartOptions()
                    });
                } else {
                    humChart.data.labels = labels;
                    humChart.data.datasets[0].data = humRaw;
                    humChart.data.datasets[1].data = humFiltered;
                    humChart.update();
                }
            }

            async function updateIncidents() {
                const res = await fetch("/api/incidents");
                const data = await res.json();

                const body = document.getElementById("incidentBody");

                if (data.length === 0) {
                    body.innerHTML = `
                        <tr>
                            <td colspan="7" class="empty-row">No incidents recorded yet</td>
                        </tr>
                    `;
                    return;
                }

                body.innerHTML = data.map(item => {
                    const badgeClass = item.status === "CRITICAL" ? "badge-critical" : "badge-warning";

                    return `
                        <tr>
                            <td>${item.time}</td>
                            <td><span class="badge ${badgeClass}">${item.status}</span></td>
                            <td>${item.message}</td>
                            <td>${item.temperature_raw} °C</td>
                            <td>${item.temperature_filtered} °C</td>
                            <td>${item.anomaly_score}</td>
                            <td>${item.water_value}</td>
                        </tr>
                    `;
                }).join("");
            }

            async function updateAll() {
                await updateCurrentData();
                await updateCharts();
                await updateIncidents();
            }

            updateAll();
            setInterval(updateAll, 2000);
        </script>
    </body>
    </html>
    """

    return render_template_string(html)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
