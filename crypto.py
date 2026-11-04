import os
from cryptography.fernet import Fernet
from dotenv import load_dotenv


load_dotenv()

def _get_fernet():
    key = os.environ["ENCRYPTION_KEY"]
    return Fernet(key)

def encrypt(text):
    return _get_fernet().encrypt(text.encode()).decode()

def decrypt(token):
    return _get_fernet().decrypt(token.encode()).decode()
