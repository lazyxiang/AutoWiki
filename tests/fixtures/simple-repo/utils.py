from .models import User

def greet(user: User) -> str:
    return f"Hello, {user.name}!"

def validate_email(email: str) -> bool:
    return "@" in email and "." in email
