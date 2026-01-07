import os
import shutil
import hashlib
import json
import argparse
import sys
import stat
import time
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
    print("[WARNING] 'hachoir' not installed. Cannot detect corrupt videos accurately.")
    print("Run: pip install hachoir")

# --- CONFIGURATION ---
MIN_FILE_SIZE = 102400
MIN_DIMENSION = 600
SEPARATE_NO_EXIF = True 

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.tiff', '.heic', '.heif'}
RAW_EXTENSIONS = {'.arw', '.cr2', '.nef', '.dng'}
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.mts', '.m2ts'}
IGNORE_DIRS = {'$RECYCLE.BIN', 'System Volume Information', 'Recycled', '.Trashes'}

def debug_log(msg, debug_mode):
    if debug_mode:
        print(f"[DEBUG {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def get_hash(filepath, mode='file', debug_mode=False):
    ext = os.path.splitext(filepath)[1].lower()
    if mode == 'content':
        if ext in IMAGE_EXTENSIONS or ext in RAW_EXTENSIONS:
            try:
                debug_log(f"Visual hashing: {filepath}", debug_mode)
                with Image.open(filepath) as img:
                    return hashlib.sha256(img.tobytes()).hexdigest()
            except: pass
    
    debug_log(f"File hashing: {filepath}", debug_mode)
    hasher = hashlib.sha256()
    try:
        with open(filepath, 'rb') as f:
            while chunk := f.read(1024 * 1024):
                hasher.update(chunk)
        return hasher.hexdigest()
    except: return None

# --- NEW: VIDEO VALIDATOR ---
def is_video_valid(filepath, debug_mode=False):
    """
    Returns True if the video structure is readable.
    Returns False if the file header/footer is corrupted.
    """
    if not HACHOIR_AVAILABLE:
        return True # Assume valid if we can't check
    
    try:
        parser = createParser(filepath)
        if not parser:
            debug_log(f"Invalid Video Container: {filepath}", debug_mode)
            return False
            
        with parser:
            metadata = extractMetadata(parser)
            if metadata:
                # If we can read duration or width, it's likely playable
                if metadata.get('duration') or metadata.get('width'):
                    return True
                    
        debug_log(f"Video parsed but empty metadata: {filepath}", debug_mode)
        return False
    except Exception as e:
        debug_log(f"Video Corruption Error: {e}", debug_mode)
        return False

def get_date_taken(filepath, debug_mode=False):
    file_ext = os.path.splitext(filepath)[1].lower()
    
    # 1. RAW FILES
    if file_ext in RAW_EXTENSIONS:
        try:
            with open(filepath, 'rb') as f:
                tags = exifread.process_file(f, details=False, stop_tag='EXIF DateTimeOriginal')
                dt = tags.get('EXIF DateTimeOriginal')
                if dt: return datetime.strptime(str(dt), '%Y:%m:%d %H:%M:%S'), 'exif'
        except: pass

    # 2. STANDARD IMAGES
    elif file_ext in IMAGE_EXTENSIONS:
        try:
            with Image.open(filepath) as img:
                exif = img.getexif()
                if exif:
                    dt = exif.get(36867) or exif.get(306)
                    if dt: return datetime.strptime(dt, '%Y:%m:%d %H:%M:%S'), 'exif'
        except: pass 
    
    # 3. VIDEOS (Metadata)
    elif file_ext in VIDEO_EXTENSIONS:
        if HACHOIR_AVAILABLE:
            try:
                parser = createParser(filepath)
                if parser:
                    with parser:
                        metadata = extractMetadata(parser)
                        if metadata:
                            date_found = metadata.get('creation_date') or metadata.get('date_time_original')
                            if date_found: return date_found, 'video_meta'
            except: pass

    # 4. FALLBACK
    debug_log("Using File System Date (mtime)", debug_mode)
    return datetime.fromtimestamp(os.path.getmtime(filepath)), 'mtime'

def passes_filters(filepath, debug_mode=False):
    if os.path.getsize(filepath) < MIN_FILE_SIZE: 
        debug_log("Filter: Too small", debug_mode)
        return False
    ext = os.path.splitext(filepath)[1].lower()
    if ext in IMAGE_EXTENSIONS:
        try:
            with Image.open(filepath) as img:
                w, h = img.size
                if w < MIN_DIMENSION and h < MIN_DIMENSION: 
                    debug_log("Filter: Dimensions too small", debug_mode)
                    return False
        except: return False
    return True

def load_db(db_path):
    if os.path.exists(db_path):
        try:
            with open(db_path, 'r') as f: return json.load(f)
        except: pass
    return {}

def save_db(db_path, data):
    try:
        with open(db_path, 'w') as f: json.dump(data, f, indent=4)
    except: pass

def force_delete(filepath):
    try:
        os.chmod(filepath, stat.S_IWRITE)
        os.remove(filepath)
        return True
    except: return False

def force_move(src, dst):
    try:
        shutil.move(src, dst)
    except Exception:
        shutil.copy2(src, dst)
        if not os.path.exists(dst): 
            raise OSError("Copy failed")
        force_delete(src)

def build_size_map(dest_dir):
    print(f"Building Size Map of {dest_dir}...")
    size_map = set()
    for root, _, files in os.walk(dest_dir):
        for f in files:
            try: size_map.add(os.path.getsize(os.path.join(root, f)))
            except: pass
    print(f"Mapped sizes of {len(size_map)} existing files.")
    return size_map

def safe_walker(top_dir, debug_mode=False):
    if debug_mode: print(f"[DEBUG] Entering directory: {top_dir}", flush=True)
    try:
        with os.scandir(top_dir) as it:
            for entry in it:
                if entry.name in IGNORE_DIRS or entry.name.startswith('.'):
                    continue
                if entry.is_dir():
                    yield from safe_walker(entry.path, debug_mode)
                elif entry.is_file():
                    yield entry.path
    except: pass

def organize_photos(src_dir, dest_dir, dry_run=False, move_files=False, 
                   dup_action='move', junk_action='ignore', compare_mode='file', debug_mode=False):
    
    src_dir = os.path.abspath(src_dir)
    dest_dir = os.path.abspath(dest_dir)
    
    # Init variables first
    action_verb = "MOVING" if move_files else "COPYING"
    stats = {'success': 0, 'duplicates': 0, 'junk': 0, 'errors': 0, 'no_meta': 0, 'corrupt_video': 0, 'deleted_dups': 0}
    SAVE_INTERVAL = 50 
    
    index_filename = "photo_index_visual.json" if compare_mode == 'content' else "photo_index.json"
    db_file = os.path.join(dest_dir, index_filename)
    seen_hashes = load_db(db_file)
    
    dest_size_map = build_size_map(dest_dir)

    duplicates_dir = os.path.join(dest_dir, "Duplicates")
    junk_dir = os.path.join(dest_dir, "Skipped_Junk")
    no_meta_dir = os.path.join(dest_dir, "No_Metadata_Images")
    corrupt_video_dir = os.path.join(dest_dir, "Corrupt_Videos") # New Folder

    if not dry_run:
        if dup_action == 'move' and not os.path.exists(duplicates_dir): os.makedirs(duplicates_dir)
        if junk_action == 'move' and not os.path.exists(junk_dir): os.makedirs(junk_dir)
        if SEPARATE_NO_EXIF and not os.path.exists(no_meta_dir): os.makedirs(no_meta_dir)
        if not os.path.exists(corrupt_video_dir): os.makedirs(corrupt_video_dir)

    files_found = 0
    print(f"Scanning {src_dir} (Video Integrity Check Active)...")

    try:
        for original_path in safe_walker(src_dir, debug_mode):
            files_found += 1
            if files_found % 100 == 0:
                print(f"[Scanning] Found {files_found} files...", end='\r', flush=True)

            filename = os.path.basename(original_path)
            file_ext = os.path.splitext(filename)[1].lower()
            
            if file_ext not in IMAGE_EXTENSIONS and file_ext not in VIDEO_EXTENSIONS and file_ext not in RAW_EXTENSIONS:
                continue

            debug_log(f"--- Processing: {filename} ---", debug_mode)

            try:
                # 1. Filters (Junk)
                if not passes_filters(original_path, debug_mode):
                    stats['junk'] += 1
                    if junk_action == 'delete': force_delete(original_path)
                    elif junk_action == 'move' and not dry_run: 
                            target = os.path.join(junk_dir, filename)
                            if move_files: force_move(original_path, target)
                            else: shutil.copy2(original_path, target)
                    continue
                
                # 2. Corrupt Video Check
                if file_ext in VIDEO_EXTENSIONS:
                    if not is_video_valid(original_path, debug_mode):
                        stats['corrupt_video'] += 1
                        print(f"[CORRUPT VIDEO] {filename} -> Corrupt_Videos/")
                        if not dry_run:
                            target = os.path.join(corrupt_video_dir, filename)
                            if move_files: force_move(original_path, target)
                            else: shutil.copy2(original_path, target)
                        continue

                # 3. Size Check (Optimization)
                src_size = os.path.getsize(original_path)
                if src_size not in dest_size_map:
                    debug_log("Size unique. Skipping hash.", debug_mode)
                    file_hash = None
                else:
                    debug_log("Size match. Hashing...", debug_mode)
                    file_hash = get_hash(original_path, mode=compare_mode, debug_mode=debug_mode)

                # 4. Duplicates
                if file_hash and file_hash in seen_hashes:
                    debug_log("Duplicate detected.", debug_mode)
                    stats['duplicates'] += 1
                    if dup_action == 'delete': 
                        if force_delete(original_path): stats['deleted_dups'] += 1
                    elif dup_action == 'move' and not dry_run:
                        target = os.path.join(duplicates_dir, filename)
                        if move_files: force_move(original_path, target)
                        else: shutil.copy2(original_path, target)
                    elif dup_action == 'ignore':
                        print(f"[IGNORED DUP] {filename}")
                    continue

                # 5. Sorting
                date_obj, source_type = get_date_taken(original_path, debug_mode)
                is_image = file_ext in IMAGE_EXTENSIONS or file_ext in RAW_EXTENSIONS
                
                if SEPARATE_NO_EXIF and is_image and source_type == 'mtime':
                    stats['no_meta'] += 1
                    if not dry_run:
                        target_path = os.path.join(no_meta_dir, filename)
                        counter = 1
                        while os.path.exists(target_path):
                            base, ext = os.path.splitext(filename)
                            target_path = os.path.join(no_meta_dir, f"{base}_{counter}{ext}")
                            counter += 1
                        if move_files: force_move(original_path, target_path)
                        else: shutil.copy2(original_path, target_path)
                        print(f"[NO METADATA] {filename}")
                    continue

                year_folder = date_obj.strftime('%Y')
                month_folder = date_obj.strftime('%m-%B')
                new_filename = date_obj.strftime('%Y-%m-%d_%H-%M-%S') + file_ext
                target_folder = os.path.join(dest_dir, year_folder, month_folder)
                
                if not dry_run:
                    if not os.path.exists(target_folder): os.makedirs(target_folder)
                    
                    target_path = os.path.join(target_folder, new_filename)
                    counter = 1
                    while os.path.exists(target_path):
                        name_no_ext = date_obj.strftime('%Y-%m-%d_%H-%M-%S')
                        target_path = os.path.join(target_folder, f"{name_no_ext}_{counter}{file_ext}")
                        counter += 1
                    
                    if move_files: force_move(original_path, target_path)
                    else: shutil.copy2(original_path, target_path)
                    
                    print(f"[OK] {filename} -> {year_folder}/{month_folder}")

                    if not file_hash:
                        file_hash = get_hash(target_path, mode=compare_mode, debug_mode=debug_mode)
                    
                    if file_hash:
                        seen_hashes[file_hash] = os.path.relpath(target_path, dest_dir)
                        dest_size_map.add(src_size)
                        stats['success'] += 1
                        if stats['success'] % SAVE_INTERVAL == 0: save_db(db_file, seen_hashes)
                else:
                    print(f"[DRY RUN] {filename} -> {year_folder}/{month_folder}")

            except (PermissionError, OSError) as e:
                print(f"[ERROR] {filename}: {e}")
                stats['errors'] += 1

    except KeyboardInterrupt:
        print("\n[STOP] User stopped.")
    
    finally:
        if not dry_run: save_db(db_file, seen_hashes)
        print("-" * 40)
        print(f"Action: {action_verb}")
        print(f"Sorted:         {stats['success']}")
        print(f"Duplicates:     {stats['duplicates']}")
        print(f"Corrupt Videos: {stats['corrupt_video']}")
        if stats['deleted_dups'] > 0: print(f"Deleted Dups:   {stats['deleted_dups']}")
        print("-" * 40)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("src", nargs="?", default=r"W:\Recovered_Files")
    parser.add_argument("dest", nargs="?", default=r"D:\Sorted")
    parser.add_argument("--move", action="store_true", help="MOVE files")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--compare-mode", choices=['file', 'content'], default='file')
    parser.add_argument("--dup-action", choices=['move', 'delete', 'ignore'], default='move')
    parser.add_argument("--junk-action", choices=['move', 'delete', 'ignore'], default='ignore')
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    
    organize_photos(args.src, args.dest, args.dry_run, args.move, 
                   args.dup_action, args.junk_action, args.compare_mode, args.debug)