from .models import User
from .utils import greet, validate_email

def run():
    user = User("Alice", "alice@example.com")
    if validate_email(user.email):
        print(greet(user))
