#!/usr/bin/python3

import sys
import os
import json
import base64
import argparse
import requests
from dotenv import load_dotenv, set_key
from bs4 import BeautifulSoup
import urllib3
import aiohttp
import asyncio
import pyfiglet
import subprocess
import platform
from requests.exceptions import SSLError

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def run_node_server():
    """Start the Node.js server in the background, supporting both Windows and Linux, without displaying output, and then terminate the Python script."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    node_dir = os.path.join(script_dir, 'node')
    js_loc = os.path.join(node_dir, 'scan.js')
    node_modules_dir = os.path.join(node_dir, 'node_modules')

    npm_path = 'npm.cmd' if platform.system() == 'Windows' else 'npm'

    try:
        if os.path.exists(node_dir):
            os.chdir(node_dir)
        else:
            raise FileNotFoundError(f"Node directory not found: {node_dir}")

        if not os.path.exists(node_modules_dir):
            print("Installing Node.js modules...")
            subprocess.run([npm_path, 'i'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            print("Node.js modules installed.")

        print("Starting Node.js server...")
        process = subprocess.Popen(['node', js_loc], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            process.communicate(timeout=10)
            print("Node.js server started successfully.")
        except subprocess.TimeoutExpired:
            print("Node.js server did not start within the expected time. It may still be running.")

        sys.exit(0)

    except FileNotFoundError as fnf_error:
        print(f"[Error] {fnf_error}")
        sys.exit(1)
    except subprocess.CalledProcessError as cpe_error:
        print(f"[Error] Failed to install Node.js modules: {cpe_error}")
        sys.exit(1)
    except Exception as e:
        print(f"[Error] An unexpected error occurred while starting the Node.js server: {e}")
        sys.exit(1)


def init_key(apikey):
    """Initialize the API key in the .env file."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env_file = os.path.join(script_dir, 'config/.env')
    
    load_dotenv(dotenv_path=env_file)
    set_key(env_file, 'APIKEY', apikey)
    print(f"API key has been written to {env_file}")

