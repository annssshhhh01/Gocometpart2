import smtplib, os
from dotenv import load_dotenv
load_dotenv()
addr = os.getenv("GMAIL_ADDRESS","").strip()
pwd  = os.getenv("GMAIL_APP_PASSWORD","").replace(" ","")
print(f"Testing SMTP for {addr}...")

# Try port 465
try:
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as s:
        s.login(addr, pwd)
        print("PORT 465 SSL: OK")
except Exception as e:
    print(f"PORT 465 SSL: FAILED -- {e}")

# Try port 587
try:
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as s:
        s.ehlo()
        s.starttls()
        s.login(addr, pwd)
        print("PORT 587 TLS: OK")
except Exception as e:
    print(f"PORT 587 TLS: FAILED -- {e}")
