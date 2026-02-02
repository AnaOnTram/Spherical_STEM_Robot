#!/bin/bash
# Fix dependency conflicts for Spherical Robot

echo "🔧 Fixing dependency conflicts..."

# Step 1: Downgrade NumPy to 1.x (required by tflite-runtime)
echo "📦 Installing NumPy 1.26.4..."
pip install "numpy==1.26.4" --force-reinstall

# Step 2: Downgrade OpenCV to version compatible with NumPy 1.x
echo "📦 Installing OpenCV 4.9.0.80 (compatible with NumPy 1.x)..."
pip install "opencv-python==4.9.0.80" --force-reinstall

# Step 3: Verify installations
echo "✅ Verifying installations..."
python3 -c "import numpy; print(f'NumPy version: {numpy.__version__}')"
python3 -c "import cv2; print(f'OpenCV version: {cv2.__version__}')"

echo ""
echo "🎉 Dependencies fixed! You can now run the robot."
echo ""
echo "To start the robot:"
echo "  python main.py"