def load_env_vars(mode):
    """Load environment variables based on the mode."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env_file = os.path.join(script_dir, 'config/.env')

    load_dotenv(dotenv_path=env_file)

    apikey = os.getenv('APIKEY') if mode == 'private' else os.getenv('PUBLIC_API_KEY')
    output_scan = os.getenv('OUTPUT_SCAN_PRIV') if mode == 'private' else os.getenv('OUTPUT_SCAN_PUB')
    host_scan = os.getenv('SCAN_API_HOST')
    host_scan_prod = os.getenv('PROD_SCAN_API_HOST')

    if not all([host_scan, output_scan]):
        raise ValueError("Missing required environment variables.")
    
    if mode == 'private' and not apikey:
        signup_url = os.getenv('SIGNUP_URL')
        raise ValueError(f"Create a password & apikey first at {signup_url}.\nThen run `subdosec -initkey your-key`")
    
    if mode == 'public':
        print(f"[WARNING] You are not using private mode; results will be public.")
    
    return apikey, output_scan, host_scan, host_scan_prod

def fetch_fingerprints(host_scan_prod):
    """Fetch fingerprints from the host scan API."""
    url = host_scan_prod.replace('/api/scan/cli', '/api/getfinger')
    response = requests.get(url,  timeout=30)
    response.raise_for_status()
    return response.json()

def extract_title(content):
    """Extract the title from the HTML content."""
    soup = BeautifulSoup(content, 'html.parser')
    return soup.title.string if soup.title else 'No title found'

async def undetect_site(siteinfo, apikey, host_scan_prod, mode):
    """Notify the server about undetected sites."""
    url = host_scan_prod.replace('/api/scan/cli', '/api/undetect/stored/cli')
    headers = {'Subdosec-Apikey': apikey}
    web_data_encoded = base64.b64encode(json.dumps(siteinfo.get('website_data')).encode('utf-8')).decode('utf-8')
    payload = {
        'web_data': web_data_encoded,
        'mode': mode
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as response:
           return await response.json()
        
async def vuln_site(siteinfo, fingerprint_id, apikey, host_scan_prod, mode):
    """Notify the server about undetected sites."""
    url = host_scan_prod.replace('/api/scan/cli', '/api/vuln/stored/cli')
    headers = {'Subdosec-Apikey': apikey}
    web_data_encoded = base64.b64encode(json.dumps(siteinfo).encode('utf-8')).decode('utf-8')
    payload = {
        'web_data': web_data_encoded,
        'mode': mode,
        'fingerprint_id': fingerprint_id
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as response:
           return await response.json()

async def save_local(w, fs, fid, o):

    save_path = os.path.join(o)
    
    if not os.path.exists(save_path):
        os.makedirs(save_path)

    file_name = f"{fs}_tko.txt"
    file_path = os.path.join(save_path, file_name)
    
    subdomain = w.get('subdomain', None)

    if subdomain:
        with open(file_path, 'a') as file:
            file.write(subdomain + '\n')
    else:
        print(f"No subdomain found in data.")

def autos_protocol(target):
    try:
        response = requests.get(target, verify=False, allow_redirects=True, timeout=30)
        if len(response.history) > 2:
            raise Exception(f"Too many redirects for {target}, skipping...")
        return response

    except SSLError as e:
        if '[SSL: TLSV1_ALERT_INTERNAL_ERROR]' in str(e):
            
            http_target = target.replace("https://", "http://")
            try:
                response = requests.get(http_target, verify=False, allow_redirects=False, timeout=30)
                return response

            except Exception as e:
                print(f"[Error] HTTP request failed: {e}")
                return None
        else:
            raise e

def analyze_target(target, mode, apikey, output_scan, host_scan, host_scan_prod, fingerprints, vuln_only, pe, o):
    """Analyze a single target and print the results."""

    try:
        response = autos_protocol(target)
        title = extract_title(response.text)
        status_code = response.history[0].status_code if response.history else response.status_code
        redirect_url = response.url if response.history else 'No redirects'

        count_finger = len(fingerprints['fingerprints'])

        match_response = []
        for index, fingerprint in enumerate(fingerprints['fingerprints'], start=1):
            in_body_match = fingerprint['rules'].get('in_body', 'subdosec') in response.text
            fingerprint_encoded = base64.b64encode(json.dumps(fingerprint).encode('utf-8')).decode('utf-8')
            progress = (index / count_finger) * 100  # Calculate percentage progress
            output_line = f"{target} [{progress:.2f}%]"

            sys.stdout.write(f"\r{output_line}")
            sys.stdout.flush()

            scan_payload = {
                'target': target,
                'mode': mode,
                'title_fu': title,
                'sc_fu': status_code,
                'body_fu': in_body_match,
                'redirect_url': redirect_url,
                'fingerprint_new': fingerprint_encoded,
            }
            scan_response = requests.post(host_scan, headers={'Subdosec-Apikey': apikey}, json=scan_payload)
            match_response.append(scan_response.json())

        if any(item.get('isMatched') for item in match_response):
     
            service = next(item.get('service').get('service') for item in match_response if item.get('isMatched'))
            web_data = next(item.get('website_data') for item in match_response if item.get('isMatched'))
            fingerprint_id = next(item.get('service').get('fid') for item in match_response if item.get('isMatched'))
            
            msg = f" [{service}] [VULN] [SAVED]" if o else f" [VULN] {output_scan}{service}"
            print(msg)

            if o:
                asyncio.run(save_local(web_data, service, fingerprint_id, o))
            else:
                asyncio.run(vuln_site(web_data, fingerprint_id, apikey, host_scan_prod, mode))

        elif not vuln_only:
            print(f" [UNDETECT]")
            asyncio.run(undetect_site(match_response[0], apikey, host_scan_prod, mode))
        else:
            pass 

    except Exception as e:
        if pe: print(f"[Error] {target} : {e}")

def check_fingerprint():
    try:
        apikey, output_scan, host_scan, host_scan_prod = load_env_vars('public')
        fingerprints = fetch_fingerprints(host_scan_prod)
        
        for fingerprint in fingerprints['fingerprints']:
            service = fingerprint['service']
            name = fingerprint['name']
            print(f"{service} | {name}")

    except Exception as e:
        print(f"[Error] : {e}")

def read_local_finger(file_path):
    try:
        with open(file_path, 'r') as file:
            data = json.load(file) 
            return data
    except FileNotFoundError:
        print(f"Error: The file at {file_path} was not found.")
        return None
    except json.JSONDecodeError:
        print(f"Error: The file at {file_path} is not a valid JSON file.")
        return None

def scan_by_web(mode, vuln_only, pe, lf, o, pf):
    """Main function to perform the web scanning."""
    try:
        apikey, output_scan, host_scan, host_scan_prod = load_env_vars(mode)
        fingerprints = fetch_fingerprints(host_scan_prod)

        lf_list = [x.strip() for x in lf.split(',')]
        
        filtered_fingerprints = {
            "fingerprints": [
                fingerprint for fingerprint in fingerprints['fingerprints']
                if fingerprint['service'] in lf_list
            ]
        }

        final_finger = read_local_finger(pf) if pf else (filtered_fingerprints if lf != 'all' else fingerprints)


        targets = [line.strip() for line in sys.stdin]

        for target in targets:
            analyze_target(target, mode, apikey, output_scan, host_scan, host_scan_prod, final_finger, vuln_only, pe, o)

    except ValueError as e:
        print(f"[Configuration Error] {e}")


def main():
    """Entry point for the script."""
    print(f"{pyfiglet.figlet_format('Subdosec')}\n")
    parser = argparse.ArgumentParser(description='Web scanner.')
    parser.add_argument('-mode', choices=['private', 'public'], default='public', help='Mode of operation (private/public)')
    parser.add_argument('-initkey', help='Initialize the API key')
    parser.add_argument('-vo', action='store_true', help='VULN Only: Hide UNDETECT messages')
    parser.add_argument('-pe', action='store_true', help='Print Error: When there are problems detecting your target')
    parser.add_argument('-ins', action='store_true', help='Prepar node & start server')
    parser.add_argument('-pf', type=str,  help='Private Fingerprint: uses your local fingerprint. Example: -pf /path/to/tko.json')
    parser.add_argument('-lf', default='all',  help='Fingerprint lock: to focus on one or multiple fingerprints. (-lf github.io,surge.sh) and leave this arg to scan all fingerprints')
    parser.add_argument('-sfid', action='store_true',  help='To view all available fingerprint ids.')
    parser.add_argument('-o', type=str, help='Save result locally to the specified path. Example: -o /path/to/dir')

    
    args = parser.parse_args()

    
    if args.initkey:
        init_key(args.initkey)
    elif args.ins:
        run_node_server()
    elif args.pf and not args.o:
        print("Error: If you use -pf, you must save the scan to local with -o /path/to/save")
        sys.exit(1)
    elif args.sfid:
        check_fingerprint()
    else:
        scan_by_web(args.mode, args.vo, args.pe, args.lf,  args.o,  args.pf)

if __name__ == "__main__":
    main()