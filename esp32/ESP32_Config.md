# ESP32 dev board Configuration
<font color='yellow'><strong>Please note that this config was designed the specific model used in the project. It may not apply for other ESP32 boards.</strong></font>
## E-Paper
SPI socket to the display using the display ribbon (if using the waveshare ESP32 dev board)
<br>or
* PIN_SPI_SCK   13  for Clock
* PIN_SPI_DIN   14  for MOSI (Data In)
* PIN_SPI_CS    15  for Chip Select
* PIN_SPI_BUSY  25  for Busy signal (INPUT)
* PIN_SPI_RST   26  for Reset
* PIN_SPI_DC    27  for Data/Command
* PIN_SPI_PWR   33  for Power control

## Motor
You can have your wiring but the scripts provided use following pins due to placement of the waveshare board.
* PIN 18 - A1
* PIN 19 - A2
* PIN 5 - B1
* PIN 17 - B2

## Arudio IDE Setting
Please select <strong> ESP32 Dev </strong> from board manager.

### AP
For debugging purposes, a web was setted up by the esp32 for bypassing the serial communication between Raspberry Pi5 and ESP32.