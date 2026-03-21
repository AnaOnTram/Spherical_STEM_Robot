# LLM Chat
A sub-module of the project that aims to interact with pre-school children through natural conversation.

## Local Deployment
* Download the models
```bash
cd models
# Download the three esential models files for this project
cd text_model
# make the downloading script executable
chmod +x qwen.sh
# download the file
./qwen.sh

cd ..
cd piper
chmod +x piper_model.sh
./piper_model.sh
```
<font color='red'>This project already has llama.cpp built for raspberry pi. If you encounter issues. Please try to compile the inference engine yourself!</font>
* Compile the inference engine (optional)
```bash
git clone https://github.com/ggml-org/llama.cpp.git
cd llama.cpp
cmake -B build
cmake --build build --config Release -j # adjust this command based on your CPU threads and available memory.
mv /build/bin ~/Spherical_STEM_ROBOT/llama_server # Copy executable files to project directory. Clean the original file first!!!
```
### Expected Performance
On a raspberry pi 5 with 4GB RAM, the system generate the audio response in around 30s for a 5 seconds audio input.
