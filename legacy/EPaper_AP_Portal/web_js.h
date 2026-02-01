#ifndef WEB_JS_H
#define WEB_JS_H

const char JS_APP[] PROGMEM = R"rawliteral(
(function() {
    'use strict';

    // Display dimensions
    const DISPLAY_WIDTH = 400;
    const DISPLAY_HEIGHT = 300;
    const ASPECT_RATIO = DISPLAY_WIDTH / DISPLAY_HEIGHT;

    // DOM Elements
    const $ = (s) => document.querySelector(s);
    const dropZone = $('#dropZone');
    const fileInput = $('#fileInput');
    const previewSection = $('#previewSection');
    const sourceCanvas = $('#sourceCanvas');
    const previewCanvas = $('#previewCanvas');
    const cropOverlay = $('#cropOverlay');
    const canvasWrapper = $('#canvasWrapper');
    const brightnessSlider = $('#brightness');
    const contrastSlider = $('#contrast');
    const brightnessVal = $('#brightnessVal');
    const contrastVal = $('#contrastVal');
    const btnSend = $('#btnSend');
    const btnReset = $('#btnReset');
    const btnClear = $('#btnClear');
    const btnTest = $('#btnTest');
    const btnSleep = $('#btnSleep');
    const statusText = $('#statusText');
    const progressBar = $('#progressBar');
    const progressFill = $('#progressFill');

    // State
    let originalImage = null;
    let cropRect = { x: 0, y: 0, w: DISPLAY_WIDTH, h: DISPLAY_HEIGHT };
    let scale = 1;
    let isDragging = false;
    let dragHandle = null;
    let dragStart = { x: 0, y: 0 };
    let cropStart = { x: 0, y: 0, w: 0, h: 0 };

    // Contexts
    const sourceCtx = sourceCanvas.getContext('2d', { willReadFrequently: true });
    const previewCtx = previewCanvas.getContext('2d', { willReadFrequently: true });

    // ===========================================
    // Status Management
    // ===========================================
    function setStatus(text, isProcessing = false) {
        statusText.textContent = text;
        document.body.classList.toggle('processing', isProcessing);
    }

    function showProgress(percent) {
        progressBar.style.display = 'block';
        progressFill.style.width = percent + '%';
    }

    function hideProgress() {
        progressBar.style.display = 'none';
        progressFill.style.width = '0%';
    }

    // ===========================================
    // File Handling - Support for JPG, PNG, HEIC
    // ===========================================
    async function handleFile(file) {
        if (!file) {
            setStatus('No file selected');
            return;
        }

        console.log('File type:', file.type, 'Name:', file.name);
        setStatus('Loading image...', true);

        // Check if HEIC
        const isHeic = file.name.toLowerCase().endsWith('.heic') ||
                       file.name.toLowerCase().endsWith('.heif') ||
                       file.type === 'image/heic' ||
                       file.type === 'image/heif';

        if (isHeic) {
            setStatus('HEIC format - loading...');
        }

        // Try multiple methods to load the image
        let loadedImage = null;

        // Method 1: Try createImageBitmap (better format support)
        if ('createImageBitmap' in window) {
            try {
                console.log('Trying createImageBitmap...');
                const bitmap = await createImageBitmap(file);
                // Convert bitmap to canvas then to image
                const canvas = document.createElement('canvas');
                canvas.width = bitmap.width;
                canvas.height = bitmap.height;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(bitmap, 0, 0);

                const img = new Image();
                img.src = canvas.toDataURL('image/png');
                await new Promise((resolve, reject) => {
                    img.onload = resolve;
                    img.onerror = reject;
                });
                loadedImage = img;
                console.log('Loaded via createImageBitmap:', img.width, 'x', img.height);
            } catch (e) {
                console.log('createImageBitmap failed:', e.message);
            }
        }

        // Method 2: Try Data URL method (standard approach)
        if (!loadedImage) {
            try {
                console.log('Trying DataURL method...');
                const dataUrl = await new Promise((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onload = (e) => resolve(e.target.result);
                    reader.onerror = reject;
                    reader.readAsDataURL(file);
                });

                const img = new Image();
                await new Promise((resolve, reject) => {
                    img.onload = resolve;
                    img.onerror = reject;
                    img.src = dataUrl;
                });
                loadedImage = img;
                console.log('Loaded via DataURL:', img.width, 'x', img.height);
            } catch (e) {
                console.log('DataURL method failed:', e.message);
            }
        }

        // Method 3: For HEIC on iOS Safari - try blob URL
        if (!loadedImage && isHeic) {
            try {
                console.log('Trying Blob URL method for HEIC...');
                const blobUrl = URL.createObjectURL(file);
                const img = new Image();
                await new Promise((resolve, reject) => {
                    img.onload = () => {
                        URL.revokeObjectURL(blobUrl);
                        resolve();
                    };
                    img.onerror = () => {
                        URL.revokeObjectURL(blobUrl);
                        reject(new Error('Blob URL failed'));
                    };
                    img.src = blobUrl;
                });
                loadedImage = img;
                console.log('Loaded via Blob URL:', img.width, 'x', img.height);
            } catch (e) {
                console.log('Blob URL method failed:', e.message);
            }
        }

        if (loadedImage) {
            originalImage = loadedImage;
            initCropper(loadedImage);
            setStatus('Adjust crop area and send to display');
        } else {
            if (isHeic) {
                setStatus('HEIC not supported. Use Safari on iOS/Mac, or convert to JPG.');
            } else {
                setStatus('Failed to load image. Try JPG or PNG format.');
            }
        }
    }

    // ===========================================
    // Cropper Initialization
    // ===========================================
    function initCropper(img) {
        // Calculate scale to fit image in container for display
        const containerWidth = canvasWrapper.clientWidth || 350;
        const maxHeight = 400;

        let displayWidth = img.width;
        let displayHeight = img.height;

        if (displayWidth > containerWidth) {
            displayWidth = containerWidth;
            displayHeight = img.height * (containerWidth / img.width);
        }
        if (displayHeight > maxHeight) {
            displayHeight = maxHeight;
            displayWidth = img.width * (maxHeight / img.height);
        }

        scale = img.width / displayWidth;
        console.log('Scale factor:', scale);

        sourceCanvas.width = displayWidth;
        sourceCanvas.height = displayHeight;
        sourceCtx.drawImage(img, 0, 0, displayWidth, displayHeight);

        // Initialize crop rect centered with display aspect ratio
        const cropW = Math.min(displayWidth, displayHeight * ASPECT_RATIO);
        const cropH = cropW / ASPECT_RATIO;
        cropRect = {
            x: (displayWidth - cropW) / 2,
            y: (displayHeight - cropH) / 2,
            w: cropW,
            h: cropH
        };

        updateCropOverlay();
        updatePreview();

        previewSection.style.display = 'block';
    }

    function updateCropOverlay() {
        cropOverlay.style.left = cropRect.x + 'px';
        cropOverlay.style.top = cropRect.y + 'px';
        cropOverlay.style.width = cropRect.w + 'px';
        cropOverlay.style.height = cropRect.h + 'px';
    }

    // ===========================================
    // Crop Interaction
    // ===========================================
    function getEventPos(e) {
        const rect = canvasWrapper.getBoundingClientRect();
        const touch = e.touches ? e.touches[0] : e;
        return {
            x: touch.clientX - rect.left,
            y: touch.clientY - rect.top
        };
    }

    function startDrag(e) {
        e.preventDefault();
        const pos = getEventPos(e);
        const target = e.target;

        isDragging = true;
        dragStart = pos;
        cropStart = { ...cropRect };

        if (target.classList.contains('handle')) {
            dragHandle = target.dataset.handle;
        } else if (target === cropOverlay || target.closest('.crop-overlay')) {
            dragHandle = 'move';
        }
    }

    function doDrag(e) {
        if (!isDragging || !originalImage) return;
        e.preventDefault();

        const pos = getEventPos(e);
        const dx = pos.x - dragStart.x;
        const dy = pos.y - dragStart.y;

        const canvasW = sourceCanvas.width;
        const canvasH = sourceCanvas.height;
        const minSize = 50;

        if (dragHandle === 'move') {
            cropRect.x = Math.max(0, Math.min(canvasW - cropRect.w, cropStart.x + dx));
            cropRect.y = Math.max(0, Math.min(canvasH - cropRect.h, cropStart.y + dy));
        } else if (dragHandle) {
            let newX = cropStart.x;
            let newY = cropStart.y;
            let newW = cropStart.w;
            let newH = cropStart.h;

            if (dragHandle.includes('l')) {
                newW = Math.max(minSize, cropStart.w - dx);
                newX = cropStart.x + cropStart.w - newW;
            }
            if (dragHandle.includes('r')) {
                newW = Math.max(minSize, cropStart.w + dx);
            }
            if (dragHandle.includes('t')) {
                newH = Math.max(minSize, cropStart.h - dy);
                newY = cropStart.y + cropStart.h - newH;
            }
            if (dragHandle.includes('b')) {
                newH = Math.max(minSize, cropStart.h + dy);
            }

            // Maintain aspect ratio
            if (newW / newH > ASPECT_RATIO) {
                newW = newH * ASPECT_RATIO;
            } else {
                newH = newW / ASPECT_RATIO;
            }

            // Constrain to canvas
            if (newX < 0) { newX = 0; }
            if (newY < 0) { newY = 0; }
            if (newX + newW > canvasW) { newW = canvasW - newX; newH = newW / ASPECT_RATIO; }
            if (newY + newH > canvasH) { newH = canvasH - newY; newW = newH * ASPECT_RATIO; }

            cropRect = { x: newX, y: newY, w: newW, h: newH };
        }

        updateCropOverlay();
        updatePreview();
    }

    function endDrag() {
        isDragging = false;
        dragHandle = null;
    }

    // ===========================================
    // Floyd-Steinberg Dithering (Simplified & Fixed)
    // ===========================================
    function applyDithering(canvas) {
        const ctx = canvas.getContext('2d');
        const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
        const data = imageData.data;
        const w = canvas.width;
        const h = canvas.height;

        const brightness = parseInt(brightnessSlider.value);
        const contrast = parseInt(contrastSlider.value);
        const contrastFactor = (259 * (contrast + 255)) / (255 * (259 - contrast));

        // Convert to grayscale array with brightness/contrast
        const gray = new Float32Array(w * h);
        for (let i = 0; i < w * h; i++) {
            const idx = i * 4;
            // Luminosity method for grayscale
            let g = 0.299 * data[idx] + 0.587 * data[idx + 1] + 0.114 * data[idx + 2];

            // Apply brightness (-100 to +100 maps to -255 to +255)
            g += brightness * 2.55;

            // Apply contrast
            g = contrastFactor * (g - 128) + 128;

            // Clamp
            gray[i] = Math.max(0, Math.min(255, g));
        }

        // Debug: check some pixel values
        console.log('Sample gray values:', gray[0], gray[1000], gray[50000]);

        // Floyd-Steinberg dithering
        for (let y = 0; y < h; y++) {
            for (let x = 0; x < w; x++) {
                const idx = y * w + x;
                const oldPixel = gray[idx];
                const newPixel = oldPixel < 128 ? 0 : 255;
                gray[idx] = newPixel;

                const error = oldPixel - newPixel;

                // Distribute error to neighbors
                if (x + 1 < w) {
                    gray[idx + 1] += error * 7 / 16;
                }
                if (y + 1 < h) {
                    if (x > 0) {
                        gray[(y + 1) * w + (x - 1)] += error * 3 / 16;
                    }
                    gray[(y + 1) * w + x] += error * 5 / 16;
                    if (x + 1 < w) {
                        gray[(y + 1) * w + (x + 1)] += error * 1 / 16;
                    }
                }
            }
        }

        // Count black and white pixels for debugging
        let blackCount = 0, whiteCount = 0;

        // Write back to image data
        for (let i = 0; i < w * h; i++) {
            const idx = i * 4;
            const val = gray[i] < 128 ? 0 : 255;
            if (val === 0) blackCount++; else whiteCount++;
            data[idx] = val;
            data[idx + 1] = val;
            data[idx + 2] = val;
            data[idx + 3] = 255;
        }

        console.log('Dithering result - Black:', blackCount, 'White:', whiteCount);

        ctx.putImageData(imageData, 0, 0);
    }

    // ===========================================
    // Preview Update
    // ===========================================
    function updatePreview() {
        if (!originalImage) return;

        // Calculate crop coordinates in original image space
        const srcX = Math.round(cropRect.x * scale);
        const srcY = Math.round(cropRect.y * scale);
        const srcW = Math.round(cropRect.w * scale);
        const srcH = Math.round(cropRect.h * scale);

        console.log('Crop area:', srcX, srcY, srcW, srcH);

        // Set preview canvas size
        previewCanvas.width = DISPLAY_WIDTH;
        previewCanvas.height = DISPLAY_HEIGHT;

        // Draw cropped and scaled image to preview canvas
        previewCtx.fillStyle = 'white';
        previewCtx.fillRect(0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT);

        try {
            previewCtx.drawImage(
                originalImage,
                srcX, srcY, srcW, srcH,
                0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT
            );
        } catch (e) {
            console.error('Draw error:', e);
            return;
        }

        // Check if image was drawn
        const testData = previewCtx.getImageData(DISPLAY_WIDTH/2, DISPLAY_HEIGHT/2, 1, 1).data;
        console.log('Center pixel before dithering:', testData[0], testData[1], testData[2]);

        // Apply dithering
        applyDithering(previewCanvas);
    }

    // ===========================================
    // Convert to 1-bit packed format
    // ===========================================
    function getPackedImageData() {
        const imageData = previewCtx.getImageData(0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT);
        const data = imageData.data;
        const w = DISPLAY_WIDTH;
        const h = DISPLAY_HEIGHT;

        // Pack into bytes: 8 pixels per byte, MSB first
        // E-paper: bit=1 means white, bit=0 means black
        const packed = new Uint8Array(w * h / 8);
        let byteIndex = 0;

        for (let y = 0; y < h; y++) {
            for (let x = 0; x < w; x += 8) {
                let byte = 0;
                for (let bit = 0; bit < 8; bit++) {
                    const pixelIndex = (y * w + x + bit) * 4;
                    // If pixel is white (> 128), set the bit
                    if (data[pixelIndex] > 128) {
                        byte |= (0x80 >> bit);
                    }
                }
                packed[byteIndex++] = byte;
            }
        }

        console.log('Packed bytes:', byteIndex, 'First bytes:', packed[0].toString(16), packed[1].toString(16));
        return packed;
    }

    // ===========================================
    // Encode bytes for transmission (nibble encoding)
    // ===========================================
    function byteToStr(v) {
        // Low nibble first, then high nibble
        // Each nibble becomes 'a' + nibble_value (0-15 -> 'a'-'p')
        return String.fromCharCode((v & 0xF) + 97, ((v >> 4) & 0xF) + 97);
    }

    // ===========================================
    // API Communication
    // ===========================================
    async function sendImage() {
        if (!originalImage) {
            setStatus('No image loaded');
            return;
        }

        btnSend.disabled = true;
        setStatus('Processing image...', true);
        showProgress(0);

        try {
            // Ensure preview is up to date
            updatePreview();

            // Get packed image data
            const packed = getPackedImageData();
            console.log('Total packed size:', packed.length, 'Expected:', DISPLAY_WIDTH * DISPLAY_HEIGHT / 8);

            // Debug: count non-zero bytes and check distribution
            let nonZeroCount = 0;
            let allFFCount = 0;
            for (let i = 0; i < packed.length; i++) {
                if (packed[i] !== 0) nonZeroCount++;
                if (packed[i] === 0xFF) allFFCount++;
            }
            console.log('Packed stats - Non-zero bytes:', nonZeroCount, 'All-FF bytes:', allFFCount, 'of', packed.length);
            console.log('First 16 packed bytes:', Array.from(packed.slice(0, 16)).map(b => b.toString(16).padStart(2, '0')).join(' '));

            // Encode data
            let encodedData = '';
            for (let i = 0; i < packed.length; i++) {
                encodedData += byteToStr(packed[i]);
            }
            console.log('Encoded length:', encodedData.length, 'First 40 chars:', encodedData.substring(0, 40));

            setStatus('Uploading to display...', true);

            // Send in chunks
            const chunkSize = 1000;
            const totalChunks = Math.ceil(encodedData.length / chunkSize);

            for (let i = 0; i < totalChunks; i++) {
                const chunk = encodedData.slice(i * chunkSize, (i + 1) * chunkSize);
                const isFirst = i === 0;

                const response = await fetch('/api/upload', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'text/plain',
                        ...(isFirst ? { 'X-Upload-Start': '1' } : {})
                    },
                    body: chunk
                });

                if (!response.ok) {
                    const text = await response.text();
                    throw new Error('Upload failed: ' + text);
                }

                const result = await response.json();
                console.log('Upload chunk', i + 1, '/', totalChunks, '- received:', result.received, 'total:', result.total);

                const progress = Math.round(((i + 1) / totalChunks) * 80);
                showProgress(progress);
            }

            setStatus('Refreshing display...', true);
            showProgress(90);

            // Trigger display update
            const displayResponse = await fetch('/api/display', { method: 'POST' });
            if (!displayResponse.ok) {
                throw new Error('Display refresh failed');
            }

            showProgress(100);
            setStatus('Image displayed successfully!');

            setTimeout(() => hideProgress(), 1000);

        } catch (error) {
            console.error('Error:', error);
            setStatus('Error: ' + error.message);
            hideProgress();
        } finally {
            btnSend.disabled = false;
        }
    }

    // ===========================================
    // Event Listeners
    // ===========================================

    // File input - accept more formats
    fileInput.setAttribute('accept', 'image/*,.heic,.heif');

    dropZone.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', (e) => {
        if (e.target.files[0]) handleFile(e.target.files[0]);
    });

    // Drag and drop
    dropZone.addEventListener('dragover', (e) => {
        e.stopPropagation();
        e.preventDefault();
        dropZone.classList.add('dragover');
    });
    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('dragover');
    });
    dropZone.addEventListener('drop', (e) => {
        e.stopPropagation();
        e.preventDefault();
        dropZone.classList.remove('dragover');
        if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
    });

    // Crop interaction - Mouse
    cropOverlay.addEventListener('mousedown', startDrag);
    document.addEventListener('mousemove', doDrag);
    document.addEventListener('mouseup', endDrag);

    // Crop interaction - Touch
    cropOverlay.addEventListener('touchstart', startDrag, { passive: false });
    document.addEventListener('touchmove', doDrag, { passive: false });
    document.addEventListener('touchend', endDrag);

    // Handle resize
    cropOverlay.querySelectorAll('.handle').forEach(handle => {
        handle.addEventListener('mousedown', (e) => {
            e.stopPropagation();
            startDrag(e);
        });
        handle.addEventListener('touchstart', (e) => {
            e.stopPropagation();
            startDrag(e);
        }, { passive: false });
    });

    // Sliders
    brightnessSlider.addEventListener('input', () => {
        brightnessVal.textContent = brightnessSlider.value;
        updatePreview();
    });
    contrastSlider.addEventListener('input', () => {
        contrastVal.textContent = contrastSlider.value;
        updatePreview();
    });

    // Buttons
    btnSend.addEventListener('click', sendImage);

    btnReset.addEventListener('click', () => {
        brightnessSlider.value = 0;
        contrastSlider.value = 0;
        brightnessVal.textContent = '0';
        contrastVal.textContent = '0';
        if (originalImage) {
            initCropper(originalImage);
        }
    });

    btnClear.addEventListener('click', async () => {
        setStatus('Clearing display...', true);
        try {
            const response = await fetch('/api/clear', { method: 'POST' });
            if (response.ok) {
                setStatus('Display cleared');
            } else {
                setStatus('Failed to clear display');
            }
        } catch (e) {
            setStatus('Error: ' + e.message);
        }
    });

    btnTest.addEventListener('click', async () => {
        setStatus('Showing test pattern...', true);
        try {
            const response = await fetch('/api/test', { method: 'POST' });
            if (response.ok) {
                setStatus('Test pattern displayed');
            } else {
                setStatus('Failed to show test pattern');
            }
        } catch (e) {
            setStatus('Error: ' + e.message);
        }
    });

    btnSleep.addEventListener('click', async () => {
        setStatus('Entering sleep mode...', true);
        try {
            const response = await fetch('/api/sleep', { method: 'POST' });
            if (response.ok) {
                setStatus('Display in sleep mode');
            } else {
                setStatus('Failed to enter sleep mode');
            }
        } catch (e) {
            setStatus('Error: ' + e.message);
        }
    });

    // Generate test image button
    const btnGenTest = $('#btnGenTest');
    if (btnGenTest) {
        btnGenTest.addEventListener('click', () => {
            setStatus('Generating test image...', true);

            // Create a test gradient image
            const testCanvas = document.createElement('canvas');
            testCanvas.width = 400;
            testCanvas.height = 300;
            const testCtx = testCanvas.getContext('2d');

            // Draw gradient background
            const gradient = testCtx.createLinearGradient(0, 0, 400, 300);
            gradient.addColorStop(0, 'white');
            gradient.addColorStop(0.5, 'gray');
            gradient.addColorStop(1, 'black');
            testCtx.fillStyle = gradient;
            testCtx.fillRect(0, 0, 400, 300);

            // Draw some shapes
            testCtx.fillStyle = 'black';
            testCtx.fillRect(20, 20, 100, 60);
            testCtx.fillStyle = 'white';
            testCtx.fillRect(280, 20, 100, 60);

            // Draw circles
            testCtx.beginPath();
            testCtx.arc(100, 200, 50, 0, Math.PI * 2);
            testCtx.fillStyle = 'black';
            testCtx.fill();

            testCtx.beginPath();
            testCtx.arc(300, 200, 50, 0, Math.PI * 2);
            testCtx.fillStyle = 'white';
            testCtx.fill();

            // Draw text
            testCtx.font = 'bold 24px sans-serif';
            testCtx.fillStyle = 'black';
            testCtx.fillText('E-Paper Test', 130, 150);

            // Convert to image and process
            const dataUrl = testCanvas.toDataURL('image/png');
            const img = new Image();
            img.onload = () => {
                originalImage = img;
                scale = 1;
                cropRect = { x: 0, y: 0, w: 400, h: 300 };

                sourceCanvas.width = 400;
                sourceCanvas.height = 300;
                sourceCtx.drawImage(img, 0, 0);

                previewSection.style.display = 'block';
                updateCropOverlay();
                updatePreview();

                setStatus('Test image ready - click Send to Display');
            };
            img.src = dataUrl;
        });
    }

    // Initial status
    setStatus('Ready - Select an image to begin');
    console.log('E-Paper Portal initialized');

})();
)rawliteral";

#endif // WEB_JS_H
