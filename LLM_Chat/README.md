# LLM Chat
A sub-module of the project that aims to interact with pre-school children through natural conversation.

## Local Deployment
* Download the models
```bash
# create a directory to store the model if you like
mkdir model && cd model
# download model files. GGUF for this project. You may consider using huggingface-cli for better download experience. Adjust quantization based on your available RAM capacity and computational power for better experience.
wget https://huggingface.co/LiquidAI/LFM2.5-Audio-1.5B-GGUF/resolve/main/LFM2.5-Audio-1.5B-Q4_0.gguf
wget https://huggingface.co/LiquidAI/LFM2.5-Audio-1.5B-GGUF/resolve/main/mmproj-LFM2.5-Audio-1.5B-Q4_0.gguf
wget https://huggingface.co/LiquidAI/LFM2.5-Audio-1.5B-GGUF/resolve/main/tokenizer-LFM2.5-Audio-1.5B-Q4_0.gguf
wget https://huggingface.co/LiquidAI/LFM2.5-Audio-1.5B-GGUF/resolve/main/vocoder-LFM2.5-Audio-1.5B-Q4_0.gguf
```
* Download the official cpp engine
```bash
cd LLM_Chat
wget https://huggingface.co/LiquidAI/LFM2.5-Audio-1.5B-GGUF/resolve/main/runners/llama-liquid-audio-ubuntu-arm64.zip
unzip llama-liquid-audio-ubuntu-arm64.zip mv llama-liquid-audio-ubuntu-arm64 server
```
### Expected Performance
On a raspberry pi 5 with 4GB RAM, the system generate the audio response in around 30s for a 5 seconds audio input.

## Cloud Solution
* Use OpenRouter LLM chat service for audio response.