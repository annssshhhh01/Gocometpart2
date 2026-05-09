import imaplib, os
from dotenv import load_dotenv
load_dotenv()
addr = os.getenv("GMAIL_ADDRESS","").strip()
pwd  = os.getenv("GMAIL_APP_PASSWORD","").replace(" ","")
print(f"Address : {addr}")
print(f"Password length: {len(pwd)} chars")
try:
    m = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    m.login(addr, pwd)
    print("LOGIN OK -- Gmail IMAP connected successfully")
    status, _ = m.select("INBOX")
    print(f"INBOX selected : {status}")
    status2, _ = m.select("[Gmail]/Spam")
    print(f"Spam selected  : {status2}")
    m.logout()
except Exception as e:
    print(f"ERROR: {e}")
