# Spherical Robot
A consumer level product that serves Monitoring, Educating, and entertaining purposes. 

## Product Composition
Control Unit
- Raspberry Pi 5 4GB
- [Waveshare ESP32 Dev Module](https://www.waveshare.com/wiki/E-Paper_ESP32_Driver_Board?srsltid=AfmBOoop9bSaBC89VYuwDVCXnrgW4Hdwje3rb5v1EfFjhawVMBZJm_nU)
Power Unit
- 2 18650 battery (serially connected)
- Step down converter (5v output for rpi5)
Motion Unit
- 2 JGA25-370 Geared Motor
- L298 Motor Drive Board
HID
- [OV5695 + Dual Mics USB camera microphone module](https://www.aliexpress.com/item/1005010376859139.html)
- [USB speaker (single channel)](https://category.yahboom.net/products/usb-sound-card?srsltid=AfmBOooxWnPrkpD9II-BqyXx3Q37XZPUlxb42uE3zIQXwkVqGKZoL-H0)
- [Waveshare 4.2 inch e-ink v2](https://www.waveshare.com/wiki/4.2inch_e-Paper_Module_Manual#Working_With_Raspberry_Pi)

## General Control Principle
ESP32 for motion related control (wired to the motor drive board) for motion control and drive the e-ink through SPI interface. RPI5 for higher level control like computer vision, video encoding and streaming, audio playing, and sound detection. The commnuication between rpi5 and esp can be serial (RX & TX) or through curling the api (need set up at the esp32). RPI5 should provide API that can accessed by the user-end app (which allows the user to control the movement of the robot, start different functions).

### Function specification
1. Omni-movement of the spherical robot.
2. Remote monitoring (video and audio streaming).
3. Sound detection/classification for crying (YAMNet as a alternative model) and alarm trigering.
4. Computer-vision enabled human-machine interaction (finger gesture detection).
5. Deliverying STEM education for pre-school children.