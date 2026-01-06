#!/usr/bin/env python3
"""
Complete script om alle audioboeken op te halen van onlinebibliotheek.nl

Dit script:
1. Logt in met username/password
2. Haalt catalogus op (HTML parsing)
3. Voor elk luisterboek: haalt alle hoofdstukken/MP3 files op
4. Slaat alles op in JSON

Gebruik:
    python get_all_audiobooks.py <username> <password> [output.json]
"""
import requests
import logging
import json
import re
import sys
import os
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

ONLINEBIBLIOTHEEK_BASE = "https://www.onlinebibliotheek.nl"
KB_LOGIN_BASE = "https://login.kb.nl"
ODILO_API_BASE = "https://nubeplayer.eu.odilo.io"


def login(username, password):
    """
    Log in op onlinebibliotheek.nl via login.kb.nl OAuth2 flow.
    
    Returns:
        requests.Session: Authenticated session with cookies
    """
    logger.info("=" * 80)
    logger.info("STEP 1: LOGIN")
    logger.info("=" * 80)
    
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Accept': 'application/json, text/html, */*',
        'Accept-Language': 'nl-NL,nl;q=0.9,en;q=0.8',
    })
    
    # Step 1: Get login page
    logger.info("Getting login page...")
    login_url = f"{ONLINEBIBLIOTHEEK_BASE}/account/inloggen.html"
    response = session.get(login_url, allow_redirects=True, timeout=10)
    
    if 'login.kb.nl' not in response.url:
        logger.error(f"Not redirected to login.kb.nl. Final URL: {response.url}")
        return None
    
    logger.info(f"✅ Redirected to: {response.url}")
    
    # Step 2: Get configuration
    logger.info("Getting configuration...")
    config_url = f"{KB_LOGIN_BASE}/si/login/api/configuration"
    session.get(config_url, timeout=10)
    
    # Step 3: Authenticate
    logger.info("Authenticating...")
    auth_url = f"{KB_LOGIN_BASE}/si/login/api/authenticate"
    auth_data = {
        'module': 'UsernameAndPassword',
        'definition': {
            'rememberMe': False,
            'username': username,
            'password': password,
        }
    }
    
    response = session.post(auth_url, json=auth_data, timeout=10)
    
    if response.status_code != 200:
        logger.error(f"Authentication failed: {response.status_code}")
        return None
    
    try:
        data = response.json()
        if 'token' not in data and 'jwtToken' not in data:
            logger.error("No token in response")
            return None
    except:
        logger.error("Failed to parse authentication response")
        return None
    
    logger.info("✅ Authentication successful")
    
    # Step 4: OAuth2 flow
    logger.info("Completing OAuth2 flow...")
    oauth_params = {
        'client_id': 'tdpweb',
        'scope': 'profile',
        'response_type': 'code',
        'redirect_uri': 'https://www.onlinebibliotheek.nl/account/logged.in.html',
    }
    
    authorize_url = f"{KB_LOGIN_BASE}/si/auth/oauth2.0/v1/authorize"
    oauth_response = session.get(authorize_url, params=oauth_params, allow_redirects=False, timeout=10)
    
    if oauth_response.status_code != 302:
        logger.error(f"OAuth2 authorize failed: {oauth_response.status_code}")
        return None
    
    location = oauth_response.headers.get('Location', '')
    if 'code=' not in location or 'onlinebibliotheek.nl' not in location:
        logger.error("No authorization code in redirect")
        return None
    
    # Step 5: Exchange code for cookies
    logger.info("Exchanging code for cookies...")
    parsed = urlparse(location)
    params = parse_qs(parsed.query)
    
    logged_in_url = f"{ONLINEBIBLIOTHEEK_BASE}/account/logged.in.html"
    logged_in_params = {
        'code': params.get('code', [None])[0],
        'scope': params.get('scope', ['profile'])[0],
        'iss': params.get('iss', [''])[0],
        'state': params.get('state', [''])[0],
        'client_id': params.get('client_id', ['tdpweb'])[0],
    }
    
    session.get(logged_in_url, params=logged_in_params, allow_redirects=True, timeout=10)
    
    if not session.cookies:
        logger.error("No cookies received after login")
        return None
    
    logger.info("✅ Login successful!")
    return session


def get_catalogus(session):
    """
    Haal catalogus op via HTML parsing.
    
    Returns:
        list: List of books with id, title, url, type, image
    """
    logger.info("\n" + "=" * 80)
    logger.info("STEP 2: GET CATALOGUS")
    logger.info("=" * 80)
    
    url = f"{ONLINEBIBLIOTHEEK_BASE}/account/boekenplank.html"
    response = session.get(url, timeout=10)
    
    if response.status_code != 200:
        logger.error(f"Failed to get catalogus: {response.status_code}")
        return []
    
    soup = BeautifulSoup(response.text, 'html.parser')
    book_links = soup.find_all('a', href=re.compile(r'/catalogus/\d+'))
    
    books = []
    seen_ids = set()
    
    for link in book_links:
        href = link.get('href', '')
        match = re.search(r'/catalogus/(\d+[Xx]?)', href)
        if match:
            book_id = match.group(1)
            if book_id not in seen_ids:
                seen_ids.add(book_id)
                title_text = link.get_text(strip=True)
                
                # Get cover image
                img = link.find('img')
                image_url = None
                if img:
                    image_url = img.get('src') or img.get('data-src')
                    if image_url and not image_url.startswith('http'):
                        image_url = f"{ONLINEBIBLIOTHEEK_BASE}{image_url}"
                
                # Determine type
                book_type = 'unknown'
                if 'luister' in href.lower() or 'luister' in title_text.lower():
                    book_type = 'luisterboek'
                elif 'e-book' in href.lower() or 'ebook' in title_text.lower():
                    book_type = 'e-book'
                
                books.append({
                    'id': book_id,
                    'title': title_text,
                    'url': href if href.startswith('http') else f"{ONLINEBIBLIOTHEEK_BASE}{href}",
                    'type': book_type,
                    'image': image_url,
                })
    
    logger.info(f"✅ Found {len(books)} books")
    return books


def get_state_from_book(session, book_url):
    """
    Haal state parameter op van boek pagina (voor luisterboeken).
    
    Returns:
        str: State parameter or None
    """
    response = session.get(book_url, timeout=10)
    
    if response.status_code != 200:
        return None
    
    soup = BeautifulSoup(response.text, 'html.parser')
    all_links = soup.find_all('a', href=re.compile(r'state='))
    
    for link in all_links:
        href = link.get('href', '')
        link_text = link.get_text(strip=True).lower()
        
        # Only consider links that explicitly mention "luister" or "audio"
        if 'luister' in link_text or 'audio' in link_text:
            state_match = re.search(r'state=([^&"\']+)', href)
            if state_match:
                return state_match.group(1)
    
    return None


def get_keyid_from_state(session, state):
    """
    Haal keyId op via state parameter redirect.
    
    Returns:
        tuple: (media_id, key_id) or (None, None)
    """
    redirect_url = f"{ONLINEBIBLIOTHEEK_BASE}/catalogus/download/redirect?state={state}"
    response = session.get(redirect_url, allow_redirects=False, timeout=10)
    
    if response.status_code == 302:
        location = response.headers.get('Location', '')
        match = re.search(r'/get/([^/]+)/key/([^/?]+)', location)
        if match:
            return match.group(1), match.group(2)
    
    return None, None


def get_all_chapters(session, media_id, key_id):
    """
    Haal alle hoofdstukken/MP3 files op via Odilo API.
    
    Returns:
        dict: API response with metadata and resources
    """
    api_url = f"{ODILO_API_BASE}/api/v1/media/{media_id}/play?keyId={key_id}"
    
    headers = {
        'Referer': f'{ODILO_API_BASE}/get/{media_id}/key/{key_id}',
        'Origin': ODILO_API_BASE,
    }
    
    response = session.get(api_url, headers=headers, timeout=10)
    
    if response.status_code != 200:
        return None
    
    try:
        return response.json()
    except:
        return None


def format_duration(seconds):
    """Format duration in seconds to readable format."""
    if not seconds or seconds < 0:
        return "N/A"
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    if hours > 0:
        return f"{hours}u {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    else:
        return f"{secs}s"


def sanitize_filename(filename):
    """Sanitize filename for filesystem."""
    # Remove or replace invalid characters
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    filename = filename.strip('. ')
    # Limit length
    if len(filename) > 200:
        filename = filename[:200]
    return filename


def download_mp3(session, url, filepath):
    """
    Download MP3 file from URL.
    
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        response = session.get(url, stream=True, timeout=30)
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0
        
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    # Show progress for large files
                    if total_size > 0:
                        percent = (downloaded / total_size) * 100
                        if downloaded % (1024 * 1024) < 8192:  # Update every MB
                            logger.debug(f"  Downloading: {percent:.1f}%")
        
        return True
    except Exception as e:
        logger.error(f"  Error downloading {url}: {e}")
        return False


def download_all_mp3s(session, audiobooks, downloads_dir):
    """
    Download all MP3 files for all audiobooks.
    
    Args:
        session: requests.Session
        audiobooks: List of audiobook dicts
        downloads_dir: Base directory for downloads
    
    Returns:
        dict: Download statistics
    """
    logger.info("\n" + "=" * 80)
    logger.info("STEP 4: DOWNLOAD ALL MP3 FILES")
    logger.info("=" * 80)
    
    stats = {
        'total_files': 0,
        'downloaded': 0,
        'failed': 0,
        'skipped': 0
    }
    
    for i, audiobook in enumerate(audiobooks, 1):
        if audiobook.get('error') or not audiobook.get('chapters'):
            continue
        
        # Create folder for this audiobook
        book_title = sanitize_filename(audiobook['title'])
        book_dir = os.path.join(downloads_dir, book_title)
        
        logger.info(f"\n[{i}/{len(audiobooks)}] Downloading: {audiobook['title']}")
        logger.info(f"  Folder: {book_dir}")
        
        for j, chapter in enumerate(audiobook['chapters'], 1):
            stats['total_files'] += 1
            
            if not chapter.get('url'):
                logger.warning(f"  ⚠️  Chapter {j}: No URL")
                stats['skipped'] += 1
                continue
            
            # Create filename
            chapter_title = sanitize_filename(chapter.get('title', f'Chapter_{j:02d}'))
            filename = f"{j:02d}_{chapter_title}.mp3"
            filepath = os.path.join(book_dir, filename)
            
            # Check if already exists
            if os.path.exists(filepath):
                logger.info(f"  ⏭️  Chapter {j}: Already exists ({filename})")
                stats['skipped'] += 1
                continue
            
            logger.info(f"  📥 Downloading chapter {j}/{len(audiobook['chapters'])}: {chapter_title}")
            
            # Download
            if download_mp3(session, chapter['url'], filepath):
                file_size = os.path.getsize(filepath) / (1024 * 1024)  # MB
                logger.info(f"  ✅ Downloaded: {filename} ({file_size:.1f} MB)")
                stats['downloaded'] += 1
                
                # Update chapter dict with local path
                chapter['local_path'] = filepath
            else:
                logger.error(f"  ❌ Failed: {filename}")
                stats['failed'] += 1
    
    return stats


def get_all_audiobooks(session, books):
    """
    Haal alle audioboeken op met hun hoofdstukken.
    
    Returns:
        list: List of audiobooks with all chapters
    """
    logger.info("\n" + "=" * 80)
    logger.info("STEP 3: GET ALL AUDIOBOOKS")
    logger.info("=" * 80)
    
    audiobooks = []
    
    for i, book in enumerate(books, 1):
        logger.info(f"\n[{i}/{len(books)}] Processing: {book['title']}")
        
        # Try to get state parameter - if it exists, it's likely a luisterboek
        # Don't skip based on type detection, try all books
        
        result = {
            'id': book['id'],
            'title': book['title'],
            'url': book['url'],
            'image': book.get('image'),
            'state': None,
            'media_id': None,
            'key_id': None,
            'metadata': None,
            'chapters': [],
            'total_chapters': 0,
            'error': None
        }
        
        try:
            # Get state - if no state, it's not a luisterboek
            state = get_state_from_book(session, book['url'])
            if not state:
                logger.info(f"  ⏭️  No state parameter found (not a luisterboek)")
                continue
            
            result['state'] = state
            
            # Get keyId
            media_id, key_id = get_keyid_from_state(session, state)
            if not key_id:
                result['error'] = 'Failed to get keyId'
                audiobooks.append(result)
                continue
            
            result['media_id'] = media_id
            result['key_id'] = key_id
            
            # Get all chapters
            data = get_all_chapters(session, media_id, key_id)
            if not data:
                result['error'] = 'Failed to get chapters'
                audiobooks.append(result)
                continue
            
            resources = data.get('resources', [])
            metadata = data.get('metadata', {})
            
            result['metadata'] = metadata
            result['total_chapters'] = len(resources)
            
            # Process chapters
            for j, resource in enumerate(resources, 1):
                chapter = {
                    'number': j,
                    'title': resource.get('title', f'Chapter {j}'),
                    'duration': resource.get('duration', 0),
                    'duration_formatted': format_duration(resource.get('duration', 0)),
                    'format': resource.get('format', 'AUDIO'),
                    'url': resource.get('url', ''),
                }
                result['chapters'].append(chapter)
            
            logger.info(f"  ✅ Found {len(resources)} chapters")
            audiobooks.append(result)
            
        except Exception as e:
            logger.error(f"  ❌ Error: {e}")
            result['error'] = str(e)
            audiobooks.append(result)
    
    return audiobooks


def main():
    """Main function."""
    if len(sys.argv) < 3:
        print("Usage: python get_all_audiobooks.py <username> <password> [output.json]")
        print("")
        print("Example:")
        print("  python get_all_audiobooks.py myuser mypass audiobooks.json")
        sys.exit(1)
    
    username = sys.argv[1]
    password = sys.argv[2]
    output_file = sys.argv[3] if len(sys.argv) > 3 else 'audiobooks.json'
    
    # Step 1: Login
    session = login(username, password)
    if not session:
        logger.error("Login failed")
        sys.exit(1)
    
    # Step 2: Get catalogus
    books = get_catalogus(session)
    if not books:
        logger.error("No books found")
        sys.exit(1)
    
    # Step 3: Get all audiobooks
    audiobooks = get_all_audiobooks(session, books)
    
    # Step 4: Download all MP3 files
    script_dir = os.path.dirname(os.path.abspath(__file__))
    downloads_dir = os.path.join(script_dir, 'downloads')
    
    download_stats = download_all_mp3s(session, audiobooks, downloads_dir)
    
    # Save results
    output = {
        'total_books': len(books),
        'total_audiobooks': len(audiobooks),
        'downloads_dir': downloads_dir,
        'download_stats': download_stats,
        'audiobooks': audiobooks
    }
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    # Summary
    logger.info("\n" + "=" * 80)
    logger.info("SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Total books: {len(books)}")
    logger.info(f"Audiobooks: {len(audiobooks)}")
    
    total_chapters = sum(a['total_chapters'] for a in audiobooks)
    logger.info(f"Total chapters: {total_chapters}")
    
    logger.info(f"\n📥 Downloads:")
    logger.info(f"  Total files: {download_stats['total_files']}")
    logger.info(f"  Downloaded: {download_stats['downloaded']}")
    logger.info(f"  Skipped: {download_stats['skipped']}")
    logger.info(f"  Failed: {download_stats['failed']}")
    logger.info(f"  Location: {downloads_dir}")
    
    logger.info(f"\n✅ Saved metadata to: {output_file}")


if __name__ == '__main__':
    main()

