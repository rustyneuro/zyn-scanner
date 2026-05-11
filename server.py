#!/usr/bin/env python3
"""Zyn Scanner — synchronous server, Playwright for login, requests for submit"""
import json, os, re, hashlib, sys, time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from playwright.sync_api import sync_playwright
import requests as req_lib

PORT = int(os.environ.get("PORT", 5050))
SESSION_DIR = os.path.expanduser("~/.zyn_sessions")
DUP_DIR = os.path.expanduser("~/.zyn_scanned")
HTML_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
LOG_FILE = "/tmp/zyn-sync-server.log"

def log(msg):
    with open(LOG_FILE, "a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    print(msg, flush=True)

os.makedirs(SESSION_DIR, exist_ok=True)
os.makedirs(DUP_DIR, exist_ok=True)
log(f"Starting on port {PORT}")

def do_login(email, password):
    """Log into Zyn via Playwright, save session."""
    uid = hashlib.sha256(email.encode()).hexdigest()[:16]
    state_file = os.path.join(SESSION_DIR, f"{uid}.json")
    
    try:
        log(f"  PW import...")
        from playwright.sync_api import sync_playwright
        log(f"  PW starting...")
        with sync_playwright() as p:
            log(f"  PW started, launching browser...")
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
            log(f"  Browser launched")
            ctx = browser.new_context(viewport={'width':1440,'height':900})
            page = ctx.new_page()
            log(f"  Navigating to Zyn...")
            page.goto('https://us.zyn.com/ZYNRewards/', timeout=30000)
            log(f"  Page loaded")
            page.wait_for_timeout(5000)
            log(f"  Waited 5s")
            
            # Debug: what's on the page?
            btns = page.evaluate("Array.from(document.querySelectorAll('button')).map(b=>b.textContent.trim().substring(0,30))")
            log(f"  Buttons: {btns[:8]}")
            
            # Dismiss any overlays/backdrops
            try:
                page.evaluate("document.querySelectorAll('.site-backdrop--active,.modal,.overlay').forEach(el=>{el.classList.remove('site-backdrop--active');el.style.display='none'})")
            except: pass
            page.wait_for_timeout(500)
            
            # Try to find LOG IN button more flexibly
            login_btn = None
            for sel in ['button:has-text("LOG IN")', 'button:has-text("Log in")', 
                        'input[value="LOG IN"]', 'button[type="submit"]',
                        '.btn--primary:has-text("LOG")']:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=1000):
                        login_btn = el
                        log(f"  Found button: {sel}")
                        break
                except: pass
            
            content = page.content()
            if 'ENTER YOUR REWARDS CODE' in content:
                ctx.storage_state(path=state_file)
                browser.close()
                return {'success':True,'user_id':uid,'message':'Already logged in'}
            
            # Find and fill login form
            email_el = None
            for sel in ['input[name="Request.Email"]', 'input[type="email"]']:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=2000):
                        email_el = el; break
                except: pass
            
            if not email_el:
                browser.close()
                return {'success':False,'message':'Login form not found'}
            
            email_el.fill(email)
            page.locator('input[type="password"]').first.fill(password)
            try:
                cb = page.locator('input[type="checkbox"]').first
                if not cb.is_checked(): cb.check()
            except: pass
            
            # Click login — try multiple selectors
            clicked = False
            for sel in ['button:has-text("LOG IN")', 'button:has-text("Log in")', 
                        '.btn--primary:has-text("LOG")', 'button[type="submit"]']:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=2000):
                        btn.click()
                        clicked = True
                        log(f"  Clicked: {sel}")
                        break
                except: pass
            if not clicked:
                page.locator('input[type="password"]').first.press('Enter')
                log(f"  Pressed Enter instead")
            
            page.wait_for_timeout(8000)
            
            content = page.content()
            if 'ENTER YOUR REWARDS CODE' in content:
                ctx.storage_state(path=state_file)
                browser.close()
                return {'success':True,'user_id':uid,'message':'Logged in'}
            elif 'incorrect' in content.lower():
                browser.close()
                return {'success':False,'message':'Invalid email or password'}
            else:
                ctx.storage_state(path=state_file)
                browser.close()
                return {'success':True,'user_id':uid,'message':'Logged in'}
    except Exception as e:
        return {'success':False,'message':str(e)[:100]}

