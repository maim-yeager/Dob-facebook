#!/usr/bin/env python3
"""
MAIM HACKER DOB EXTRACTOR v7.0 - REAL WORKING EDITION
A functional Telegram bot for extracting DOB from social media profiles
Author: Maim Hacker
License: Educational Purpose Only
"""

import os
import re
import json
import time
import random
import logging
import sqlite3
import hashlib
import asyncio
import requests
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from urllib.parse import urlparse, quote
from collections import defaultdict
from pathlib import Path

# Telegram bot libraries
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ContextTypes, filters
)

# Web scraping libraries
import aiohttp
from bs4 import BeautifulSoup
import cloudscraper

# Database
import sqlite3

# Rich console for logging
from rich.console import Console
from rich.table import Table
from rich.progress import Progress

# ==================== CONFIGURATION ====================

# Bot Configuration
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8655021772:AAEWhtwy36KYlxjE0qAhdXsZ4KxNmThqd60")
BOT_USERNAME = "Maim Hacker"
BOT_VERSION = "7.0"
BOT_AUTHOR = "Maim Hacker"

# Database paths
DB_PATH = "maim_dob_bot.db"
CACHE_PATH = "cache.db"

# API Keys (optional)
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")
WHOISXML_API_KEY = os.environ.get("WHOISXML_API_KEY", "")

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
console = Console()

# ==================== DATABASE MANAGER ====================

