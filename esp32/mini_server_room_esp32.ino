#include <WiFi.h>
#include <HTTPClient.h>
#include <DHT.h>

// ===================== Wi-Fi Setting =====================
const char* ssid = "YOUR_WIFI_NAME";
const char* password = "YOUR_WIFI_PASSWORD";
String serverURL = "http://YOUR_COMPUTER_IP:5000/data";

// ===================== Pin Setting =====================
#define DHTPIN 4
#define DHTTYPE DHT22

#define WATER_PIN 1      // Analog pin
#define LED_GREEN 5
#define LED_YELLOW 6
#define LED_RED 7
#define BUZZER_PIN 15

DHT dht(DHTPIN, DHTTYPE);

// ===================== Threshold Setting =====================
// ต่ำกว่า 33 = NORMAL
// 33 ถึงต่ำกว่า 36 = WARNING
// 36 ขึ้นไป = CRITICAL
float TEMP_WARNING = 33.0;
float TEMP_CRITICAL = 36.0;

// น้ำรั่ว: ค่า water sensor >= 1000 = CRITICAL
int WATER_THRESHOLD = 1000;

// ถ้าเซนเซอร์น้ำของโฟนแตะน้ำแล้วค่ามากขึ้น ให้ true
// ตอนนี้ตั้งตามที่โฟนบอก: น้ำ 1000 ขึ้น critical
bool WATER_DETECTED_WHEN_HIGH = true;

// ===================== Setup =====================
void setup() {
  Serial.begin(115200);
  delay(1000);

  dht.begin();

  pinMode(WATER_PIN, INPUT);

  pinMode(LED_GREEN, OUTPUT);
  pinMode(LED_YELLOW, OUTPUT);
  pinMode(LED_RED, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);

  allOff();

  Serial.println();
  Serial.println("Mini Server Room Monitoring Started");

  connectWiFi();
}

// ===================== Main Loop =====================
void loop() {
  float temp = dht.readTemperature();
  float hum = dht.readHumidity();
  int waterValue = analogRead(WATER_PIN);

  bool dhtError = isnan(temp) || isnan(hum);

  bool waterLeak = false;
  if (WATER_DETECTED_WHEN_HIGH) {
    waterLeak = waterValue >= WATER_THRESHOLD;
  } else {
    waterLeak = waterValue <= WATER_THRESHOLD;
  }

  String status = "NORMAL";

  if (dhtError) {
    status = "SENSOR_ERROR";
  }
  else if (waterLeak || temp >= TEMP_CRITICAL) {
    status = "CRITICAL";
  }
  else if (temp >= TEMP_WARNING) {
    status = "WARNING";
  }
  else {
    status = "NORMAL";
  }

  showStatus(status);

  printSerialData(temp, hum, waterValue, waterLeak, status, dhtError);

  if (!dhtError) {
    sendDataToServer(temp, hum, waterValue, waterLeak, status);
  }

  delay(2000);
}

// ===================== Wi-Fi Function =====================
void connectWiFi() {
  WiFi.begin(ssid, password);
  Serial.print("Connecting WiFi");

  int retry = 0;
  while (WiFi.status() != WL_CONNECTED && retry < 30) {
    delay(500);
    Serial.print(".");
    retry++;
  }

  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("WiFi Connected!");
    Serial.print("ESP32 IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("WiFi Failed!");
  }
}

// ===================== Send Data to Flask Server =====================
void sendDataToServer(float temp, float hum, int waterValue, bool waterLeak, String status) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi disconnected. Reconnecting...");
    connectWiFi();
    return;
  }

  HTTPClient http;
  http.begin(serverURL);
  http.addHeader("Content-Type", "application/json");

  String json = "{";
  json += "\"temperature\":" + String(temp, 2) + ",";
  json += "\"humidity\":" + String(hum, 2) + ",";
  json += "\"water_value\":" + String(waterValue) + ",";
  json += "\"water_leak\":" + String(waterLeak ? "true" : "false") + ",";
  json += "\"status\":\"" + status + "\"";
  json += "}";

  int httpResponseCode = http.POST(json);

  Serial.print("Send JSON: ");
  Serial.println(json);

  Serial.print("HTTP Response: ");
  Serial.println(httpResponseCode);

  http.end();
}

// ===================== LED + Buzzer Status =====================
void showStatus(String status) {
  allOff();

  if (status == "NORMAL") {
    digitalWrite(LED_GREEN, HIGH);
    noTone(BUZZER_PIN);
  }
  else if (status == "WARNING") {
    digitalWrite(LED_YELLOW, HIGH);
    noTone(BUZZER_PIN);
  }
  else if (status == "CRITICAL") {
    digitalWrite(LED_RED, HIGH);

    // Passive buzzer ต้องใช้ tone()
    tone(BUZZER_PIN, 2000);
  }
  else if (status == "SENSOR_ERROR") {
    noTone(BUZZER_PIN);

    // ไฟแดงกระพริบ ถ้า DHT22 อ่านค่าไม่ได้
    digitalWrite(LED_RED, HIGH);
    delay(200);
    digitalWrite(LED_RED, LOW);
    delay(200);
  }
}

void allOff() {
  digitalWrite(LED_GREEN, LOW);
  digitalWrite(LED_YELLOW, LOW);
  digitalWrite(LED_RED, LOW);
  noTone(BUZZER_PIN);
}

// ===================== Serial Monitor =====================
void printSerialData(float temp, float hum, int waterValue, bool waterLeak, String status, bool dhtError) {
  Serial.println("========== DATA ==========");

  if (dhtError) {
    Serial.println("DHT22 Error: Cannot read temperature/humidity");
  } else {
    Serial.print("Temperature: ");
    Serial.print(temp);
    Serial.println(" °C");

    Serial.print("Humidity: ");
    Serial.print(hum);
    Serial.println(" %");
  }

  Serial.print("Water Sensor Value: ");
  Serial.println(waterValue);

  Serial.print("Water Leak: ");
  Serial.println(waterLeak ? "YES" : "NO");

  Serial.print("System Status: ");
  Serial.println(status);

  Serial.println("==========================");
  Serial.println();
}
