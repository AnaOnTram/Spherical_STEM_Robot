#ifndef WEB_HTML_H
#define WEB_HTML_H

const char HTML_PAGE[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="mobile-web-app-capable" content="yes">
    <title>E-Paper Display</title>
    <link rel="stylesheet" href="/styles.css">
</head>
<body>
    <div class="container">
        <header>
            <h1>E-Paper Display</h1>
            <p class="subtitle">4.2" 400x300 Mono</p>
        </header>

        <section class="upload-section">
            <div class="drop-zone" id="dropZone">
                <input type="file" id="fileInput" accept="image/*" hidden>
                <div class="drop-content">
                    <svg class="drop-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                        <polyline points="17 8 12 3 7 8"/>
                        <line x1="12" y1="3" x2="12" y2="15"/>
                    </svg>
                    <p>Tap to select image<br><span class="hint">or drag & drop</span></p>
                </div>
            </div>
        </section>

        <section class="preview-section" id="previewSection" style="display:none;">
            <div class="preview-container">
                <div class="canvas-wrapper" id="canvasWrapper">
                    <canvas id="sourceCanvas"></canvas>
                    <div class="crop-overlay" id="cropOverlay">
                        <div class="crop-handles">
                            <div class="handle tl" data-handle="tl"></div>
                            <div class="handle tr" data-handle="tr"></div>
                            <div class="handle bl" data-handle="bl"></div>
                            <div class="handle br" data-handle="br"></div>
                        </div>
                    </div>
                </div>
                <div class="preview-result">
                    <p>Preview (dithered):</p>
                    <canvas id="previewCanvas" width="400" height="300"></canvas>
                </div>
            </div>

            <div class="controls">
                <div class="control-group">
                    <label>Brightness</label>
                    <input type="range" id="brightness" min="-100" max="100" value="0">
                    <span id="brightnessVal">0</span>
                </div>
                <div class="control-group">
                    <label>Contrast</label>
                    <input type="range" id="contrast" min="-100" max="100" value="0">
                    <span id="contrastVal">0</span>
                </div>
            </div>

            <div class="actions">
                <button class="btn btn-secondary" id="btnReset">Reset</button>
                <button class="btn btn-primary" id="btnSend">Send to Display</button>
            </div>
        </section>

        <section class="status-section">
            <div class="status-bar">
                <div class="status-item">
                    <span class="status-label">Status:</span>
                    <span class="status-value" id="statusText">Ready</span>
                </div>
                <div class="progress-bar" id="progressBar" style="display:none;">
                    <div class="progress-fill" id="progressFill"></div>
                </div>
            </div>
        </section>

        <section class="tools-section">
            <button class="btn btn-small" id="btnClear">Clear Display</button>
            <button class="btn btn-small" id="btnTest">Test Pattern</button>
            <button class="btn btn-small" id="btnGenTest">Gen Test Image</button>
            <button class="btn btn-small" id="btnSleep">Sleep</button>
        </section>

        <footer>
            <p>API: <code>/api/status</code> | <code>/api/upload</code> | <code>/api/display</code></p>
        </footer>
    </div>

    <script src="/app.js"></script>
</body>
</html>
)rawliteral";

#endif // WEB_HTML_H