class Database:
    """SQLite database manager for storing results"""
    
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        """Initialize database tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Create targets table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target TEXT UNIQUE,
                target_hash TEXT UNIQUE,
                dob TEXT,
                confidence REAL,
                methods TEXT,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                hit_count INTEGER DEFAULT 1
            )
        ''')
        
        # Create users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                queries INTEGER DEFAULT 0,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP
            )
        ''')
        
        # Create cache table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                value TEXT,
                expires INTEGER
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("Database initialized")
    
    def save_result(self, target: str, dob: Optional[str], confidence: float, methods: List[str]):
        """Save extraction result to database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        target_hash = hashlib.md5(target.encode()).hexdigest()
        now = datetime.now().isoformat()
        methods_str = json.dumps(methods)
        
        try:
            cursor.execute('''
                INSERT INTO targets (target, target_hash, dob, confidence, methods, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (target, target_hash, dob, confidence, methods_str, now, now))
        except sqlite3.IntegrityError:
            # Update existing
            cursor.execute('''
                UPDATE targets 
                SET dob = ?, confidence = ?, methods = ?, last_seen = ?, hit_count = hit_count + 1
                WHERE target_hash = ?
            ''', (dob, confidence, methods_str, now, target_hash))
        
        conn.commit()
        conn.close()
    
    def get_result(self, target: str) -> Optional[Dict]:
        """Get cached result from database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        target_hash = hashlib.md5(target.encode()).hexdigest()
        
        cursor.execute('''
            SELECT target, dob, confidence, methods, last_seen, hit_count
            FROM targets WHERE target_hash = ?
        ''', (target_hash,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                'target': row[0],
                'dob': row[1],
                'confidence': row[2],
                'methods': json.loads(row[3]),
                'last_seen': row[4],
                'hit_count': row[5]
            }
        return None
    
    def track_user(self, user_id: int, username: str, first_name: str, last_name: str = None):
        """Track user activity"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        now = datetime.now().isoformat()
        
        try:
            cursor.execute('''
                INSERT INTO users (user_id, username, first_name, last_name, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, username, first_name, last_name, now, now))
        except sqlite3.IntegrityError:
            cursor.execute('''
                UPDATE users 
                SET queries = queries + 1, last_seen = ?
                WHERE user_id = ?
            ''', (now, user_id))
        
        conn.commit()
        conn.close()
    
    def get_stats(self) -> Dict:
        """Get database statistics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        stats = {}
        
        cursor.execute('SELECT COUNT(*) FROM targets')
        stats['total_targets'] = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM targets WHERE dob IS NOT NULL')
        stats['successful'] = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM users')
        stats['total_users'] = cursor.fetchone()[0]
        
        cursor.execute('SELECT AVG(confidence) FROM targets WHERE dob IS NOT NULL')
        avg_conf = cursor.fetchone()[0]
        stats['avg_confidence'] = avg_conf if avg_conf else 0
        
        conn.close()
        return stats

# ==================== DOB EXTRACTION ENGINE ====================

class DOBExtractor:
    """Main extraction engine for finding DOB from profiles"""
    
    def __init__(self):
        self.scraper = cloudscraper.create_scraper()
        self.session = None
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/537.36 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/537.36',
            'Mozilla/5.0 (iPad; CPU OS 17_1 like Mac OS X) AppleWebKit/537.36 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/537.36'
        ]
    
    async def get_session(self):
        """Get or create aiohttp session"""
        if not self.session:
            self.session = aiohttp.ClientSession()
        return self.session
    
    def get_headers(self) -> Dict:
        """Generate random headers"""
        return {
            'User-Agent': random.choice(self.user_agents),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0'
        }
    
    async def extract_from_facebook(self, username: str) -> Tuple[Optional[str], float, str]:
        """Extract DOB from Facebook profile"""
        try:
            # Try multiple Facebook endpoints
            urls = [
                f"https://www.facebook.com/{username}",
                f"https://www.facebook.com/{username}/about",
                f"https://m.facebook.com/{username}",
                f"https://mbasic.facebook.com/{username}",
                f"https://touch.facebook.com/{username}"
            ]
            
            session = await self.get_session()
            
            for url in urls:
                try:
                    async with session.get(url, headers=self.get_headers(), timeout=10) as response:
                        if response.status == 200:
                            html = await response.text()
                            
                            # Pattern matching for DOB
                            patterns = [
                                r'"birthday":"(\d{2}/\d{2}/\d{4})"',
                                r'"birthday":"(\d{4}-\d{2}-\d{2})"',
                                r'birthday["\']?\s*[:=]\s*["\']([^"\']+\d{4}[^"\']*)',
                                r'Born on[:\s]+([A-Za-z]+\s+\d{1,2},?\s+\d{4})',
                                r'Birthday[:\s]+([A-Za-z]+\s+\d{1,2},?\s+\d{4})',
                                r'data-birthday["\']?\s*[:=]\s*["\']([^"\']+\d{4})',
                                r'<div[^>]*>Born</div>\s*<div[^>]*>([^<]+\d{4})</div>',
                                r'<span[^>]*>Birthday</span>\s*<span[^>]*>([^<]+\d{4})</span>'
                            ]
                            
                            for pattern in patterns:
                                match = re.search(pattern, html, re.IGNORECASE)
                                if match:
                                    dob = match.group(1).strip()
                                    # Clean up the DOB
                                    dob = re.sub(r'\s+', ' ', dob)
                                    return dob, 0.85, f"Facebook - {url.split('/')[2]}"
                            
                            # Try BeautifulSoup parsing
                            soup = BeautifulSoup(html, 'html.parser')
                            
                            # Look for birthday in meta tags
                            meta_birthday = soup.find('meta', {'property': 'profile:birthday'})
                            if meta_birthday and meta_birthday.get('content'):
                                return meta_birthday['content'], 0.80, "Facebook Meta Tag"
                            
                            # Look for birthday in JSON-LD
                            json_ld = soup.find('script', {'type': 'application/ld+json'})
                            if json_ld and json_ld.string:
                                try:
                                    data = json.loads(json_ld.string)
                                    if isinstance(data, dict):
                                        if 'birthDate' in data:
                                            return data['birthDate'], 0.85, "Facebook JSON-LD"
                                except:
                                    pass
                            
                            # Check mobile version
                            if 'mbasic' in url or 'm.' in url:
                                birthday_divs = soup.find_all('div', string=re.compile(r'Birthday|Born', re.I))
                                for div in birthday_divs:
                                    next_div = div.find_next('div')
                                    if next_div and re.search(r'\d{4}', next_div.text):
                                        return next_div.text.strip(), 0.75, "Facebook Mobile"
                
                except Exception as e:
                    continue
            
            return None, 0.0, ""
            
        except Exception as e:
            logger.error(f"Facebook extraction error: {e}")
            return None, 0.0, ""
    
    async def extract_from_instagram(self, username: str) -> Tuple[Optional[str], float, str]:
        """Extract DOB from Instagram profile"""
        try:
            # Try Instagram API endpoints
            urls = [
                f"https://www.instagram.com/{username}/",
                f"https://www.instagram.com/{username}/?__a=1",
                f"https://i.instagram.com/api/v1/users/web_profile_info/?username={username}"
            ]
            
            session = await self.get_session()
            
            # Instagram specific headers
            insta_headers = self.get_headers()
            insta_headers['X-Requested-With'] = 'XMLHttpRequest'
            
            for url in urls:
                try:
                    async with session.get(url, headers=insta_headers, timeout=10) as response:
                        if response.status == 200:
                            text = await response.text()
                            
                            # Try JSON parsing first
                            if url.endswith('__a=1') or 'api' in url:
                                try:
                                    data = json.loads(text)
                                    # Navigate through Instagram's JSON structure
                                    if 'graphql' in data and 'user' in data['graphql']:
                                        user = data['graphql']['user']
                                        if 'birthday' in user:
                                            return user['birthday'], 0.90, "Instagram API"
                                except:
                                    pass
                            
                            # Pattern matching
                            patterns = [
                                r'"birthday":"(\d{4}-\d{2}-\d{2})"',
                                r'"birthday":\{"day":(\d+),"month":(\d+),"year":(\d+)\}',
                                r'data-birthday="([^"]+)"',
                                r'Birthday[:\s]+([A-Za-z]+\s+\d{1,2},?\s+\d{4})'
                            ]
                            
                            for pattern in patterns:
                                match = re.search(pattern, text, re.IGNORECASE)
                                if match:
                                    if len(match.groups()) == 3:
                                        day, month, year = match.groups()
                                        dob = f"{day.zfill(2)}/{month.zfill(2)}/{year}"
                                    else:
                                        dob = match.group(1)
                                    return dob, 0.85, f"Instagram Pattern"
                            
                except Exception as e:
                    continue
            
            return None, 0.0, ""
            
        except Exception as e:
            logger.error(f"Instagram extraction error: {e}")
            return None, 0.0, ""
    
    async def extract_from_twitter(self, username: str) -> Tuple[Optional[str], float, str]:
        """Extract DOB from Twitter/X profile"""
        try:
            urls = [
                f"https://twitter.com/{username}",
                f"https://x.com/{username}",
                f"https://nitter.net/{username}"
            ]
            
            session = await self.get_session()
            
            for url in urls:
                try:
                    async with session.get(url, headers=self.get_headers(), timeout=10) as response:
                        if response.status == 200:
                            html = await response.text()
                            
                            patterns = [
                                r'"birthday":"(\d{4}-\d{2}-\d{2})"',
                                r'"birthDate":"([^"]+)"',
                                r'Born[:\s]+([A-Za-z]+\s+\d{1,2},?\s+\d{4})',
                                r'Birthday[:\s]+([A-Za-z]+\s+\d{1,2},?\s+\d{4})'
                            ]
                            
                            for pattern in patterns:
                                match = re.search(pattern, html, re.IGNORECASE)
                                if match:
                                    return match.group(1), 0.80, f"Twitter - {url.split('/')[2]}"
                            
                except Exception as e:
                    continue
            
            return None, 0.0, ""
            
        except Exception as e:
            logger.error(f"Twitter extraction error: {e}")
            return None, 0.0, ""
    
    async def extract_from_linkedin(self, username: str) -> Tuple[Optional[str], float, str]:
        """Extract DOB from LinkedIn profile"""
        try:
            url = f"https://www.linkedin.com/in/{username}/"
            
            session = await self.get_session()
            
            async with session.get(url, headers=self.get_headers(), timeout=10) as response:
                if response.status == 200:
                    html = await response.text()
                    
                    patterns = [
                        r'"birthDate":\{"day":(\d+),"month":(\d+),"year":(\d+)\}',
                        r'"birthday":"([^"]+)"',
                        r'data-birthday="([^"]+)"'
                    ]
                    
                    for pattern in patterns:
                        match = re.search(pattern, html, re.IGNORECASE)
                        if match:
                            if len(match.groups()) == 3:
                                day, month, year = match.groups()
                                return f"{day.zfill(2)}/{month.zfill(2)}/{year}", 0.75, "LinkedIn"
                            else:
                                return match.group(1), 0.75, "LinkedIn"
            
            return None, 0.0, ""
            
        except Exception as e:
            logger.error(f"LinkedIn extraction error: {e}")
            return None, 0.0, ""
    
    async def search_google(self, query: str) -> Tuple[Optional[str], float, str]:
        """Search Google for DOB information"""
        try:
            url = f"https://www.google.com/search?q={quote(query)}+birthday+OR+birth+date"
            
            session = await self.get_session()
            
            async with session.get(url, headers=self.get_headers(), timeout=10) as response:
                if response.status == 200:
                    html = await response.text()
                    
                    # Look for date patterns in search results
                    date_pattern = r'\b(\d{1,2}[/.-]\d{1,2}[/.-]\d{4})\b'
                    matches = re.findall(date_pattern, html)
                    
                    if matches:
                        # Return the most common date
                        from collections import Counter
                        most_common = Counter(matches).most_common(1)
                        if most_common:
                            return most_common[0][0], 0.60, "Google Search"
            
            return None, 0.0, ""
            
        except Exception as e:
            logger.error(f"Google search error: {e}")
            return None, 0.0, ""
    
    async def check_wayback_machine(self, url: str) -> Tuple[Optional[str], float, str]:
        """Check Wayback Machine for archived pages"""
        try:
            # First, get available snapshots
            cdx_url = f"https://web.archive.org/cdx/search/cdx?url={quote(url)}&output=json&limit=10"
            
            session = await self.get_session()
            
            async with session.get(cdx_url, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    if len(data) > 1:  # Has snapshots
                        # Get the latest snapshot
                        latest = data[-1]
                        timestamp = latest[1]
                        archive_url = f"https://web.archive.org/web/{timestamp}/{url}"
                        
                        async with session.get(archive_url, headers=self.get_headers(), timeout=10) as arch_response:
                            if arch_response.status == 200:
                                html = await arch_response.text()
                                
                                patterns = [
                                    r'"birthday":"([^"]+)"',
                                    r'"dob":"([^"]+)"',
                                    r'(\d{1,2}[/.-]\d{1,2}[/.-]\d{4})',
                                    r'Born[:\s]+([A-Za-z]+\s+\d{1,2},?\s+\d{4})',
                                    r'Birthday[:\s]+([A-Za-z]+\s+\d{1,2},?\s+\d{4})'
                                ]
                                
                                for pattern in patterns:
                                    match = re.search(pattern, html, re.IGNORECASE)
                                    if match:
                                        return match.group(1), 0.70, "Wayback Machine"
            
            return None, 0.0, ""
            
        except Exception as e:
            logger.error(f"Wayback Machine error: {e}")
            return None, 0.0, ""
    
    async def extract_from_generic(self, username: str) -> Tuple[Optional[str], float, str]:
        """Generic extraction from various sources"""
        try:
            # Try to find the person on various platforms
            platforms = [
                f"https://about.me/{username}",
                f"https://linktr.ee/{username}",
                f"https://linktree.com/{username}",
                f"https://bio.link/{username}"
            ]
            
            session = await self.get_session()
            
            for platform_url in platforms:
                try:
                    async with session.get(platform_url, headers=self.get_headers(), timeout=5) as response:
                        if response.status == 200:
                            html = await response.text()
                            
                            date_pattern = r'\b(\d{1,2}[/.-]\d{1,2}[/.-]\d{4})\b'
                            match = re.search(date_pattern, html)
                            if match:
                                return match.group(1), 0.55, f"Generic - {platform_url.split('/')[2]}"
                                
                except Exception:
                    continue
            
            return None, 0.0, ""
            
        except Exception as e:
            logger.error(f"Generic extraction error: {e}")
            return None, 0.0, ""
    
    def normalize_dob(self, dob: str) -> str:
        """Normalize DOB to standard format DD/MM/YYYY"""
        # Month name mapping
        months = {
            'january': '01', 'jan': '01',
            'february': '02', 'feb': '02',
            'march': '03', 'mar': '03',
            'april': '04', 'apr': '04',
            'may': '05',
            'june': '06', 'jun': '06',
            'july': '07', 'jul': '07',
            'august': '08', 'aug': '08',
            'september': '09', 'sep': '09',
            'october': '10', 'oct': '10',
            'november': '11', 'nov': '11',
            'december': '12', 'dec': '12'
        }
        
        # Remove extra whitespace
        dob = re.sub(r'\s+', ' ', dob.strip())
        
        # Try different formats
        patterns = [
            # DD/MM/YYYY
            (r'^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$', 
             lambda m: f"{m.group(1).zfill(2)}/{m.group(2).zfill(2)}/{m.group(3)}"),
            
            # YYYY-MM-DD
            (r'^(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})$',
             lambda m: f"{m.group(3).zfill(2)}/{m.group(2).zfill(2)}/{m.group(1)}"),
            
            # Month DD, YYYY
            (r'^([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})$',
             lambda m: f"{m.group(2).zfill(2)}/{months.get(m.group(1).lower(), '01')}/{m.group(3)}"),
            
            # DD Month YYYY
            (r'^(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})$',
             lambda m: f"{m.group(1).zfill(2)}/{months.get(m.group(2).lower(), '01')}/{m.group(3)}")
        ]
        
        for pattern, formatter in patterns:
            match = re.search(pattern, dob, re.IGNORECASE)
            if match:
                return formatter(match)
        
        # If no pattern matches, return as is
        return dob
    
    async def extract(self, target: str) -> Dict[str, Any]:
        """Main extraction method"""
        result = {
            'success': False,
            'dob': None,
            'confidence': 0.0,
            'methods': [],
            'platform': 'unknown',
            'username': None,
            'error': None
        }
        
        try:
            # Parse target
            parsed = urlparse(target)
            
            # Extract username
            if parsed.netloc:
                # It's a URL
                path_parts = [p for p in parsed.path.strip('/').split('/') if p]
                if path_parts:
                    result['username'] = path_parts[-1]
                
                # Detect platform
                domain = parsed.netloc.lower()
                if 'facebook' in domain or 'fb.com' in domain:
                    result['platform'] = 'facebook'
                elif 'instagram' in domain:
                    result['platform'] = 'instagram'
                elif 'twitter' in domain or 'x.com' in domain:
                    result['platform'] = 'twitter'
                elif 'linkedin' in domain:
                    result['platform'] = 'linkedin'
                else:
                    result['platform'] = 'other'
            else:
                # It's a username
                result['username'] = target.strip()
                result['platform'] = 'username'
            
            username = result['username']
            if not username:
                result['error'] = "Could not extract username"
                return result
            
            # Try different extraction methods based on platform
            extraction_tasks = []
            
            if result['platform'] == 'facebook':
                extraction_tasks.append(self.extract_from_facebook(username))
            elif result['platform'] == 'instagram':
                extraction_tasks.append(self.extract_from_instagram(username))
            elif result['platform'] == 'twitter':
                extraction_tasks.append(self.extract_from_twitter(username))
            elif result['platform'] == 'linkedin':
                extraction_tasks.append(self.extract_from_linkedin(username))
            else:
                # Try all platforms for username
                extraction_tasks.extend([
                    self.extract_from_facebook(username),
                    self.extract_from_instagram(username),
                    self.extract_from_twitter(username),
                    self.extract_from_linkedin(username),
                    self.search_google(f"{username}"),
                    self.extract_from_generic(username)
                ])
            
            # Also try Wayback Machine
            if parsed.netloc:
                extraction_tasks.append(self.check_wayback_machine(target))
            
            # Run all extraction tasks
            dob_results = []
            for task in extraction_tasks:
                try:
                    dob, confidence, method = await task
                    if dob:
                        normalized_dob = self.normalize_dob(dob)
                        dob_results.append({
                            'dob': normalized_dob,
                            'confidence': confidence,
                            'method': method
                        })
                except Exception as e:
                    logger.error(f"Task error: {e}")
            
            # Aggregate results
            if dob_results:
                # Group by DOB
                dob_groups = {}
                for r in dob_results:
                    if r['dob'] not in dob_groups:
                        dob_groups[r['dob']] = []
                    dob_groups[r['dob']].append(r)
                
                # Calculate best DOB
                best_dob = None
                best_confidence = 0
                best_methods = []
                
                for dob, group in dob_groups.items():
                    avg_confidence = sum(r['confidence'] for r in group) / len(group)
                    methods = [r['method'] for r in group]
                    
                    if avg_confidence > best_confidence:
                        best_confidence = avg_confidence
                        best_dob = dob
                        best_methods = methods
                
                result['success'] = True
                result['dob'] = best_dob
                result['confidence'] = best_confidence
                result['methods'] = best_methods
                result['all_results'] = dob_results
            
            return result
            
        except Exception as e:
            logger.error(f"Extraction error: {e}")
            result['error'] = str(e)
            return result
    
    async def close(self):
        """Close session"""
        if self.session:
            await self.session.close()

# ==================== TELEGRAM BOT ====================

class DOBBot:
    """Telegram bot for DOB extraction"""
    
    def __init__(self, token: str):
        self.token = token
        self.db = Database()
        self.extractor = DOBExtractor()
        self.application = None
        self.user_sessions = {}
        
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user = update.effective_user
        
        # Track user
        self.db.track_user(
            user.id, 
            user.username, 
            user.first_name, 
            user.last_name
        )
        
        # Create welcome message
        welcome_msg = f"""
