#!/usr/bin/env python3
"""Download YAMNet TFLite model and class mapping for sound classification."""
import logging
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

# YAMNet model URLs
YAMNET_TFLITE_URL = "https://tfhub.dev/google/lite-model/yamnet/tflite/1?lite-format=tflite"
YAMNET_CLASS_MAP_URL = "https://raw.githubusercontent.com/tensorflow/models/master/research/audioset/yamnet/yamnet_class_map.csv"

# Alternative direct URLs (if TF Hub redirects fail)
YAMNET_TFLITE_DIRECT = "https://storage.googleapis.com/tfhub-lite-models/google/lite-model/yamnet/tflite/1.tflite"


def download_file(url: str, destination: Path, timeout: int = 120) -> bool:
    """Download a file from URL to destination."""
    try:
        logger.info(f"Downloading {url}...")
        
        # Create headers to mimic a browser
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        req = urllib.request.Request(url, headers=headers)
        
        with urllib.request.urlopen(req, timeout=timeout) as response:
            # Check if we got a redirect to another URL
            final_url = response.geturl()
            if final_url != url:
                logger.info(f"Redirected to: {final_url}")
            
            # Read and save content
            data = response.read()
            
            # Check if it's HTML (error page) instead of binary
            content_type = response.headers.get('Content-Type', '')
            if 'text/html' in content_type and len(data) > 0:
                # Might be a redirect page or error
                logger.warning(f"Received HTML content, may need to use alternative URL")
            
            destination.write_bytes(data)
            logger.info(f"Downloaded {len(data)} bytes to {destination}")
            return True
            
    except Exception as e:
        logger.error(f"Failed to download {url}: {e}")
        return False


def download_yamnet_model(models_dir: Path) -> bool:
    """Download YAMNet TFLite model.
    
    Args:
        models_dir: Directory to save the model
        
    Returns:
        True if successful
    """
    models_dir = Path(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    
    model_path = models_dir / "yamnet.tflite"
    
    if model_path.exists():
        logger.info(f"YAMNet model already exists at {model_path}")
        return True
    
    # Try direct URL first (more reliable)
    if download_file(YAMNET_TFLITE_DIRECT, model_path):
        return True
    
    # Fallback to TF Hub URL
    logger.info("Trying alternative download URL...")
    if download_file(YAMNET_TFLITE_URL, model_path):
        return True
    
    logger.error("Failed to download YAMNet model from all sources")
    return False


def download_class_map(models_dir: Path) -> bool:
    """Download YAMNet class mapping CSV.
    
    Args:
        models_dir: Directory to save the class map
        
    Returns:
        True if successful
    """
    models_dir = Path(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    
    class_map_path = models_dir / "yamnet_class_map.csv"
    
    if class_map_path.exists():
        logger.info(f"Class map already exists at {class_map_path}")
        return True
    
    if download_file(YAMNET_CLASS_MAP_URL, class_map_path):
        return True
    
    logger.error("Failed to download class map")
    return False


def verify_model(model_path: Path) -> bool:
    """Verify that the downloaded model is valid.
    
    Args:
        model_path: Path to the model file
        
    Returns:
        True if valid
    """
    try:
        model_path = Path(model_path)
        if not model_path.exists():
            return False
        
        # Check file size (should be around 3.9MB)
        size = model_path.stat().st_size
        if size < 1000000:  # Less than 1MB is suspicious
            logger.warning(f"Model file seems too small ({size} bytes)")
            return False
        
        # Try to load with TFLite to verify
        try:
            import tensorflow.lite as tflite
            interpreter = tflite.Interpreter(model_path=str(model_path))
            interpreter.allocate_tensors()
            logger.info(f"Model verified successfully ({size} bytes)")
            return True
        except ImportError:
            try:
                import tflite_runtime.interpreter as tflite
                interpreter = tflite.Interpreter(model_path=str(model_path))
                interpreter.allocate_tensors()
                logger.info(f"Model verified successfully ({size} bytes)")
                return True
            except ImportError:
                logger.warning("TFLite not available for verification, checking file size only")
                return size > 1000000
                
    except Exception as e:
        logger.error(f"Model verification failed: {e}")
        return False


def download_all(models_dir: str = None) -> bool:
    """Download all required YAMNet files.
    
    Args:
        models_dir: Directory to save files (default: audio/models)
        
    Returns:
        True if all downloads successful
    """
    if models_dir is None:
        models_dir = Path(__file__).parent
    else:
        models_dir = Path(models_dir)
    
    logger.info(f"Downloading YAMNet files to {models_dir}")
    
    success = True
    
    # Download model
    if not download_yamnet_model(models_dir):
        success = False
    else:
        # Verify model
        model_path = models_dir / "yamnet.tflite"
        if not verify_model(model_path):
            logger.error("Downloaded model failed verification")
            success = False
    
    # Download class map
    if not download_class_map(models_dir):
        success = False
    
    if success:
        logger.info("All YAMNet files downloaded successfully")
    else:
        logger.error("Some downloads failed")
    
    return success


def main():
    """Main entry point for command-line usage."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Download YAMNet model files")
    parser.add_argument(
        "--models-dir",
        type=str,
        default=None,
        help="Directory to save models (default: audio/models)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    
    # Setup logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    # Download files
    success = download_all(args.models_dir)
    
    if success:
        print("\n✓ YAMNet model files downloaded successfully!")
        print(f"  Location: {Path(__file__).parent if args.models_dir is None else args.models_dir}")
        return 0
    else:
        print("\n✗ Failed to download some files")
        print("  You can manually download from:")
        print(f"    Model: {YAMNET_TFLITE_DIRECT}")
        print(f"    Class map: {YAMNET_CLASS_MAP_URL}")
        return 1


if __name__ == "__main__":
    exit(main())
