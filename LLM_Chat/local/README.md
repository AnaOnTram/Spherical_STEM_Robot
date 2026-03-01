# Local ASR+TTS Solution
Local ASR + TTS solution using faster whisper + TTS piper

## ASR
Start the faster-whisper-host service
```bash
python faster-whisper-host
```
It would then start a service at port `8803`
## TTS
Start the TTS piper http server
```bash
python3 -m piper.http_server -m /home/admin/piper/en_US-amy-medium --port 8805
```
The server can handle API call with sample like
```bash
curl -X POST -H 'Content-Type: application/json' -d '{ "text": "This is a test." }' -o test.wav localhost:8805
```
And it would generate a wav file