def do_submit(code, user_id):
    """Submit code to Zyn via requests."""
    state_file = os.path.join(SESSION_DIR, f"{user_id}.json")
    if not os.path.exists(state_file):
        return {'success':False,'message':'Not logged in'}
    
    session = json.load(open(state_file))
    s = req_lib.Session()
    for c in session['cookies']:
        if c['domain'] in ['us.zyn.com','.us.zyn.com']:
            s.cookies.set(c['name'], c['value'], domain=c['domain'].lstrip('.'))
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml',
    }
    
    try:
        r = s.get('https://us.zyn.com/ZYNRewards/', headers=headers, timeout=15)
        m = re.search(r'__RequestVerificationToken["\']?\s*:\s*["\']([^"\']+)', r.text)
        if not m:
            m = re.search(r'name="__RequestVerificationToken"[^>]+value="([^"]+)"', r.text)
        if not m:
            return {'success':False,'message':'Session expired — re-login'}
        
        token = m.group(1)
        sh = {**headers, 'Origin':'https://us.zyn.com','Referer':'https://us.zyn.com/ZYNRewards/'}
        r2 = s.post('https://us.zyn.com/rewardsblock/redeemcode/',
                     data={'__RequestVerificationToken':token,'BlockId':'486','code':code},
                     headers=sh, timeout=15, allow_redirects=True)
        
        body = r2.text.lower()
        if 'invalid' in body: return {'success':False,'message':'Invalid code'}
        elif 'success' in body or 'points' in body or 'congratulations' in body:
            return {'success':True,'message':'+15 pts!'}
        elif 'already used' in body or 'already redeemed' in body:
            return {'success':False,'message':'Already used on Zyn','dedup':'zyn'}
        elif 'technical error' in body: return {'success':False,'message':'Zyn error — retry'}
        elif 'log in' in body or 'login' in body:
            return {'success':False,'message':'Session expired — re-login'}
        else: return {'success':False,'message':'Unknown response'}
    except Exception as e:
        return {'success':False,'message':str(e)[:80]}

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    
    def do_GET(self):
        p = urlparse(self.path).path
        if p == '/':
            self.send_response(200)
            self.send_header('Content-Type','text/html')
            self.end_headers()
            self.wfile.write(open(HTML_FILE,'rb').read())
        else:
            self.send_error(404)
    
    def do_POST(self):
        p = urlparse(self.path).path
        length = int(self.headers.get('Content-Length',0))
        body = json.loads(self.rfile.read(length).decode())
        
        if p == '/api/login':
            email = body.get('email','').strip()
            password = body.get('password','')
            if not email or not password:
                self.send_json({'success':False,'message':'Email and password required'})
                return
            log(f"LOGIN: {email[:30]}")
            result = do_login(email, password)
            log(f"  -> {result['message']}")
            self.send_json(result)
            
        elif p == '/api/submit':
            code = body.get('code','').strip()
            user_id = body.get('user_id','').strip()
            if not code or not user_id:
                self.send_json({'success':False,'message':'Missing code or user_id'})
                return
            
            # Dedup
            dup_file = os.path.join(DUP_DIR, f"{user_id}.json")
            scanned = set(json.load(open(dup_file))) if os.path.exists(dup_file) else set()
            cu = code.upper()
            if cu in scanned:
                self.send_json({'success':False,'message':'Already scanned!','dedup':'local'})
                return
            
            result = do_submit(code, user_id)
            if result['success']:
                scanned.add(cu)
                with open(dup_file,'w') as f: json.dump(list(scanned), f)
            elif 'already used' in result.get('message','').lower():
                scanned.add(cu)
                with open(dup_file,'w') as f: json.dump(list(scanned), f)
            
            log(f"SUBMIT [{user_id[:8]}] {code} -> {result['message']}")
            self.send_json(result)
            
        elif p == '/api/status':
            user_id = body.get('user_id','')
            state_file = os.path.join(SESSION_DIR, f"{user_id}.json")
            self.send_json({'logged_in': user_id and os.path.exists(state_file), 'user_id': user_id})
        
        else:
            self.send_error(404)
    
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin','*')
        self.send_header('Access-Control-Allow-Methods','POST,OPTIONS')
        self.send_header('Access-Control-Allow-Headers','Content-Type')
        self.end_headers()
    
    def send_json(self, d):
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Access-Control-Allow-Origin','*')
        self.end_headers()
        self.wfile.write(json.dumps(d).encode())

log(f"Zyn Scanner on port {PORT}")
HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
