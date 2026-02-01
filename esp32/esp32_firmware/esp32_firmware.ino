/*
 * ESP32 Combined Firmware for Spherical Robot with Web Portal
 * 
 * Features:
 * - Serial communication with Pi5 using protocol
 * - Motor control (A1, A2, B1, B2 on GPIO 18, 19, 5, 17)
 * - E-Paper display control (4.2" V2 on SPI pins)
 * - WiFi Access Point with Web Portal for image upload
 * - Command protocol: <CMD><PARAM_LENGTH>\n<DATA>\n<CRC>\n
 * 
 * Motor Commands:
 * - MVEL: Motor velocity (left, right, duration_ms)
 * - MSTOP: Emergency stop
 * 
 * Display Commands:
 * - DIMG: Display image (15000 bytes of 1-bit packed data)
 * - DCLEAR: Clear display
 * - DSTATUS: Get display status
 * 
 * System Commands:
 * - SRESET: Soft reset
 * - SHALT: Enter deep sleep
 * - SPING: Heartbeat/ping
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <DNSServer.h>

// ===========================================
// WiFi Access Point Configuration
// ===========================================
const char* AP_SSID = "SphericalRobot";
const char* AP_PASSWORD = "12345678";  // Change this!
const int AP_CHANNEL = 1;
const int AP_MAX_CONNECTIONS = 4;

IPAddress AP_LOCAL_IP(192, 168, 4, 1);
IPAddress AP_GATEWAY(192, 168, 4, 1);
IPAddress AP_SUBNET(255, 255, 255, 0);

// Web Server
WebServer server(80);
DNSServer dnsServer;
const byte DNS_PORT = 53;

// ===========================================
// Motor Configuration
// ===========================================
// Motor A pins (Left Motor) - Updated pinout
#define MOTOR_A1 18
#define MOTOR_A2 19

// Motor B pins (Right Motor) - Updated pinout
#define MOTOR_B1 5
#define MOTOR_B2 17

// PWM properties
#define PWM_FREQ 5000
#define PWM_RESOLUTION 8

// ===========================================
// E-Paper Display Configuration (4.2" V2)
// ===========================================
#define EPD_WIDTH   400
#define EPD_HEIGHT  300
#define IMAGE_BUFFER_SIZE  15000  // 400*300/8

// SPI Pin definitions
#define PIN_SPI_SCK   13  // Clock
#define PIN_SPI_DIN   14  // MOSI (Data In)
#define PIN_SPI_CS    15  // Chip Select
#define PIN_SPI_BUSY  25  // Busy signal (INPUT)
#define PIN_SPI_RST   26  // Reset
#define PIN_SPI_DC    27  // Data/Command
#define PIN_SPI_PWR   33  // Power control

// ===========================================
// Protocol Configuration
// ===========================================
#define SERIAL_BAUD 115200
#define MAX_COMMAND_SIZE 15050  // Max data size (image + overhead)
#define CMD_TIMEOUT_MS 5000

// CRC-CCITT polynomial
#define CRC_POLYNOMIAL 0x1021

// ===========================================
// Global Variables
// ===========================================
// Image buffer
uint8_t* imageBuffer = nullptr;
uint16_t bufferIndex = 0;
bool bufferReady = false;
bool uploadInProgress = false;

// Command buffer
char cmdBuffer[16];
uint8_t dataBuffer[MAX_COMMAND_SIZE];
int cmdIndex = 0;
int dataIndex = 0;
int expectedDataLength = 0;
bool receivingData = false;

// Motor state
int motorSpeed = 200;
bool motorsRunning = false;
unsigned long motorStopTime = 0;

// ===========================================
// HTML Web Interface
// ===========================================
const char* HTML_PAGE = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Spherical Robot - E-Paper Control</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a2e;
            color: #fff;
            padding: 20px;
            max-width: 600px;
            margin: 0 auto;
        }
        h1 {
            text-align: center;
            color: #00d4ff;
            margin-bottom: 30px;
            font-size: 24px;
        }
        .card {
            background: #16213e;
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 20px;
            border: 1px solid #0f3460;
        }
        h2 {
            color: #e94560;
            margin-bottom: 15px;
            font-size: 18px;
        }
        .upload-area {
            border: 2px dashed #0f3460;
            border-radius: 10px;
            padding: 40px;
            text-align: center;
            cursor: pointer;
            transition: all 0.3s;
            margin-bottom: 15px;
        }
        .upload-area:hover {
            border-color: #00d4ff;
            background: rgba(0, 212, 255, 0.05);
        }
        .upload-area.dragover {
            border-color: #00d4ff;
            background: rgba(0, 212, 255, 0.1);
        }
        .upload-icon {
            font-size: 48px;
            margin-bottom: 10px;
        }
        .upload-text {
            color: #888;
            font-size: 14px;
        }
        input[type="file"] {
            display: none;
        }
        .btn {
            background: #0f3460;
            color: #fff;
            border: none;
            padding: 12px 24px;
            border-radius: 5px;
            cursor: pointer;
            font-size: 14px;
            width: 100%;
            transition: background 0.3s;
        }
        .btn:hover {
            background: #1a5490;
        }
        .btn:disabled {
            background: #333;
            cursor: not-allowed;
        }
        .btn-primary {
            background: #00d4ff;
            color: #000;
        }
        .btn-primary:hover {
            background: #33ddff;
        }
        .btn-danger {
            background: #e94560;
        }
        .btn-danger:hover {
            background: #ff6b6b;
        }
        .status {
            padding: 10px;
            border-radius: 5px;
            margin-top: 10px;
            font-size: 14px;
            display: none;
        }
        .status.success {
            display: block;
            background: rgba(46, 204, 113, 0.2);
            color: #2ecc71;
        }
        .status.error {
            display: block;
            background: rgba(231, 76, 60, 0.2);
            color: #e74c3c;
        }
        .status.uploading {
            display: block;
            background: rgba(0, 212, 255, 0.2);
            color: #00d4ff;
        }
        .preview {
            max-width: 100%;
            border-radius: 5px;
            margin-top: 15px;
            display: none;
        }
        .info {
            background: rgba(0, 212, 255, 0.1);
            border-left: 3px solid #00d4ff;
            padding: 10px;
            margin-top: 15px;
            font-size: 12px;
            color: #888;
        }
        .motor-controls {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 10px;
            margin-top: 15px;
        }
        .motor-btn {
            padding: 20px;
            font-size: 24px;
            background: #0f3460;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            color: #fff;
        }
        .motor-btn:hover {
            background: #1a5490;
        }
        .motor-btn.stop {
            background: #e94560;
        }
        .motor-btn.stop:hover {
            background: #ff6b6b;
        }
    </style>
</head>
<body>
    <h1>🤖 Spherical Robot Control</h1>
    
    <div class="card">
        <h2>📷 Image Upload</h2>
        <div class="upload-area" id="uploadArea" onclick="document.getElementById('fileInput').click()">
            <div class="upload-icon">📁</div>
            <div class="upload-text">
                Click or drag image here<br>
                <small>Supports JPG, PNG, BMP (will be converted to 400x300 B&W)</small>
            </div>
        </div>
        <input type="file" id="fileInput" accept="image/*" onchange="handleFileSelect(event)">
        <img id="preview" class="preview">
        <button class="btn btn-primary" id="uploadBtn" onclick="uploadImage()" disabled>📤 Upload to Display</button>
        <button class="btn btn-danger" onclick="clearDisplay()">🗑 Clear Display</button>
        <div id="status" class="status"></div>
        <div class="info">
            <strong>Tip:</strong> Images will be automatically resized to 400x300 and converted to black & white using Floyd-Steinberg dithering for best results on E-Paper display.
        </div>
    </div>
    
    <div class="card">
        <h2>🎮 Motor Control</h2>
        <div class="motor-controls">
            <div></div>
            <button class="motor-btn" onclick="sendCommand('forward')">⬆</button>
            <div></div>
            <button class="motor-btn" onclick="sendCommand('left')">⬅</button>
            <button class="motor-btn stop" onclick="sendCommand('stop')">⏹</button>
            <button class="motor-btn" onclick="sendCommand('right')">➡</button>
            <div></div>
            <button class="motor-btn" onclick="sendCommand('backward')">⬇</button>
            <div></div>
        </div>
    </div>
    
    <script>
        let selectedFile = null;
        let canvas = document.createElement('canvas');
        let ctx = canvas.getContext('2d');
        
        // Drag and drop
        const uploadArea = document.getElementById('uploadArea');
        
        uploadArea.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadArea.classList.add('dragover');
        });
        
        uploadArea.addEventListener('dragleave', () => {
            uploadArea.classList.remove('dragover');
        });
        
        uploadArea.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadArea.classList.remove('dragover');
            const files = e.dataTransfer.files;
            if (files.length > 0) {
                handleFile(files[0]);
            }
        });
        
        function handleFileSelect(event) {
            const file = event.target.files[0];
            if (file) {
                handleFile(file);
            }
        }
        
        function handleFile(file) {
            selectedFile = file;
            
            // Show preview
            const reader = new FileReader();
            reader.onload = (e) => {
                const img = new Image();
                img.onload = () => {
                    document.getElementById('preview').src = e.target.result;
                    document.getElementById('preview').style.display = 'block';
                    document.getElementById('uploadBtn').disabled = false;
                };
                img.src = e.target.result;
            };
            reader.readAsDataURL(file);
            
            showStatus('Image selected: ' + file.name, 'uploading');
        }
        
        function uploadImage() {
            if (!selectedFile) return;
            
            const statusEl = document.getElementById('status');
            statusEl.className = 'status uploading';
            statusEl.textContent = 'Processing image...';
            statusEl.style.display = 'block';
            
            const reader = new FileReader();
            reader.onload = (e) => {
                const img = new Image();
                img.onload = () => {
                    // Process image
                    processAndUpload(img);
                };
                img.src = e.target.result;
            };
            reader.readAsDataURL(selectedFile);
        }
        
        function processAndUpload(img) {
            // Resize to 400x300
            canvas.width = 400;
            canvas.height = 300;
            
            // Draw and resize
            ctx.drawImage(img, 0, 0, 400, 300);
            
            // Get image data
            const imageData = ctx.getImageData(0, 0, 400, 300);
            const data = imageData.data;
            
            // Convert to 1-bit with Floyd-Steinberg dithering
            const binaryData = floydSteinbergDither(data, 400, 300);
            
            // Upload to server - send raw binary data
            fetch('/upload', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/octet-stream',
                    'Content-Length': binaryData.length
                },
                body: binaryData
            })
            .then(response => response.text())
            .then(result => {
                showStatus('✓ ' + result, 'success');
            })
            .catch(error => {
                showStatus('✗ Error: ' + error.message, 'error');
            });
        }
        
        function floydSteinbergDither(data, width, height) {
            // Convert to grayscale first
            const gray = new Float32Array(width * height);
            for (let i = 0; i < width * height; i++) {
                const r = data[i * 4];
                const g = data[i * 4 + 1];
                const b = data[i * 4 + 2];
                gray[i] = r * 0.299 + g * 0.587 + b * 0.114;
            }
            
            // Apply Floyd-Steinberg dithering
            for (let y = 0; y < height; y++) {
                for (let x = 0; x < width; x++) {
                    const idx = y * width + x;
                    const oldPixel = gray[idx];
                    const newPixel = oldPixel < 128 ? 0 : 255;
                    const error = oldPixel - newPixel;
                    gray[idx] = newPixel;
                    
                    // Distribute error
                    if (x + 1 < width) {
                        gray[idx + 1] += error * 7 / 16;
                    }
                    if (x - 1 >= 0 && y + 1 < height) {
                        gray[idx + width - 1] += error * 3 / 16;
                    }
                    if (y + 1 < height) {
                        gray[idx + width] += error * 5 / 16;
                    }
                    if (x + 1 < width && y + 1 < height) {
                        gray[idx + width + 1] += error * 1 / 16;
                    }
                }
            }
            
            // Pack into bytes (1 bit per pixel, 0=black, 1=white)
            const packed = new Uint8Array(width * height / 8);
            for (let i = 0; i < width * height; i++) {
                const byteIdx = Math.floor(i / 8);
                const bitIdx = 7 - (i % 8);
                if (gray[i] < 128) {
                    packed[byteIdx] &= ~(1 << bitIdx);
                } else {
                    packed[byteIdx] |= (1 << bitIdx);
                }
            }
            
            return packed;
        }
        
        function clearDisplay() {
            fetch('/clear', {method: 'POST'})
            .then(response => response.text())
            .then(result => {
                showStatus('✓ ' + result, 'success');
                document.getElementById('preview').style.display = 'none';
                document.getElementById('uploadBtn').disabled = true;
                selectedFile = null;
            })
            .catch(error => {
                showStatus('✗ Error: ' + error.message, 'error');
            });
        }
        
        function sendCommand(cmd) {
            fetch('/motor?cmd=' + cmd, {method: 'POST'})
            .then(response => response.text())
            .then(result => {
                console.log('Motor command:', cmd, result);
            })
            .catch(error => {
                console.error('Motor error:', error);
            });
        }
        
        function showStatus(message, type) {
            const statusEl = document.getElementById('status');
            statusEl.className = 'status ' + type;
            statusEl.textContent = message;
            statusEl.style.display = 'block';
            
            if (type === 'success' || type === 'error') {
                setTimeout(() => {
                    statusEl.style.display = 'none';
                }, 5000);
            }
        }
    </script>
</body>
</html>
)rawliteral";

// ===========================================
// CRC Calculation
// ===========================================
uint16_t calculateCRC(const uint8_t* data, int length) {
    uint16_t crc = 0xFFFF;
    for (int i = 0; i < length; i++) {
        crc ^= data[i] << 8;
        for (int j = 0; j < 8; j++) {
            if (crc & 0x8000) {
                crc = (crc << 1) ^ CRC_POLYNOMIAL;
            } else {
                crc <<= 1;
            }
            crc &= 0xFFFF;
        }
    }
    return crc;
}

// ===========================================
// Serial Protocol Functions
// ===========================================
void sendResponse(const char* status, const char* message) {
    Serial.print(status);
    Serial.print(strlen(message));
    Serial.print("\n");
    Serial.print(message);
    Serial.print("\n");
}

void sendOK(const char* message = "") {
    sendResponse("OK", message);
}

void sendError(const char* message) {
    sendResponse("ERR", message);
}

// ===========================================
// Motor Control Functions
// ===========================================
// Forward declaration
void stopMotors();

void initMotors() {
    // Configure PWM for each pin (ESP32 core 3.x API)
    ledcAttach(MOTOR_A1, PWM_FREQ, PWM_RESOLUTION);
    ledcAttach(MOTOR_A2, PWM_FREQ, PWM_RESOLUTION);
    ledcAttach(MOTOR_B1, PWM_FREQ, PWM_RESOLUTION);
    ledcAttach(MOTOR_B2, PWM_FREQ, PWM_RESOLUTION);
    
    stopMotors();
    Serial.println("[OK] Motors initialized on pins 18, 19, 5, 17");
}

void setMotorSpeed(int left, int right, int duration_ms) {
    // Constrain values
    left = constrain(left, -255, 255);
    right = constrain(right, -255, 255);
    
    // Set motor A (left)
    if (left >= 0) {
        ledcWrite(MOTOR_A1, left);
        ledcWrite(MOTOR_A2, 0);
    } else {
        ledcWrite(MOTOR_A1, 0);
        ledcWrite(MOTOR_A2, -left);
    }
    
    // Set motor B (right)
    if (right >= 0) {
        ledcWrite(MOTOR_B1, right);
        ledcWrite(MOTOR_B2, 0);
    } else {
        ledcWrite(MOTOR_B1, 0);
        ledcWrite(MOTOR_B2, -right);
    }
    
    // Set stop time if duration specified
    if (duration_ms > 0) {
        motorStopTime = millis() + duration_ms;
        motorsRunning = true;
    } else {
        motorStopTime = 0;
        motorsRunning = (left != 0 || right != 0);
    }
}

void stopMotors() {
    ledcWrite(MOTOR_A1, 0);
    ledcWrite(MOTOR_A2, 0);
    ledcWrite(MOTOR_B1, 0);
    ledcWrite(MOTOR_B2, 0);
    motorsRunning = false;
    motorStopTime = 0;
}

void checkMotorTimeout() {
    if (motorsRunning && motorStopTime > 0 && millis() >= motorStopTime) {
        stopMotors();
    }
}

// ===========================================
// E-Paper Display Functions
// ===========================================
void initEPD() {
    // Initialize pins
    pinMode(PIN_SPI_BUSY, INPUT);
    pinMode(PIN_SPI_RST, OUTPUT);
    pinMode(PIN_SPI_DC, OUTPUT);
    pinMode(PIN_SPI_SCK, OUTPUT);
    pinMode(PIN_SPI_DIN, OUTPUT);
    pinMode(PIN_SPI_CS, OUTPUT);
    pinMode(PIN_SPI_PWR, OUTPUT);

    digitalWrite(PIN_SPI_CS, HIGH);
    digitalWrite(PIN_SPI_PWR, HIGH);
    digitalWrite(PIN_SPI_SCK, LOW);
    
    Serial.println("[OK] EPD pins initialized");
}

void EPD_SPI_Transfer(uint8_t data) {
    digitalWrite(PIN_SPI_CS, LOW);
    
    for (int i = 0; i < 8; i++) {
        digitalWrite(PIN_SPI_DIN, (data & 0x80) ? HIGH : LOW);
        data <<= 1;
        digitalWrite(PIN_SPI_SCK, HIGH);
        digitalWrite(PIN_SPI_SCK, LOW);
    }
    
    digitalWrite(PIN_SPI_CS, HIGH);
}

void EPD_SendCommand(uint8_t cmd) {
    digitalWrite(PIN_SPI_DC, LOW);
    EPD_SPI_Transfer(cmd);
}

void EPD_SendData(uint8_t data) {
    digitalWrite(PIN_SPI_DC, HIGH);
    EPD_SPI_Transfer(data);
}

void EPD_WaitUntilIdle_high() {
    while (digitalRead(PIN_SPI_BUSY) == 1) {
        delay(100);
    }
}

void EPD_Reset() {
    digitalWrite(PIN_SPI_RST, HIGH);
    delay(200);
    digitalWrite(PIN_SPI_RST, LOW);
    delay(2);
    digitalWrite(PIN_SPI_RST, HIGH);
    delay(200);
}

void EPD_4in2_V2_Init() {
    EPD_Reset();
    EPD_WaitUntilIdle_high();
    
    EPD_SendCommand(0x12);  // SW Reset
    EPD_WaitUntilIdle_high();
    
    EPD_SendCommand(0x21);
    EPD_SendData(0x40);
    EPD_SendData(0x00);
    
    EPD_SendCommand(0x3C);
    EPD_SendData(0x05);
    
    EPD_SendCommand(0x11);
    EPD_SendData(0x03);
    
    EPD_SendCommand(0x44);
    EPD_SendData(0x00);
    EPD_SendData(0x31);
    
    EPD_SendCommand(0x45);
    EPD_SendData(0x00);
    EPD_SendData(0x00);
    EPD_SendData(0x2B);
    EPD_SendData(0x01);
    
    EPD_SendCommand(0x4E);
    EPD_SendData(0x00);
    
    EPD_SendCommand(0x4F);
    EPD_SendData(0x00);
    EPD_SendData(0x00);
    
    // Clear display with white
    EPD_SendCommand(0x24);
    for (int i = 0; i < IMAGE_BUFFER_SIZE; i++) {
        EPD_SendData(0xFF);
    }
    
    // Trigger refresh
    EPD_SendCommand(0x22);
    EPD_SendData(0xF7);
    EPD_SendCommand(0x20);
    EPD_WaitUntilIdle_high();
    
    // Ready for new data
    EPD_SendCommand(0x24);
}

void EPD_4in2_V2_Show() {
    EPD_SendCommand(0x22);
    EPD_SendData(0xF7);
    EPD_SendCommand(0x20);
    EPD_WaitUntilIdle_high();
    
    // Enter deep sleep
    EPD_SendCommand(0x10);
    EPD_SendData(0x01);
}

void EPD_4in2_V2_Display(const uint8_t* image) {
    EPD_Reset();
    EPD_WaitUntilIdle_high();
    
    EPD_SendCommand(0x12);
    EPD_WaitUntilIdle_high();
    
    EPD_SendCommand(0x21);
    EPD_SendData(0x40);
    EPD_SendData(0x00);
    
    EPD_SendCommand(0x3C);
    EPD_SendData(0x05);
    
    EPD_SendCommand(0x11);
    EPD_SendData(0x03);
    
    EPD_SendCommand(0x44);
    EPD_SendData(0x00);
    EPD_SendData(0x31);
    
    EPD_SendCommand(0x45);
    EPD_SendData(0x00);
    EPD_SendData(0x00);
    EPD_SendData(0x2B);
    EPD_SendData(0x01);
    
    EPD_SendCommand(0x4E);
    EPD_SendData(0x00);
    
    EPD_SendCommand(0x4F);
    EPD_SendData(0x00);
    EPD_SendData(0x00);
    
    // Write image data
    EPD_SendCommand(0x24);
    for (int i = 0; i < IMAGE_BUFFER_SIZE; i++) {
        EPD_SendData(image[i]);
    }
    
    // Trigger display refresh
    EPD_4in2_V2_Show();
}

void EPD_4in2_V2_Clear() {
    EPD_4in2_V2_Init();
}

// ===========================================
// Image Buffer Functions
// ===========================================
void initImageBuffer() {
    // Try to allocate in PSRAM first
    if (psramFound()) {
        imageBuffer = (uint8_t*)ps_malloc(IMAGE_BUFFER_SIZE);
        Serial.println("[OK] Image buffer in PSRAM");
    } else {
        imageBuffer = (uint8_t*)malloc(IMAGE_BUFFER_SIZE);
        Serial.println("[OK] Image buffer in RAM");
    }
    
    if (imageBuffer == nullptr) {
        Serial.println("[ERR] Failed to allocate image buffer!");
        return;
    }
    
    memset(imageBuffer, 0xFF, IMAGE_BUFFER_SIZE);
    bufferIndex = 0;
    bufferReady = false;
}

void clearImageBuffer() {
    if (imageBuffer != nullptr) {
        memset(imageBuffer, 0xFF, IMAGE_BUFFER_SIZE);
    }
    bufferIndex = 0;
    bufferReady = false;
}

// ===========================================
// Web Server Handlers
// ===========================================
void handleRoot() {
    server.send(200, "text/html", HTML_PAGE);
}

void handleUpload() {
    if (server.method() != HTTP_POST) {
        server.send(405, "text/plain", "Method Not Allowed");
        return;
    }
    
    // Get content length from header
    String contentLengthStr = server.header("Content-Length");
    int contentLength = contentLengthStr.length() > 0 ? contentLengthStr.toInt() : 0;
    
    if (contentLength != IMAGE_BUFFER_SIZE) {
        char msg[128];
        snprintf(msg, sizeof(msg), "Invalid content length: expected %d bytes, got %d bytes", 
                 IMAGE_BUFFER_SIZE, contentLength);
        server.send(400, "text/plain", msg);
        return;
    }
    
    // Read raw binary data directly
    WiFiClient client = server.client();
    int bytesRead = 0;
    
    // Wait for data with timeout
    unsigned long startTime = millis();
    while (bytesRead < IMAGE_BUFFER_SIZE && millis() - startTime < 10000) {
        while (client.available() && bytesRead < IMAGE_BUFFER_SIZE) {
            imageBuffer[bytesRead++] = client.read();
        }
        if (bytesRead < IMAGE_BUFFER_SIZE) {
            delay(10);
        }
    }
    
    if (bytesRead != IMAGE_BUFFER_SIZE) {
        char msg[128];
        snprintf(msg, sizeof(msg), "Incomplete data: expected %d bytes, received %d bytes", 
                 IMAGE_BUFFER_SIZE, bytesRead);
        server.send(400, "text/plain", msg);
        return;
    }
    
    bufferReady = true;
    
    // Display the image
    EPD_4in2_V2_Display(imageBuffer);
    
    server.send(200, "text/plain", "Image uploaded and displayed successfully");
}

void handleClear() {
    clearImageBuffer();
    EPD_4in2_V2_Clear();
    server.send(200, "text/plain", "Display cleared");
}

void handleMotor() {
    if (!server.hasArg("cmd")) {
        server.send(400, "text/plain", "No command specified");
        return;
    }
    
    String cmd = server.arg("cmd");
    
    if (cmd == "forward") {
        setMotorSpeed(200, 200, 1000);
        server.send(200, "text/plain", "Moving forward");
    } else if (cmd == "backward") {
        setMotorSpeed(-200, -200, 1000);
        server.send(200, "text/plain", "Moving backward");
    } else if (cmd == "left") {
        setMotorSpeed(-200, 200, 1000);
        server.send(200, "text/plain", "Turning left");
    } else if (cmd == "right") {
        setMotorSpeed(200, -200, 1000);
        server.send(200, "text/plain", "Turning right");
    } else if (cmd == "stop") {
        stopMotors();
        server.send(200, "text/plain", "Stopped");
    } else {
        server.send(400, "text/plain", "Unknown command");
    }
}

// ===========================================
// Command Handlers (Serial Protocol)
// ===========================================
void handleMVEL(const uint8_t* data, int length) {
    if (length != 6) {
        sendError("Invalid MVEL data length");
        return;
    }
    
    // Unpack: left (int16), right (int16), duration (uint16)
    int16_t left = data[0] | (data[1] << 8);
    int16_t right = data[2] | (data[3] << 8);
    uint16_t duration = data[4] | (data[5] << 8);
    
    setMotorSpeed(left, right, duration);
    
    char msg[64];
    snprintf(msg, sizeof(msg), "MVEL L:%d R:%d D:%d", left, right, duration);
    sendOK(msg);
}

void handleMSTOP() {
    stopMotors();
    sendOK("Motors stopped");
}

void handleDIMG(const uint8_t* data, int length) {
    if (length != IMAGE_BUFFER_SIZE) {
        char msg[64];
        snprintf(msg, sizeof(msg), "Invalid image size: expected %d, got %d", 
                 IMAGE_BUFFER_SIZE, length);
        sendError(msg);
        return;
    }
    
    // Copy data to image buffer
    memcpy(imageBuffer, data, IMAGE_BUFFER_SIZE);
    bufferReady = true;
    
    // Display the image
    EPD_4in2_V2_Display(imageBuffer);
    
    sendOK("Image displayed");
}

void handleDCLEAR() {
    clearImageBuffer();
    EPD_4in2_V2_Clear();
    sendOK("Display cleared");
}

void handleDSTATUS() {
    char msg[64];
    snprintf(msg, sizeof(msg), "Buffer:%d Ready:%d", 
             bufferIndex, bufferReady ? 1 : 0);
    sendOK(msg);
}

void handleSRESET() {
    stopMotors();
    clearImageBuffer();
    sendOK("System reset");
    delay(100);
    ESP.restart();
}

void handleSHALT() {
    stopMotors();
    sendOK("Entering deep sleep");
    delay(100);
    esp_deep_sleep_start();
}

void handleSPING() {
    sendOK("pong");
}

// ===========================================
// Protocol Parser
// ===========================================
void processCommand(const char* cmd, const uint8_t* data, int dataLength, const char* crcStr) {
    // Verify CRC
    uint16_t expectedCRC = calculateCRC(data, dataLength);
    uint16_t receivedCRC = (uint16_t)strtol(crcStr, nullptr, 16);
    
    if (expectedCRC != receivedCRC) {
        char msg[64];
        snprintf(msg, sizeof(msg), "CRC mismatch: expected %04X, got %04X", 
                 expectedCRC, receivedCRC);
        sendError(msg);
        return;
    }
    
    // Dispatch command
    if (strcmp(cmd, "MVEL") == 0) {
        handleMVEL(data, dataLength);
    } else if (strcmp(cmd, "MSTOP") == 0) {
        handleMSTOP();
    } else if (strcmp(cmd, "DIMG") == 0) {
        handleDIMG(data, dataLength);
    } else if (strcmp(cmd, "DCLEAR") == 0) {
        handleDCLEAR();
    } else if (strcmp(cmd, "DSTATUS") == 0) {
        handleDSTATUS();
    } else if (strcmp(cmd, "SRESET") == 0) {
        handleSRESET();
    } else if (strcmp(cmd, "SHALT") == 0) {
        handleSHALT();
    } else if (strcmp(cmd, "SPING") == 0) {
        handleSPING();
    } else {
        char msg[32];
        snprintf(msg, sizeof(msg), "Unknown command: %s", cmd);
        sendError(msg);
    }
}

void parseSerialData() {
    while (Serial.available() > 0) {
        char c = Serial.read();
        
        if (!receivingData) {
            // Reading command header: CMD<LENGTH>\n
            if (c == '\n') {
                cmdBuffer[cmdIndex] = '\0';
                
                // Parse command and length
                char* cmdEnd = cmdBuffer;
                while (*cmdEnd && !isdigit(*cmdEnd)) cmdEnd++;
                
                if (*cmdEnd) {
                    expectedDataLength = atoi(cmdEnd);
                    *cmdEnd = '\0';
                    
                    receivingData = true;
                    dataIndex = 0;
                }
                cmdIndex = 0;
            } else if (cmdIndex < sizeof(cmdBuffer) - 1) {
                cmdBuffer[cmdIndex++] = c;
            }
        } else {
            // Reading data + CRC
            if (dataIndex < expectedDataLength) {
                dataBuffer[dataIndex++] = c;
            } else if (c == '\n') {
                // Read CRC
                char crcBuffer[8];
                int crcIndex = 0;
                while (Serial.available() > 0 && crcIndex < 5) {
                    char crcChar = Serial.read();
                    if (crcChar == '\n') break;
                    crcBuffer[crcIndex++] = crcChar;
                }
                crcBuffer[crcIndex] = '\0';
                
                processCommand(cmdBuffer, dataBuffer, expectedDataLength, crcBuffer);
                
                receivingData = false;
                cmdIndex = 0;
                dataIndex = 0;
                expectedDataLength = 0;
            }
        }
    }
}

// ===========================================
// Setup and Loop
// ===========================================
void setup() {
    Serial.begin(SERIAL_BAUD);
    delay(1000);
    
    Serial.println("\n========================================");
    Serial.println("Spherical Robot ESP32 Firmware");
    Serial.println("Motor + E-Paper + Web Portal");
    Serial.println("========================================");
    
    // Initialize subsystems
    initMotors();
    initEPD();
    initImageBuffer();
    
    // Initialize display
    EPD_4in2_V2_Init();
    
    // Setup WiFi Access Point
    WiFi.mode(WIFI_AP);
    WiFi.softAPConfig(AP_LOCAL_IP, AP_GATEWAY, AP_SUBNET);
    WiFi.softAP(AP_SSID, AP_PASSWORD, AP_CHANNEL, 0, AP_MAX_CONNECTIONS);
    
    Serial.println("\n[OK] WiFi Access Point started");
    Serial.printf("    SSID: %s\n", AP_SSID);
    Serial.printf("    Password: %s\n", AP_PASSWORD);
    Serial.printf("    IP: %s\n", AP_LOCAL_IP.toString().c_str());
    
    // Start DNS server for captive portal
    dnsServer.start(DNS_PORT, "*", AP_LOCAL_IP);
    Serial.println("[OK] DNS server started");
    
    // Setup web server routes
    server.on("/", HTTP_GET, handleRoot);
    server.on("/upload", HTTP_POST, handleUpload);
    server.on("/clear", HTTP_POST, handleClear);
    server.on("/motor", HTTP_POST, handleMotor);
    server.begin();
    Serial.println("[OK] Web server started on port 80");
    
    Serial.println("\n========================================");
    Serial.println("Ready!");
    Serial.println("Serial: <CMD><LEN>\\n<DATA>\\n<CRC>\\n");
    Serial.println("Web: Connect to WiFi and open browser");
    Serial.println("========================================\n");
}

void loop() {
    // Handle DNS and web server
    dnsServer.processNextRequest();
    server.handleClient();
    
    // Check for serial commands
    parseSerialData();
    
    // Check motor timeout
    checkMotorTimeout();
    
    // Small delay to prevent tight loop
    delay(1);
}
