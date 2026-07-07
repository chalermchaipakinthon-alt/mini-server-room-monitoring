# 🌡️ Mini Server Room Monitoring

Smart room monitoring dashboard with sensor filtering, anomaly detection, and Telegram alerts.

<p align="center">
  <img src="https://img.shields.io/badge/ESP32-Sensor%20Node-303030?style=for-the-badge&logo=espressif&logoColor=white" />
  <img src="https://img.shields.io/badge/Flask-Dashboard-000000?style=for-the-badge&logo=flask&logoColor=white" />
  <img src="https://img.shields.io/badge/Chart.js-Visualization-FF6384?style=for-the-badge&logo=chartdotjs&logoColor=white" />
  <img src="https://img.shields.io/badge/Telegram-Alert-26A5E4?style=for-the-badge&logo=telegram&logoColor=white" />
</p>

---

## 🚀 Project Overview

This project turns a normal room monitoring system into a smarter system by adding sensor filtering, dashboard visualization, anomaly detection, incident logging, and Telegram alerts.

```txt
ESP32 + Sensors
      ↓
HTTP JSON
      ↓
Flask Server
      ↓
Moving Average Filter + Z-score Anomaly Detection
      ↓
Dashboard + Incident Log + Telegram Alert
