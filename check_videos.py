import os
import shutil
import argparse
import sys
from datetime import datetime

# --- LIBRARIES ---
try:
    from hachoir.parser import createParser
    from hachoir.metadata import extractMetadata
except ImportError:
    print("[ERROR] 'hachoir' library not found.")
    print("Please run: pip install hachoir")
    sys.exit(1)

# --- CONFIGURATION ---
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.mts', '.m2ts'}
IGNORE_DIRS = {'$RECYCLE.BIN', 'System Volume Information', 'Recycled', '.Trashes', '.venv', '.git'}

def get_video_info(filepath):
    """
    Attempts to parse the video.
    Returns: (is_valid, details_string)
    """
    try:
        parser = createParser(filepath)
        if not parser:
            return False, "Header unreadable"

        with parser:
            metadata = extractMetadata(parser)
            if not metadata:
                return False, "No metadata found"

            # A valid video must have at least a duration or a resolution
            duration = metadata.get('duration')
            width = metadata.get('width')
            
            if duration or width:
                return True, f"Valid (Duration: {duration}, Res: {width})"
            else:
                return False, "Empty metadata (Zombie file)"
                
    except Exception as e:
        return False, f"Crash during parse: {str(e)}"

def scan_videos(src_dir, quarantine_dir=None, move_corrupt=False):
    src_dir = os.path.abspath(src_dir)
    print(f"Scanning for videos in: {src_dir}")
    print("-" * 60)

    stats = {'total': 0, 'valid': 0, 'corrupt': 0}
    
    if move_corrupt and quarantine_dir:
        if not os.path.exists(quarantine_dir):
            os.makedirs(quarantine_dir)
            print(f"[INFO] Created quarantine folder: {quarantine_dir}")

    for root, dirs, files in os.walk(src_dir):
        # Skip ignore dirs
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith('.')]
        
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in VIDEO_EXTENSIONS:
                continue

            filepath = os.path.join(root, filename)
            stats['total'] += 1
            
            # Print current file (overwrite line for clean output)
            print(f"Checking: {filename}...", end='\r')

            is_valid, message = get_video_info(filepath)

            if is_valid:
                stats['valid'] += 1
                # Optional: Uncomment to see valid files
                # print(f"[OK] {filename} - {message}") 
            else:
                stats['corrupt'] += 1
                print(f" [CORRUPT] {filename}")
                print(f"    -> Reason: {message}")
                
                if move_corrupt and quarantine_dir:
                    try:
                        target = os.path.join(quarantine_dir, filename)
                        # Handle name collision
                        if os.path.exists(target):
                            base, ext = os.path.splitext(filename)
                            target = os.path.join(quarantine_dir, f"{base}_{datetime.now().strftime('%M%S')}{ext}")
                            
                        shutil.move(filepath, target)
                        print(f"    -> MOVED to Quarantine")
                    except Exception as e:
                        print(f"    -> ERROR MOVING: {e}")

    print("-" * 60)
    print(f"Scan Complete.")
    print(f"Total Videos:   {stats['total']}")
    print(f"Valid Videos:   {stats['valid']}")
    print(f"Corrupt Videos: {stats['corrupt']}")
    if move_corrupt:
        print(f"Corrupt files moved to: {quarantine_dir}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Find and quarantine corrupt video files.")
    parser.add_argument("src", help="Folder to scan")
    parser.add_argument("--move", action="store_true", help="Move corrupt videos to a 'Corrupt_Quarantine' folder")
    
    args = parser.parse_args()
    
    # Define quarantine path inside the source folder
    quarantine_path = os.path.join(args.src, "Corrupt_Quarantine")
    
    scan_videos(args.src, quarantine_path, args.move)