╔══════════════════════════════════╗
║     MAIM HACKER DOB EXTRACTOR    ║
║          Version {BOT_VERSION}           ║
║         Author: {BOT_AUTHOR}       ║
╚══════════════════════════════════╝

👋 **Welcome {user.first_name}!**

🔍 **I can help you find Date of Birth from:**
• Facebook Profiles
• Instagram Profiles
• Twitter/X Profiles
• LinkedIn Profiles
• Username Search
• Archived Pages

📌 **How to use:**
• Send me a profile URL
• Or just send a username
• Use /help for commands
• Use /stats for statistics

⚡ **Examples:**
`https://facebook.com/username`
`https://instagram.com/username`
`@username`
`username`

⚠️ **Note:** This tool is for educational purposes only.
        """
        
        # Create keyboard
        keyboard = [
            [
                InlineKeyboardButton("🔍 Quick Search", callback_data='quick_search'),
                InlineKeyboardButton("📊 Statistics", callback_data='stats')
            ],
            [
                InlineKeyboardButton("📚 Help", callback_data='help'),
                InlineKeyboardButton("ℹ️ About", callback_data='about')
            ],
            [
                InlineKeyboardButton("📁 Batch Process", callback_data='batch'),
                InlineKeyboardButton("💾 Saved Results", callback_data='saved')
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            welcome_msg,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        help_text = """
📚 **COMMANDS & USAGE**
══════════════════════

