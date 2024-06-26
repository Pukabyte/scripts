import os
import argparse
import time
import shared.debrid  # Run validation
from shared.arr import Sonarr, Radarr
from shared.discord import discordUpdate
from shared.shared import repair, realdebrid, torbox, intersperse
import requests

def parse_interval(interval_str):
    """Parse a smart interval string (e.g., '1w2d3h4m5s') into seconds."""
    if not interval_str:
        return 0
    total_seconds = 0
    time_dict = {'w': 604800, 'd': 86400, 'h': 3600, 'm': 60, 's': 1}
    current_number = ''
    for char in interval_str:
        if char.isdigit():
            current_number += char
        elif char in time_dict and current_number:
            total_seconds += int(current_number) * time_dict[char]
            current_number = ''
    return total_seconds

def refresh_and_rescan(media, arr):
    """Send refresh and rescan command to Sonarr or Radarr."""
    try:
        if isinstance(arr, Sonarr):
            url = f"{os.getenv('SONARR_HOST')}/api/command"
            api_key = os.getenv('SONARR_API_KEY')
            payload = {'name': 'RescanSeries', 'seriesId': media.id}
        elif isinstance(arr, Radarr):
            url = f"{os.getenv('RADARR_HOST')}/api/v3/command"
            api_key = os.getenv('RADARR_API_KEY')
            payload = {'name': 'RescanMovie', 'movieId': media.id}
        else:
            print("Unknown Arr instance.")
            return

        headers = {
            'Content-Type': 'application/json',
            'X-Api-Key': api_key
        }

        print(f"Sending refresh & rescan command to {arr.__class__.__name__} for {media.title}. URL: {url}, Payload: {payload}")

        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 201:
            print(f"Successfully sent refresh & rescan command to {arr.__class__.__name__} for {media.title}.")
        else:
            print(f"Failed to send refresh & rescan command to {arr.__class__.__name__} for {media.title}. Response: {response.text}")

    except Exception as e:
        print(f"Error sending refresh & rescan command: {str(e)}")

# Parse arguments for dry run, no confirm options, and optional intervals
parser = argparse.ArgumentParser(description='Repair broken symlinks and manage media files.')
parser.add_argument('--dry-run', action='store_true', help='Perform a dry run without making any changes.')
parser.add_argument('--no-confirm', action='store_true', help='Execute without confirmation prompts.')
parser.add_argument('--repair-interval', type=str, default=repair['repairInterval'], help='Optional interval in smart format (e.g. 1h2m3s) to wait between repairing each media file.')
parser.add_argument('--run-interval', type=str, default=repair['runInterval'], help='Optional interval in smart format (e.g. 1w2d3h4m5s) to run the repair process.')
args = parser.parse_args()

if not args.repair_interval and not args.run_interval:
    print("Running repair once")
else:
    print(f"Running repair{' once every ' + args.run_interval if args.run_interval else ''}{', and waiting ' + args.repair_interval + ' between each repair.' if args.repair_interval else '.'}")

try:
    repair_interval_seconds = parse_interval(args.repair_interval)
except Exception as e:
    print(f"Invalid interval format for repair interval: {args.repair_interval}")
    exit(1)

try:
    run_interval_seconds = parse_interval(args.run_interval)
except Exception as e:
    print(f"Invalid interval format for run interval: {args.run_interval}")
    exit(1)

def main():
    print("Collecting media...")
    sonarr = Sonarr()
    radarr = Radarr()
    sonarrMedia = [(sonarr, media) for media in sonarr.getAll() if media.anyMonitoredChildren]
    radarrMedia = [(radarr, media) for media in radarr.getAll() if media.anyMonitoredChildren]
    print("Finished collecting media.")
    
    for arr, media in intersperse(sonarrMedia, radarrMedia):
        files = {}
        for file in arr.getFiles(media):
            if file.parentId in files:
                files[file.parentId].append(file)
            else:
                files[file.parentId] = [file]
        for childId in media.monitoredChildrenIds:
            realPaths = []
            brokenSymlinks = []

            childFiles = files.get(childId, [])
            for childFile in childFiles:
                fullPath = childFile.path
                try:
                    destinationPath = os.readlink(fullPath)
                    realPath = os.path.realpath(fullPath)
                    realPaths.append(realPath)
                    
                    if os.path.islink(fullPath):
                        if ((realdebrid['enabled'] and destinationPath.startswith(realdebrid['mountTorrentsPath']) and not os.path.exists(destinationPath)) or 
                           (torbox['enabled'] and destinationPath.startswith(torbox['mountTorrentsPath']) and not os.path.exists(realPath))):
                            brokenSymlinks.append(realPath)
                except FileNotFoundError:
                    print(f"FileNotFoundError: {fullPath} not found.")
                    refresh_and_rescan(media, arr)
                    continue
            
            # If not full season just repair individual episodes?
            if brokenSymlinks:
                print("Title:", media.title)
                print("Movie ID/Season Number:", childId)
                print("Broken symlinks:")
                [print(brokenSymlink) for brokenSymlink in brokenSymlinks]
                print()
                if args.dry_run or args.no_confirm or input("Do you want to delete and re-grab? (y/n): ").lower() == 'y':
                    discordUpdate(f"Repairing... {media.title} - {childId}")
                    print("Deleting files:")
                    [print(childFile.path) for childFile in childFiles]
                    if not args.dry_run:
                        try:
                            results = arr.deleteFiles(childFiles)
                            print("Remonitoring")
                            media = arr.get(media.id)
                            media.setChildMonitored(childId, False)
                            arr.put(media)
                            media.setChildMonitored(childId, True)
                            arr.put(media)
                            print("Searching for new files")
                            results = arr.automaticSearch(media, childId)
                            print(results)
                        except FileNotFoundError as e:
                            print(f"File not found error: {str(e)}")
                            refresh_and_rescan(media, arr)
                        
                        if repair_interval_seconds > 0:
                            time.sleep(repair_interval_seconds)
                else:
                    print("Skipping")
                print()
            else:
                parentFolders = set(os.path.dirname(path) for path in realPaths)
                if childId in media.fullyAvailableChildrenIds and len(parentFolders) > 1:
                    print("Title:", media.title)
                    print("Movie ID/Season Number:", childId)
                    print("Inconsistent folders:")
                    [print(parentFolder) for parentFolder in parentFolders]
                    print()

if run_interval_seconds > 0:
    while True:
        main()
        time.sleep(run_interval_seconds)
else:
    main()
