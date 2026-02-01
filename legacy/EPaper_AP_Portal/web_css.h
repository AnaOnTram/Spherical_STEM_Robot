#ifndef WEB_CSS_H
#define WEB_CSS_H

const char CSS_STYLES[] PROGMEM = R"rawliteral(
:root {
    --primary: #2563eb;
    --primary-dark: #1d4ed8;
    --secondary: #64748b;
    --success: #22c55e;
    --danger: #ef4444;
    --bg: #f8fafc;
    --surface: #ffffff;
    --text: #1e293b;
    --text-muted: #64748b;
    --border: #e2e8f0;
    --radius: 12px;
    --shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1);
}

* {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
    -webkit-tap-highlight-color: transparent;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    line-height: 1.5;
}

.container {
    max-width: 600px;
    margin: 0 auto;
    padding: 16px;
    padding-bottom: 32px;
}

header {
    text-align: center;
    padding: 24px 0;
}

header h1 {
    font-size: 24px;
    font-weight: 600;
    margin-bottom: 4px;
}

.subtitle {
    color: var(--text-muted);
    font-size: 14px;
}

/* Upload Section */
.upload-section {
    margin-bottom: 24px;
}

.drop-zone {
    background: var(--surface);
    border: 2px dashed var(--border);
    border-radius: var(--radius);
    padding: 48px 24px;
    text-align: center;
    cursor: pointer;
    transition: all 0.2s ease;
}

.drop-zone:hover, .drop-zone.dragover {
    border-color: var(--primary);
    background: #eff6ff;
}

.drop-zone:active {
    transform: scale(0.99);
}

.drop-icon {
    width: 48px;
    height: 48px;
    color: var(--secondary);
    margin-bottom: 16px;
}

.drop-content p {
    font-size: 16px;
    color: var(--text);
}

.drop-content .hint {
    font-size: 14px;
    color: var(--text-muted);
}

/* Preview Section */
.preview-section {
    background: var(--surface);
    border-radius: var(--radius);
    padding: 16px;
    margin-bottom: 24px;
    box-shadow: var(--shadow);
}

.preview-container {
    margin-bottom: 16px;
}

.canvas-wrapper {
    position: relative;
    width: 100%;
    background: #f1f5f9;
    border-radius: 8px;
    overflow: hidden;
    touch-action: none;
}

.canvas-wrapper canvas {
    display: block;
    width: 100%;
    height: auto;
}

/* Crop Overlay */
.crop-overlay {
    position: absolute;
    border: 2px solid var(--primary);
    background: rgba(37, 99, 235, 0.1);
    cursor: move;
    touch-action: none;
}

.crop-handles {
    position: absolute;
    inset: 0;
}

.handle {
    position: absolute;
    width: 24px;
    height: 24px;
    background: var(--primary);
    border: 2px solid white;
    border-radius: 50%;
    transform: translate(-50%, -50%);
}

.handle.tl { top: 0; left: 0; cursor: nwse-resize; }
.handle.tr { top: 0; right: 0; transform: translate(50%, -50%); cursor: nesw-resize; }
.handle.bl { bottom: 0; left: 0; transform: translate(-50%, 50%); cursor: nesw-resize; }
.handle.br { bottom: 0; right: 0; transform: translate(50%, 50%); cursor: nwse-resize; }

.preview-result {
    margin-top: 16px;
    text-align: center;
}

.preview-result p {
    font-size: 14px;
    color: var(--text-muted);
    margin-bottom: 8px;
}

.preview-result canvas {
    max-width: 100%;
    height: auto;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: white;
}

/* Controls */
.controls {
    display: flex;
    flex-direction: column;
    gap: 12px;
    margin-bottom: 16px;
}

.control-group {
    display: flex;
    align-items: center;
    gap: 12px;
}

.control-group label {
    width: 80px;
    font-size: 14px;
    color: var(--text-muted);
}

.control-group input[type="range"] {
    flex: 1;
    height: 6px;
    -webkit-appearance: none;
    background: var(--border);
    border-radius: 3px;
    outline: none;
}

.control-group input[type="range"]::-webkit-slider-thumb {
    -webkit-appearance: none;
    width: 20px;
    height: 20px;
    background: var(--primary);
    border-radius: 50%;
    cursor: pointer;
}

.control-group span {
    width: 40px;
    text-align: right;
    font-size: 14px;
    font-family: monospace;
}

/* Actions */
.actions {
    display: flex;
    gap: 12px;
}

/* Buttons */
.btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 12px 24px;
    font-size: 16px;
    font-weight: 500;
    border: none;
    border-radius: 8px;
    cursor: pointer;
    transition: all 0.2s ease;
}

.btn:active {
    transform: scale(0.98);
}

.btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}

.btn-primary {
    flex: 1;
    background: var(--primary);
    color: white;
}

.btn-primary:hover:not(:disabled) {
    background: var(--primary-dark);
}

.btn-secondary {
    background: var(--border);
    color: var(--text);
}

.btn-secondary:hover:not(:disabled) {
    background: #cbd5e1;
}

.btn-small {
    padding: 8px 16px;
    font-size: 14px;
    background: var(--surface);
    color: var(--text);
    border: 1px solid var(--border);
}

.btn-small:hover:not(:disabled) {
    background: var(--bg);
}

/* Status Section */
.status-section {
    margin-bottom: 24px;
}

.status-bar {
    background: var(--surface);
    border-radius: var(--radius);
    padding: 12px 16px;
    box-shadow: var(--shadow);
}

.status-item {
    display: flex;
    gap: 8px;
    font-size: 14px;
}

.status-label {
    color: var(--text-muted);
}

.status-value {
    font-weight: 500;
}

.progress-bar {
    margin-top: 12px;
    height: 6px;
    background: var(--border);
    border-radius: 3px;
    overflow: hidden;
}

.progress-fill {
    height: 100%;
    background: var(--primary);
    width: 0%;
    transition: width 0.3s ease;
}

/* Tools Section */
.tools-section {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 24px;
}

/* Footer */
footer {
    text-align: center;
    padding: 16px;
    color: var(--text-muted);
    font-size: 12px;
}

footer code {
    background: var(--border);
    padding: 2px 6px;
    border-radius: 4px;
    font-family: monospace;
}

/* Mobile Optimizations */
@media (max-width: 480px) {
    .container {
        padding: 12px;
    }

    header {
        padding: 16px 0;
    }

    header h1 {
        font-size: 20px;
    }

    .drop-zone {
        padding: 32px 16px;
    }

    .handle {
        width: 32px;
        height: 32px;
    }

    .actions {
        flex-direction: column;
    }

    .btn {
        width: 100%;
    }

    .tools-section {
        justify-content: center;
    }
}

/* Loading State */
.loading {
    position: relative;
    pointer-events: none;
}

.loading::after {
    content: '';
    position: absolute;
    inset: 0;
    background: rgba(255, 255, 255, 0.8);
    display: flex;
    align-items: center;
    justify-content: center;
}

/* Animation */
@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
}

.processing .status-value {
    animation: pulse 1.5s ease-in-out infinite;
}
)rawliteral";

#endif // WEB_CSS_H
