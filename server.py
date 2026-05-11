"""Zyn Scanner Server — login + submit, Playwright for login, requests for submit"""
import json, os, re, hashlib, secrets, io, base64
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

PORT = int(os.environ.get("PORT", 5050))
SESSION_DIR = "/tmp/zyn_sessions"
DUP_DIR = "/tmp/zyn_scanned"
os.makedirs(SESSION_DIR, exist_ok=True)
os.makedirs(DUP_DIR, exist_ok=True)

# --- Login ---
def playwright_login(email: str, password: str) -> dict:
    from playwright.sync_api import sync_playwright
    uid = hashlib.sha256(email.encode()).hexdigest()[:16]
    state_file = os.path.join(SESSION_DIR, f"{uid}.json")
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
            ctx = browser.new_context(viewport={'width': 1440, 'height': 900})
            page = ctx.new_page()
            page.goto('https://us.zyn.com/ZYNRewards/', timeout=30000)
            page.wait_for_timeout(3000)
            
            # Age gate
            try:
                page.click('text=YES', timeout=5000)
                page.wait_for_timeout(1000)
            except: pass
            
            # Check already logged in
            content = page.content()
            if 'ENTER YOUR REWARDS CODE' in content:
                ctx.storage_state(path=state_file)
                browser.close()
                return {'success': True, 'user_id': uid, 'message': 'Already logged in'}
            
            # Fill login
            page.fill('input[type="email"]', email)
            page.fill('input[type="password"]', password)
            try:
                rm = page.locator('input[type="checkbox"]').first
                if not rm.is_checked(): rm.check()
            except: pass
            
            page.click('button:has-text("LOG IN")')
            page.wait_for_timeout(5000)
            
            content = page.content()
            if 'ENTER YOUR REWARDS CODE' in content:
                ctx.storage_state(path=state_file)
                browser.close()
                return {'success': True, 'user_id': uid, 'message': 'Logged in'}
            elif 'incorrect' in content.lower() or 'invalid' in content.lower():
                browser.close()
                return {'success': False, 'message': 'Invalid email or password'}
            else:
                ctx.storage_state(path=state_file)
                browser.close()
                return {'success': True, 'user_id': uid, 'message': 'Logged in (unverified)'}
    except Exception as e:
        return {'success': False, 'message': str(e)[:100]}

# --- Submit ---
def submit_via_requests(code: str, user_id: str) -> dict:
    import requests as req
    state_file = os.path.join(SESSION_DIR, f"{user_id}.json")
    if not os.path.exists(state_file):
        return {'success': False, 'message': 'Not logged in'}
    
    session = json.load(open(state_file))
    s = req.Session()
    for c in session['cookies']:
        if c['domain'] in ['us.zyn.com', '.us.zyn.com']:
            s.cookies.set(c['name'], c['value'], domain=c['domain'].lstrip('.'))
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml',
    }
    
    try:
        # Get fresh CSRF token
        r = s.get('https://us.zyn.com/ZYNRewards/', headers=headers, timeout=15)
        token_match = re.search(r'__RequestVerificationToken["\']?\s*:\s*["\']([^"\']+)', r.text)
        if not token_match:
            token_match = re.search(r'name="__RequestVerificationToken"[^>]+value="([^"]+)"', r.text)
        
        if not token_match:
            return {'success': False, 'message': 'Session expired — re-login needed'}
        
        token = token_match.group(1)
        
        # Submit
        submit_headers = {**headers, 'Origin': 'https://us.zyn.com', 'Referer': 'https://us.zyn.com/ZYNRewards/'}
        data = {'__RequestVerificationToken': token, 'BlockId': '486', 'code': code}
        
        r2 = s.post('https://us.zyn.com/rewardsblock/redeemcode/', data=data, headers=submit_headers, timeout=15, allow_redirects=True)
        
        body = r2.text.lower()
        if 'invalid' in body:
            return {'success': False, 'message': 'Invalid code'}
        elif 'success' in body or 'points' in body or 'congratulations' in body:
            return {'success': True, 'message': '+15 pts!'}
        elif 'already used' in body or 'already redeemed' in body:
            return {'success': False, 'message': 'Already used on Zyn', 'dedup': 'zyn'}
        elif 'technical error' in body:
            return {'success': False, 'message': 'Zyn error — retry'}
        elif 'log in' in body or 'login' in body:
            return {'success': False, 'message': 'Session expired — re-login needed'}
        else:
            return {'success': False, 'message': 'Unknown response'}
    except Exception as e:
        return {'success': False, 'message': str(e)[:80]}

# --- API Routes ---
@app.get("/")
async def index():
    return FileResponse("index.html")

@app.post("/api/login")
async def login(req: Request):
    body = await req.json()
    email = body.get('email', '').strip()
    password = body.get('password', '')
    if not email or not password:
        return JSONResponse({'success': False, 'message': 'Email and password required'})
    result = playwright_login(email, password)
    print(f"LOGIN: {email[:30]} -> {result['message']}")
    return JSONResponse(result)

@app.post("/api/submit")
async def submit(req: Request):
    body = await req.json()
    code = body.get('code', '').strip()
    user_id = body.get('user_id', '').strip()
    if not code or not user_id:
        return JSONResponse({'success': False, 'message': 'Missing code or user_id'})
    
    # Dedup check
    dup_file = os.path.join(DUP_DIR, f"{user_id}.json")
    scanned = set(json.load(open(dup_file))) if os.path.exists(dup_file) else set()
    cu = code.upper()
    if cu in scanned:
        return JSONResponse({'success': False, 'message': 'Already scanned!', 'dedup': 'local'})
    
    result = submit_via_requests(code, user_id)
    
    if result['success']:
        scanned.add(cu)
        with open(dup_file, 'w') as f: json.dump(list(scanned), f)
    elif 'already used' in result.get('message', '').lower():
        scanned.add(cu)
        with open(dup_file, 'w') as f: json.dump(list(scanned), f)
    
    print(f"SUBMIT: [{user_id[:8]}] {code} -> {result['message']}")
    return JSONResponse(result)

@app.post("/api/status")
async def status(req: Request):
    body = await req.json()
    user_id = body.get('user_id', '')
    state_file = os.path.join(SESSION_DIR, f"{user_id}.json")
    if user_id and os.path.exists(state_file):
        return JSONResponse({'logged_in': True, 'user_id': user_id})
    return JSONResponse({'logged_in': False})

if __name__ == "__main__":
    print(f"Starting on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
