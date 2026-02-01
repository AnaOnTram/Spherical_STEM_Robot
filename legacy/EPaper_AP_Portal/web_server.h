#ifndef WEB_SERVER_H
#define WEB_SERVER_H

#include <WebServer.h>
#include <ArduinoJson.h>
#include "config.h"
#include "epd_driver.h"
#include "image_buffer.h"
#include "web_html.h"
#include "web_css.h"
#include "web_js.h"

// ===========================================
// CORS Headers for API
// ===========================================
void sendCorsHeaders(WebServer* server) {
    server->sendHeader("Access-Control-Allow-Origin", "*");
    server->sendHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    server->sendHeader("Access-Control-Allow-Headers", "Content-Type");
}

// ===========================================
// API Response Helpers
// ===========================================
void sendJsonResponse(WebServer* server, int code, const char* status, const char* message) {
    sendCorsHeaders(server);
    StaticJsonDocument<256> doc;
    doc["status"] = status;
    doc["message"] = message;

    String response;
    serializeJson(doc, response);
    server->send(code, "application/json", response);
}

void sendJsonSuccess(WebServer* server, const char* message) {
    sendJsonResponse(server, 200, "success", message);
}

void sendJsonError(WebServer* server, int code, const char* message) {
    sendJsonResponse(server, code, "error", message);
}

// ===========================================
// Route Handlers
// ===========================================

// GET / - Main page
void handleRoot(WebServer* server) {
    server->send(200, "text/html", HTML_PAGE);
}

// GET /styles.css
void handleCSS(WebServer* server) {
    server->send(200, "text/css", CSS_STYLES);
}

// GET /app.js
void handleJS(WebServer* server) {
    server->send(200, "application/javascript", JS_APP);
}

// GET /api/status - Get display status
void handleApiStatus(WebServer* server) {
    sendCorsHeaders(server);

    StaticJsonDocument<512> doc;
    doc["status"] = "success";
    doc["display"]["width"] = EPD_WIDTH;
    doc["display"]["height"] = EPD_HEIGHT;
    doc["display"]["model"] = "4.2 inch V2";
    doc["buffer"]["size"] = IMAGE_BUFFER_SIZE;
    doc["buffer"]["filled"] = ImageBuffer_GetFillLevel();
    doc["buffer"]["ready"] = ImageBuffer_IsReady();
    doc["api"]["version"] = API_VERSION;
    doc["heap"]["free"] = ESP.getFreeHeap();
    doc["heap"]["total"] = ESP.getHeapSize();

    if (psramFound()) {
        doc["psram"]["free"] = ESP.getFreePsram();
        doc["psram"]["total"] = ESP.getPsramSize();
    }

    String response;
    serializeJson(doc, response);
    server->send(200, "application/json", response);
}

// POST /api/clear - Clear display
void handleApiClear(WebServer* server) {
    EPD_4in2_V2_Clear();
    ImageBuffer_Clear();
    sendJsonSuccess(server, "Display cleared");
}

// Decode nibble from character (matching original byteToStr format)
// Original encodes: low_nibble+'a', high_nibble+'a'
inline uint8_t decodeNibble(char c) {
    if (c >= 'a' && c <= 'p') {
        return c - 'a';
    }
    return 0;
}

// POST /api/upload - Upload image data (1-bit packed format)
// Data is encoded as pairs of chars: each char is 'a' + nibble value
// First char = low nibble, second char = high nibble
void handleApiUpload(WebServer* server) {
    if (!server->hasArg("plain")) {
        sendJsonError(server, 400, "No data received");
        return;
    }

    String body = server->arg("plain");

    // Check if this is the start of a new upload - clear buffer completely
    if (server->hasHeader("X-Upload-Start")) {
        ImageBuffer_Clear();  // Clear to 0x00 (black) - actual image data will overwrite
        memset(ImageBuffer_GetPtr(), 0x00, IMAGE_BUFFER_SIZE);  // Ensure clean slate
        ImageBuffer_Reset();
        Serial.println("Starting new image upload - buffer cleared");
    }

    int len = body.length();
    if (len == 0) {
        sendJsonError(server, 400, "Empty data");
        return;
    }

    // Decode nibble-encoded data (matching original format)
    // Each byte is encoded as 2 chars: low_nibble+'a', high_nibble+'a'
    uint8_t chunk[UPLOAD_CHUNK_SIZE];
    uint16_t chunkLen = 0;

    for (int i = 0; i < len - 1 && chunkLen < UPLOAD_CHUNK_SIZE; i += 2) {
        uint8_t lowNibble = decodeNibble(body[i]);
        uint8_t highNibble = decodeNibble(body[i + 1]);
        chunk[chunkLen++] = lowNibble | (highNibble << 4);
    }

    if (chunkLen > 0) {
        // Debug: print first few bytes of this chunk
        Serial.printf("Chunk data (first 8 bytes): ");
        for (int i = 0; i < min(8, (int)chunkLen); i++) {
            Serial.printf("%02X ", chunk[i]);
        }
        Serial.println();

        ImageBuffer_Receive(chunk, chunkLen);
        Serial.printf("Received %d bytes, total: %d\n", chunkLen, ImageBuffer_GetFillLevel());
    }

    sendCorsHeaders(server);
    StaticJsonDocument<256> doc;
    doc["status"] = "success";
    doc["received"] = chunkLen;
    doc["total"] = ImageBuffer_GetFillLevel();
    doc["complete"] = ImageBuffer_IsReady();

    String response;
    serializeJson(doc, response);
    server->send(200, "application/json", response);
}

