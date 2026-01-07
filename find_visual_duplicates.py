import os
import hashlib
import argparse
from datetime import datetime
from PIL import Image

# --- LIBRARIES ---
# Image Support
try:
    from pillow_heif import register_heif_opener
    import exifread
    register_heif_opener()
except ImportError:
    pass

# Video Support (Hachoir)
try:
    from hachoir.parser import createParser
    from hachoir.metadata import extractMetadata
    HACHOIR_AVAILABLE = True
except ImportError:
    HACHOIR_AVAILABLE = False
    print("[WARNING] 'hachoir' not installed. Video dates will rely on file system.")

# --- CONFIGURATION ---
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.tiff', '.bmp', '.gif', '.heic', '.heif'}
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.mts', '.m2ts'}

def get_content_hash(filepath):
    """
    IMAGES: Decodes pixels and hashes them (Visual).
    VIDEOS: Hashes the file content stream (Binary).
    """
    ext = os.path.splitext(filepath)[1].lower()
    
    # 1. VISUAL HASH (Images)
    if ext in IMAGE_EXTENSIONS:
        try:
            with Image.open(filepath) as img:
                # Convert to RGB to ensure consistent pixel data
                img = img.convert('RGB')
                return hashlib.sha256(img.tobytes()).hexdigest()
        except Exception:
            # If image is corrupt, we can't visually compare it.
            return None

    # 2. BINARY HASH (Videos)
    elif ext in VIDEO_EXTENSIONS:
        try:
            hasher = hashlib.sha256()
            with open(filepath, 'rb') as f:
                while chunk := f.read(1024 * 1024): # Read in 1MB chunks
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception:
            return None

    return None

def get_date_taken(filepath):
    """
    Returns the oldest possible date for the file (Video Meta > EXIF > File System).
    """
    ext = os.path.splitext(filepath)[1].lower()
    
    # A. VIDEOS (Try Hachoir first)
    if ext in VIDEO_EXTENSIONS and HACHOIR_AVAILABLE:
        try:
            parser = createParser(filepath)
            if parser:
                with parser:
                    metadata = extractMetadata(parser)
                    if metadata:
                        date_found = metadata.get('creation_date') or metadata.get('date_time_original')
                        if date_found: return date_found
        except: pass

    # B. IMAGES (Try EXIF)
    if ext in IMAGE_EXTENSIONS:
        # 1. Try ExifRead
        try:
            with open(filepath, 'rb') as f:
                tags = exifread.process_file(f, details=False, stop_tag='EXIF DateTimeOriginal')
                date_tag = tags.get('EXIF DateTimeOriginal')
                if date_tag:
                    return datetime.strptime(str(date_tag), '%Y:%m:%d %H:%M:%S')
        except: pass
        
        # 2. Try Pillow
        try:
            with Image.open(filepath) as img:
                exif = img.getexif()
                if exif:
                    dt = exif.get(36867) or exif.get(306)
                    if dt: return datetime.strptime(dt, '%Y:%m:%d %H:%M:%S')
        except: pass

    # C. FALLBACK (File System)
    return datetime.fromtimestamp(os.path.getmtime(filepath))

def find_duplicates(folder, delete=False):
    folder = os.path.abspath(folder)
    print(f"Scanning {folder} for duplicates (Images & Videos)...")
    
    # { "hash": [ {"path": "...", "date": datetime}, ... ] }
    files_by_hash = {}
    
    count = 0
    
    # 1. SCAN AND HASH
    for root, dirs, files in os.walk(folder):
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in IMAGE_EXTENSIONS and ext not in VIDEO_EXTENSIONS:
                continue
            
            filepath = os.path.join(root, filename)
            count += 1
            print(f"Hashing {count}: {filename}...", end='\r')
            
            content_hash = get_content_hash(filepath)
            
            if content_hash:
                date_taken = get_date_taken(filepath)
                
                if content_hash not in files_by_hash:
                    files_by_hash[content_hash] = []
                
                files_by_hash[content_hash].append({
                    'path': filepath,
                    'date': date_taken
                })

    print(f"\nScanned {count} files.")
    print("Analyzing duplicates...")
    print("-" * 50)

    total_dups_found = 0
    bytes_saved = 0

    # 2. ANALYZE
    for content_hash, file_list in files_by_hash.items():
        if len(file_list) > 1:
            # Sort: Oldest Date First, then Shortest Filename
            file_list.sort(key=lambda x: (x['date'], len(x['path'])))
            
            keeper = file_list[0]
            duplicates = file_list[1:]
            
            print(f"\n[GROUP] Found {len(duplicates)} duplicate(s):")
            print(f"  KEEPING (Oldest): {os.path.basename(keeper['path'])} ({keeper['date']})")
            
            for dup in duplicates:
                print(f"  DUPLICATE:        {os.path.basename(dup['path'])} ({dup['date']})")
                
                file_size = os.path.getsize(dup['path'])
                total_dups_found += 1
                bytes_saved += file_size
                
                if delete:
                    try:
                        os.remove(dup['path'])
                        print(f"     -> DELETED")
                    except Exception as e:
                        print(f"     -> ERROR DELETING: {e}")
                else:
                    print(f"     -> (Run with --delete to remove)")

    print("-" * 50)
    print(f"Total Duplicates Found: {total_dups_found}")
    print(f"Potential Space Reclaimed: {bytes_saved / (1024*1024):.2f} MB")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Find visually identical images and binary identical videos.")
    parser.add_argument("folder", help="Folder to scan")
    parser.add_argument("--delete", action="store_true", help="Actually delete the duplicates (Oldest is kept)")
    
    args = parser.parse_args()
    
    find_duplicates(args.folder, args.delete)