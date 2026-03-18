# WonderBall
Devoting in building stories with both wonderful childs and their loving parents.

## Project Components
- [x] [Raspberry Pi 5 4GB](https://www.digikey.com/en/products/detail/raspberry-pi/SC1431/21658261)
- [x] [Pi 5 stock Aluminum heatsink](https://www.digikey.com/en/products/detail/raspberry-pi/SC1148/21658255)
- [x] [ESP32](https://www.waveshare.com/e-paper-esp32-driver-board.htm?srsltid=AfmBOooVDNgk-HFykA8Ws8oFT9lrV8T0cf_o-iDSYbRKhV2WtcnhanRq)
- [x] [4.2" E-ink Display](https://www.waveshare.com/pico-epaper-4.2.htm)
- [x] [5MP USB Camera module with dual mics](https://www.aliexpress.com/item/1005010376859139.html)
- [x] [USB DAC + Mono Channel Speaker](https://category.yahboom.net/products/usb-sound-card?srsltid=AfmBOooxWnPrkpD9II-BqyXx3Q37XZPUlxb42uE3zIQXwkVqGKZoL-H0)
- [x] L298 Motor driver board
- [x] JGA25-370 Geared Motor (two)
- [x] 18650 Battery (two)
- [x] 18650 Battery Compartment
- [x] Stepdown Converter

## Functionalities
- [x] Monitoring: Realtime video (mjpeg) and audio streaming[^1]
- [x] Bidirectional Communication: Client side can (record) send audio to the device and playback at the device. 
- [x] Motion Control: Remote control of dual motors.[^2]
- [x] Display Updates: Rendering the 4.2” e-ink through either upload image (400*300) or input text (string).[^2]
- [x] Sound Detection: Background sound-inference for crying detection. 
- [x] Local LLM Conversation: ASR (fast Whisper) --> LLM Inference (llama.cpp Qwen3.5 0.9B) --> TTS (piper). Inspired by <i>[PiSugar/whisplay-ai-chatbot](https://github.com/PiSugar/whisplay-ai-chatbot.git) </i> project.
- [x] API call: Setted up [API](API.md) server for front-end UI/UX development.
- [x] Gesture Dection: Using Google's mediapipe library, the solution detects numbers of fingers shown in the camera as input for STEM content interaction. 
- [x] Education Content: Eight demonstrative question that can be shuffled.

## Project Framework
Please refers to this [documentation](framework.md)

## Quick start
* Clone the repository to your Pi5 or equavalent SBC (ensure serial com has been enabled)
* Prepare the python environment
```bash
conda create -n bot python=3.12 -y
conda activate bot
```
* Install required packages for the SBC
```bash
pip install -r requirements.txt
```
* Compile the [ESP32 sketch](/esp32/esp32_firmware/esp32_firmware.ino) using Arduino IDE.
* Connect ESP32, USB Cam, and USB sound card to the Pi5
* Start the program
~~* Set the keys if you would like to use the cloud inference service.[^3]~~
```bash
export INTERMEDIATE_SERVER_AUTH=[your-authentication-key] # for legacy function that no longer support
```
* Start the service
```bash
python main.py
```
* You may use another host machine to check the status using the [sample debugging webface](debug/index_alt.html)

## Front-end UI/UX
> __<i>This repo only contains back-end solutions with simple debugging pannel.</i>__

For front-end, please check [Xd06eR/wonderball-app](https://github.com/Xd06eR/wonderball-app) designed by Pan Wong.

## Notes

[^1]: Fallback to MJPEG video + separate H.264 audio stream (hardware encoder initialization via ffmpeg failed)

[^2]: Implemented via ESP32  
  ESP32 is connected to Raspberry Pi 5 through USB serial (appears as `/dev/ttyACM0` on Pi 5)

[^3]: ~~The current cloud inference service adopts a self-hosted API transfer node (VPN-liked) as GPT/Gemini are currently unavailable in Hong Kong. You may directly use the OpenAI API platform or use 3rd party platform like openrouter.~~