// POST /api/display - Display the buffered image
void handleApiDisplay(WebServer* server) {
    if (!ImageBuffer_IsReady()) {
        // If buffer is not full but has some data, use it anyway
        if (ImageBuffer_GetFillLevel() == 0) {
            sendJsonError(server, 400, "No image data in buffer");
            return;
        }
    }

    // Debug: print first 16 bytes of buffer
    uint8_t* buf = ImageBuffer_GetPtr();
    Serial.printf("Buffer before display (first 16 bytes): ");
    for (int i = 0; i < 16; i++) {
        Serial.printf("%02X ", buf[i]);
    }
    Serial.println();

    // Count non-zero bytes
    int nonZero = 0;
    for (int i = 0; i < IMAGE_BUFFER_SIZE; i++) {
        if (buf[i] != 0) nonZero++;
    }
    Serial.printf("Non-zero bytes in buffer: %d of %d\n", nonZero, IMAGE_BUFFER_SIZE);

    EPD_4in2_V2_Display(ImageBuffer_GetPtr());
    sendJsonSuccess(server, "Image displayed");
}

// POST /api/sleep - Put display to sleep
void handleApiSleep(WebServer* server) {
    EPD_4in2_V2_Sleep();
    sendJsonSuccess(server, "Display in sleep mode");
}

// POST /api/test - Display test pattern
void handleApiTest(WebServer* server) {
    ImageBuffer_TestPattern();
    EPD_4in2_V2_Display(ImageBuffer_GetPtr());
    sendJsonSuccess(server, "Test pattern displayed");
}

// OPTIONS handler for CORS preflight
void handleOptions(WebServer* server) {
    sendCorsHeaders(server);
    server->send(204);
}

// Captive portal redirect
void handleCaptive(WebServer* server) {
    server->sendHeader("Location", String("http://") + AP_LOCAL_IP.toString(), true);
    server->send(302, "text/plain", "");
}

// ===========================================
// Server Setup
// ===========================================
void setupWebServer(WebServer* server) {
    // Register custom headers to be collected
    const char* headerKeys[] = {"X-Upload-Start"};
    server->collectHeaders(headerKeys, 1);

    // Main page routes
    server->on("/", HTTP_GET, [server]() { handleRoot(server); });
    server->on("/styles.css", HTTP_GET, [server]() { handleCSS(server); });
    server->on("/app.js", HTTP_GET, [server]() { handleJS(server); });

    // API routes
    server->on("/api/status", HTTP_GET, [server]() { handleApiStatus(server); });
    server->on("/api/clear", HTTP_POST, [server]() { handleApiClear(server); });
    server->on("/api/upload", HTTP_POST, [server]() { handleApiUpload(server); });
    server->on("/api/display", HTTP_POST, [server]() { handleApiDisplay(server); });
    server->on("/api/sleep", HTTP_POST, [server]() { handleApiSleep(server); });
    server->on("/api/test", HTTP_POST, [server]() { handleApiTest(server); });

    // CORS preflight handlers
    server->on("/api/status", HTTP_OPTIONS, [server]() { handleOptions(server); });
    server->on("/api/clear", HTTP_OPTIONS, [server]() { handleOptions(server); });
    server->on("/api/upload", HTTP_OPTIONS, [server]() { handleOptions(server); });
    server->on("/api/display", HTTP_OPTIONS, [server]() { handleOptions(server); });
    server->on("/api/sleep", HTTP_OPTIONS, [server]() { handleOptions(server); });
    server->on("/api/test", HTTP_OPTIONS, [server]() { handleOptions(server); });

    // Captive portal handlers
    server->on("/generate_204", HTTP_GET, [server]() { handleCaptive(server); });
    server->on("/fwlink", HTTP_GET, [server]() { handleCaptive(server); });
    server->on("/hotspot-detect.html", HTTP_GET, [server]() { handleCaptive(server); });
    server->on("/library/test/success.html", HTTP_GET, [server]() { handleCaptive(server); });
    server->on("/connecttest.txt", HTTP_GET, [server]() { handleCaptive(server); });

    // 404 handler
    server->onNotFound([server]() {
        // Redirect unknown requests to main page (captive portal behavior)
        handleCaptive(server);
    });
}

#endif // WEB_SERVER_H
