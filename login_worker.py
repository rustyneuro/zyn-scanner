"""Subprocess helper — Playwright login for Zyn"""
import sys, json, hashlib, os
from playwright.sync_api import sync_playwright

email = sys.argv[1]
password = sys.argv[2]
state_dir = sys.argv[3]

uid = hashlib.sha256(email.encode()).hexdigest()[:16]
state_file = os.path.join(state_dir, f"{uid}.json")

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
        ctx = browser.new_context(viewport={'width':1440,'height':900})
        page = ctx.new_page()
        
        page.goto('https://us.zyn.com/ZYNRewards/', timeout=30000)
        page.wait_for_timeout(5000)
        
        # Check if already logged in (rewards code field visible)
        content = page.content()
        if 'ENTER YOUR REWARDS CODE' in content:
            ctx.storage_state(path=state_file)
            browser.close()
            print(json.dumps({'success':True,'user_id':uid,'message':'Already logged in'}))
            sys.exit(0)
        
        # Find email field — try both selectors
        email_el = None
        for sel in ['input[name="Request.Email"]', 'input[type="email"]']:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000):
                    email_el = el
                    break
            except: pass
        
        if not email_el:
            browser.close()
            print(json.dumps({'success':False,'message':'Login form not found'}))
            sys.exit(0)
        
        email_el.fill(email)
        page.locator('input[type="password"]').first.fill(password)
        
        # Check remember me
        try:
            cb = page.locator('input[type="checkbox"]').first
            if not cb.is_checked(): cb.check()
        except: pass
        
        # Click LOG IN
        page.locator('button:has-text("LOG IN")').first.click()
        page.wait_for_timeout(8000)
        
        content = page.content()
        if 'ENTER YOUR REWARDS CODE' in content:
            ctx.storage_state(path=state_file)
            browser.close()
            print(json.dumps({'success':True,'user_id':uid,'message':'Logged in'}))
        elif 'incorrect' in content.lower() or 'invalid' in content.lower():
            browser.close()
            print(json.dumps({'success':False,'message':'Invalid email or password'}))
        else:
            ctx.storage_state(path=state_file)
            browser.close()
            print(json.dumps({'success':True,'user_id':uid,'message':'Logged in'}))
except Exception as e:
    print(json.dumps({'success':False,'message':str(e)[:100]}))