**🔍 Basic Commands:**
/start - Start the bot
/help - Show this help
/stats - View statistics
/about - About the bot

**🎯 Search Methods:**
1️⃣ **Profile URL:**
   • Facebook: fb.com/username
   • Instagram: instagram.com/username
   • Twitter: twitter.com/username
   • LinkedIn: linkedin.com/in/username

2️⃣ **Username:**
   • Just send a username
   • Example: `johndoe`

3️⃣ **Batch Process:**
   • Send a .txt file with targets
   • One target per line

**💡 Tips:**
• Public profiles work best
• Try full profile URLs
• Check archived versions
• Use with real usernames

**⚠️ Disclaimer:**
This tool is for educational purposes.
Respect privacy and terms of service.
        """
        
        await update.message.reply_text(help_text, parse_mode='Markdown')
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stats command"""
        stats = self.db.get_stats()
        
        stats_text = f"""
📊 **BOT STATISTICS**
══════════════════

**📈 Database Stats:**
• Total Targets: {stats['total_targets']}
• Successful Extractions: {stats['successful']}
• Success Rate: {(stats['successful']/stats['total_targets']*100) if stats['total_targets'] > 0 else 0:.1f}%
• Average Confidence: {stats['avg_confidence']:.1%}
• Total Users: {stats['total_users']}

**⚡ Bot Info:**
• Version: {BOT_VERSION}
• Author: {BOT_AUTHOR}
• Uptime: Calculating...

**💾 Database:**
• Location: SQLite
• Size: {os.path.getsize(DB_PATH) / 1024:.1f} KB
        """
        
        keyboard = [
            [InlineKeyboardButton("🔄 Refresh", callback_data='refresh_stats')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            stats_text, 
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def about_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /about command"""
        about_text = f"""
ℹ️ **ABOUT THIS BOT**
══════════════════

**🤖 Name:** MAIM HACKER DOB EXTRACTOR
**📌 Version:** {BOT_VERSION}
**👨‍💻 Author:** {BOT_AUTHOR}
**📅 Release:** 2024

**🔧 Features:**
• Multi-platform extraction
• Intelligent pattern matching
• Archive.org integration
• Result caching
• User tracking
• Batch processing

**🛠️ Technology:**
• Python 3.8+
• python-telegram-bot
• aiohttp
• BeautifulSoup4
• SQLite3
• Cloudscraper

**📝 Disclaimer:**
This bot is created for educational 
and research purposes only. Users are 
responsible for complying with all 
applicable laws and platform terms.

**📧 Contact:**
@MaimHacker (Telegram)
        """
        
        await update.message.reply_text(about_text, parse_mode='Markdown')
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle text messages"""
        text = update.message.text.strip()
        
        # Send typing indicator
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action='typing'
        )
        
        # Send initial message
        status_msg = await update.message.reply_text(
            f"🔍 **Searching for:** `{text}`\n\n"
            f"🔄 Initializing extraction engines...",
            parse_mode='Markdown'
        )
        
        try:
            # Start extraction
            start_time = time.time()
            
            # Update status
            await status_msg.edit_text(
                f"🔍 **Searching for:** `{text}`\n\n"
                f"🔄 Scanning multiple platforms...\n"
                f"📡 Checking Facebook, Instagram, Twitter...",
                parse_mode='Markdown'
            )
            
            result = await self.extractor.extract(text)
            elapsed = time.time() - start_time
            
            # Save to database
            if result['success']:
                self.db.save_result(
                    text,
                    result['dob'],
                    result['confidence'],
                    result['methods']
                )
            
            # Format result
            if result['success']:
                response = f"""
✅ **EXTRACTION SUCCESSFUL!**
══════════════════════════

🎂 **Date of Birth:** `{result['dob']}`
📊 **Confidence:** {result['confidence']:.1%}
⏱️ **Time:** {elapsed:.2f}s
🎯 **Platform:** {result['platform'].title()}

**🔧 Methods Used:**
"""
                for i, method in enumerate(result['methods'][:5], 1):
                    response += f"{i}. {method}\n"
                
                if len(result['methods']) > 5:
                    response += f"... and {len(result['methods']) - 5} more\n"
                
                response += f"""
💾 **Saved to database**
🔍 **Target:** `{result['username']}`

📌 **Commands:**
/save - Save to favorites
/check - Check other sources
/report - Report incorrect data
                """
                
                keyboard = [
                    [
                        InlineKeyboardButton("💾 Save", callback_data=f"save_{result['dob']}"),
                        InlineKeyboardButton("🔄 Search Again", callback_data=f"again_{text}")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
            else:
                response = f"""
❌ **NO DOB FOUND**
══════════════════

Target: `{text}`
Time: {elapsed:.2f}s
Platform: {result['platform'].title()}

**Possible Reasons:**
• Profile is private
• No birthday information
• Account doesn't exist
• Platform restrictions

**💡 Suggestions:**
• Try full profile URL
• Use different username
• Check with /help
• Try archived version
                """
                
                keyboard = [
                    [
                        InlineKeyboardButton("🔄 Try Again", callback_data=f"again_{text}"),
                        InlineKeyboardButton("📚 Help", callback_data='help')
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
            
            await status_msg.edit_text(
                response,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            await status_msg.edit_text(
                f"❌ **ERROR OCCURRED**\n\n"
                f"`{str(e)}`\n\n"
                f"Please try again or contact @MaimHacker",
                parse_mode='Markdown'
            )
            logger.error(f"Message handling error: {e}")
    
    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle document uploads for batch processing"""
        document = update.message.document
        
        if not document.file_name.endswith('.txt'):
            await update.message.reply_text(
                "❌ Please send a .txt file with one target per line.",
                parse_mode='Markdown'
            )
            return
        
        # Download file
        status_msg = await update.message.reply_text(
            "📁 **Downloading file...**",
            parse_mode='Markdown'
        )
        
        try:
            file = await context.bot.get_file(document.file_id)
            file_path = f"downloads/{document.file_name}"
            
            # Create downloads directory if not exists
            os.makedirs('downloads', exist_ok=True)
            
            await file.download_to_drive(file_path)
            
            # Read targets
            with open(file_path, 'r') as f:
                targets = [line.strip() for line in f if line.strip()]
            
            await status_msg.edit_text(
                f"📁 **File loaded**\n\n"
                f"Total targets: {len(targets)}\n"
                f"File: {document.file_name}\n\n"
                f"⏳ Starting batch process...",
                parse_mode='Markdown'
            )
            
            # Process batch
            results = []
            for i, target in enumerate(targets, 1):
                # Update status every 5 targets
                if i % 5 == 0 or i == len(targets):
                    await status_msg.edit_text(
                        f"📁 **Batch Processing**\n\n"
                        f"Progress: {i}/{len(targets)}\n"
                        f"Current: {target[:30]}...\n"
                        f"Found: {len([r for r in results if r.get('success')])}",
                        parse_mode='Markdown'
                    )
                
                result = await self.extractor.extract(target)
                results.append({
                    'target': target,
                    'success': result['success'],
                    'dob': result.get('dob'),
                    'confidence': result.get('confidence', 0)
                })
            
            # Create result file
            output_file = f"results/batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            os.makedirs('results', exist_ok=True)
            
            with open(output_file, 'w') as f:
                json.dump(results, f, indent=2)
            
            # Send results
            success_count = len([r for r in results if r['success']])
            
            # Create summary table
            summary = f"""
✅ **BATCH PROCESSING COMPLETE**
══════════════════════════

📊 **Summary:**
• Total: {len(targets)}
• Successful: {success_count}
• Failed: {len(targets) - success_count}
• Success Rate: {success_count/len(targets)*100:.1f}%

📁 **Results saved to:** `{output_file}`

🔍 **Top Results:**
"""
            
            # Show top 5 successful results
            successful_results = [r for r in results if r['success']]
            for i, r in enumerate(successful_results[:5], 1):
                summary += f"{i}. {r['target'][:30]}... → {r['dob']} ({r['confidence']:.1%})\n"
            
            # Send result file
            await update.message.reply_document(
                document=open(output_file, 'rb'),
                caption=summary,
                parse_mode='Markdown'
            )
            
            # Delete status message
            await status_msg.delete()
            
        except Exception as e:
            await status_msg.edit_text(
                f"❌ **Batch processing error:**\n\n`{str(e)}`",
                parse_mode='Markdown'
            )
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle callback queries"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        
        if data == 'quick_search':
            await query.edit_message_text(
                "🔍 Send me a username or URL to search:",
                parse_mode='Markdown'
            )
        
        elif data == 'stats' or data == 'refresh_stats':
            stats = self.db.get_stats()
            
            stats_text = f"""
📊 **BOT STATISTICS**
══════════════════

**📈 Database Stats:**
• Total Targets: {stats['total_targets']}
• Successful: {stats['successful']}
• Success Rate: {(stats['successful']/stats['total_targets']*100) if stats['total_targets'] > 0 else 0:.1f}%
• Avg Confidence: {stats['avg_confidence']:.1%}
• Total Users: {stats['total_users']}

**⚡ Bot Info:**
• Version: {BOT_VERSION}
• Author: {BOT_AUTHOR}
            """
            
            keyboard = [
                [InlineKeyboardButton("🔄 Refresh", callback_data='refresh_stats')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                stats_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        
        elif data == 'help':
            help_text = """
📚 **QUICK HELP**
══════════════

**🔍 To search:**
• Send profile URL
• Send username

**📁 Batch process:**
• Send .txt file

**📊 Commands:**
/start - Restart bot
/help - Full help
/stats - Statistics
/about - About bot
            """
            
            await query.edit_message_text(help_text, parse_mode='Markdown')
        
        elif data == 'about':
            about_text = f"""
ℹ️ **MAIM HACKER DOB EXTRACTOR**
══════════════════════════

**Version:** {BOT_VERSION}
**Author:** {BOT_AUTHOR}
**Released:** 2024

Educational purpose only.
            """
            
            await query.edit_message_text(about_text, parse_mode='Markdown')
        
        elif data == 'batch':
            await query.edit_message_text(
                "📁 **Batch Processing**\n\n"
                "Send me a .txt file with one target per line.\n\n"
                "Example format:\n"
                "facebook.com/user1\n"
                "instagram.com/user2\n"
                "username3\n"
                "https://twitter.com/user4",
                parse_mode='Markdown'
            )
        
        elif data == 'saved':
            await query.edit_message_text(
                "💾 **Saved Results**\n\n"
                "This feature is coming soon!\n\n"
                "You'll be able to:\n"
                "• View your saved DOBs\n"
                "• Export to CSV/JSON\n"
                "• Compare results",
                parse_mode='Markdown'
            )
        
        elif data.startswith('save_'):
            dob = data.replace('save_', '')
            await query.edit_message_text(
                f"✅ DOB `{dob}` saved to your favorites!",
                parse_mode='Markdown'
            )
        
        elif data.startswith('again_'):
            target = data.replace('again_', '')
            # Trigger search again
            context.args = [target]
            await self.handle_message(update, context)
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors"""
        logger.error(f"Update {update} caused error {context.error}")
        
        try:
            if update and update.effective_message:
                await update.effective_message.reply_text(
                    "❌ An error occurred. Please try again later.\n"
                    "If the problem persists, contact @MaimHacker",
                    parse_mode='Markdown'
                )
        except:
            pass
    
    def setup(self):
        """Setup bot handlers"""
        self.application = Application.builder().token(self.token).build()
        
        # Command handlers
        self.application.add_handler(CommandHandler('start', self.start_command))
        self.application.add_handler(CommandHandler('help', self.help_command))
        self.application.add_handler(CommandHandler('stats', self.stats_command))
        self.application.add_handler(CommandHandler('about', self.about_command))
        
        # Message handlers
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        self.application.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))
        
        # Callback handler
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        
        # Error handler
        self.application.add_error_handler(self.error_handler)
        
        logger.info("Bot handlers setup complete")
    
    async def run(self):
        """Run the bot"""
        self.setup()
        
        console.print("[bold green]╔════════════════════════════════════╗[/bold green]")
        console.print("[bold green]║   MAIM HACKER DOB EXTRACTOR v7.0  ║[/bold green]")
        console.print("[bold green]║         Bot is running...         ║[/bold green]")
        console.print("[bold green]╚════════════════════════════════════╝[/bold green]")
        console.print(f"[yellow]Author: {BOT_AUTHOR}[/yellow]")
        console.print(f"[cyan]Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/cyan]")
        
        await self.application.initialize()
        await self.application.start()
        
        # Start polling
        await self.application.updater.start_polling()
        
        # Keep running
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            await self.shutdown()
    
    async def shutdown(self):
        """Graceful shutdown"""
        logger.info("Shutting down bot...")
        
        # Stop updater
        if self.application.updater:
            await self.application.updater.stop()
        
        # Stop application
        await self.application.stop()
        
        # Close extractor session
        await self.extractor.close()
        
        logger.info("Bot shutdown complete")

# ==================== MAIN FUNCTION ====================

async def main():
    """Main function"""
    # Check for bot token
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        console.print("[bold red]ERROR: Bot token not configured![/bold red]")
        console.print("[yellow]Please set your bot token in the script or as environment variable BOT_TOKEN[/yellow]")
        console.print("\nTo get a bot token:")
        console.print("1. Message @BotFather on Telegram")
        console.print("2. Send /newbot and follow instructions")
        console.print("3. Copy the token and set it in the script\n")
        return
    
    # Create necessary directories
    os.makedirs('downloads', exist_ok=True)
    os.makedirs('results', exist_ok=True)
    
    # Create and run bot
    bot = DOBBot(BOT_TOKEN)
    
    try:
        await bot.run()
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutting down...[/yellow]")
        await bot.shutdown()
    except Exception as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        logger.error(f"Main error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
