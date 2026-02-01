/**
 * EPaper AP Portal
 * ESP32 Access Point with Web Portal for 4.2" V2 E-Paper Display
 *
 * Features:
 * - WiFi Access Point for direct client connection
 * - Mobile-friendly web interface
 * - Image upload with crop functionality
 * - Floyd-Steinberg dithering (mono)
 * - RESTful API for programmatic access
 */

#include <WiFi.h>
#include <WebServer.h>
#include <DNSServer.h>

#include "config.h"
#include "epd_driver.h"
#include "web_server.h"
#include "image_buffer.h"

// DNS Server for captive portal
DNSServer dnsServer;
const byte DNS_PORT = 53;

// Web Server
WebServer server(80);

void setup() {
    Serial.begin(115200);
    delay(100);

    Serial.println("\n========================================");
    Serial.println("EPaper AP Portal - Starting...");
    Serial.println("========================================");

    // Initialize E-Paper display pins
    EPD_Init_Pins();
    Serial.println("[OK] EPD pins initialized");

    // Initialize image buffer
    ImageBuffer_Init();
    Serial.println("[OK] Image buffer initialized");

    // Setup WiFi Access Point
    WiFi.mode(WIFI_AP);
    WiFi.softAPConfig(AP_LOCAL_IP, AP_GATEWAY, AP_SUBNET);
    WiFi.softAP(AP_SSID, AP_PASSWORD, AP_CHANNEL, 0, AP_MAX_CONNECTIONS);

    Serial.printf("[OK] Access Point started\n");
    Serial.printf("    SSID: %s\n", AP_SSID);
    Serial.printf("    Password: %s\n", AP_PASSWORD);
    Serial.printf("    IP: %s\n", AP_LOCAL_IP.toString().c_str());

    // Start DNS server for captive portal
    dnsServer.start(DNS_PORT, "*", AP_LOCAL_IP);
    Serial.println("[OK] DNS server started (captive portal)");

    // Setup web server routes
    setupWebServer(&server);
    server.begin();
    Serial.println("[OK] Web server started on port 80");

    Serial.println("========================================");
    Serial.println("Ready! Connect to WiFi and open browser");
    Serial.println("========================================\n");
}

void loop() {
    dnsServer.processNextRequest();
    server.handleClient();
}
