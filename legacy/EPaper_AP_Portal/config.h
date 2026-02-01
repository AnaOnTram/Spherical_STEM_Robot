#ifndef CONFIG_H
#define CONFIG_H

#include <IPAddress.h>

// ===========================================
// WiFi Access Point Configuration
// ===========================================
const char* AP_SSID = "EPaper-Display";
const char* AP_PASSWORD = "";
const int AP_CHANNEL = 1;
const int AP_MAX_CONNECTIONS = 4;

// Static IP configuration
IPAddress AP_LOCAL_IP(192, 168, 4, 1);
IPAddress AP_GATEWAY(192, 168, 4, 1);
IPAddress AP_SUBNET(255, 255, 255, 0);

// ===========================================
// E-Paper Display Configuration (4.2" V2)
// ===========================================
#define EPD_WIDTH   400
#define EPD_HEIGHT  300

// SPI Pin definitions (matching Waveshare ESP32 Driver Board)
#define PIN_SPI_SCK   13  // Clock
#define PIN_SPI_DIN   14  // MOSI (Data In)
#define PIN_SPI_CS    15  // Chip Select
#define PIN_SPI_BUSY  25  // Busy signal (INPUT)
#define PIN_SPI_RST   26  // Reset
#define PIN_SPI_DC    27  // Data/Command
#define PIN_SPI_PWR   33  // Power control (optional)

// ===========================================
// Image Buffer Configuration
// ===========================================
// Buffer size for 4.2" display: 400*300/8 = 15000 bytes
#define IMAGE_BUFFER_SIZE  15000

// Upload chunk size (optimized for memory)
#define UPLOAD_CHUNK_SIZE  4096

// ===========================================
// Web Server Configuration
// ===========================================
#define SERVER_PORT  80

// Maximum upload file size (2MB for safety)
#define MAX_UPLOAD_SIZE  2097152

// API Version
#define API_VERSION  "1.0"

#endif // CONFIG_H
