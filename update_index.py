import os
import hashlib
import json
import time
import argparse
import sys

# --- CONFIGURATION ---
EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.gif', '.heic', '.heif',
    '.arw', '.cr2', '.nef', '.dng',
    '.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.mts', '.m2ts'
}

def get_file_hash(filepath):
    """ Generates SHA-256 hash. """
    hasher = hashlib.sha256()
    try:
        with open(filepath, 'rb') as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        return None

def load_db(db_path):
    if os.path.exists(db_path):
        try:
            with open(db_path, 'r') as f:
                return json.load(f)
        except: pass
    return {}

def save_db(db_path, data):
    try:
        with open(db_path, 'w') as f:
            json.dump(data, f, indent=4)
        print(f"[SAVED] Database updated at {db_path}")
    except Exception as e:
        print(f"[ERROR] Could not save database: {e}")

def update_index(target_folder):
    # Ensure the path is absolute (handles relative paths like '.')
    target_folder = os.path.abspath(target_folder)
    
    if not os.path.exists(target_folder):
        print(f"[ERROR] Path does not exist: {target_folder}")
        return

    db_file = os.path.join(target_folder, "photo_index.json")
    
    print(f"Target Directory: {target_folder}")
    print(f"Database File:    {db_file}")
    
    # 1. Load existing data
    seen_hashes = load_db(db_file)
    initial_count = len(seen_hashes)
    print(f"Loaded {initial_count} existing entries.")

    print(f"Scanning folder for new files...")
    
    new_count = 0
    start_time = time.time()

    # 2. Walk the folder
    for root, dirs, files in os.walk(target_folder):
        for filename in files:
            file_ext = os.path.splitext(filename)[1].lower()
            if file_ext not in EXTENSIONS:
                continue

            # Skip the index file itself
            if filename == "photo_index.json":
                continue

            full_path = os.path.join(root, filename)
            
            # Create relative path from the target root
            rel_path = os.path.relpath(full_path, target_folder)

            # 3. Check if we already know this RELATIVE path
            if rel_path in seen_hashes.values():
                continue

            # 4. If unknown, HASH IT
            print(f"Indexing: {filename}...", end='\r')
            file_hash = get_file_hash(full_path)
            
            if file_hash:
                seen_hashes[file_hash] = rel_path
                new_count += 1
                
                # Periodic save
                if new_count % 100 == 0:
                    save_db(db_file, seen_hashes)

    # 5. Final Save
    save_db(db_file, seen_hashes)
    
    duration = time.time() - start_time
    print("\n" + "="*40)
    print(f"Scan Complete in {duration:.2f} seconds")
    print(f"New items added: {new_count}")
    print(f"Total items in index: {len(seen_hashes)}")
    print("="*40)

if __name__ == "__main__":
    # Setup command line argument parsing
    parser = argparse.ArgumentParser(description="Scan a folder and update the photo hash index.")
    
    # Add optional argument for the folder path
    parser.add_argument("folder", nargs="?", help="The folder to scan. Defaults to current directory.", default=os.getcwd())
    
    args = parser.parse_args()
    
    # Run the function with the provided or default folder
    update_index(args.folder)