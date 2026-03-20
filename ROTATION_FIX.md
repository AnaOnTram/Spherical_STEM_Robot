# Camera Rotation Fix

## Problem

When changing `CAMERA_ROTATION_DEGREES` in `config.py` to a non-zero value (90, 180, or 270), the client-side video stream would freeze.

## Root Cause

The issue occurred due to a dimension mismatch between the server and client:

1. **90° and 270° rotations swap frame dimensions** - a 640×480 frame becomes 480×640
2. **The `frame_size` property didn't account for rotation** - it always returned the original camera dimensions (640×480)
3. **The client expected a 4:3 aspect ratio** but received frames with a 3:4 aspect ratio after rotation
4. **The MJPEG stream parser in browsers** got confused by the unexpected dimensions, causing the stream to freeze

## Solution

### 1. Fixed `frame_size` Property (cv_engine/video_encoder.py)

Updated the `frame_size` property to return the correct dimensions after rotation:

```python
@property
def frame_size(self) -> tuple[int, int]:
    """Get frame size (width, height) after rotation."""
    rotation = int(self.config.rotation_degrees) % 360
    
    if self._capture:
        width = int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        # Swap dimensions for 90° and 270° rotations
        if rotation in (90, 270):
            return (height, width)
        return (width, height)
    
    # Swap config dimensions if needed
    if rotation in (90, 270):
        return (self.config.height, self.config.width)
    return (self.config.width, self.config.height)
```

### 2. Added Stream Info Endpoint (api/routes.py)

Created `/api/stream/info` endpoint to provide actual stream dimensions:

```python
@app.get("/api/stream/info")
async def get_stream_info():
    """Get video stream information including dimensions after rotation."""
    video_enc = _app_state.get("video_encoder")
    if not video_enc:
        raise HTTPException(status_code=503, detail="Video encoder not available")

    width, height = video_enc.frame_size
    return {
        "width": width,
        "height": height,
        "rotation_degrees": video_enc.config.rotation_degrees,
        "fps": video_enc.config.fps,
        "codec": video_enc.config.codec,
        "is_running": video_enc.is_running,
    }
```

### 3. Updated Client to Adjust Aspect Ratio Dynamically (debug/index.html)

Modified `startVideo()` to query stream dimensions and adjust the container's aspect ratio:

```javascript
async function startVideo() {
    const container = document.getElementById('videoContainer');
    
    // Get stream info to set correct aspect ratio
    try {
        const info = await apiCall('/api/stream/info');
        if (info) {
            const aspectRatio = `${info.width}/${info.height}`;
            container.style.aspectRatio = aspectRatio;
            log(`Video dimensions: ${info.width}×${info.height} (${info.rotation_degrees}° rotation)`);
        }
    } catch (error) {
        log('Using default aspect ratio', 'warning');
    }
    
    container.innerHTML = `<img src="http://${apiBase}/api/stream/video" style="width: 100%; height: 100%; object-fit: contain;">`;
    log('Video stream started');
}
```

## Testing

The fix has been tested with the following rotation values:
- ✓ 0° - No rotation (640×480)
- ✓ 90° - Clockwise rotation (480×640)
- ✓ 180° - Upside down (640×480)
- ✓ 270° - Counter-clockwise rotation (480×640)
- ✓ 360° - Full rotation, same as 0° (640×480)

## Usage

You can now safely change `CAMERA_ROTATION_DEGREES` in `config.py` to any of the supported values:

```python
# Camera image rotation in degrees (clockwise): 0, 90, 180, 270
CAMERA_ROTATION_DEGREES = 90  # Example: rotate 90° clockwise
```

The video stream will automatically:
1. Rotate frames on the server side
2. Report correct dimensions via the API
3. Display with the proper aspect ratio in the client

## API Changes

### New Endpoint

**GET /api/stream/info**

Returns video stream metadata:

```json
{
  "width": 480,
  "height": 640,
  "rotation_degrees": 90,
  "fps": 30,
  "codec": "h264",
  "is_running": true
}
```

## Backward Compatibility

This fix is fully backward compatible:
- Existing code continues to work without changes
- The default rotation of 0° maintains original behavior
- The new `/api/stream/info` endpoint is optional
